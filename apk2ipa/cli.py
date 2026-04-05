"""
apk2ipa CLI

Commands:
  apk2ipa convert <apk>      APK → Xcode project (runnable on iPad/Mac)
  apk2ipa info <apk>         Print APK metadata
  apk2ipa decompile <apk>    Decompile DEX → Java only (no translation)
  apk2ipa translate <java>   Translate a Java file to Swift
"""

from __future__ import annotations

import sys
import logging
from pathlib import Path

import click

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich import print as rprint
    _HAS_RICH = True
except ImportError:
    _HAS_RICH = False


# Configure logging
logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s: %(message)s"
)


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def main(verbose: bool) -> None:
    """apk2ipa — Convert Android APKs to iOS Xcode projects."""
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)


# ---------------------------------------------------------------------------
# convert
# ---------------------------------------------------------------------------

@main.command()
@click.argument("apk_path", type=click.Path(exists=True, path_type=Path))
@click.option("--output", "-o", type=click.Path(path_type=Path),
              default=None, help="Output directory (default: ./output/<app_name>)")
@click.option("--no-decompile", is_flag=True,
              help="Skip DEX decompilation (generate stubs only)")
def convert(apk_path: Path, output: Path | None, no_decompile: bool) -> None:
    # Note: supports .apk, .xapk, .apks, .apkm bundle formats
    """
    Convert an APK to a buildable Xcode project.

    \b
    Example:
        apk2ipa convert myapp.apk
        apk2ipa convert myapp.apk --output ~/Desktop/ios_project
    """
    from apk2ipa.core.ipa_builder import XcodeProjectBuilder

    if output is None:
        output = Path("output")

    click.echo(f"\napk2ipa — Converting {apk_path.name}")
    click.echo("=" * 50)

    try:
        builder = XcodeProjectBuilder(apk_path)
        xcodeproj = builder.build(output_dir=output)

        click.echo("\n✓ Done!")
        click.echo(f"\nXcode project: {xcodeproj}")
        click.echo(f"Review guide:  {xcodeproj.parent / 'README_REVIEW.md'}")
        click.echo("\nNext steps:")
        click.echo("  1. Open the .xcodeproj in Xcode (Mac or iPad)")
        click.echo("  2. Set your Development Team (Signing & Capabilities)")
        click.echo("  3. Build — review README_REVIEW.md for known issues")

    except FileNotFoundError as e:
        click.echo(f"\n✗ Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"\n✗ Conversion failed: {e}", err=True)
        logging.exception("Conversion error")
        sys.exit(1)


# ---------------------------------------------------------------------------
# info
# ---------------------------------------------------------------------------

@main.command()
@click.argument("apk_path", type=click.Path(exists=True, path_type=Path))
def info(apk_path: Path) -> None:
    """Print APK metadata."""
    from apk2ipa.core.apk_parser import ApkParser

    try:
        parser = ApkParser(apk_path)
        apk_info = parser.parse()
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if _HAS_RICH:
        console = Console()
        table = Table(title=f"APK Info — {apk_path.name}", show_header=False)
        table.add_column("Key",   style="bold cyan", width=24)
        table.add_column("Value", style="white")

        size_mb = apk_info.total_size_bytes / (1024 * 1024) if apk_info.total_size_bytes else 0
        table.add_row("Package",        apk_info.package_name or "(unknown)")
        table.add_row("App Name",       apk_info.app_name or "(unknown)")
        table.add_row("Version",        f"{apk_info.version_name} (code {apk_info.version_code})")
        table.add_row("Min SDK",        str(apk_info.min_sdk))
        table.add_row("Target SDK",     str(apk_info.target_sdk))
        table.add_row("Main Activity",  apk_info.main_activity or "(none)")
        table.add_row("Game Engine",    apk_info.detected_engine or "(none)")
        table.add_row("Total Files",    str(apk_info.total_files))
        table.add_row("Uncompressed",   f"{size_mb:.1f} MB")
        table.add_row("DEX files",      str(len(apk_info.dex_files)))
        table.add_row("Layout files",   str(len(apk_info.layout_files)))
        table.add_row("Drawable files", str(len(apk_info.drawable_files)))
        table.add_row("Sound files",    str(len(apk_info.sound_files)))
        table.add_row("Font files",     str(len(apk_info.font_files)))
        table.add_row("Game data",      str(len(apk_info.game_data_files)))
        table.add_row("Native libs",    str(len(apk_info.native_libs)))
        table.add_row("Architectures",  ", ".join(sorted(apk_info.native_lib_archs.keys())) or "(none)")
        table.add_row("Permissions",    str(len(apk_info.permissions)))
        table.add_row("Activities",     str(len(apk_info.activities)))
        table.add_row("Services",       str(len(apk_info.services)))
        table.add_row("SHA-256",        apk_info.apk_hash[:16] + "...")

        console.print(table)

        if apk_info.permissions:
            console.print("\n[bold]Permissions:[/bold]")
            for p in sorted(apk_info.permissions):
                console.print(f"  • {p}")

        if apk_info.detected_engine and apk_info.engine_details:
            console.print(f"\n[bold]Game Engine: {apk_info.detected_engine.title()}[/bold]")
            if apk_info.engine_details.get("note"):
                console.print(f"  {apk_info.engine_details['note']}")
            if apk_info.engine_details.get("ios_equivalent"):
                console.print(f"  iOS: {apk_info.engine_details['ios_equivalent']}")

        if apk_info.native_lib_archs:
            console.print("\n[bold]Native Libraries by Architecture:[/bold]")
            for arch, libs in sorted(apk_info.native_lib_archs.items()):
                console.print(f"  [cyan]{arch}[/cyan]: {', '.join(libs[:10])}"
                              + (f" (+{len(libs)-10} more)" if len(libs) > 10 else ""))

    else:
        click.echo(f"Package:    {apk_info.package_name}")
        click.echo(f"App Name:   {apk_info.app_name}")
        click.echo(f"Version:    {apk_info.version_name} (code {apk_info.version_code})")
        click.echo(f"Min SDK:    {apk_info.min_sdk}")
        click.echo(f"Main:       {apk_info.main_activity}")
        click.echo(f"DEX files:  {len(apk_info.dex_files)}")
        click.echo(f"Layouts:    {len(apk_info.layout_files)}")
        if apk_info.permissions:
            click.echo("Permissions:")
            for p in sorted(apk_info.permissions):
                click.echo(f"  {p}")


# ---------------------------------------------------------------------------
# translate (single Java file)
# ---------------------------------------------------------------------------

@main.command()
@click.argument("java_path", type=click.Path(exists=True, path_type=Path))
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None)
def translate(java_path: Path, output: Path | None) -> None:
    """
    Translate a single Java source file to Swift.

    \b
    Example:
        apk2ipa translate MainActivity.java
        apk2ipa translate MainActivity.java -o MainViewController.swift
    """
    from apk2ipa.translators.java_to_swift import JavaToSwiftTranspiler

    source = java_path.read_text(encoding="utf-8", errors="replace")
    transpiler = JavaToSwiftTranspiler()
    result = transpiler.transpile(source, java_path.stem)

    if output is None:
        click.echo(result.swift_source)
    else:
        output.write_text(result.swift_source, encoding="utf-8")
        click.echo(f"Written to {output}")

    if result.issues:
        click.echo(f"\n{len(result.issues)} translation issue(s):", err=True)
        for issue in result.issues[:10]:
            click.echo(f"  Line {issue.line}: {issue.note}", err=True)


