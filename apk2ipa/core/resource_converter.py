"""
Resource Converter — Android resources → iOS Asset Catalog + Localizable.strings

Converts:
  res/drawable*/         → Images.xcassets/
  res/mipmap*/           → Images.xcassets/AppIcon.appiconset/
  res/values/strings.xml → en.lproj/Localizable.strings
  res/values/colors.xml  → Color.swift (Color extension with named colors)
  res/values/dimens.xml  → Dimensions.swift (CGFloat constants)
  res/values/styles.xml  → Styles.swift (commented mappings)
  res/raw/               → Resources/ (copied as-is)
  assets/                → Resources/ (copied as-is)
"""

from __future__ import annotations

import os
import json
import shutil
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Android density buckets → iOS scale factors
DENSITY_MAP = {
    "mdpi":    "@1x",
    "hdpi":    "@2x",   # approximate
    "xhdpi":   "@2x",
    "xxhdpi":  "@3x",
    "xxxhdpi": "@3x",
    "nodpi":   "@1x",
    "tvdpi":   "@2x",
}


@dataclass
class ConversionStats:
    images_converted: int = 0
    strings_converted: int = 0
    colors_converted: int = 0
    dimensions_converted: int = 0
    files_copied: int = 0
    sounds_copied: int = 0
    fonts_copied: int = 0
    videos_copied: int = 0
    game_data_copied: int = 0
    configs_copied: int = 0
    native_libs_cataloged: int = 0
    xml_resources_copied: int = 0
    other_files_copied: int = 0
    total_bytes_copied: int = 0
    warnings: list[str] = field(default_factory=list)


