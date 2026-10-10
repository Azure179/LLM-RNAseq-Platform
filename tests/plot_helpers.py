"""Shared synthetic image for renderer fault injection, never an analysis result."""

import struct
import zlib


def fixture_png() -> bytes:
    def chunk(name: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + name + payload + struct.pack(">I", zlib.crc32(name + payload))
    header = struct.pack(">IIBBBBB", 120, 100, 8, 2, 0, 0, 0)
    pixels = (b"\x00" + b"\xff" * (120 * 3)) * 100
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")
