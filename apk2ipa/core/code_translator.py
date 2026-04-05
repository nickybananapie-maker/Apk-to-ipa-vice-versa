"""
Code Translator — orchestrates DEX decompilation and Java→Swift transpilation.

Decompilation strategy (in order of preference):
  1. jadx (external tool, best quality Java output)
  2. androguard (Python-native, good pseudocode)
  3. Raw DEX string scanning (last resort — extracts class/method names only)

The output is a directory of .swift files mirroring the original package structure.
"""

from __future__ import annotations

import os
import re
import subprocess
import logging
import shutil
from pathlib import Path
from typing import Optional

from apk2ipa.translators.java_to_swift import JavaToSwiftTranspiler
from apk2ipa.translators.layout_to_swiftui import LayoutConverter

logger = logging.getLogger(__name__)


class CodeTranslator:
    """
    Decompile and translate all code from an extracted APK directory.

    Usage::

        translator = CodeTranslator(apk_root="/tmp/extracted", apk_info=info)
        translator.translate(output_dir="/tmp/ios_src")
    """

    def __init__(self, apk_root: str | Path, apk_info=None):
        self.apk_root = Path(apk_root)
        self.apk_info = apk_info
        self._transpiler = JavaToSwiftTranspiler()
        self._layout_converter = LayoutConverter()
        self._decompiled_java: Optional[Path] = None
        self._proguard_map: dict[str, str] = {}  # obfuscated → original names

    def translate(self, output_dir: str | Path) -> dict:
        """
        Run full code translation pipeline.
        Returns a summary dict with counts and warnings.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        summary = {
            "java_files_found": 0,
            "swift_files_written": 0,
            "layout_files_converted": 0,
            "warnings": [],
            "decompiler_used": "none",
            "obfuscation_detected": False,
            "proguard_mappings_loaded": 0,
        }

        # Step 0: Load ProGuard mapping if available
        self._load_proguard_mapping(summary)

        # Step 1: Decompile DEX → Java
        java_dir = self._decompile_dex(summary)

        # Step 2: Transpile Java → Swift (with parallel processing for speed)
        if java_dir and java_dir.exists():
            self._transpile_java_dir_parallel(java_dir, output_dir, summary)

        # Step 3: Convert XML layouts → SwiftUI
        self._convert_layouts(output_dir, summary)

        # Step 4: Generate boilerplate files
        self._generate_app_boilerplate(output_dir)

        return summary

    # ------------------------------------------------------------------
    # ProGuard / Obfuscation handling
    # ------------------------------------------------------------------

    def _load_proguard_mapping(self, summary: dict) -> None:
        """Load ProGuard/R8 mapping.txt if present in the APK."""
        # Common locations for mapping files
        mapping_candidates = [
            self.apk_root / "mapping.txt",
            self.apk_root / "proguard" / "mapping.txt",
            self.apk_root / "META-INF" / "mapping.txt",
        ]

        for path in mapping_candidates:
            if path.exists():
                try:
                    self._parse_proguard_mapping(path)
                    summary["proguard_mappings_loaded"] = len(self._proguard_map)
                    logger.info("Loaded %d ProGuard mappings from %s",
                                len(self._proguard_map), path)
                    return
                except Exception as e:
                    logger.warning("Failed to parse ProGuard mapping: %s", e)

    def _parse_proguard_mapping(self, path: Path) -> None:
        """Parse a ProGuard mapping.txt file."""
        current_class_obf = ""
        current_class_orig = ""

        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.rstrip()
            if not line or line.startswith("#"):
                continue

            if not line.startswith(" ") and " -> " in line and line.endswith(":"):
                # Class mapping: com.original.Name -> a.b.c:
                parts = line.rstrip(":").split(" -> ")
                if len(parts) == 2:
                    original = parts[0].strip()
                    obfuscated = parts[1].strip()
                    self._proguard_map[obfuscated] = original
                    current_class_obf = obfuscated
                    current_class_orig = original

            elif line.startswith("    ") and " -> " in line:
                # Member mapping:     originalMethod -> a
                parts = line.strip().split(" -> ")
                if len(parts) == 2 and current_class_obf:
                    original = parts[0].strip().split()[-1]  # get name after type
                    obfuscated = parts[1].strip()
                    key = f"{current_class_obf}.{obfuscated}"
                    self._proguard_map[key] = f"{current_class_orig}.{original}"

    def _detect_obfuscation(self, java_dir: Path, summary: dict) -> None:
        """Detect if the code is obfuscated by checking class/method name patterns."""
        short_name_count = 0
        total_files = 0

        for java_file in java_dir.rglob("*.java"):
            total_files += 1
            name = java_file.stem
            # Single letter or very short names suggest obfuscation
            if len(name) <= 2 and name.isalpha():
                short_name_count += 1

        if total_files > 0:
            ratio = short_name_count / total_files
            if ratio > 0.3:  # More than 30% short names
                summary["obfuscation_detected"] = True
                summary["warnings"].append(
                    f"Obfuscation detected: {short_name_count}/{total_files} classes "
                    f"have very short names ({ratio:.0%}). "
                    "Code quality will be limited. If you have a ProGuard mapping.txt, "
                    "place it alongside the APK."
                )

    def _deobfuscate_source(self, source: str) -> str:
        """Apply ProGuard mappings to deobfuscate class/method names in source."""
        if not self._proguard_map:
            return source
        result = source
        # Sort by length descending to avoid partial replacements
        for obf, orig in sorted(self._proguard_map.items(),
                                  key=lambda x: len(x[0]), reverse=True):
            # Only replace full identifiers (word boundaries)
            short_obf = obf.split(".")[-1]
            short_orig = orig.split(".")[-1]
            if short_obf and short_orig and short_obf != short_orig:
                result = re.sub(rf"\b{re.escape(short_obf)}\b", short_orig, result)
        return result

    # ------------------------------------------------------------------
    # DEX Decompilation
    # ------------------------------------------------------------------

    def _decompile_dex(self, summary: dict) -> Optional[Path]:
        """Try all decompilers in preference order."""
        dex_files = list(self.apk_root.glob("classes*.dex"))
        if not dex_files:
            summary["warnings"].append("No .dex files found in APK")
            return None

        java_dir = self.apk_root / "_decompiled_java"

        # Try jadx first
        if shutil.which("jadx"):
            try:
                result = subprocess.run(
                    ["jadx", "--output-dir", str(java_dir),
                     "--deobf",
                     str(self.apk_root / "classes.dex")],
                    capture_output=True, text=True, timeout=300
                )
                if result.returncode == 0 and java_dir.exists():
                    summary["decompiler_used"] = "jadx"
                    logger.info("Decompiled with jadx")
                    return java_dir / "sources"
                else:
                    logger.warning("jadx failed: %s", result.stderr[:200])
            except Exception as e:
                logger.warning("jadx error: %s", e)

        # Try androguard
        try:
            return self._decompile_with_androguard(java_dir, summary)
        except ImportError:
            pass
        except Exception as e:
            summary["warnings"].append(f"androguard decompilation failed: {e}")

        # Last resort: extract class names from DEX
        self._extract_class_stubs(dex_files, java_dir, summary)
        summary["decompiler_used"] = "stub_extraction"
        return java_dir

    def _decompile_with_androguard(self, java_dir: Path, summary: dict) -> Optional[Path]:
        """Use androguard to decompile DEX files."""
        from androguard.misc import AnalyzeAPK
        from androguard.core.bytecodes import dvm

        java_dir.mkdir(parents=True, exist_ok=True)

        apk_files = list(self.apk_root.parent.glob("*.apk"))
        if not apk_files:
            # Try to reconstruct path
            raise ValueError("Cannot find original APK for androguard analysis")

        a, d, dx = AnalyzeAPK(str(apk_files[0]))
        summary["decompiler_used"] = "androguard"

        for dex in d:
            for cls in dex.get_classes():
                self._write_androguard_class(cls, dx, java_dir)

        return java_dir

    def _write_androguard_class(self, cls, dx, java_dir: Path) -> None:
        """Write a single decompiled class to a .java file."""
        try:
            from androguard.core.analysis.decompiler import DAD

            class_name = cls.get_name()
            # Convert Lcom/example/MyClass; → com/example/MyClass.java
            java_path = class_name.lstrip("L").rstrip(";").replace("/", os.sep) + ".java"
            dest = java_dir / java_path
            dest.parent.mkdir(parents=True, exist_ok=True)

            # Get decompiled source
            dad = DAD(cls, dx)
            source = dad.get_source()
            if source:
                dest.write_text(source, encoding="utf-8")
        except Exception:
            pass

    def _extract_class_stubs(self, dex_files: list[Path],
                              java_dir: Path, summary: dict) -> None:
        """
        Without a decompiler, scan DEX for class/method names and generate stubs.
        This produces compilable (empty) Swift stubs so the project structure exists.
        """
        java_dir.mkdir(parents=True, exist_ok=True)
        classes: set[str] = set()

        for dex_file in dex_files:
            data = dex_file.read_bytes()
            # DEX string pool scanning for class descriptors
            # Pattern: Lcom/example/ClassName;
            for m in re.finditer(rb"L([a-zA-Z][a-zA-Z0-9_/\$]*);", data):
                descriptor = m.group(1).decode("ascii", errors="ignore")
                if "/" in descriptor and not descriptor.startswith("java/") \
                        and not descriptor.startswith("android/") \
                        and not descriptor.startswith("kotlin/"):
                    classes.add(descriptor)

        for descriptor in sorted(classes):
            parts = descriptor.split("/")
            class_name = parts[-1].split("$")[0]  # strip inner class suffix
            pkg = ".".join(parts[:-1])

            java_path = descriptor.replace("/", os.sep) + ".java"
            dest = java_dir / java_path
            dest.parent.mkdir(parents=True, exist_ok=True)

            # Write a minimal Java stub
            stub = (
                f"package {pkg};\n\n"
                f"// STUB: Decompiler not available. Install jadx for full source.\n"
                f"public class {class_name} {{\n"
                f"    // TODO: Implement based on original APK behaviour\n"
                f"}}\n"
            )
            dest.write_text(stub, encoding="utf-8")

        summary["warnings"].append(
            f"No decompiler available — generated {len(classes)} class stubs. "
            "Install jadx (https://github.com/skylot/jadx) for full decompilation."
        )

    # ------------------------------------------------------------------
    # Java → Swift Transpilation
    # ------------------------------------------------------------------

    def _transpile_java_dir(self, java_dir: Path,
                             output_dir: Path, summary: dict) -> None:
        """Walk all .java files and transpile each one (sequential)."""
        for java_file in sorted(java_dir.rglob("*.java")):
            summary["java_files_found"] += 1
            try:
                source = java_file.read_text(encoding="utf-8", errors="replace")

                # Apply deobfuscation if mappings available
                source = self._deobfuscate_source(source)

                class_name = java_file.stem

                result = self._transpiler.transpile(source, class_name)

                # Mirror directory structure
                rel = java_file.relative_to(java_dir)
                swift_path = output_dir / rel.with_suffix(".swift")
                swift_path.parent.mkdir(parents=True, exist_ok=True)
                swift_path.write_text(result.swift_source, encoding="utf-8")

                summary["swift_files_written"] += 1

                if result.issues:
                    summary["warnings"].append(
                        f"{java_file.name}: {len(result.issues)} translation issues"
                    )
            except Exception as e:
                summary["warnings"].append(f"Failed to transpile {java_file.name}: {e}")
                logger.debug("Transpile error for %s: %s", java_file, e)

    def _transpile_java_dir_parallel(self, java_dir: Path,
                                       output_dir: Path, summary: dict) -> None:
        """Walk all .java files and transpile using thread pool for speed."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        java_files = sorted(java_dir.rglob("*.java"))
        summary["java_files_found"] = len(java_files)

        if not java_files:
            return

        # Detect obfuscation
        self._detect_obfuscation(java_dir, summary)

        def transpile_one(java_file: Path) -> tuple[bool, str]:
            """Transpile a single file. Returns (success, warning_or_empty)."""
            try:
                source = java_file.read_text(encoding="utf-8", errors="replace")
                source = self._deobfuscate_source(source)
                class_name = java_file.stem

                result = self._transpiler.transpile(source, class_name)

                rel = java_file.relative_to(java_dir)
                swift_path = output_dir / rel.with_suffix(".swift")
                swift_path.parent.mkdir(parents=True, exist_ok=True)
                swift_path.write_text(result.swift_source, encoding="utf-8")

                warning = ""
                if result.issues:
                    warning = f"{java_file.name}: {len(result.issues)} translation issues"
                return (True, warning)
            except Exception as e:
                return (False, f"Failed to transpile {java_file.name}: {e}")

        # Use thread pool — javalang parsing is CPU-bound but GIL-limited,
        # file I/O is the real bottleneck for large APKs
        workers = min(8, len(java_files))

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(transpile_one, f): f for f in java_files}

            for future in as_completed(futures):
                success, warning = future.result()
                if success:
                    summary["swift_files_written"] += 1
                if warning:
                    summary["warnings"].append(warning)

    # ------------------------------------------------------------------
    # Layout conversion
    # ------------------------------------------------------------------

    def _convert_layouts(self, output_dir: Path, summary: dict) -> None:
        """Convert all Android XML layouts to SwiftUI files."""
        res_dir = self.apk_root / "res"
        if not res_dir.exists():
            return

        views_dir = output_dir / "Views"
        views_dir.mkdir(exist_ok=True)

        for layout_dir in sorted(res_dir.iterdir()):
            if not layout_dir.name.startswith("layout"):
                continue
            for xml_file in sorted(layout_dir.glob("*.xml")):
                try:
                    xml_source = xml_file.read_text(encoding="utf-8", errors="replace")
                    view_name = _to_swift_type_name(xml_file.stem)
                    swift_code = self._layout_converter.convert(xml_source, view_name)

                    dest = views_dir / f"{view_name}.swift"
                    dest.write_text(swift_code, encoding="utf-8")
                    summary["layout_files_converted"] += 1
                except Exception as e:
                    summary["warnings"].append(
                        f"Layout conversion failed for {xml_file.name}: {e}"
                    )

    # ------------------------------------------------------------------
    # App boilerplate
    # ------------------------------------------------------------------

    def _generate_app_boilerplate(self, output_dir: Path) -> None:
        """Generate AppDelegate.swift and the app entry point."""
        app_name = "ConvertedApp"
        if self.apk_info:
            app_name = _to_swift_type_name(
                self.apk_info.app_name or
                (self.apk_info.package_name.split(".")[-1] if self.apk_info.package_name else "ConvertedApp")
            )

        main_vc = "UIViewController"
        if self.apk_info and self.apk_info.main_activity:
            # e.g. com.example.app.MainActivity → MainActivityViewController (mapped name)
            raw = self.apk_info.main_activity.split(".")[-1]
            main_vc = raw  # the transpiled class will have this name

        # AppDelegate.swift
        app_delegate = f'''\
// Auto-generated by apk2ipa
import UIKit

@main
class AppDelegate: UIResponder, UIApplicationDelegate {{

    var window: UIWindow?

    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
    ) -> Bool {{
        window = UIWindow(frame: UIScreen.main.bounds)
        window?.rootViewController = UINavigationController(
            rootViewController: {main_vc}()
        )
        window?.makeKeyAndVisible()
        return true
    }}
}}
'''
        (output_dir / "AppDelegate.swift").write_text(app_delegate, encoding="utf-8")

        # SceneDelegate.swift (for iOS 13+ scene lifecycle)
        scene_delegate = f'''\
// Auto-generated by apk2ipa
import UIKit

class SceneDelegate: UIResponder, UIWindowSceneDelegate {{

    var window: UIWindow?

    func scene(
        _ scene: UIScene,
        willConnectTo session: UISceneSession,
        options connectionOptions: UIScene.ConnectionOptions
    ) {{
        guard let windowScene = (scene as? UIWindowScene) else {{ return }}
        window = UIWindow(windowScene: windowScene)
        window?.rootViewController = UINavigationController(
            rootViewController: {main_vc}()
        )
        window?.makeKeyAndVisible()
    }}
}}
'''
        (output_dir / "SceneDelegate.swift").write_text(scene_delegate, encoding="utf-8")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_swift_type_name(name: str) -> str:
    """
    Convert an Android name (snake_case, kebab-case, or mixed) to a Swift
    type name (UpperCamelCase).
    """
    # Split on underscores, hyphens, spaces
    parts = re.split(r"[_\-\s]+", name)
    return "".join(p.capitalize() for p in parts if p)
