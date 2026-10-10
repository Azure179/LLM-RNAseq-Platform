"""Shared lightweight checks for plot files; visual QA is still required."""

from pathlib import Path
import struct


def check_png(path: Path) -> None:
    """Check signature, dimensions and end marker, not a full pixel decode."""
    data = path.read_bytes()
    if len(data) < 45 or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Invalid PNG signature")
    if data[8:16] != b"\x00\x00\x00\rIHDR":
        raise ValueError("Invalid PNG header")
    width, height = struct.unpack(">II", data[16:24])
    if not (100 <= width <= 10000 and 100 <= height <= 10000):
        raise ValueError("Invalid plot dimensions")
    if data[-12:] != b"\x00\x00\x00\x00IEND\xaeB`\x82":
        raise ValueError("Truncated PNG")
