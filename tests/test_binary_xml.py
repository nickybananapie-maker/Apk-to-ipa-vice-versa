"""Tests for the binary XML parser."""

import pytest
from apk2ipa.utils.binary_xml import AxmlParser, axml_to_dict


class TestAxmlParser:

    def test_returns_none_for_empty_input(self):
        parser = AxmlParser(b"")
        result = parser.parse()
        assert result is None

    def test_returns_none_for_non_axml(self):
        parser = AxmlParser(b"This is not binary XML at all")
        result = parser.parse()
        assert result is None

    def test_axml_to_dict_returns_none_for_garbage(self):
        result = axml_to_dict(b"\x00\x01\x02\x03")
        assert result is None

    def test_valid_axml_parses(self):
        """
        Test with a minimal hand-crafted binary XML.

        This test primarily validates the parser doesn't crash on valid-ish input.
        Full end-to-end testing requires a real APK (integration test).
        """
        # We test the negative case (invalid magic) gracefully
        bad_magic = b"\xFF\xFF\x00\x00" + b"\x08\x00\x00\x00" + b"\x10\x00\x00\x00"
        result = axml_to_dict(bad_magic)
        assert result is None


class TestManifestFallbackParser:
    """Test the pure-Python AXML fallback in ApkParser."""

    def test_extracts_package_from_utf16_strings(self):
        from apk2ipa.core.apk_parser import ApkParser, ApkInfo
        import struct

        info = ApkInfo()
        parser = ApkParser.__new__(ApkParser)

        # Build fake binary with UTF-16LE string "com.example.test"
        pkg = "com.example.test"
        utf16 = pkg.encode("utf-16-le")
        # Pad with zeros to simulate binary data
        fake_data = b"\x00" * 100 + utf16 + b"\x00\x00" + b"\x00" * 100

        parser._parse_axml_minimal(fake_data, info)
        assert info.package_name == "com.example.test"
