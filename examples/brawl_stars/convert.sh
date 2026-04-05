#!/usr/bin/env bash
#
# Brawl Stars APK → iOS Xcode Project conversion
#
# Usage:
#   ./convert.sh /path/to/brawl_stars.apk
#   ./convert.sh /path/to/brawl_stars.apk /path/to/output
#
set -euo pipefail

APK_PATH="${1:?Usage: $0 <apk_path> [output_dir]}"
OUTPUT_DIR="${2:-./brawl_stars_ios}"

if [ ! -f "$APK_PATH" ]; then
    echo "Error: APK not found at $APK_PATH"
    exit 1
fi

echo "============================================"
echo "  Brawl Stars APK → iOS Conversion"
echo "============================================"
echo ""
echo "APK:    $APK_PATH"
echo "Output: $OUTPUT_DIR"
echo ""

# Show APK info first
echo "--- APK Info ---"
python -m apk2ipa info "$APK_PATH"
echo ""

# Run full conversion
echo "--- Starting Conversion ---"
python -m apk2ipa convert "$APK_PATH" --output "$OUTPUT_DIR" -v
echo ""

echo "============================================"
echo "  Conversion Complete!"
echo "============================================"
echo ""
echo "Output: $OUTPUT_DIR"
echo ""
echo "Next steps:"
echo "  1. Open the .xcodeproj in Xcode"
echo "  2. Set your Development Team"
echo "  3. Review README_REVIEW.md for what needs manual work"
echo "  4. Review Resources/NativeLibs/NativeLibs_README.md"
echo "     for native library → iOS framework mapping"
echo ""
echo "Note: Brawl Stars uses Supercell's custom engine."
echo "The game logic is in native C++ libraries that need"
echo "the iOS version of Supercell's engine to run."
echo "All game assets (.sc textures, CSV data, sounds)"
echo "have been copied to Resources/GameData."
