"""
APK Parser — extracts and analyses all contents of an Android APK.

An APK is a ZIP archive containing:
  classes.dex / classes2.dex … — Dalvik bytecode
  AndroidManifest.xml          — binary-encoded XML
  resources.arsc               — compiled resource table
  res/                         — XML layouts, drawables, etc.
  assets/                      — raw assets
  lib/                         — native .so libraries
  META-INF/                    — signing certificates
"""

from __future__ import annotations

import zipfile
import os
import re
import struct
import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ApkInfo:
    """Structured summary of an APK's contents."""

    # Identity
    package_name: str = ""
    app_name: str = ""
    version_name: str = ""
    version_code: int = 0
    min_sdk: int = 0
    target_sdk: int = 0

    # Permissions declared in the manifest
    permissions: list[str] = field(default_factory=list)

    # Activities, services, receivers, providers
    activities: list[dict] = field(default_factory=list)
    services: list[dict] = field(default_factory=list)
    receivers: list[dict] = field(default_factory=list)
    providers: list[dict] = field(default_factory=list)

    # Main launcher activity (entry point)
    main_activity: str = ""

    # File inventory (relative paths inside the APK)
    dex_files: list[str] = field(default_factory=list)
    layout_files: list[str] = field(default_factory=list)
    drawable_files: list[str] = field(default_factory=list)
    asset_files: list[str] = field(default_factory=list)
    native_libs: list[str] = field(default_factory=list)
    other_files: list[str] = field(default_factory=list)

    # Extended inventory for full-fidelity conversion
    sound_files: list[str] = field(default_factory=list)
    config_files: list[str] = field(default_factory=list)
    font_files: list[str] = field(default_factory=list)
    video_files: list[str] = field(default_factory=list)
    game_data_files: list[str] = field(default_factory=list)     # .csv, .json, .sc, etc.
    raw_resource_files: list[str] = field(default_factory=list)  # res/raw/*
    xml_files: list[str] = field(default_factory=list)           # non-layout XML
    kotlin_modules: list[str] = field(default_factory=list)      # kotlin metadata
    signing_files: list[str] = field(default_factory=list)       # META-INF/*

    # Game engine detection
    detected_engine: str = ""          # "supercell", "unity", "unreal", "cocos2d", "libgdx", ""
    engine_details: dict = field(default_factory=dict)  # engine-specific metadata

    # Native lib details: arch → list of lib names
    native_lib_archs: dict = field(default_factory=dict)

    # Total file count and size
    total_files: int = 0
    total_size_bytes: int = 0

    # Raw bytes of key files (populated by ApkParser)
    raw_manifest: bytes = b""

    # SHA-256 of the APK itself
    apk_hash: str = ""


