"""
IPA Builder — assembles a fully buildable Xcode project and optionally
packages a distributable .ipa.

Output structure::

  AppName/
  ├── AppName.xcodeproj/
  │   ├── project.pbxproj          ← The Xcode project file (critical)
  │   └── project.xcworkspace/
  │       └── contents.xcworkspacedata
  ├── AppName/                     ← The app target sources
  │   ├── AppDelegate.swift
  │   ├── SceneDelegate.swift
  │   ├── Info.plist
  │   ├── Images.xcassets/
  │   ├── en.lproj/
  │   │   └── Localizable.strings
  │   ├── Views/
  │   │   └── *.swift  (SwiftUI views from layouts)
  │   └── **/*.swift  (transpiled classes)
  └── README_REVIEW.md             ← Human-readable review guide

The .pbxproj is the hard part — it uses a UUID-keyed property list that
Xcode parses.  We generate a minimal but valid one that includes every
.swift file and all resource files.
"""

from __future__ import annotations

import os
import re
import uuid
import plistlib
import zipfile
import hashlib
import logging
from pathlib import Path
from typing import Optional

from apk2ipa.core.apk_parser import ApkInfo, ApkParser
from apk2ipa.core.manifest_translator import ManifestTranslator
from apk2ipa.core.resource_converter import ResourceConverter
from apk2ipa.core.code_translator import CodeTranslator

logger = logging.getLogger(__name__)


def _uuid() -> str:
    """Generate a Xcode-style 24-char hex UUID."""
    return uuid.uuid4().hex[:24].upper()