class ResourceConverter:
    """
    Convert Android resources extracted from an APK into iOS resource formats.

    Usage::

        converter = ResourceConverter(apk_root="/tmp/extracted_apk")
        stats = converter.convert(output_dir="/tmp/ios_project")
    """

    def __init__(self, apk_root: str | Path):
        self.apk_root = Path(apk_root)
        self._stats = ConversionStats()

    def convert(self, output_dir: str | Path, apk_info=None) -> ConversionStats:
        """Run all conversions. Returns stats. Pass apk_info for full-fidelity mode."""
        self._stats = ConversionStats()
        output_dir = Path(output_dir)

        self._convert_drawables(output_dir)
        self._convert_mipmaps(output_dir)
        self._convert_strings(output_dir)
        self._convert_colors(output_dir)
        self._convert_dimens(output_dir)
        self._copy_assets(output_dir)
        self._copy_raw(output_dir)

        # Full-fidelity: copy everything else that exists
        self._copy_sounds(output_dir)
        self._copy_fonts(output_dir)
        self._copy_videos(output_dir)
        self._copy_game_data(output_dir)
        self._copy_xml_resources(output_dir)
        self._copy_native_libs(output_dir, apk_info)
        self._copy_remaining_files(output_dir)

        return self._stats

    # ------------------------------------------------------------------
    # Drawables → xcassets
    # ------------------------------------------------------------------

    def _convert_drawables(self, output_dir: Path) -> None:
        """Convert drawable directories to Images.xcassets."""
        xcassets = output_dir / "Images.xcassets"
        xcassets.mkdir(parents=True, exist_ok=True)

        # Write xcassets Contents.json
        (xcassets / "Contents.json").write_text(
            json.dumps({"info": {"author": "apk2ipa", "version": 1}}, indent=2)
        )

        res_dir = self.apk_root / "res"
        if not res_dir.exists():
            return

        # Group files by base name across densities
        image_groups: dict[str, dict[str, Path]] = {}

        for subdir in sorted(res_dir.iterdir()):
            if not subdir.name.startswith("drawable"):
                continue
            density = self._extract_density(subdir.name)
            for f in subdir.iterdir():
                if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                    base = f.stem
                    if base not in image_groups:
                        image_groups[base] = {}
                    image_groups[base][density] = f

        for base, density_files in image_groups.items():
            self._write_imageset(xcassets, base, density_files)
            self._stats.images_converted += 1

    def _convert_mipmaps(self, output_dir: Path) -> None:
        """Convert mipmap icons to AppIcon.appiconset."""
        res_dir = self.apk_root / "res"
        if not res_dir.exists():
            return

        xcassets = output_dir / "Images.xcassets"
        icon_set = xcassets / "AppIcon.appiconset"
        icon_set.mkdir(parents=True, exist_ok=True)

        # iOS icon sizes we need (size × scale → filename)
        ios_icon_slots = [
            (20, 2, "Icon-20@2x.png"),
            (20, 3, "Icon-20@3x.png"),
            (29, 2, "Icon-29@2x.png"),
            (29, 3, "Icon-29@3x.png"),
            (40, 2, "Icon-40@2x.png"),
            (40, 3, "Icon-40@3x.png"),
            (60, 2, "Icon-60@2x.png"),
            (60, 3, "Icon-60@3x.png"),
            (76, 1, "Icon-76.png"),
            (76, 2, "Icon-76@2x.png"),
            (83, 2, "Icon-83.5@2x.png"),
            (1024, 1, "Icon-1024.png"),
        ]

        # Find the best source icon
        source_icon: Optional[Path] = None
        for subdir in sorted(res_dir.iterdir(), reverse=True):
            if not subdir.name.startswith("mipmap"):
                continue
            for name in ("ic_launcher.png", "ic_launcher_round.png",
                         "ic_launcher_foreground.png"):
                candidate = subdir / name
                if candidate.exists():
                    source_icon = candidate
                    break
            if source_icon:
                break

        images_json: list[dict] = []

        if source_icon:
            try:
                from PIL import Image as PILImage
                with PILImage.open(source_icon) as img:
                    for size, scale, filename in ios_icon_slots:
                        px = size * scale
                        resized = img.resize((px, px), PILImage.LANCZOS)
                        resized.save(str(icon_set / filename))
                        images_json.append({
                            "filename": filename,
                            "idiom": "iphone" if size < 76 else "ipad",
                            "scale": f"{scale}x",
                            "size": f"{size}x{size}",
                        })
            except ImportError:
                logger.warning("Pillow not installed — copying icon without resizing")
                shutil.copy(source_icon, icon_set / "AppIcon.png")
                images_json.append({
                    "filename": "AppIcon.png",
                    "idiom": "universal",
                    "scale": "1x",
                    "size": "1024x1024",
                })
            except Exception as e:
                logger.warning("Icon conversion failed: %s", e)
                self._stats.warnings.append(f"Icon conversion failed: {e}")
        else:
            # No icon found — create a placeholder slot
            images_json.append({
                "idiom": "universal",
                "platform": "ios",
                "size": "1024x1024",
            })

        (icon_set / "Contents.json").write_text(json.dumps({
            "images": images_json,
            "info": {"author": "apk2ipa", "version": 1},
        }, indent=2))

    def _write_imageset(self, xcassets: Path, name: str,
                         density_files: dict[str, Path]) -> None:
        """Write a single .imageset directory."""
        imageset = xcassets / f"{name}.imageset"
        imageset.mkdir(exist_ok=True)

        images_json: list[dict] = []
        used_scales: set[str] = set()

        for density, src_path in density_files.items():
            scale = DENSITY_MAP.get(density, "@1x")
            if scale in used_scales:
                scale_int = int(scale[1])  # @2x → 2
                # Try next scale
                for try_scale in ("@1x", "@2x", "@3x"):
                    if try_scale not in used_scales:
                        scale = try_scale
                        break

            used_scales.add(scale)
            dest_name = f"{name}{scale}{src_path.suffix}"
            shutil.copy2(src_path, imageset / dest_name)
            images_json.append({
                "filename": dest_name,
                "idiom": "universal",
                "scale": scale.lstrip("@"),
            })

        # Fill missing scales with the best available
        all_scales = {"1x", "2x", "3x"}
        present = {e["scale"] for e in images_json}
        for missing in all_scales - present:
            if images_json:
                images_json.append({
                    "filename": images_json[0]["filename"],
                    "idiom": "universal",
                    "scale": missing,
                })

        (imageset / "Contents.json").write_text(json.dumps({
            "images": images_json,
            "info": {"author": "apk2ipa", "version": 1},
        }, indent=2))

    @staticmethod
    def _extract_density(dir_name: str) -> str:
        """drawable-xxhdpi → xxhdpi"""
        if "-" in dir_name:
            parts = dir_name.split("-")
            for p in parts[1:]:
                if p in DENSITY_MAP:
                    return p
        return "xhdpi"  # default

    # ------------------------------------------------------------------
    # strings.xml → Localizable.strings
    # ------------------------------------------------------------------

    def _convert_strings(self, output_dir: Path) -> None:
        strings_xml = self.apk_root / "res" / "values" / "strings.xml"
        if not strings_xml.exists():
            return

        lproj = output_dir / "en.lproj"
        lproj.mkdir(exist_ok=True)

        try:
            tree = ET.parse(strings_xml)
            root = tree.getroot()
        except ET.ParseError as e:
            self._stats.warnings.append(f"strings.xml parse error: {e}")
            return

        lines: list[str] = ['/* Auto-generated by apk2ipa */\n']

        for elem in root:
            if elem.tag == "string" and elem.get("name"):
                name = elem.get("name", "")
                value = (elem.text or "").replace('"', '\\"').replace("'", "\\'")
                # Unescape Android escape sequences
                value = value.replace("\\n", "\n").replace("\\t", "\t")
                lines.append(f'"{name}" = "{value}";')
                self._stats.strings_converted += 1

            elif elem.tag == "string-array" and elem.get("name"):
                # Convert string arrays to individual keyed entries
                name = elem.get("name", "")
                for i, item in enumerate(elem.findall("item")):
                    value = (item.text or "").replace('"', '\\"')
                    lines.append(f'"{name}_{i}" = "{value}";')
                    self._stats.strings_converted += 1

            elif elem.tag == "plurals" and elem.get("name"):
                name = elem.get("name", "")
                for item in elem.findall("item"):
                    quantity = item.get("quantity", "other")
                    value = (item.text or "").replace('"', '\\"')
                    # iOS pluralization uses .stringsdict; here we just
                    # output the "other" form in Localizable.strings
                    if quantity == "other":
                        lines.append(f'"{name}" = "{value}";')
                        self._stats.strings_converted += 1

        (lproj / "Localizable.strings").write_text("\n".join(lines), encoding="utf-8")

    # ------------------------------------------------------------------
    # colors.xml → Color+AppColors.swift
    # ------------------------------------------------------------------

    def _convert_colors(self, output_dir: Path) -> None:
        colors_xml = self.apk_root / "res" / "values" / "colors.xml"
        if not colors_xml.exists():
            return

        try:
            tree = ET.parse(colors_xml)
            root = tree.getroot()
        except ET.ParseError as e:
            self._stats.warnings.append(f"colors.xml parse error: {e}")
            return

        lines: list[str] = [
            "// Auto-generated by apk2ipa",
            "import SwiftUI",
            "",
            "extension Color {",
        ]

        for elem in root:
            if elem.tag != "color" or not elem.get("name"):
                continue
            name = _swift_identifier(elem.get("name", ""))
            value = (elem.text or "").strip()
            swift_color = _android_color_to_swift(value)
            lines.append(f"    static let {name} = {swift_color}")
            self._stats.colors_converted += 1

        lines.append("}")

        (output_dir / "Color+AppColors.swift").write_text(
            "\n".join(lines), encoding="utf-8"
        )

    # ------------------------------------------------------------------
    # dimens.xml → Dimensions.swift
    # ------------------------------------------------------------------

    def _convert_dimens(self, output_dir: Path) -> None:
        dimens_xml = self.apk_root / "res" / "values" / "dimens.xml"
        if not dimens_xml.exists():
            return

        try:
            tree = ET.parse(dimens_xml)
            root = tree.getroot()
        except ET.ParseError as e:
            self._stats.warnings.append(f"dimens.xml parse error: {e}")
            return

        lines: list[str] = [
            "// Auto-generated by apk2ipa",
            "import CoreGraphics",
            "",
            "enum Dimens {",
        ]

        for elem in root:
            if elem.tag != "dimen" or not elem.get("name"):
                continue
            name = _swift_identifier(elem.get("name", ""))
            value = (elem.text or "").strip()
            # Convert dp/sp/px to a CGFloat
            import re
            m = re.match(r"^(\d+(?:\.\d+)?)(dp|dip|sp|px)?$", value)
            if m:
                num = m.group(1)
                lines.append(f"    static let {name}: CGFloat = {num}")
                self._stats.dimensions_converted += 1

        lines.append("}")

        (output_dir / "Dimensions.swift").write_text(
            "\n".join(lines), encoding="utf-8"
        )

    # ------------------------------------------------------------------
    # assets/ and res/raw/ → Resources/
    # ------------------------------------------------------------------

    def _copy_assets(self, output_dir: Path) -> None:
        assets_dir = self.apk_root / "assets"
        if not assets_dir.exists():
            return
        dest = output_dir / "Resources" / "assets"
        dest.mkdir(parents=True, exist_ok=True)
        for item in assets_dir.rglob("*"):
            if item.is_file():
                rel = item.relative_to(assets_dir)
                target = dest / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
                self._stats.files_copied += 1

    def _copy_raw(self, output_dir: Path) -> None:
        raw_dir = self.apk_root / "res" / "raw"
        if not raw_dir.exists():
            return
        dest = output_dir / "Resources" / "raw"
        dest.mkdir(parents=True, exist_ok=True)
        for item in raw_dir.rglob("*"):
            if item.is_file():
                rel = item.relative_to(raw_dir)
                target = dest / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
                self._stats.files_copied += 1

    # ------------------------------------------------------------------
    # Full-fidelity: copy ALL remaining file types
    # ------------------------------------------------------------------

    _SOUND_EXTS = {".ogg", ".mp3", ".wav", ".flac", ".aac", ".m4a", ".opus", ".mid", ".midi"}
    _FONT_EXTS = {".ttf", ".otf", ".woff", ".woff2"}
    _VIDEO_EXTS = {".mp4", ".3gp", ".webm", ".mkv", ".avi"}
    _GAME_DATA_EXTS = {".csv", ".sc", ".json", ".bin", ".dat", ".db", ".sqlite",
                       ".lua", ".luac", ".pkl", ".tex", ".pvr", ".ktx", ".astc",
                       ".bnk", ".wem", ".fsb", ".atlas", ".skel", ".fnt", ".tmx"}

    def _copy_by_extension(self, output_dir: Path, subdir: str,
                            extensions: set, search_dirs: list[str] | None = None) -> int:
        """Generic helper: copy files matching extensions into output_dir/subdir."""
        dest = output_dir / "Resources" / subdir
        count = 0
        dirs_to_scan = []

        if search_dirs:
            for d in search_dirs:
                p = self.apk_root / d
                if p.exists():
                    dirs_to_scan.append((p, d))
        else:
            dirs_to_scan.append((self.apk_root, ""))

        for scan_dir, prefix in dirs_to_scan:
            for item in scan_dir.rglob("*"):
                if item.is_file() and item.suffix.lower() in extensions:
                    rel = item.relative_to(self.apk_root)
                    target = dest / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if not target.exists():
                        shutil.copy2(item, target)
                        self._stats.total_bytes_copied += item.stat().st_size
                        count += 1
        return count

    def _copy_sounds(self, output_dir: Path) -> None:
        """Copy all sound/music files."""
        count = self._copy_by_extension(output_dir, "Sounds", self._SOUND_EXTS)
        self._stats.sounds_copied = count
        if count:
            logger.info("Copied %d sound files", count)

    def _copy_fonts(self, output_dir: Path) -> None:
        """Copy all font files."""
        count = self._copy_by_extension(output_dir, "Fonts", self._FONT_EXTS)
        self._stats.fonts_copied = count
        if count:
            logger.info("Copied %d font files", count)

    def _copy_videos(self, output_dir: Path) -> None:
        """Copy all video files."""
        count = self._copy_by_extension(output_dir, "Videos", self._VIDEO_EXTS)
        self._stats.videos_copied = count
        if count:
            logger.info("Copied %d video files", count)

    def _copy_game_data(self, output_dir: Path) -> None:
        """Copy all game data files (.sc, .csv, .bin, .lua, textures, etc.)."""
        count = self._copy_by_extension(output_dir, "GameData", self._GAME_DATA_EXTS)
        self._stats.game_data_copied = count
        if count:
            logger.info("Copied %d game data files", count)

    def _copy_xml_resources(self, output_dir: Path) -> None:
        """Copy all non-layout XML resources (anim, menu, values, xml, etc.)."""
        res_dir = self.apk_root / "res"
        if not res_dir.exists():
            return
        dest = output_dir / "Resources" / "xml"
        count = 0
        for subdir in sorted(res_dir.iterdir()):
            # Skip directories we already handle
            if subdir.name.startswith(("layout", "drawable", "mipmap", "raw", "values")):
                continue
            if not subdir.is_dir():
                continue
            for item in subdir.rglob("*"):
                if item.is_file():
                    rel = item.relative_to(res_dir)
                    target = dest / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, target)
                    self._stats.total_bytes_copied += item.stat().st_size
                    count += 1
        self._stats.xml_resources_copied = count
        if count:
            logger.info("Copied %d XML resource files (anim, menu, etc.)", count)

    def _copy_native_libs(self, output_dir: Path, apk_info=None) -> None:
        """
        Catalog and copy native .so libraries.
        Generates a NativeLibs_README.md mapping them to iOS framework suggestions.
        """
        lib_dir = self.apk_root / "lib"
        if not lib_dir.exists():
            return

        dest = output_dir / "Resources" / "NativeLibs"
        dest.mkdir(parents=True, exist_ok=True)
        count = 0

        # Copy all libs preserving arch directory structure
        for item in lib_dir.rglob("*.so"):
            rel = item.relative_to(lib_dir)
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
            self._stats.total_bytes_copied += item.stat().st_size
            count += 1

        self._stats.native_libs_cataloged = count

        # Generate mapping guide
        self._generate_native_lib_guide(dest, apk_info)

        if count:
            logger.info("Cataloged %d native libraries", count)

    def _generate_native_lib_guide(self, dest: Path, apk_info=None) -> None:
        """Generate a guide mapping Android native libs to iOS equivalents."""
        # Well-known native lib → iOS framework mapping
        LIB_TO_IOS = {
            "libg.so": ("Metal / SceneKit", "Supercell custom game engine — renders via Metal on iOS"),
            "libsupercell.so": ("Supercell Framework", "Core Supercell engine — same codebase compiles for iOS"),
            "libunity.so": ("Unity.framework", "Unity runtime — rebuild from Unity project"),
            "libil2cpp.so": ("IL2CPP (compiled)", "Unity IL2CPP — C++ compiled from C# scripts"),
            "libmain.so": ("Unity main", "Unity entry point — auto-generated by Unity build"),
            "libUE4.so": ("Unreal Engine", "Unreal Engine — rebuild from UE project"),
            "libcocos2dcpp.so": ("Cocos2d-x", "Cocos2d-x — rebuild from Cocos project"),
            "libgdx.so": ("libGDX / RoboVM", "libGDX — use RoboVM or Multi-OS Engine for iOS"),
            "libfmod.so": ("FMOD.framework", "FMOD audio engine — iOS version available"),
            "libfmodstudio.so": ("FMODStudio.framework", "FMOD Studio — iOS version available"),
            "libwwise.so": ("AkSoundEngine.framework", "Wwise audio — iOS SDK available"),
            "libcrypto.so": ("Security.framework", "OpenSSL crypto — use iOS Security framework"),
            "libssl.so": ("Security.framework", "OpenSSL SSL — use iOS URLSession / Security framework"),
            "libsqlite.so": ("libsqlite3.tbd", "SQLite — built into iOS"),
            "libz.so": ("libz.tbd", "zlib compression — built into iOS"),
            "libc++_shared.so": ("libc++.tbd", "C++ standard library — built into iOS"),
            "libGLESv2.so": ("Metal / OpenGLES.framework", "OpenGL ES — use Metal on modern iOS"),
            "libEGL.so": ("Metal / OpenGLES.framework", "EGL — use Metal/CAEAGLLayer on iOS"),
            "libvulkan.so": ("Metal", "Vulkan — use Metal (MoltenVK available as bridge)"),
            "libFirebaseCrashlytics.so": ("FirebaseCrashlytics (SPM)", "Firebase Crashlytics — install via SPM"),
            "libgoogleplay.so": ("GameKit.framework", "Google Play Games → Apple GameKit / Game Center"),
        }

        lines = [
            "# Native Library Mapping Guide",
            "",
            "This document maps Android native libraries (`.so`) to their iOS equivalents.",
            "Native libraries cannot be directly converted — they must be replaced with",
            "iOS framework equivalents or rebuilt from source.",
            "",
        ]

        if apk_info and apk_info.detected_engine:
            lines += [
                f"## Detected Engine: **{apk_info.detected_engine.title()}**",
                "",
            ]
            if apk_info.engine_details.get("note"):
                lines.append(f"> {apk_info.engine_details['note']}")
                lines.append("")
            if apk_info.engine_details.get("ios_equivalent"):
                lines.append(f"> **iOS equivalent:** {apk_info.engine_details['ios_equivalent']}")
                lines.append("")

        if apk_info and apk_info.native_lib_archs:
            lines.append("## Architectures Found")
            lines.append("")
            for arch, libs in sorted(apk_info.native_lib_archs.items()):
                lines.append(f"### `{arch}` ({len(libs)} libraries)")
                lines.append("")
                lines.append("| Android Library | iOS Equivalent | Notes |")
                lines.append("|---|---|---|")
                for lib in sorted(libs):
                    if lib in LIB_TO_IOS:
                        ios_fw, note = LIB_TO_IOS[lib]
                        lines.append(f"| `{lib}` | **{ios_fw}** | {note} |")
                    else:
                        lines.append(f"| `{lib}` | ⚠️ *Manual mapping needed* | Custom native library |")
                lines.append("")
        else:
            # Scan the dest directory
            lines.append("## Libraries")
            lines.append("")
            for so_file in sorted(dest.rglob("*.so")):
                lib = so_file.name
                if lib in LIB_TO_IOS:
                    ios_fw, note = LIB_TO_IOS[lib]
                    lines.append(f"- `{lib}` → **{ios_fw}** — {note}")
                else:
                    lines.append(f"- `{lib}` → ⚠️ Manual mapping needed")
            lines.append("")

        lines += [
            "## Next Steps",
            "",
            "1. For game engines (Supercell/Unity/Unreal): rebuild from the original project source",
            "2. For common libraries (SSL, SQLite, zlib): iOS has built-in equivalents",
            "3. For audio engines (FMOD, Wwise): download the iOS SDK from the vendor",
            "4. For custom `.so` files: rewrite in Swift/C++ or find iOS alternatives",
        ]

        (dest / "NativeLibs_README.md").write_text("\n".join(lines), encoding="utf-8")

    def _copy_remaining_files(self, output_dir: Path) -> None:
        """Copy any remaining files not yet handled — nothing left behind."""
        dest = output_dir / "Resources" / "Other"
        count = 0

        # Track what we've already copied by checking existing output dirs
        already_handled_prefixes = {
            "classes", "res/layout", "res/drawable", "res/mipmap",
            "res/raw", "res/values", "assets/", "lib/", "META-INF/",
            "kotlin/", "AndroidManifest.xml", "resources.arsc",
        }

        for item in self.apk_root.rglob("*"):
            if not item.is_file():
                continue
            rel = item.relative_to(self.apk_root)
            rel_str = str(rel)

            # Skip if already handled
            skip = False
            for prefix in already_handled_prefixes:
                if rel_str.startswith(prefix):
                    skip = True
                    break
            if skip:
                continue

            # Skip files already copied by extension-based methods
            ext = item.suffix.lower()
            all_exts = self._SOUND_EXTS | self._FONT_EXTS | self._VIDEO_EXTS | self._GAME_DATA_EXTS
            if ext in all_exts:
                continue

            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copy2(item, target)
                self._stats.total_bytes_copied += item.stat().st_size
                count += 1

        self._stats.other_files_copied = count
        if count:
            logger.info("Copied %d remaining files", count)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _swift_identifier(name: str) -> str:
    """Convert an Android resource name to a Swift identifier."""
    import re
    # snake_case → camelCase
    parts = name.split("_")
    if not parts:
        return name
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _android_color_to_swift(value: str) -> str:
    """Convert an Android color value string to a SwiftUI Color expression."""
    import re
    value = value.strip()
    m = re.match(r"^#([0-9A-Fa-f]{6,8})$", value)
    if m:
        hex_val = m.group(1).upper()
        if len(hex_val) == 6:
            r = int(hex_val[0:2], 16) / 255
            g = int(hex_val[2:4], 16) / 255
            b = int(hex_val[4:6], 16) / 255
            return f"Color(red: {r:.3f}, green: {g:.3f}, blue: {b:.3f})"
        elif len(hex_val) == 8:
            a = int(hex_val[0:2], 16) / 255
            r = int(hex_val[2:4], 16) / 255
            g = int(hex_val[4:6], 16) / 255
            b = int(hex_val[6:8], 16) / 255
            return f"Color(red: {r:.3f}, green: {g:.3f}, blue: {b:.3f}, opacity: {a:.3f})"
    # Reference to another color
    ref_m = re.match(r"^@(?:android:)?color/(\w+)$", value)
    if ref_m:
        return f"Color(\"{ref_m.group(1)}\")"
    return f"Color.primary  // TODO: map '{value}'"
