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

    def _inventory_files(self, zf: zipfile.ZipFile, info: ApkInfo) -> None:
        """Categorise every file inside the APK."""
        for name in zf.namelist():
            if name.startswith("classes") and name.endswith(".dex"):
                info.dex_files.append(name)
            elif name.startswith("res/layout") and name.endswith(".xml"):
                info.layout_files.append(name)
            elif name.startswith("res/drawable") or name.startswith("res/mipmap"):
                info.drawable_files.append(name)
            elif name.startswith("assets/"):
                info.asset_files.append(name)
            elif name.startswith("lib/") and name.endswith(".so"):
                info.native_libs.append(name)
            elif name != "AndroidManifest.xml" and name != "resources.arsc":
                info.other_files.append(name)

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

        Android Binary XML is complex; this just extracts UTF-16 strings
        that look like package names / class names from the string pool.
        Not accurate but better than nothing.
        """
        # Collect UTF-16LE strings from the binary
        strings: list[str] = []
        i = 0
        while i < len(data) - 1:
            if data[i + 1] == 0 and 0x20 <= data[i] < 0x7f:
                # Possible start of a UTF-16LE ASCII character
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

        for s in strings:
            if re.match(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){1,}$", s):
                if not info.package_name:
                    info.package_name = s
            elif s.endswith("Activity") and "." in s:
                if not info.main_activity:
                    info.main_activity = s

    @staticmethod
    def _sha256(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
