"""
Split APK / Bundle handler.

Modern Android apps are often distributed as bundles:
  - .xapk  — APKPure format (ZIP containing base.apk + split APKs + manifest)
  - .apks  — SAI format (ZIP containing base.apk + split APKs)
  - .apkm  — APKMirror format (similar to .xapk)
  - Split APKs — multiple APK files that together form the app

This module detects the format and merges everything into a single
extracted directory that the rest of the pipeline can process.
"""

from __future__ import annotations

import json
import shutil
import zipfile
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def detect_bundle_type(path: Path) -> str:
    """
    Detect if the given file is a bundle or standard APK.

    Returns: "apk", "xapk", "apks", "apkm", or "unknown"
    """
    suffix = path.suffix.lower()

    if suffix in (".xapk", ".apkm"):
        return "xapk"
    if suffix == ".apks":
        return "apks"

    # Check if it's a ZIP containing APKs (might be .xapk/.apks with wrong extension)
    if suffix in (".apk", ".zip"):
        try:
            with zipfile.ZipFile(path, "r") as zf:
                names = zf.namelist()
                # Standard APK has AndroidManifest.xml at root
                if "AndroidManifest.xml" in names:
                    return "apk"
                # XAPK has a manifest.json + *.apk files
                if "manifest.json" in names and any(n.endswith(".apk") for n in names):
                    return "xapk"
                # APKS is just APK files bundled together
                if any(n.endswith(".apk") for n in names) and "AndroidManifest.xml" not in names:
                    return "apks"
        except zipfile.BadZipFile:
            pass

    if suffix == ".apk":
        return "apk"

    return "unknown"


def extract_bundle(bundle_path: Path, output_dir: Path) -> tuple[Path, dict]:
    """
    Extract a bundle (XAPK/APKS/APKM) into a merged directory.

    Returns:
        (merged_apk_root, bundle_info_dict)

    The merged_apk_root looks like a standard extracted APK directory
    with all splits merged together.
    """
    bundle_type = detect_bundle_type(bundle_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    info = {
        "bundle_type": bundle_type,
        "split_apks": [],
        "total_apks": 0,
    }

    if bundle_type == "apk":
        # Standard APK — just extract normally
        with zipfile.ZipFile(bundle_path, "r") as zf:
            zf.extractall(output_dir)
        info["total_apks"] = 1
        return output_dir, info

    if bundle_type in ("xapk", "apks"):
        return _extract_split_bundle(bundle_path, output_dir, info)

    # Unknown — try as standard APK
    logger.warning("Unknown bundle type for %s, trying as standard APK", bundle_path)
    with zipfile.ZipFile(bundle_path, "r") as zf:
        zf.extractall(output_dir)
    info["total_apks"] = 1
    return output_dir, info


def _extract_split_bundle(bundle_path: Path, output_dir: Path, info: dict) -> tuple[Path, dict]:
    """Extract XAPK/APKS bundle by merging all split APKs."""
    temp_dir = output_dir / "_bundle_temp"
    temp_dir.mkdir(exist_ok=True)

    # First, extract the outer ZIP
    with zipfile.ZipFile(bundle_path, "r") as outer:
        outer.extractall(temp_dir)

    # Read bundle manifest if present
    manifest_path = temp_dir / "manifest.json"
    if manifest_path.exists():
        try:
            with open(manifest_path) as f:
                bundle_manifest = json.load(f)
            info["bundle_manifest"] = bundle_manifest
            logger.info("Bundle: %s v%s",
                        bundle_manifest.get("name", "unknown"),
                        bundle_manifest.get("version_name", "?"))
        except Exception as e:
            logger.warning("Could not parse bundle manifest: %s", e)

    # Find all APK files in the bundle
    apk_files = sorted(temp_dir.rglob("*.apk"))
    info["total_apks"] = len(apk_files)

    # Determine which is the base APK
    base_apk: Optional[Path] = None
    split_apks: list[Path] = []

    for apk in apk_files:
        name_lower = apk.stem.lower()
        if name_lower in ("base", "base.apk", "com") or "base" in name_lower:
            base_apk = apk
        else:
            split_apks.append(apk)
            info["split_apks"].append(apk.name)

    # If no base found, use the largest APK
    if not base_apk and apk_files:
        base_apk = max(apk_files, key=lambda p: p.stat().st_size)
        split_apks = [a for a in apk_files if a != base_apk]

    if not base_apk:
        logger.error("No APK files found in bundle")
        return output_dir, info

    # Extract base APK first
    logger.info("Extracting base APK: %s", base_apk.name)
    with zipfile.ZipFile(base_apk, "r") as zf:
        zf.extractall(output_dir)

    # Merge split APKs on top (split APKs contain additional resources,
    # native libs for different architectures, language packs, etc.)
    for split_apk in split_apks:
        logger.info("Merging split APK: %s", split_apk.name)
        try:
            with zipfile.ZipFile(split_apk, "r") as zf:
                for member in zf.namelist():
                    dest = output_dir / member
                    if not dest.exists():  # Don't overwrite base files
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(member) as src, open(dest, "wb") as dst:
                            shutil.copyfileobj(src, dst)
        except Exception as e:
            logger.warning("Failed to merge %s: %s", split_apk.name, e)

    # Copy any non-APK files from the bundle (icons, OBB data, etc.)
    for item in temp_dir.rglob("*"):
        if item.is_file() and not item.suffix.lower() == ".apk" and item.name != "manifest.json":
            rel = item.relative_to(temp_dir)
            dest = output_dir / "bundle_extras" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                shutil.copy2(item, dest)

    # Clean up temp
    shutil.rmtree(temp_dir, ignore_errors=True)

    return output_dir, info