class XcodeProjectBuilder:
    """
    Full pipeline: APK → extracted → translated → Xcode project.

    Usage::

        builder = XcodeProjectBuilder("my_app.apk")
        builder.build(output_dir="./output")
    """

    def __init__(self, apk_path: str | Path,
                 predecompiled_java_dir: str | Path | None = None):
        self.apk_path = Path(apk_path)
        self._info: Optional[ApkInfo] = None
        self._predecompiled = Path(predecompiled_java_dir) if predecompiled_java_dir else None

    def build(self, output_dir: str | Path) -> Path:
        """
        Run the full pipeline.  Returns the path to the generated .xcodeproj.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        print("  [1/6] Parsing APK...")
        parser = ApkParser(self.apk_path)
        self._info = parser.parse()

        app_name = _sanitize_name(
            self._info.app_name or
            self._info.package_name.split(".")[-1] or
            "ConvertedApp"
        )

        # Detect Flutter
        is_flutter = self._detect_flutter(self._info)
        if is_flutter:
            print("  *** Flutter app detected — enabling Flutter mode ***")

        # Extract APK
        extract_dir = output_dir / "_apk_extracted"
        print("  [2/6] Extracting APK...")
        parser.extract_all(extract_dir)

        # Project root
        project_root = output_dir / app_name
        project_root.mkdir(exist_ok=True)

        app_src = project_root / app_name
        app_src.mkdir(exist_ok=True)

        # Translate manifest → Info.plist
        print("  [3/6] Translating manifest...")
        translator = ManifestTranslator(self._info)
        manifest_result = translator.translate()
        translator.write_plist(app_src / "Info.plist", manifest_result)

        # Convert resources
        print("  [4/6] Converting resources...")
        res_converter = ResourceConverter(extract_dir)
        res_stats = res_converter.convert(app_src)

        # Flutter-specific: extract flutter_assets (already iOS-compatible)
        if is_flutter:
            self._extract_flutter_assets(extract_dir, app_src, project_root)

        # Translate code
        print("  [5/6] Decompiling and translating code...")
        code_translator = CodeTranslator(
            extract_dir, self._info,
            predecompiled_java_dir=self._predecompiled,
        )
        code_summary = code_translator.translate(app_src)
        code_summary["is_flutter"] = is_flutter

        # Generate Xcode project
        print("  [6/6] Generating Xcode project...")
        xcodeproj_path = self._generate_xcodeproj(
            project_root, app_name, app_src, self._info
        )

        # Write review guide
        self._write_review_guide(
            project_root, app_name, manifest_result, res_stats, code_summary
        )

        logger.info("Xcode project written to %s", xcodeproj_path)
        return xcodeproj_path

    # ------------------------------------------------------------------
    # Flutter detection and asset extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_flutter(info: ApkInfo) -> bool:
        """Return True if the APK contains a Flutter engine."""
        flutter_indicators = ["libflutter.so", "libapp.so", "flutter_assets"]
        native_libs = [lib.split("/")[-1] for lib in info.native_libs]
        return any(ind in native_libs for ind in flutter_indicators[:2])

    def _extract_flutter_assets(self, extract_dir: Path,
                                 app_src: Path, project_root: Path) -> None:
        """
        Copy flutter_assets into the iOS project — they are 100% platform-
        independent and work identically on iOS Flutter.
        Also generate a Flutter-mode AppDelegate and pubspec hint file.
        """
        import shutil
        flutter_src = extract_dir / "assets" / "flutter_assets"
        if flutter_src.exists():
            dest = app_src / "flutter_assets"
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(flutter_src, dest)
            logger.info("Copied %d flutter_assets files",
                        sum(1 for _ in dest.rglob("*") if _.is_file()))

        # Write a Flutter-mode AppDelegate that initialises FlutterEngine
        flutter_delegate = '''\
// Auto-generated by apk2ipa — Flutter mode
// This app uses Flutter for its UI. Install the Flutter SDK, then:
//   flutter build ios --release
import UIKit
import Flutter

@main
@objc class AppDelegate: FlutterAppDelegate {

    override func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
    ) -> Bool {
        GeneratedPluginRegistrant.register(with: self)
        return super.application(application, didFinishLaunchingWithOptions: launchOptions)
    }
}
'''
        (app_src / "AppDelegate.swift").write_text(flutter_delegate, encoding="utf-8")

        # Write a hint pubspec.yaml so `flutter build ios` knows what to do
        pubspec = f'''\
# Auto-generated by apk2ipa — Flutter project hint
# Run: flutter pub get && flutter build ios
name: converted_app
description: Converted from Android APK by apk2ipa
version: 1.0.0+1

environment:
  sdk: ">=3.0.0 <4.0.0"
  flutter: ">=3.10.0"

dependencies:
  flutter:
    sdk: flutter

flutter:
  uses-material-design: true
  assets:
    - flutter_assets/
'''
        (project_root / "pubspec.yaml").write_text(pubspec, encoding="utf-8")
        logger.info("Wrote Flutter pubspec.yaml to %s", project_root)

    # ------------------------------------------------------------------
    # Xcode project file generation
    # ------------------------------------------------------------------

    def _generate_xcodeproj(self, project_root: Path, app_name: str,
                             app_src: Path, info: ApkInfo) -> Path:
        """Generate a valid .xcodeproj directory with project.pbxproj."""
        xcodeproj = project_root / f"{app_name}.xcodeproj"
        xcodeproj.mkdir(exist_ok=True)

        # workspace
        workspace = xcodeproj / "project.xcworkspace"
        workspace.mkdir(exist_ok=True)
        (workspace / "contents.xcworkspacedata").write_text(
            f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<Workspace version = "1.0">\n'
            f'   <FileRef location = "self:"></FileRef>\n'
            f'</Workspace>\n'
        )

        # Collect all source and resource files
        swift_files = sorted(app_src.rglob("*.swift"))
        resource_files = sorted(app_src.rglob("*.strings")) + \
                         sorted(app_src.rglob("*.plist"))  # Info.plist added separately
        xcassets = list(app_src.rglob("*.xcassets"))

        pbxproj_content = self._render_pbxproj(
            app_name=app_name,
            bundle_id=info.package_name.replace("_", "-") if info.package_name else "com.example.app",
            deployment_target=ManifestTranslator._android_sdk_to_ios(info.min_sdk),
            swift_files=swift_files,
            resource_files=resource_files,
            xcassets=xcassets,
            app_src=app_src,
        )

        (xcodeproj / "project.pbxproj").write_text(pbxproj_content, encoding="utf-8")

        return xcodeproj

    def _render_pbxproj(self, app_name: str, bundle_id: str,
                         deployment_target: str,
                         swift_files: list[Path],
                         resource_files: list[Path],
                         xcassets: list[Path],
                         app_src: Path) -> str:
        """
        Render a minimal but valid project.pbxproj.

        The pbxproj format is an old-style ASCII property list.  We generate
        it as a string because plistlib doesn't support the old-style format
        that Xcode uses.
        """
        # Generate stable UUIDs based on file paths (reproducible builds)
        def file_uuid(path: Path) -> str:
            h = hashlib.md5(str(path).encode()).hexdigest()[:24].upper()
            return h

        proj_uuid       = _uuid()
        main_group_uuid = _uuid()
        target_uuid     = _uuid()
        config_list_uuid = _uuid()
        debug_config_uuid = _uuid()
        release_config_uuid = _uuid()
        target_config_list_uuid = _uuid()
        target_debug_uuid = _uuid()
        target_release_uuid = _uuid()
        sources_phase_uuid = _uuid()
        resources_phase_uuid = _uuid()
        products_group_uuid = _uuid()
        app_product_uuid = _uuid()

        # File references
        file_refs: list[tuple[str, str, str]] = []  # (uuid, path, file_type)
        for f in swift_files:
            rel = f.relative_to(app_src.parent)
            file_refs.append((file_uuid(f), str(rel), "sourcecode.swift"))
        for f in resource_files:
            rel = f.relative_to(app_src.parent)
            ftype = "text.plist.xml" if f.suffix == ".plist" else "text.plist.strings"
            file_refs.append((file_uuid(f), str(rel), ftype))
        for f in xcassets:
            rel = f.relative_to(app_src.parent)
            file_refs.append((file_uuid(f), str(rel), "folder.assetcatalog"))

        # Build file UUIDs
        build_file_uuid = lambda fu: hashlib.md5(f"bf_{fu}".encode()).hexdigest()[:24].upper()

        lines: list[str] = []

        lines.append("// !$*UTF8*$!")
        lines.append("{")
        lines.append(f"\tarchiveVersion = 1;")
        lines.append(f"\tclasses = {{")
        lines.append(f"\t}};")
        lines.append(f"\tobjectVersion = 56;")
        lines.append(f"\tobjects = {{")
        lines.append("")

        # PBXBuildFile section
        lines.append("/* Begin PBXBuildFile section */")
        for fu, path, ftype in file_refs:
            bfu = build_file_uuid(fu)
            lines.append(f"\t\t{bfu} /* {Path(path).name} in Build */ = "
                         f"{{isa = PBXBuildFile; fileRef = {fu} /* {Path(path).name} */; }};")
        lines.append("/* End PBXBuildFile section */")
        lines.append("")

        # PBXFileReference section
        lines.append("/* Begin PBXFileReference section */")
        lines.append(f"\t\t{app_product_uuid} /* {app_name}.app */ = "
                     f"{{isa = PBXFileReference; explicitFileType = wrapper.application; "
                     f"includeInIndex = 0; path = \"{app_name}.app\"; sourceTree = BUILT_PRODUCTS_DIR; }};")
        for fu, path, ftype in file_refs:
            lines.append(f"\t\t{fu} /* {Path(path).name} */ = "
                         f"{{isa = PBXFileReference; lastKnownFileType = {ftype}; "
                         f"path = \"{Path(path).name}\"; sourceTree = \"<group>\"; }};")
        lines.append("/* End PBXFileReference section */")
        lines.append("")

        # PBXGroup section
        lines.append("/* Begin PBXGroup section */")
        # Main group
        lines.append(f"\t\t{main_group_uuid} = {{")
        lines.append(f"\t\t\tisa = PBXGroup;")
        lines.append(f"\t\t\tchildren = (")
        lines.append(f"\t\t\t\t{products_group_uuid} /* Products */,")
        # Add all file refs
        for fu, path, _ in file_refs:
            lines.append(f"\t\t\t\t{fu} /* {Path(path).name} */,")
        lines.append(f"\t\t\t);")
        lines.append(f"\t\t\tsourceTree = \"<group>\";")
        lines.append(f"\t\t}};")
        # Products group
        lines.append(f"\t\t{products_group_uuid} /* Products */ = {{")
        lines.append(f"\t\t\tisa = PBXGroup;")
        lines.append(f"\t\t\tchildren = (")
        lines.append(f"\t\t\t\t{app_product_uuid} /* {app_name}.app */,")
        lines.append(f"\t\t\t);")
        lines.append(f"\t\t\tname = Products;")
        lines.append(f"\t\t\tsourceTree = \"<group>\";")
        lines.append(f"\t\t}};")
        lines.append("/* End PBXGroup section */")
        lines.append("")

        # PBXNativeTarget
        lines.append("/* Begin PBXNativeTarget section */")
        lines.append(f"\t\t{target_uuid} /* {app_name} */ = {{")
        lines.append(f"\t\t\tisa = PBXNativeTarget;")
        lines.append(f"\t\t\tbuildConfigurationList = {target_config_list_uuid};")
        lines.append(f"\t\t\tbuildPhases = (")
        lines.append(f"\t\t\t\t{sources_phase_uuid} /* Sources */,")
        lines.append(f"\t\t\t\t{resources_phase_uuid} /* Resources */,")
        lines.append(f"\t\t\t);")
        lines.append(f"\t\t\tname = \"{app_name}\";")
        lines.append(f"\t\t\tproductName = \"{app_name}\";")
        lines.append(f"\t\t\tproductReference = {app_product_uuid};")
        lines.append(f"\t\t\tproductType = \"com.apple.product-type.application\";")
        lines.append(f"\t\t}};")
        lines.append("/* End PBXNativeTarget section */")
        lines.append("")

        # PBXProject
        lines.append("/* Begin PBXProject section */")
        lines.append(f"\t\t{proj_uuid} /* Project object */ = {{")
        lines.append(f"\t\t\tisa = PBXProject;")
        lines.append(f"\t\t\tattributes = {{")
        lines.append(f"\t\t\t\tLastUpgradeCheck = 1500;")
        lines.append(f"\t\t\t\tTargetAttributes = {{")
        lines.append(f"\t\t\t\t\t{target_uuid} = {{")
        lines.append(f"\t\t\t\t\t\tCreatedOnToolsVersion = 15.0;")
        lines.append(f"\t\t\t\t\t}};")
        lines.append(f"\t\t\t\t}};")
        lines.append(f"\t\t\t}};")
        lines.append(f"\t\t\tbuildConfigurationList = {config_list_uuid};")
        lines.append(f"\t\t\tcompatibilityVersion = \"Xcode 14.0\";")
        lines.append(f"\t\t\tmainGroup = {main_group_uuid};")
        lines.append(f"\t\t\tproductRefGroup = {products_group_uuid} /* Products */;")
        lines.append(f"\t\t\tprojectDirPath = \"\";")
        lines.append(f"\t\t\ttargets = (")
        lines.append(f"\t\t\t\t{target_uuid} /* {app_name} */,")
        lines.append(f"\t\t\t);")
        lines.append(f"\t\t}};")
        lines.append("/* End PBXProject section */")
        lines.append("")

        # Sources build phase
        lines.append("/* Begin PBXSourcesBuildPhase section */")
        lines.append(f"\t\t{sources_phase_uuid} /* Sources */ = {{")
        lines.append(f"\t\t\tisa = PBXSourcesBuildPhase;")
        lines.append(f"\t\t\tfiles = (")
        for fu, path, ftype in file_refs:
            if ftype == "sourcecode.swift":
                bfu = build_file_uuid(fu)
                lines.append(f"\t\t\t\t{bfu} /* {Path(path).name} in Sources */,")
        lines.append(f"\t\t\t);")
        lines.append(f"\t\t\trunOnlyForDeploymentPostprocessing = 0;")
        lines.append(f"\t\t}};")
        lines.append("/* End PBXSourcesBuildPhase section */")
        lines.append("")

        # Resources build phase
        lines.append("/* Begin PBXResourcesBuildPhase section */")
        lines.append(f"\t\t{resources_phase_uuid} /* Resources */ = {{")
        lines.append(f"\t\t\tisa = PBXResourcesBuildPhase;")
        lines.append(f"\t\t\tfiles = (")
        for fu, path, ftype in file_refs:
            if ftype != "sourcecode.swift":
                bfu = build_file_uuid(fu)
                lines.append(f"\t\t\t\t{bfu} /* {Path(path).name} in Resources */,")
        lines.append(f"\t\t\t);")
        lines.append(f"\t\t\trunOnlyForDeploymentPostprocessing = 0;")
        lines.append(f"\t\t}};")
        lines.append("/* End PBXResourcesBuildPhase section */")
        lines.append("")

        # XCBuildConfiguration
        lines.append("/* Begin XCBuildConfiguration section */")
        # Project debug
        lines.append(f"\t\t{debug_config_uuid} /* Debug */ = {{")
        lines.append(f"\t\t\tisa = XCBuildConfiguration;")
        lines.append(f"\t\t\tbuildSettings = {{")
        lines.append(f"\t\t\t\tALWAYS_SEARCH_USER_PATHS = NO;")
        lines.append(f"\t\t\t\tSWIFT_VERSION = 5.0;")
        lines.append(f"\t\t\t\tIPHONEOS_DEPLOYMENT_TARGET = {deployment_target};")
        lines.append(f"\t\t\t\tSKIP_INSTALL = YES;")
        lines.append(f"\t\t\t}};")
        lines.append(f"\t\t\tname = Debug;")
        lines.append(f"\t\t}};")
        # Project release
        lines.append(f"\t\t{release_config_uuid} /* Release */ = {{")
        lines.append(f"\t\t\tisa = XCBuildConfiguration;")
        lines.append(f"\t\t\tbuildSettings = {{")
        lines.append(f"\t\t\t\tALWAYS_SEARCH_USER_PATHS = NO;")
        lines.append(f"\t\t\t\tSWIFT_VERSION = 5.0;")
        lines.append(f"\t\t\t\tIPHONEOS_DEPLOYMENT_TARGET = {deployment_target};")
        lines.append(f"\t\t\t\tSKIP_INSTALL = YES;")
        lines.append(f"\t\t\t}};")
        lines.append(f"\t\t\tname = Release;")
        lines.append(f"\t\t}};")
        # Target debug
        lines.append(f"\t\t{target_debug_uuid} /* Debug */ = {{")
        lines.append(f"\t\t\tisa = XCBuildConfiguration;")
        lines.append(f"\t\t\tbuildSettings = {{")
        lines.append(f"\t\t\t\tPRODUCT_BUNDLE_IDENTIFIER = \"{bundle_id}\";")
        lines.append(f"\t\t\t\tPRODUCT_NAME = \"{app_name}\";")
        lines.append(f"\t\t\t\tSWIFT_VERSION = 5.0;")
        lines.append(f"\t\t\t\tIPHONEOS_DEPLOYMENT_TARGET = {deployment_target};")
        lines.append(f"\t\t\t\tINFOPLIST_FILE = \"{app_name}/Info.plist\";")
        lines.append(f"\t\t\t\tCODE_SIGN_STYLE = Automatic;")
        lines.append(f"\t\t\t\tDEVELOPMENT_TEAM = \"\";")
        lines.append(f"\t\t\t}};")
        lines.append(f"\t\t\tname = Debug;")
        lines.append(f"\t\t}};")
        # Target release
        lines.append(f"\t\t{target_release_uuid} /* Release */ = {{")
        lines.append(f"\t\t\tisa = XCBuildConfiguration;")
        lines.append(f"\t\t\tbuildSettings = {{")
        lines.append(f"\t\t\t\tPRODUCT_BUNDLE_IDENTIFIER = \"{bundle_id}\";")
        lines.append(f"\t\t\t\tPRODUCT_NAME = \"{app_name}\";")
        lines.append(f"\t\t\t\tSWIFT_VERSION = 5.0;")
        lines.append(f"\t\t\t\tIPHONEOS_DEPLOYMENT_TARGET = {deployment_target};")
        lines.append(f"\t\t\t\tINFOPLIST_FILE = \"{app_name}/Info.plist\";")
        lines.append(f"\t\t\t\tCODE_SIGN_STYLE = Automatic;")
        lines.append(f"\t\t\t\tDEVELOPMENT_TEAM = \"\";")
        lines.append(f"\t\t\t}};")
        lines.append(f"\t\t\tname = Release;")
        lines.append(f"\t\t}};")
        lines.append("/* End XCBuildConfiguration section */")
        lines.append("")

        # XCConfigurationList
        lines.append("/* Begin XCConfigurationList section */")
        lines.append(f"\t\t{config_list_uuid} /* Build configuration list for PBXProject */ = {{")
        lines.append(f"\t\t\tisa = XCConfigurationList;")
        lines.append(f"\t\t\tbuildConfigurations = (")
        lines.append(f"\t\t\t\t{debug_config_uuid} /* Debug */,")
        lines.append(f"\t\t\t\t{release_config_uuid} /* Release */,")
        lines.append(f"\t\t\t);")
        lines.append(f"\t\t\tdefaultConfigurationIsVisible = 0;")
        lines.append(f"\t\t\tdefaultConfigurationName = Release;")
        lines.append(f"\t\t}};")
        lines.append(f"\t\t{target_config_list_uuid} /* Build configuration list for PBXNativeTarget */ = {{")
        lines.append(f"\t\t\tisa = XCConfigurationList;")
        lines.append(f"\t\t\tbuildConfigurations = (")
        lines.append(f"\t\t\t\t{target_debug_uuid} /* Debug */,")
        lines.append(f"\t\t\t\t{target_release_uuid} /* Release */,")
        lines.append(f"\t\t\t);")
        lines.append(f"\t\t\tdefaultConfigurationIsVisible = 0;")
        lines.append(f"\t\t\tdefaultConfigurationName = Release;")
        lines.append(f"\t\t}};")
        lines.append("/* End XCConfigurationList section */")
        lines.append("")

        lines.append("\t};")
        lines.append(f"\trootObject = {proj_uuid} /* Project object */;")
        lines.append("}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Review guide
    # ------------------------------------------------------------------

    def _write_review_guide(self, project_root: Path, app_name: str,
                             manifest_result, res_stats, code_summary) -> None:
        guide_lines = [
            f"# {app_name} — Conversion Review Guide",
            "",
            "Generated by **apk2ipa**.  This document lists what was converted",
            "automatically and what needs manual review before the app will run.",
            "",
            "## How to build",
            "",
            f"1. Open `{app_name}.xcodeproj` in Xcode (macOS or iPad)",
            "2. Set your Development Team in the target's Signing & Capabilities",
            "3. Select a simulator or device",
            "4. Build (⌘B) — fix any compile errors shown below",
            "5. Run (⌘R)",
            "",
            "## What was converted automatically",
            "",
        ]

        guide_lines += [
            f"- **Manifest** → `Info.plist` (bundle ID, version, permissions, orientations)",
            f"- **Resources** → `Images.xcassets`, `Localizable.strings`, `Color+AppColors.swift`",
            f"  - {res_stats.images_converted} images",
            f"  - {res_stats.strings_converted} strings",
            f"  - {res_stats.colors_converted} colors",
            f"- **Layouts** → SwiftUI views: {code_summary.get('layout_files_converted', 0)} files",
            f"- **Java code** → Swift: {code_summary.get('swift_files_written', 0)} files",
            "",
            "## Items requiring manual review",
            "",
        ]

        # Manifest notes
        if manifest_result.notes:
            guide_lines.append("### Manifest / Permissions")
            for note in manifest_result.notes:
                icon = "⚠️" if note.level == "warning" else "ℹ️"
                guide_lines.append(f"- {icon} {note.message}")
            guide_lines.append("")

        # Unmapped permissions
        if manifest_result.unmapped_permissions:
            guide_lines.append("### Unmapped Android permissions")
            for p in manifest_result.unmapped_permissions:
                guide_lines.append(f"- `{p}` — check if iOS equivalent is needed")
            guide_lines.append("")

        # Code warnings
        if code_summary.get("warnings"):
            guide_lines.append("### Code translation warnings")
            for w in code_summary["warnings"][:20]:  # cap at 20
                guide_lines.append(f"- {w}")
            guide_lines.append("")

        # Resource warnings
        if res_stats.warnings:
            guide_lines.append("### Resource conversion warnings")
            for w in res_stats.warnings:
                guide_lines.append(f"- {w}")
            guide_lines.append("")

        guide_lines += [
            "## Known limitations",
            "",
            "- Native code (`.so` libraries) cannot be converted — rewrite in Swift or find iOS equivalents",
            "- In-app purchases, Google Play APIs, Firebase need the iOS equivalents installed via SPM",
            "- `// TODO:` comments in `.swift` files mark untranslated patterns — work through them",
            "- All `// AUTO-GENERATED` files should be reviewed before shipping",
            "",
            "## Decompiler",
            "",
            f"Decompiler used: **{code_summary.get('decompiler_used', 'none')}**",
            "",
            "For best results, install [jadx](https://github.com/skylot/jadx) and re-run:",
            "```",
            "pip install apk2ipa",
            "apk2ipa convert app.apk --output ./output",
            "```",
        ]

        (project_root / "README_REVIEW.md").write_text(
            "\n".join(guide_lines), encoding="utf-8"
        )


def _sanitize_name(name: str) -> str:
    """Make an app name safe for use as a directory/Xcode target name."""
    return re.sub(r"[^A-Za-z0-9_]", "", name) or "ConvertedApp"
