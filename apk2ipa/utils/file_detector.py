"""
Magic-byte file type detection.

Instead of relying solely on file extensions, this module reads the first
few bytes of a file to determine its true type.  This catches renamed,
extensionless, or misextensioned files common in game APKs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


# (magic_bytes, offset, detected_type, category)
SIGNATURES: list[tuple[bytes, int, str, str]] = [
    # Images
    (b"\x89PNG\r\n\x1a\n",     0, "png",     "image"),
    (b"\xff\xd8\xff",           0, "jpeg",    "image"),
    (b"GIF87a",                 0, "gif",     "image"),
    (b"GIF89a",                 0, "gif",     "image"),
    (b"RIFF",                   0, "webp",    "image"),   # + "WEBP" at offset 8
    (b"BM",                     0, "bmp",     "image"),
    (b"\x00\x00\x01\x00",      0, "ico",     "image"),

    # Audio
    (b"OggS",                   0, "ogg",     "sound"),
    (b"ID3",                    0, "mp3",     "sound"),
    (b"\xff\xfb",               0, "mp3",     "sound"),
    (b"\xff\xf3",               0, "mp3",     "sound"),
    (b"RIFF",                   0, "wav",     "sound"),   # + "WAVE" at offset 8
    (b"fLaC",                   0, "flac",    "sound"),
    (b"BKHD",                   0, "bnk",     "sound"),   # Wwise soundbank
    (b"FSB5",                   0, "fsb",     "sound"),   # FMOD soundbank
    (b"RIFF",                   0, "wem",     "sound"),   # Wwise encoded media

    # Video
    (b"\x00\x00\x00",          0, "mp4",     "video"),   # ftyp at offset 4
    (b"\x1aE\xdf\xa3",         0, "webm",    "video"),   # matroska/webm

    # Fonts
    (b"\x00\x01\x00\x00",      0, "ttf",     "font"),
    (b"OTTO",                   0, "otf",     "font"),
    (b"wOFF",                   0, "woff",    "font"),
    (b"wOF2",                   0, "woff2",   "font"),

    # Compressed
    (b"PK\x03\x04",            0, "zip",     "archive"),
    (b"\x1f\x8b",              0, "gzip",    "archive"),
    (b"BZ",                    0, "bzip2",   "archive"),
    (b"\xfd7zXZ",              0, "xz",      "archive"),
    (b"7z\xbc\xaf\x27\x1c",   0, "7z",      "archive"),
    (b"Rar!\x1a\x07",         0, "rar",      "archive"),

    # Game data
    (b"SC",                     0, "sc",      "game_data"),  # Supercell compressed texture
    (b"SQLite format 3",       0, "sqlite",  "database"),
    (b"LZMA",                  0, "lzma",    "compressed"),
    (b"UnityFS",               0, "unity",   "game_data"),  # Unity asset bundle
    (b"\xce\xfa\xed\xfe",     0, "macho",   "binary"),     # Mach-O (iOS binary)
    (b"\x7fELF",               0, "elf",     "binary"),     # ELF (Linux/Android binary)

    # Certificates / signing
    (b"\x30\x82",              0, "der",     "certificate"),

    # XML / text
    (b"<?xml",                 0, "xml",     "xml"),
    (b"{",                     0, "json",    "data"),       # Heuristic

    # Android-specific
    (b"\x03\x00\x08\x00",     0, "axml",    "android"),    # Android binary XML
    (b"\x02\x00\x0c\x00",     0, "arsc",    "android"),    # Android resources.arsc

    # Protobuf (common in games)
    (b"\x0a",                  0, "proto",   "data"),       # Very rough heuristic

    # Lua bytecode
    (b"\x1bLua",               0, "luac",    "game_data"),
    (b"\x1bLJ",                0, "luajit",  "game_data"),  # LuaJIT bytecode
]


def detect_file_type(path: Path) -> tuple[str, str]:
    """
    Detect file type by magic bytes.

    Returns (type_name, category) e.g. ("png", "image"), ("sc", "game_data").
    Falls back to extension-based detection if magic bytes don't match.
    """
    try:
        with open(path, "rb") as f:
            header = f.read(32)
    except (OSError, IOError):
        return _from_extension(path)

    if not header:
        return _from_extension(path)

    for magic, offset, type_name, category in SIGNATURES:
        end = offset + len(magic)
        if end <= len(header) and header[offset:end] == magic:
            # Disambiguation for RIFF container (WAV vs WEBP vs WEM)
            if magic == b"RIFF" and len(header) >= 12:
                sub = header[8:12]
                if sub == b"WEBP":
                    return ("webp", "image")
                elif sub == b"WAVE":
                    return ("wav", "sound")
                elif sub == b"XWMA":
                    return ("wem", "sound")
                return ("riff", "sound")

            # Disambiguation for MP4 container
            if type_name == "mp4" and len(header) >= 8:
                if header[4:8] == b"ftyp":
                    return ("mp4", "video")
                continue  # Not actually mp4

            return (type_name, category)

    return _from_extension(path)


def _from_extension(path: Path) -> tuple[str, str]:
    """Fallback: detect by file extension."""
    ext = path.suffix.lower().lstrip(".")

    EXT_MAP = {
        # Images
        "png": ("png", "image"), "jpg": ("jpeg", "image"), "jpeg": ("jpeg", "image"),
        "gif": ("gif", "image"), "webp": ("webp", "image"), "bmp": ("bmp", "image"),
        "svg": ("svg", "image"), "ico": ("ico", "image"),
        "pvr": ("pvr", "image"), "ktx": ("ktx", "image"), "astc": ("astc", "image"),
        "tex": ("tex", "image"), "dds": ("dds", "image"),

        # Sound
        "ogg": ("ogg", "sound"), "mp3": ("mp3", "sound"), "wav": ("wav", "sound"),
        "flac": ("flac", "sound"), "aac": ("aac", "sound"), "m4a": ("m4a", "sound"),
        "opus": ("opus", "sound"), "mid": ("mid", "sound"), "midi": ("midi", "sound"),
        "bnk": ("bnk", "sound"), "wem": ("wem", "sound"), "fsb": ("fsb", "sound"),

        # Video
        "mp4": ("mp4", "video"), "3gp": ("3gp", "video"), "webm": ("webm", "video"),
        "mkv": ("mkv", "video"), "avi": ("avi", "video"),

        # Fonts
        "ttf": ("ttf", "font"), "otf": ("otf", "font"),
        "woff": ("woff", "font"), "woff2": ("woff2", "font"),

        # Game data
        "sc": ("sc", "game_data"), "csv": ("csv", "game_data"),
        "json": ("json", "data"), "bin": ("bin", "game_data"),
        "dat": ("dat", "game_data"), "lua": ("lua", "game_data"),
        "luac": ("luac", "game_data"), "atlas": ("atlas", "game_data"),
        "skel": ("skel", "game_data"), "fnt": ("fnt", "game_data"),
        "tmx": ("tmx", "game_data"), "pkl": ("pkl", "game_data"),

        # Database
        "db": ("db", "database"), "sqlite": ("sqlite", "database"),

        # Config
        "properties": ("properties", "config"), "cfg": ("cfg", "config"),
        "ini": ("ini", "config"), "conf": ("conf", "config"),
        "yml": ("yml", "config"), "yaml": ("yaml", "config"),
        "toml": ("toml", "config"), "xml": ("xml", "xml"),

        # Archives
        "zip": ("zip", "archive"), "gz": ("gz", "archive"),
        "lz4": ("lz4", "archive"), "zst": ("zst", "archive"),

        # Code
        "js": ("js", "code"), "html": ("html", "code"), "css": ("css", "code"),
        "so": ("so", "binary"), "dex": ("dex", "binary"),
    }

    return EXT_MAP.get(ext, ("unknown", "unknown"))


def detect_from_bytes(data: bytes) -> tuple[str, str]:
    """Detect type from raw bytes (first 32 bytes checked)."""
    header = data[:32]
    for magic, offset, type_name, category in SIGNATURES:
        end = offset + len(magic)
        if end <= len(header) and header[offset:end] == magic:
            return (type_name, category)
    return ("unknown", "unknown")
