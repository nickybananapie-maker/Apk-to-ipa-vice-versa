"""Tests for the manifest translator."""

import plistlib
import pytest

from apk2ipa.core.apk_parser import ApkInfo
from apk2ipa.core.manifest_translator import ManifestTranslator, PERMISSION_MAP


def _make_info(**kwargs) -> ApkInfo:
    defaults = dict(
        package_name="com.example.myapp",
        app_name="My App",
        version_name="2.1.0",
        version_code=42,
        min_sdk=26,
        target_sdk=34,
        permissions=[],
        activities=[],
        services=[],
        receivers=[],
        providers=[],
        main_activity="com.example.myapp.MainActivity",
    )
    defaults.update(kwargs)
    return ApkInfo(**defaults)


class TestManifestTranslator:

    def test_bundle_id_basic(self):
        info = _make_info(package_name="com.example.myapp")
        result = ManifestTranslator(info).translate()
        assert result.plist_data["CFBundleIdentifier"] == "com.example.myapp"

    def test_bundle_id_underscore_to_hyphen(self):
        info = _make_info(package_name="com.example.my_app")
        result = ManifestTranslator(info).translate()
        assert result.plist_data["CFBundleIdentifier"] == "com.example.my-app"

    def test_version_fields(self):
        info = _make_info(version_name="3.2.1", version_code=99)
        result = ManifestTranslator(info).translate()
        assert result.plist_data["CFBundleShortVersionString"] == "3.2.1"
        assert result.plist_data["CFBundleVersion"] == "99"

    def test_app_name(self):
        info = _make_info(app_name="Awesome App")
        result = ManifestTranslator(info).translate()
        assert result.plist_data["CFBundleDisplayName"] == "Awesome App"
        assert result.plist_data["CFBundleName"] == "Awesome App"

    def test_camera_permission_mapped(self):
        info = _make_info(permissions=["android.permission.CAMERA"])
        result = ManifestTranslator(info).translate()
        assert "NSCameraUsageDescription" in result.plist_data

    def test_microphone_permission_mapped(self):
        info = _make_info(permissions=["android.permission.RECORD_AUDIO"])
        result = ManifestTranslator(info).translate()
        assert "NSMicrophoneUsageDescription" in result.plist_data

    def test_location_permission_mapped(self):
        info = _make_info(permissions=["android.permission.ACCESS_FINE_LOCATION"])
        result = ManifestTranslator(info).translate()
        assert "NSLocationWhenInUseUsageDescription" in result.plist_data

    def test_internet_permission_no_ios_key(self):
        info = _make_info(permissions=["android.permission.INTERNET"])
        result = ManifestTranslator(info).translate()
        # INTERNET has no iOS equivalent — should not appear as a key
        assert "NSInternetUsageDescription" not in result.plist_data

    def test_unknown_permission_in_unmapped(self):
        info = _make_info(permissions=["com.example.CUSTOM_PERMISSION"])
        result = ManifestTranslator(info).translate()
        assert "com.example.CUSTOM_PERMISSION" in result.unmapped_permissions

    def test_min_sdk_to_ios_version(self):
        assert ManifestTranslator._android_sdk_to_ios(26) == "13.0"
        assert ManifestTranslator._android_sdk_to_ios(33) == "16.0"
        assert ManifestTranslator._android_sdk_to_ios(0) == "14.0"

    def test_plist_is_serializable(self):
        info = _make_info(permissions=["android.permission.CAMERA"])
        result = ManifestTranslator(info).translate()
        # Must be serializable by plistlib
        data = plistlib.dumps(result.plist_data, fmt=plistlib.FMT_XML)
        assert b"CFBundleIdentifier" in data

    def test_url_scheme_derived_from_package(self):
        info = _make_info(package_name="com.example.myapp")
        result = ManifestTranslator(info).translate()
        url_types = result.plist_data.get("CFBundleURLTypes", [])
        assert url_types
        assert "myapp" in url_types[0]["CFBundleURLSchemes"]

    def test_background_modes_for_audio_service(self):
        info = _make_info(services=[{"name": "com.example.MusicPlayerService"}])
        result = ManifestTranslator(info).translate()
        modes = result.plist_data.get("UIBackgroundModes", [])
        assert "audio" in modes

    def test_empty_package_fallback(self):
        info = _make_info(package_name="")
        result = ManifestTranslator(info).translate()
        assert result.plist_data["CFBundleIdentifier"] == "com.example.convertedapp"
        assert any(n.level == "warning" for n in result.notes)
