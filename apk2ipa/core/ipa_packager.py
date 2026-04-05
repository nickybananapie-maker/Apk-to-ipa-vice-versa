"""
IPA Packager — packages a converted Xcode project into a .ipa file.

An .ipa is a ZIP archive with the structure:

    Payload/
      AppName.app/
        Info.plist
        (all resources, assets, game data, etc.)

Since we're on Linux (no Xcode/swiftc), the .ipa contains the full
app bundle with all resources but no compiled binary.  This is useful
for:
  - Distributing the converted assets
  - Inspecting in tools like iExplorer
  - Sideloading resource-only bundles for modding
"""

from __future__ import annotations

import os
import time
import zipfile
from pathlib import Path


def package_ipa(
    project_dir: Path,
    output_path: Path | None = None,
    app_name: str | None = None,
    compression: int = zipfile.ZIP_DEFLATED,
    compresslevel: int = 6,
) -> Path:
    """
    Package a converted Xcode project directory into a .ipa file.

    Args:
        project_dir: Path to the converted app directory (e.g., output/brawl_stars/ConvertedApp)
        output_path: Where to write the .ipa (default: same dir as project)
        app_name: Name for the .app bundle (default: directory name)
        compression: ZIP compression method
        compresslevel: Compression level (1-9)

    Returns:
        Path to the generated .ipa file.
    """
    project_dir = Path(project_dir)
    if not project_dir.exists():
        raise FileNotFoundError(f"Project directory not found: {project_dir}")

    # Find the app source directory (contains Info.plist)
    app_name = app_name or project_dir.name
    app_src = _find_app_source(project_dir)
    if not app_src:
        raise FileNotFoundError(
            f"Could not find app source (Info.plist) in {project_dir}"
        )

    if output_path is None:
        output_path = project_dir.parent / f"{app_name}.ipa"

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    bundle_name = f"{app_name}.app"
    t0 = time.time()
    file_count = 0
    total_bytes = 0

    print(f"  Packaging {app_name}.ipa...")
    print(f"  Source: {app_src}")

    with zipfile.ZipFile(output_path, "w", compression, compresslevel=compresslevel) as zf:
        for root, dirs, files in os.walk(app_src):
            # Skip .xcodeproj, .xcworkspace, and build directories
            dirs[:] = [
                d for d in dirs
                if not d.endswith((".xcodeproj", ".xcworkspace", ".build"))
            ]

            for filename in files:
                filepath = Path(root) / filename
                # Relative path inside the .app bundle
                rel_path = filepath.relative_to(app_src)
                arcname = f"Payload/{bundle_name}/{rel_path}"

                zf.write(filepath, arcname)
                file_count += 1
                total_bytes += filepath.stat().st_size

                if file_count % 5000 == 0:
                    print(f"    ... {file_count} files packed")

    elapsed = time.time() - t0
    ipa_size = output_path.stat().st_size
    ipa_mb = ipa_size / (1024 * 1024)
    src_mb = total_bytes / (1024 * 1024)

    print(f"  Done! {file_count} files packed in {elapsed:.1f}s")
    print(f"  Source size:  {src_mb:.1f} MB")
    print(f"  IPA size:     {ipa_mb:.1f} MB")
    print(f"  Compression:  {(1 - ipa_size / total_bytes) * 100:.0f}% smaller")

    return output_path


def _find_app_source(project_dir: Path) -> Path | None:
    """Find the directory containing Info.plist (the app source root)."""
    # Direct Info.plist in project_dir
    if (project_dir / "Info.plist").exists():
        return project_dir

    # Look one level down (e.g., ConvertedApp/ConvertedApp/Info.plist)
    for child in project_dir.iterdir():
        if child.is_dir() and (child / "Info.plist").exists():
            # Make sure this isn't the .xcodeproj
            if not child.name.endswith((".xcodeproj", ".xcworkspace")):
                return child

    # Search deeper
    for plist in project_dir.rglob("Info.plist"):
        parent = plist.parent
        if not any(p.name.endswith((".xcodeproj", ".xcworkspace"))
                   for p in parent.parents):
            return parent

    return None
