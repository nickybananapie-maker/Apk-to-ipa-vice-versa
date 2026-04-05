"""
Manifest Translator — AndroidManifest.xml → iOS Info.plist

Android manifest fields and their iOS equivalents:

  package               → CFBundleIdentifier
  android:versionName   → CFBundleShortVersionString
  android:versionCode   → CFBundleVersion
  android:label         → CFBundleDisplayName
  android:icon          → CFBundleIconFiles
  uses-permission       → NSXxx UsageDescription keys (where applicable)
  uses-feature          → capabilities (documented in report)
  activity (launcher)   → UIMainStoryboardFile / NSPrincipalClass
  android:screenOrient. → UISupportedInterfaceOrientations

Permissions that have iOS equivalents:
  CAMERA                → NSCameraUsageDescription
  RECORD_AUDIO          → NSMicrophoneUsageDescription
  ACCESS_FINE_LOCATION  → NSLocationWhenInUseUsageDescription
  ACCESS_COARSE_LOCATION→ NSLocationWhenInUseUsageDescription
  READ_CONTACTS         → NSContactsUsageDescription
  WRITE_CONTACTS        → NSContactsUsageDescription
  READ_CALENDAR         → NSCalendarsUsageDescription
  WRITE_CALENDAR        → NSCalendarsUsageDescription
  USE_FINGERPRINT       → NSFaceIDUsageDescription
  USE_BIOMETRIC         → NSFaceIDUsageDescription
  BLUETOOTH             → NSBluetoothAlwaysUsageDescription
  BODY_SENSORS          → NSMotionUsageDescription
  ACTIVITY_RECOGNITION  → NSMotionUsageDescription
"""

from __future__ import annotations

import plistlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from apk2ipa.core.apk_parser import ApkInfo
from apk2ipa.utils.binary_xml import AxmlParser, XmlElement

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Permission mapping: Android permission → (iOS plist key, default description)
# ---------------------------------------------------------------------------
PERMISSION_MAP: dict[str, tuple[str, str]] = {
    "android.permission.CAMERA":
        ("NSCameraUsageDescription", "This app uses the camera."),
    "android.permission.RECORD_AUDIO":
        ("NSMicrophoneUsageDescription", "This app uses the microphone."),
    "android.permission.ACCESS_FINE_LOCATION":
        ("NSLocationWhenInUseUsageDescription", "This app uses your location."),
    "android.permission.ACCESS_COARSE_LOCATION":
        ("NSLocationWhenInUseUsageDescription", "This app uses your approximate location."),
    "android.permission.ACCESS_BACKGROUND_LOCATION":
        ("NSLocationAlwaysAndWhenInUseUsageDescription", "This app uses your location in the background."),
    "android.permission.READ_CONTACTS":
        ("NSContactsUsageDescription", "This app accesses your contacts."),
    "android.permission.WRITE_CONTACTS":
        ("NSContactsUsageDescription", "This app modifies your contacts."),
    "android.permission.READ_CALENDAR":
        ("NSCalendarsUsageDescription", "This app accesses your calendar."),
    "android.permission.WRITE_CALENDAR":
        ("NSCalendarsUsageDescription", "This app modifies your calendar."),
    "android.permission.USE_FINGERPRINT":
        ("NSFaceIDUsageDescription", "This app uses biometric authentication."),
    "android.permission.USE_BIOMETRIC":
        ("NSFaceIDUsageDescription", "This app uses biometric authentication."),
    "android.permission.BLUETOOTH":
        ("NSBluetoothAlwaysUsageDescription", "This app uses Bluetooth."),
    "android.permission.BLUETOOTH_CONNECT":
        ("NSBluetoothAlwaysUsageDescription", "This app uses Bluetooth."),
    "android.permission.BLUETOOTH_SCAN":
        ("NSBluetoothPeripheralUsageDescription", "This app scans for Bluetooth devices."),
    "android.permission.BODY_SENSORS":
        ("NSMotionUsageDescription", "This app accesses motion and fitness data."),
    "android.permission.ACTIVITY_RECOGNITION":
        ("NSMotionUsageDescription", "This app tracks your physical activity."),
    "android.permission.READ_MEDIA_IMAGES":
        ("NSPhotoLibraryUsageDescription", "This app accesses your photo library."),
    "android.permission.READ_EXTERNAL_STORAGE":
        ("NSPhotoLibraryUsageDescription", "This app accesses your photo library."),
    "android.permission.WRITE_EXTERNAL_STORAGE":
        ("NSPhotoLibraryAddUsageDescription", "This app saves to your photo library."),
    "android.permission.NFC":
        ("NFCReaderUsageDescription", "This app uses NFC."),
    "android.permission.MANAGE_MEDIA":
        ("NSPhotoLibraryUsageDescription", "This app manages your media library."),
    "android.permission.SPEECH_RECOGNITION":
        ("NSSpeechRecognitionUsageDescription", "This app uses speech recognition."),
    "android.permission.HEALTH":
        ("NSHealthUpdateUsageDescription", "This app updates your health data."),
}