# ---------------------------------------------------------------------------
# layout (single XML file)
# ---------------------------------------------------------------------------

@main.command()
@click.argument("xml_path", type=click.Path(exists=True, path_type=Path))
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None)
@click.option("--name", "-n", default=None, help="SwiftUI struct name")
def layout(xml_path: Path, output: Path | None, name: str | None) -> None:
    """
    Convert an Android XML layout file to SwiftUI.

    \b
    Example:
        apk2ipa layout res/layout/activity_main.xml
        apk2ipa layout activity_main.xml -o MainView.swift -n MainView
    """
    from apk2ipa.translators.layout_to_swiftui import LayoutConverter

    xml_source = xml_path.read_text(encoding="utf-8", errors="replace")
    view_name = name or _to_type_name(xml_path.stem)
    converter = LayoutConverter()
    swift_code = converter.convert(xml_source, view_name)

    if output is None:
        click.echo(swift_code)
    else:
        output.write_text(swift_code, encoding="utf-8")
        click.echo(f"Written to {output}")


# ---------------------------------------------------------------------------
# decompile
# ---------------------------------------------------------------------------

@main.command()
@click.argument("apk_path", type=click.Path(exists=True, path_type=Path))
@click.option("--output", "-o", type=click.Path(path_type=Path),
              default=None, help="Output directory for Java sources")
def decompile(apk_path: Path, output: Path | None) -> None:
    """
    Decompile DEX bytecode to Java source (no translation).

    Requires jadx or androguard to be installed.

    \b
    Example:
        apk2ipa decompile myapp.apk -o ./java_src
    """
    from apk2ipa.core.apk_parser import ApkParser
    from apk2ipa.core.code_translator import CodeTranslator

    if output is None:
        output = Path("output") / "decompiled_java"

    parser = ApkParser(apk_path)
    apk_info = parser.parse()
    extract_dir = output / "_apk_extracted"
    parser.extract_all(extract_dir)

    translator = CodeTranslator(extract_dir, apk_info)
    summary: dict = {"decompiler_used": "none", "warnings": []}
    java_dir = translator._decompile_dex(summary)

    click.echo(f"Decompiler: {summary['decompiler_used']}")
    if java_dir:
        click.echo(f"Output:     {java_dir}")
    if summary["warnings"]:
        for w in summary["warnings"]:
            click.echo(f"Warning:    {w}", err=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_type_name(name: str) -> str:
    import re
    parts = re.split(r"[_\-\s]+", name)
    return "".join(p.capitalize() for p in parts if p)


if __name__ == "__main__":
    main()
