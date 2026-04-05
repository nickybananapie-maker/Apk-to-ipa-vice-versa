"""
Android Binary XML (AXML) parser — pure Python, no external deps.

The Android Binary XML format is a compact binary encoding of XML used for
AndroidManifest.xml and compiled layout files.  The format:

  [File Header]            8 bytes
  [String Pool chunk]      variable
  [Resource IDs chunk]     variable  (optional)
  [XML nodes]              variable

Each chunk begins with a 4-byte type, 4-byte header size, 4-byte chunk size.

Reference: https://android.googlesource.com/platform/frameworks/base/+/master/libs/androidfw/include/androidfw/ResourceTypes.h
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Optional


# Chunk types
_RES_NULL_TYPE           = 0x0000
_RES_STRING_POOL_TYPE    = 0x0001
_RES_XML_TYPE            = 0x0003
_RES_XML_START_NAMESPACE = 0x0100
_RES_XML_END_NAMESPACE   = 0x0101
_RES_XML_START_ELEMENT   = 0x0102
_RES_XML_END_ELEMENT     = 0x0103
_RES_XML_CDATA           = 0x0104
_RES_TABLE_PACKAGE_TYPE  = 0x0200

# String pool flags
_UTF8_FLAG = 1 << 8


@dataclass
class XmlAttribute:
    namespace: str
    name: str
    value: str
    resource_id: int = 0


@dataclass
class XmlElement:
    tag: str
    namespace: str
    attributes: list[XmlAttribute] = field(default_factory=list)
    children: list["XmlElement"] = field(default_factory=list)
    text: str = ""


class AxmlParser:
    """
    Parse Android Binary XML and return a tree of XmlElement nodes.

    Usage::

        parser = AxmlParser(raw_bytes)
        root = parser.parse()
        # root is an XmlElement (the <manifest> element, etc.)
    """

    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0
        self._strings: list[str] = []
        self._namespaces: dict[str, str] = {}  # prefix → URI
        self._ns_uri_to_prefix: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def parse(self) -> Optional[XmlElement]:
        """Return root XmlElement or None on failure."""
        try:
            return self._parse()
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _parse(self) -> Optional[XmlElement]:
        self._pos = 0
        # File header (type=0x0003, header_size=8, file_size=N)
        file_type, header_size, file_size = struct.unpack_from("<HHI", self._data, 0)
        if file_type != _RES_XML_TYPE:
            raise ValueError(f"Not a binary XML file (type={file_type:#x})")

        self._pos = header_size  # skip file header

        element_stack: list[XmlElement] = []
        root: Optional[XmlElement] = None

        while self._pos < file_size and self._pos < len(self._data):
            chunk_start = self._pos
            if chunk_start + 8 > len(self._data):
                break

            chunk_type, chunk_header_size, chunk_size = struct.unpack_from(
                "<HHI", self._data, chunk_start
            )

            if chunk_size == 0:
                break

            if chunk_type == _RES_STRING_POOL_TYPE:
                self._parse_string_pool(chunk_start, chunk_size)

            elif chunk_type == _RES_XML_START_NAMESPACE:
                self._parse_namespace(chunk_start, chunk_header_size, start=True)

            elif chunk_type == _RES_XML_END_NAMESPACE:
                self._parse_namespace(chunk_start, chunk_header_size, start=False)

            elif chunk_type == _RES_XML_START_ELEMENT:
                elem = self._parse_start_element(chunk_start, chunk_header_size)
                if elem:
                    if element_stack:
                        element_stack[-1].children.append(elem)
                    else:
                        root = elem
                    element_stack.append(elem)

            elif chunk_type == _RES_XML_END_ELEMENT:
                if element_stack:
                    element_stack.pop()

            elif chunk_type == _RES_XML_CDATA:
                text = self._parse_cdata(chunk_start, chunk_header_size)
                if element_stack and text:
                    element_stack[-1].text += text

            self._pos = chunk_start + chunk_size

        return root

    def _parse_string_pool(self, chunk_start: int, chunk_size: int) -> None:
        """Parse the string pool chunk and populate self._strings."""
        # String pool header
        # type(2) + header_size(2) + chunk_size(4) + string_count(4) +
        # style_count(4) + flags(4) + strings_start(4) + styles_start(4)
        if chunk_start + 28 > len(self._data):
            return

        (string_count, style_count, flags,
         strings_start, styles_start) = struct.unpack_from(
            "<IIIII", self._data, chunk_start + 8
        )

        is_utf8 = bool(flags & _UTF8_FLAG)
        offsets_start = chunk_start + 28  # after 28-byte header

        self._strings = []
        for i in range(string_count):
            offset_pos = offsets_start + i * 4
            if offset_pos + 4 > len(self._data):
                break
            (offset,) = struct.unpack_from("<I", self._data, offset_pos)
            str_pos = chunk_start + strings_start + offset
            try:
                s = self._read_string(str_pos, is_utf8)
            except Exception:
                s = ""
            self._strings.append(s)

    def _read_string(self, pos: int, is_utf8: bool) -> str:
        if is_utf8:
            # UTF-8 encoded: u16_len then u8_len then bytes
            if pos + 2 > len(self._data):
                return ""
            # character length (may be 2 bytes)
            char_len = self._data[pos]
            if char_len & 0x80:
                char_len = ((char_len & 0x7F) << 8) | self._data[pos + 1]
                pos += 2
            else:
                pos += 1
            # byte length
            byte_len = self._data[pos]
            if byte_len & 0x80:
                byte_len = ((byte_len & 0x7F) << 8) | self._data[pos + 1]
                pos += 2
            else:
                pos += 1
            return self._data[pos: pos + byte_len].decode("utf-8", errors="replace")
        else:
            # UTF-16LE: u16 length then u16 chars
            if pos + 2 > len(self._data):
                return ""
            (char_len,) = struct.unpack_from("<H", self._data, pos)
            if char_len & 0x8000:
                if pos + 4 > len(self._data):
                    return ""
                (lo, hi) = struct.unpack_from("<HH", self._data, pos)
                char_len = ((lo & 0x7FFF) << 16) | hi
                pos += 4
            else:
                pos += 2
            byte_len = char_len * 2
            return self._data[pos: pos + byte_len].decode("utf-16-le", errors="replace")

    def _get_string(self, idx: int) -> str:
        if 0 <= idx < len(self._strings):
            return self._strings[idx]
        return ""

    def _parse_namespace(self, chunk_start: int, header_size: int, start: bool) -> None:
        if chunk_start + header_size + 8 > len(self._data):
            return
        prefix_idx, uri_idx = struct.unpack_from("<II", self._data, chunk_start + header_size)
        prefix = self._get_string(prefix_idx)
        uri = self._get_string(uri_idx)
        if start:
            self._namespaces[prefix] = uri
            self._ns_uri_to_prefix[uri] = prefix
        else:
            self._namespaces.pop(prefix, None)
            self._ns_uri_to_prefix.pop(uri, None)

    def _parse_start_element(self, chunk_start: int, header_size: int) -> Optional[XmlElement]:
        """
        Start element node layout (after generic header):
          line_number(4) + comment(4) + ns_idx(4) + name_idx(4) +
          attr_start(2) + attr_size(2) + attr_count(2) + id_idx(2) +
          class_idx(2) + style_idx(2)
        Then attr_count * attribute records:
          ns_idx(4) + name_idx(4) + raw_val_idx(4) +
          value_size(2) + res0(1) + data_type(1) + data(4)
        """
        base = chunk_start + header_size
        if base + 20 > len(self._data):
            return None

        ns_idx, name_idx = struct.unpack_from("<II", self._data, base + 4)
        (attr_start, attr_size, attr_count,
         id_idx, class_idx, style_idx) = struct.unpack_from("<HHHHHH", self._data, base + 12)

        tag_name = self._get_string(name_idx)
        tag_ns = self._get_string(ns_idx)
        elem = XmlElement(tag=tag_name, namespace=tag_ns)

        attr_base = chunk_start + header_size + attr_start
        for i in range(attr_count):
            ap = attr_base + i * attr_size
            if ap + 20 > len(self._data):
                break
            (a_ns_idx, a_name_idx, a_raw_idx,
             a_val_size, a_res0, a_data_type, a_data) = struct.unpack_from(
                "<IIIHBBi", self._data, ap
            )
            attr_ns = self._get_string(a_ns_idx)
            attr_name = self._get_string(a_name_idx)
            attr_value = self._resolve_attr_value(a_data_type, a_data, a_raw_idx)
            elem.attributes.append(XmlAttribute(
                namespace=attr_ns,
                name=attr_name,
                value=attr_value,
            ))

        return elem

    def _resolve_attr_value(self, data_type: int, data: int, raw_idx: int) -> str:
        """Convert a binary attribute value to a string representation."""
        TYPE_STRING    = 0x03
        TYPE_INT_DEC   = 0x10
        TYPE_INT_HEX   = 0x11
        TYPE_INT_BOOL  = 0x12
        TYPE_INT_COLOR = 0x1C

        if data_type == TYPE_STRING:
            return self._get_string(raw_idx)
        elif data_type == TYPE_INT_BOOL:
            return "true" if data else "false"
        elif data_type == TYPE_INT_DEC:
            return str(data)
        elif data_type == TYPE_INT_HEX:
            return f"0x{data & 0xFFFFFFFF:08x}"
        elif data_type == TYPE_INT_COLOR:
            return f"#{data & 0xFFFFFFFF:08x}"
        elif raw_idx >= 0:
            raw = self._get_string(raw_idx)
            if raw:
                return raw
        return str(data)

    def _parse_cdata(self, chunk_start: int, header_size: int) -> str:
        base = chunk_start + header_size
        if base + 8 > len(self._data):
            return ""
        (data_idx,) = struct.unpack_from("<I", self._data, base + 4)
        return self._get_string(data_idx)


def axml_to_dict(data: bytes) -> Optional[dict]:
    """
    Parse Android Binary XML and return a nested dict representation.
    Useful for debugging and testing.
    """
    parser = AxmlParser(data)
    root = parser.parse()
    if root is None:
        return None
    return _elem_to_dict(root)


def _elem_to_dict(elem: XmlElement) -> dict:
    return {
        "tag": elem.tag,
        "namespace": elem.namespace,
        "text": elem.text,
        "attributes": [
            {"ns": a.namespace, "name": a.name, "value": a.value}
            for a in elem.attributes
        ],
        "children": [_elem_to_dict(c) for c in elem.children],
    }