class ApkParser:
    """
    Parses an APK file and extracts all its contents to an output directory.

    Usage::

        parser = ApkParser("my_app.apk")
        info = parser.parse()          # returns ApkInfo
        parser.extract_all("/tmp/out") # writes everything to disk
    """

    def __init__(self, apk_path: str | Path):
        self.apk_path = Path(apk_path)
        if not self.apk_path.exists():
            raise FileNotFoundError(f"APK not found: {self.apk_path}")
        self._zf: Optional[zipfile.ZipFile] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(self) -> ApkInfo:
        """Parse the APK and return an ApkInfo summary."""
        info = ApkInfo()
        info.apk_hash = self._sha256(self.apk_path)

        with zipfile.ZipFile(self.apk_path, "r") as zf:
            self._zf = zf
            self._inventory_files(zf, info)
            self._parse_manifest(zf, info)

            # If package name still not found, try to infer from DEX class paths
            if not info.package_name:
                self._infer_package_from_dex_paths(zf, info)

        self._zf = None
        return info

    def extract_all(self, output_dir: str | Path) -> Path:
        """
        Extract every file from the APK into *output_dir*.

        Returns the output directory path.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(self.apk_path, "r") as zf:
            zf.extractall(output_dir)

        logger.info("Extracted APK to %s", output_dir)
        return output_dir

    def extract_file(self, apk_path: str, output_dir: str | Path) -> Path:
        """Extract a single file from the APK."""
        output_dir = Path(output_dir)
        with zipfile.ZipFile(self.apk_path, "r") as zf:
            zf.extract(apk_path, output_dir)
        return output_dir / apk_path

    def list_files(self) -> list[str]:
        """Return all file paths inside the APK."""
        with zipfile.ZipFile(self.apk_path, "r") as zf:
            return zf.namelist()

    def read_file(self, apk_path: str) -> bytes:
        """Read raw bytes of a file inside the APK."""
        with zipfile.ZipFile(self.apk_path, "r") as zf:
            return zf.read(apk_path)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    # File extensions for categorization
    _SOUND_EXTS = {".ogg", ".mp3", ".wav", ".flac", ".aac", ".m4a", ".opus", ".mid", ".midi"}
    _VIDEO_EXTS = {".mp4", ".3gp", ".webm", ".mkv", ".avi"}
    _FONT_EXTS = {".ttf", ".otf", ".woff", ".woff2"}
    _CONFIG_EXTS = {".properties", ".cfg", ".ini", ".conf", ".yml", ".yaml", ".toml"}
    _GAME_DATA_EXTS = {".csv", ".sc", ".json", ".bin", ".dat", ".db", ".sqlite",
                       ".lua", ".luac", ".pkl", ".tex", ".pvr", ".ktx", ".astc",
                       ".bnk", ".wem", ".fsb", ".atlas", ".skel", ".fnt", ".tmx"}

    def _inventory_files(self, zf: zipfile.ZipFile, info: ApkInfo) -> None:
        """Categorise every file inside the APK — leaves nothing out."""
        info.total_files = len(zf.namelist())
        info.total_size_bytes = sum(zi.file_size for zi in zf.infolist())

        for zi in zf.infolist():
            name = zi.filename
            if name.endswith("/"):  # directory entry
                continue

            ext = os.path.splitext(name)[1].lower()

            # DEX bytecode
            if name.startswith("classes") and name.endswith(".dex"):
                info.dex_files.append(name)

            # Layouts
            elif name.startswith("res/layout") and name.endswith(".xml"):
                info.layout_files.append(name)

            # Drawables & mipmaps
            elif name.startswith("res/drawable") or name.startswith("res/mipmap"):
                info.drawable_files.append(name)

            # Raw resources
            elif name.startswith("res/raw/"):
                info.raw_resource_files.append(name)

            # Other res/ XML files (values, menu, anim, etc.)
            elif name.startswith("res/") and name.endswith(".xml"):
                info.xml_files.append(name)

            # Assets — sub-categorize
            elif name.startswith("assets/"):
                info.asset_files.append(name)
                if ext in self._SOUND_EXTS:
                    info.sound_files.append(name)
                elif ext in self._VIDEO_EXTS:
                    info.video_files.append(name)
                elif ext in self._FONT_EXTS:
                    info.font_files.append(name)
                elif ext in self._GAME_DATA_EXTS:
                    info.game_data_files.append(name)
                elif ext in self._CONFIG_EXTS:
                    info.config_files.append(name)

            # Native libraries
            elif name.startswith("lib/") and name.endswith(".so"):
                info.native_libs.append(name)
                # Track by architecture
                parts = name.split("/")
                if len(parts) >= 3:
                    arch = parts[1]  # e.g. arm64-v8a, armeabi-v7a, x86_64
                    if arch not in info.native_lib_archs:
                        info.native_lib_archs[arch] = []
                    info.native_lib_archs[arch].append(parts[-1])

            # Kotlin metadata
            elif name.startswith("kotlin/") or name.endswith(".kotlin_module"):
                info.kotlin_modules.append(name)

            # Signing / META-INF
            elif name.startswith("META-INF/"):
                info.signing_files.append(name)

            # Sounds/fonts/videos/game data outside of assets/
            elif ext in self._SOUND_EXTS:
                info.sound_files.append(name)
            elif ext in self._FONT_EXTS:
                info.font_files.append(name)
            elif ext in self._VIDEO_EXTS:
                info.video_files.append(name)
            elif ext in self._GAME_DATA_EXTS:
                info.game_data_files.append(name)
            elif ext in self._CONFIG_EXTS:
                info.config_files.append(name)

            # Everything else
            elif name != "AndroidManifest.xml" and name != "resources.arsc":
                info.other_files.append(name)

        # Detect game engine
        self._detect_game_engine(zf, info)

    def _parse_manifest(self, zf: zipfile.ZipFile, info: ApkInfo) -> None:
        """
        Parse AndroidManifest.xml.

        Android stores the manifest as Android Binary XML (AXML).  We
        attempt to use androguard's AXMLPrinter first; if that's not
        installed we fall back to a lightweight pure-Python parser.
        """
        try:
            raw = zf.read("AndroidManifest.xml")
        except KeyError:
            logger.warning("No AndroidManifest.xml found in APK")
            return

        info.raw_manifest = raw

        # Try androguard (best results)
        try:
            from androguard.core.axml import AXMLPrinter
            xml_text = AXMLPrinter(raw).get_xml_obj()
            self._extract_manifest_fields_etree(xml_text, info)
            return
        except Exception:
            pass

        # Try pyaxmlparser
        try:
            import pyaxmlparser
            axp = pyaxmlparser.APK(str(self.apk_path))
            info.package_name = axp.get_package()
            info.app_name = axp.get_app_name()
            info.version_name = axp.get_androidversion_name() or ""
            info.version_code = int(axp.get_androidversion_code() or 0)
            info.min_sdk = int(axp.get_min_sdk_version() or 0)
            info.target_sdk = int(axp.get_target_sdk_version() or 0)
            info.permissions = list(axp.get_permissions())
            info.activities = [{"name": a} for a in axp.get_activities()]
            info.main_activity = axp.get_main_activity() or ""
            return
        except Exception:
            pass

        # Last resort: minimal binary-AXML heuristic
        self._parse_axml_minimal(raw, info)

    def _extract_manifest_fields_etree(self, root, info: ApkInfo) -> None:
        """Pull fields out of an lxml/ElementTree Element."""
        ANDROID = "http://schemas.android.com/apk/res/android"

        info.package_name = root.get("package", "")

        for tag in root.iter():
            localname = tag.tag.split("}")[-1] if "}" in tag.tag else tag.tag

            if localname == "manifest":
                info.version_name = tag.get(f"{{{ANDROID}}}versionName", "")
                vc = tag.get(f"{{{ANDROID}}}versionCode", "0")
                info.version_code = int(vc) if vc.isdigit() else 0

            elif localname == "uses-sdk":
                min_sdk = tag.get(f"{{{ANDROID}}}minSdkVersion", "0")
                tgt_sdk = tag.get(f"{{{ANDROID}}}targetSdkVersion", "0")
                info.min_sdk = int(min_sdk) if min_sdk.isdigit() else 0
                info.target_sdk = int(tgt_sdk) if tgt_sdk.isdigit() else 0

            elif localname == "uses-permission":
                perm = tag.get(f"{{{ANDROID}}}name", "")
                if perm:
                    info.permissions.append(perm)

            elif localname == "activity":
                name = tag.get(f"{{{ANDROID}}}name", "")
                if name:
                    entry: dict = {"name": name, "is_launcher": False}
                    # Check for MAIN/LAUNCHER intent filters
                    for child in tag:
                        child_local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                        if child_local == "intent-filter":
                            for sub in child:
                                sub_local = sub.tag.split("}")[-1] if "}" in sub.tag else sub.tag
                                if sub_local == "action":
                                    act = sub.get(f"{{{ANDROID}}}name", "")
                                    if act == "android.intent.action.MAIN":
                                        entry["is_launcher"] = True
                    if entry["is_launcher"] and not info.main_activity:
                        info.main_activity = name
                    info.activities.append(entry)

            elif localname == "service":
                name = tag.get(f"{{{ANDROID}}}name", "")
                if name:
                    info.services.append({"name": name})

            elif localname == "receiver":
                name = tag.get(f"{{{ANDROID}}}name", "")
                if name:
                    info.receivers.append({"name": name})

            elif localname == "provider":
                name = tag.get(f"{{{ANDROID}}}name", "")
                if name:
                    info.providers.append({"name": name})

            elif localname == "application":
                label = tag.get(f"{{{ANDROID}}}label", "")
                if label and not label.startswith("@"):
                    info.app_name = label

    def _parse_axml_minimal(self, data: bytes, info: ApkInfo) -> None:
        """
        Minimal fallback AXML string scanner.

        Android Binary XML stores strings in a string pool near the start
        of the file.  The pool can be encoded as UTF-8 or UTF-16LE (flag
        bit 0x100 in the pool header).  We extract strings from both
        encodings, then look for package names and activity class names.
        """
        strings: list[str] = self._extract_axml_string_pool(data)

        # Also scan for embedded UTF-8 and UTF-16LE ASCII runs as fallback
        strings.extend(self._scan_utf16le_strings(data))
        strings.extend(self._scan_utf8_strings(data))

        # Deduplicate while preserving order
        seen: set[str] = set()
        unique_strings: list[str] = []
        for s in strings:
            if s not in seen:
                seen.add(s)
                unique_strings.append(s)

        # Collect all package-name candidates and pick the best one
        package_candidates: list[str] = []
        activity_candidates: list[str] = []

        for s in unique_strings:
            # Package names: com.something.something
            if re.match(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){2,}$", s):
                package_candidates.append(s)
            # Activities: fully qualified class names ending in Activity
            elif "Activity" in s and "." in s:
                activity_candidates.append(s)

        # The first package-like string in the AXML string pool is typically
        # the manifest's `package` attribute — trust it as the primary package name.
        # Filter out obvious system/framework packages only.
        for candidate in package_candidates:
            if candidate.startswith(("android.", "com.android.", "com.google.",
                                     "io.sentry", "com.facebook.", "com.instagram.",
                                     "com.samsung.", "link.", "play.")):
                continue
            if ".api_key" in candidate or ".enable" in candidate or ".dsn" in candidate:
                continue
            if candidate.count(".") >= 2:
                info.package_name = candidate
                break

        if not info.package_name and package_candidates:
            info.package_name = package_candidates[0]

        # Pick main activity
        for candidate in activity_candidates:
            if not info.main_activity:
                info.main_activity = candidate

        # Try to extract version from strings
        for s in unique_strings:
            if re.match(r"^\d+\.\d+", s) and not info.version_name:
                info.version_name = s

    def _extract_axml_string_pool(self, data: bytes) -> list[str]:
        """Parse the AXML string pool header to extract strings properly."""
        strings: list[str] = []
        if len(data) < 16:
            return strings

        try:
            # AXML starts with: magic(4) + file_size(4)
            # Then string pool chunk: type(2) + header_size(2) + chunk_size(4) +
            #   string_count(4) + style_count(4) + flags(4) + strings_start(4) + styles_start(4)
            # Check for ResChunk_header (type 0x0001 = string pool)
            offset = 8  # skip file header
            chunk_type = struct.unpack_from("<H", data, offset)[0]
            if chunk_type != 0x0001:
                return strings

            header_size = struct.unpack_from("<H", data, offset + 2)[0]
            string_count = struct.unpack_from("<I", data, offset + 8)[0]
            flags = struct.unpack_from("<I", data, offset + 16)[0]
            strings_start = struct.unpack_from("<I", data, offset + 20)[0]

            is_utf8 = bool(flags & (1 << 8))

            # String offsets start after the pool header
            offsets_start = offset + header_size
            abs_strings_start = offset + strings_start

            for i in range(min(string_count, 5000)):  # cap to avoid runaway
                str_offset_pos = offsets_start + i * 4
                if str_offset_pos + 4 > len(data):
                    break
                str_offset = struct.unpack_from("<I", data, str_offset_pos)[0]
                pos = abs_strings_start + str_offset

                if is_utf8:
                    # UTF-8: 2 length bytes (chars), 2 length bytes (bytes), then null-terminated
                    if pos + 4 > len(data):
                        continue
                    # Skip the char count (1 or 2 bytes) and byte count (1 or 2 bytes)
                    char_len = data[pos]
                    pos += 1
                    if char_len & 0x80:
                        pos += 1
                    byte_len = data[pos]
                    pos += 1
                    if byte_len & 0x80:
                        byte_len = ((byte_len & 0x7F) << 8) | data[pos]
                        pos += 1
                    if pos + byte_len > len(data):
                        continue
                    s = data[pos:pos + byte_len].decode("utf-8", errors="replace")
                    if s and len(s) > 1:
                        strings.append(s)
                else:
                    # UTF-16LE: 2-byte length then string data
                    if pos + 2 > len(data):
                        continue
                    char_len = struct.unpack_from("<H", data, pos)[0]
                    if char_len & 0x8000:
                        char_len = ((char_len & 0x7FFF) << 16) | struct.unpack_from("<H", data, pos + 2)[0]
                        pos += 4
                    else:
                        pos += 2
                    byte_len = char_len * 2
                    if pos + byte_len > len(data):
                        continue
                    s = data[pos:pos + byte_len].decode("utf-16-le", errors="replace")
                    if s and len(s) > 1:
                        strings.append(s)

        except (struct.error, IndexError, ValueError):
            pass

        return strings

    @staticmethod
    def _scan_utf16le_strings(data: bytes) -> list[str]:
        """Scan for UTF-16LE ASCII string runs in binary data."""
        strings: list[str] = []
        i = 0
        while i < len(data) - 1:
            if data[i + 1] == 0 and 0x20 <= data[i] < 0x7f:
                s = bytearray()
                j = i
                while j < len(data) - 1 and data[j + 1] == 0 and 0x20 <= data[j] < 0x7f:
                    s.append(data[j])
                    j += 2
                if len(s) > 4:
                    strings.append(s.decode("ascii", errors="ignore"))
                i = j
            else:
                i += 1
        return strings

    @staticmethod
    def _scan_utf8_strings(data: bytes) -> list[str]:
        """Scan for UTF-8 ASCII string runs in binary data."""
        strings: list[str] = []
        i = 0
        while i < len(data):
            if 0x20 <= data[i] < 0x7f or data[i] in (0x09, 0x0a):
                j = i
                while j < len(data) and (0x20 <= data[j] < 0x7f or data[j] in (0x09, 0x0a)):
                    j += 1
                if j - i > 8:  # minimum length to avoid noise
                    s = data[i:j].decode("ascii", errors="ignore").strip()
                    if s:
                        strings.append(s)
                i = j
            else:
                i += 1
        return strings

    def _detect_game_engine(self, zf: zipfile.ZipFile, info: ApkInfo) -> None:
        """Detect which game engine the APK uses based on file signatures."""
        all_files = set(zf.namelist())
        lib_names = {os.path.basename(f) for f in info.native_libs}

        # --- Supercell (Brawl Stars, Clash Royale, etc.) ---
        supercell_signs = {
            "libg.so", "libsupercell.so", "libtool.so",
        }
        sc_files = [f for f in info.asset_files if f.endswith(".sc")]
        sc_csv_files = [f for f in info.asset_files if f.endswith(".csv")]
        has_supercell_pkg = any("supercell" in (info.package_name or "").lower()
                                for _ in [1])
        if (supercell_signs & lib_names) or (sc_files and has_supercell_pkg) or \
           (sc_files and sc_csv_files and len(sc_files) > 5):
            info.detected_engine = "supercell"
            info.engine_details = {
                "sc_texture_files": sc_files,
                "csv_data_files": sc_csv_files,
                "native_libs": sorted(lib_names),
                "note": "Supercell custom engine — uses .sc compressed textures, "
                        "CSV game data, and native C++ game logic",
                "ios_equivalent": "The iOS version uses the same engine compiled "
                                  "for ARM64 (native Metal/OpenGL ES rendering)",
            }
            return

        # --- Unity ---
        unity_signs = {"libunity.so", "libil2cpp.so", "libmain.so"}
        unity_assets = any(f.startswith("assets/bin/Data/") for f in all_files)
        if (unity_signs & lib_names) or unity_assets:
            info.detected_engine = "unity"
            info.engine_details = {
                "il2cpp": "libil2cpp.so" in lib_names,
                "mono": "libmono.so" in lib_names or "libmonobdwgc-2.0.so" in lib_names,
                "data_files": [f for f in all_files if f.startswith("assets/bin/Data/")],
                "note": "Unity engine — native C++ runtime with IL2CPP or Mono scripting",
                "ios_equivalent": "Rebuild from Unity project targeting iOS (Xcode export)",
            }
            return

        # --- Unreal Engine ---
        unreal_signs = {"libUE4.so", "libUnreal.so"}
        unreal_assets = any(f.endswith(".uasset") or f.endswith(".umap") for f in all_files)
        if (unreal_signs & lib_names) or unreal_assets:
            info.detected_engine = "unreal"
            info.engine_details = {
                "note": "Unreal Engine — native C++ with Blueprint assets",
                "ios_equivalent": "Rebuild from Unreal project targeting iOS",
            }
            return

        # --- Cocos2d-x ---
        cocos_signs = {"libcocos2dcpp.so", "libcocos2d.so", "libcocos2djs.so"}
        if cocos_signs & lib_names:
            info.detected_engine = "cocos2d"
            info.engine_details = {
                "note": "Cocos2d-x engine — C++ with Lua/JS scripting",
                "ios_equivalent": "Rebuild from Cocos2d-x project targeting iOS",
            }
            return

        # --- libGDX ---
        gdx_signs = {"libgdx.so", "libgdx-box2d.so", "libgdx-freetype.so"}
        if gdx_signs & lib_names:
            info.detected_engine = "libgdx"
            info.engine_details = {
                "note": "libGDX engine — Java-based with native rendering",
                "ios_equivalent": "Use RoboVM or MOE to run on iOS",
            }
            return

        # --- No engine detected (standard Android app) ---
        if info.native_libs:
            info.detected_engine = "native"
            info.engine_details = {
                "native_libs": sorted(lib_names),
                "note": "App uses native libraries — these need iOS equivalents",
            }

    def _infer_package_from_dex_paths(self, zf: zipfile.ZipFile, info: ApkInfo) -> None:
        """
        Try to infer the package name by scanning DEX file headers for class paths,
        or by looking at well-known metadata files inside the APK.
        """
        all_files = zf.namelist()

        # Method 1: Check META-INF/*.kotlin_module or other metadata
        for name in all_files:
            if name == "assets/supercell-id-config.json" or \
               name.startswith("assets/") and "supercell" in name.lower():
                if not info.package_name:
                    # Known Supercell package patterns
                    apk_name = self.apk_path.stem.lower()
                    if "brawl" in apk_name:
                        info.package_name = "com.supercell.brawlstars"
                    elif "clash" in apk_name and "royal" in apk_name:
                        info.package_name = "com.supercell.clashroyale"
                    elif "clash" in apk_name:
                        info.package_name = "com.supercell.clashofclans"
                    elif "boom" in apk_name:
                        info.package_name = "com.supercell.boombeach"
                    elif "hay" in apk_name:
                        info.package_name = "com.supercell.hayday"
                    elif "squad" in apk_name:
                        info.package_name = "com.supercell.squadbusters"
                    else:
                        info.package_name = "com.supercell.game"
                    return

        # Method 2: Look for package name in Google Play metadata
        for name in all_files:
            if "com.supercell" in name or "com/supercell" in name:
                # Extract package from path like com/supercell/brawlstars/...
                match = re.search(r"com[/.]supercell[/.]([a-z]+)", name)
                if match:
                    info.package_name = f"com.supercell.{match.group(1)}"
                    return

        # Method 3: Look at directory structure inside the APK for Java packages
        pkg_counter: dict[str, int] = {}
        for name in all_files:
            # Skip common non-app paths
            if name.startswith(("android/", "kotlin/", "META-INF/",
                                "res/", "assets/", "lib/", "org/", "okhttp3/",
                                "com/google/", "com/android/", "androidx/")):
                continue
            # Look for smali-style paths or class paths
            match = re.match(r"^(com/[a-z][a-z0-9]*/[a-z][a-z0-9]*)/", name)
            if match:
                pkg = match.group(1).replace("/", ".")
                pkg_counter[pkg] = pkg_counter.get(pkg, 0) + 1

        if pkg_counter:
            # Pick the most common one
            best = max(pkg_counter, key=pkg_counter.get)  # type: ignore[arg-type]
            info.package_name = best

    @staticmethod
    def _sha256(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