# Permissions that have no iOS equivalent (informational only)
NO_IOS_EQUIVALENT = {
    "android.permission.INTERNET",
    "android.permission.ACCESS_NETWORK_STATE",
    "android.permission.ACCESS_WIFI_STATE",
    "android.permission.WAKE_LOCK",
    "android.permission.RECEIVE_BOOT_COMPLETED",
    "android.permission.VIBRATE",
    "android.permission.FLASHLIGHT",
}


@dataclass
class TranslationNote:
    """A human-readable note about a translation decision or limitation."""
    level: str  # "info" | "warning" | "error"
    message: str


@dataclass
class ManifestTranslationResult:
    plist_data: dict
    notes: list[TranslationNote] = field(default_factory=list)
    unmapped_permissions: list[str] = field(default_factory=list)


class ManifestTranslator:
    """
    Translates Android manifest metadata into an iOS Info.plist dictionary.

    Usage::

        translator = ManifestTranslator(apk_info)
        result = translator.translate()
        # result.plist_data is the Info.plist dictionary
        # write it with plistlib.dump(result.plist_data, f, fmt=plistlib.FMT_XML)
    """

    def __init__(self, apk_info: ApkInfo):
        self.apk_info = apk_info

    def translate(self) -> ManifestTranslationResult:
        info = self.apk_info
        notes: list[TranslationNote] = []
        unmapped: list[str] = []

        # Derive a clean bundle identifier from the Android package name
        bundle_id = self._sanitize_bundle_id(info.package_name)
        if not bundle_id:
            bundle_id = "com.example.convertedapp"
            notes.append(TranslationNote("warning",
                "Could not determine package name; using 'com.example.convertedapp'"))

        app_name = info.app_name or info.package_name.split(".")[-1] or "ConvertedApp"
        version_name = info.version_name or "1.0"
        version_code = str(info.version_code) if info.version_code else "1"

        plist: dict = {
            # Bundle identity
            "CFBundleIdentifier": bundle_id,
            "CFBundleName": app_name,
            "CFBundleDisplayName": app_name,
            "CFBundleShortVersionString": version_name,
            "CFBundleVersion": version_code,
            "CFBundleExecutable": "$(EXECUTABLE_NAME)",
            "CFBundlePackageType": "APPL",
            "CFBundleInfoDictionaryVersion": "6.0",
            "LSRequiresIPhoneOS": True,

            # Minimum iOS version (derived from Android minSdk)
            "MinimumOSVersion": self._android_sdk_to_ios(info.min_sdk),

            # Interface
            "UILaunchStoryboardName": "LaunchScreen",
            "UIMainStoryboardFile": "Main",

            # Supported orientations (default: all)
            "UISupportedInterfaceOrientations": [
                "UIInterfaceOrientationPortrait",
                "UIInterfaceOrientationLandscapeLeft",
                "UIInterfaceOrientationLandscapeRight",
            ],
            "UISupportedInterfaceOrientations~ipad": [
                "UIInterfaceOrientationPortrait",
                "UIInterfaceOrientationPortraitUpsideDown",
                "UIInterfaceOrientationLandscapeLeft",
                "UIInterfaceOrientationLandscapeRight",
            ],

            # App Transport Security (allow HTTP like Android often does)
            "NSAppTransportSecurity": {
                "NSAllowsArbitraryLoads": True,
            },

            # Icon (placeholder — will be populated from drawable conversion)
            "CFBundleIcons": {
                "CFBundlePrimaryIcon": {
                    "CFBundleIconFiles": ["AppIcon"],
                    "UIPrerenderedIcon": False,
                }
            },
        }

        # Map permissions
        seen_ios_keys: set[str] = set()
        for perm in info.permissions:
            if perm in NO_IOS_EQUIVALENT:
                notes.append(TranslationNote("info",
                    f"Android permission '{perm}' has no iOS equivalent — no action needed."))
                continue

            if perm in PERMISSION_MAP:
                ios_key, desc = PERMISSION_MAP[perm]
                if ios_key not in seen_ios_keys:
                    plist[ios_key] = desc
                    seen_ios_keys.add(ios_key)
                    notes.append(TranslationNote("info",
                        f"'{perm}' → '{ios_key}' (update the description string!)"))
            else:
                unmapped.append(perm)
                notes.append(TranslationNote("warning",
                    f"No iOS mapping for Android permission '{perm}'. "
                    "Check if it's needed and add the relevant NSXxxUsageDescription manually."))

        # Background modes (derived from services/receivers)
        bg_modes = self._infer_background_modes(info)
        if bg_modes:
            plist["UIBackgroundModes"] = bg_modes

        # URL schemes (if deep links / intent filters are detected)
        url_schemes = self._infer_url_schemes(info)
        if url_schemes:
            plist["CFBundleURLTypes"] = [
                {"CFBundleURLSchemes": url_schemes}
            ]

        # Requires full-screen (iPhone apps typically do)
        plist["UIRequiresFullScreen"] = True

        # Status bar
        plist["UIStatusBarStyle"] = "UIStatusBarStyleDefault"
        plist["UIViewControllerBasedStatusBarAppearance"] = True

        return ManifestTranslationResult(
            plist_data=plist,
            notes=notes,
            unmapped_permissions=unmapped,
        )

    def write_plist(self, path: str | Path, result: ManifestTranslationResult) -> None:
        """Write Info.plist to *path* in XML format."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            plistlib.dump(result.plist_data, f, fmt=plistlib.FMT_XML)
        logger.info("Wrote Info.plist to %s", path)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _sanitize_bundle_id(package: str) -> str:
        """
        Convert an Android package name to a valid CFBundleIdentifier.
        Android: com.example.my_app  →  iOS: com.example.my-app
        """
        if not package:
            return ""
        # Replace underscores with hyphens (iOS convention)
        clean = package.replace("_", "-")
        # Remove any characters that aren't alphanumeric, hyphens, or dots
        import re
        clean = re.sub(r"[^a-zA-Z0-9.\-]", "", clean)
        # Must start with a letter
        while clean and not clean[0].isalpha():
            clean = clean[1:]
        return clean

    @staticmethod
    def _android_sdk_to_ios(sdk: int) -> str:
        """
        Map Android minSdkVersion to a minimum iOS version string.

        This is a rough heuristic based on feature parity timelines.
        """
        if sdk <= 0:
            return "14.0"
        mapping = {
            # Android API  →  Approx iOS minimum
            16: "9.0",   # Android 4.1 (2012)  ↔  iOS 9
            17: "9.0",   # Android 4.2
            18: "9.0",   # Android 4.3
            19: "10.0",  # Android 4.4 (KitKat)
            21: "11.0",  # Android 5.0 (Lollipop) — Material Design era
            22: "11.0",  # Android 5.1
            23: "12.0",  # Android 6.0 (Marshmallow) — runtime permissions
            24: "12.0",  # Android 7.0 (Nougat)
            25: "12.0",  # Android 7.1
            26: "13.0",  # Android 8.0 (Oreo)
            27: "13.0",  # Android 8.1
            28: "13.0",  # Android 9 (Pie)
            29: "14.0",  # Android 10
            30: "14.0",  # Android 11
            31: "15.0",  # Android 12
            32: "15.0",  # Android 12L
            33: "16.0",  # Android 13
            34: "17.0",  # Android 14
        }
        for api in sorted(mapping.keys(), reverse=True):
            if sdk >= api:
                return mapping[api]
        return "14.0"

    def _infer_background_modes(self, info: ApkInfo) -> list[str]:
        """Guess iOS background modes from Android services/permissions."""
        modes: set[str] = set()
        perms = set(info.permissions)

        if "android.permission.FOREGROUND_SERVICE" in perms:
            modes.add("fetch")
        if any("music" in s["name"].lower() or "audio" in s["name"].lower()
               or "player" in s["name"].lower() or "media" in s["name"].lower()
               for s in info.services):
            modes.add("audio")
        if ("android.permission.ACCESS_BACKGROUND_LOCATION" in perms
                or "android.permission.ACCESS_FINE_LOCATION" in perms):
            if any("location" in s["name"].lower() for s in info.services):
                modes.add("location")
        if any("push" in s["name"].lower() or "notification" in s["name"].lower()
               or "fcm" in s["name"].lower() for s in info.services):
            modes.add("remote-notification")
        if any("bluetooth" in s["name"].lower() for s in info.services):
            modes.add("bluetooth-central")

        return sorted(modes)

    def _infer_url_schemes(self, info: ApkInfo) -> list[str]:
        """Guess URL schemes from the package name (deep link convention)."""
        if not info.package_name:
            return []
        # e.g. com.example.myapp → myapp
        parts = info.package_name.split(".")
        if parts:
            scheme = parts[-1].lower().replace("_", "-")
            return [scheme]
        return []
