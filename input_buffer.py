"""
input_buffer.py
---------------
Binary stream reader with bounds-checked primitives and MinBin float decoding.
"""

import struct
import logging

log = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
_ALLOWED_STRING_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789_-/ ."
)


class BufferError(Exception):
    """Raised when a read would exceed the buffer bounds."""


class InputBuffer:
    """
    Wraps a bytes object and provides sequential, bounds-checked reads.

    Attributes
    ----------
    data   : bytes  – raw binary payload
    offset : int    – current read position
    """

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.offset = 0
        self.length = len(data)

    # ── Low-level primitives ──────────────────────────────────────────────────

    def read_byte(self) -> int:
        if self.offset >= self.length:
            raise BufferError("End of buffer reading byte")
        val = self.data[self.offset]
        self.offset += 1
        return val

    def read_short(self) -> int:
        """Read a signed little-endian 16-bit integer."""
        if self.offset + 2 > self.length:
            raise BufferError("End of buffer reading short")
        val = struct.unpack_from('<h', self.data, self.offset)[0]
        self.offset += 2
        return val

    def read_int(self) -> int:
        """Read a signed little-endian 32-bit integer."""
        if self.offset + 4 > self.length:
            raise BufferError("End of buffer reading int")
        val = struct.unpack_from('<i', self.data, self.offset)[0]
        self.offset += 4
        return val

    def read_bytes(self, length: int) -> bytes:
        if self.offset + length > self.length:
            raise BufferError(f"End of buffer reading {length} bytes")
        val = self.data[self.offset: self.offset + length]
        self.offset += length
        return val

    # ── Higher-level reads ────────────────────────────────────────────────────

    def read_pascal_string(self) -> str:
        """Read a length-prefixed UTF-8 string (1-byte length prefix)."""
        length = self.read_byte()
        if length == 0:
            return ""
        raw = self.read_bytes(length)
        try:
            return raw.decode('utf-8')
        except UnicodeDecodeError:
            return f"<BINARY:{raw.hex()}>"

    def read_float_min(self, divisor: float) -> float:
        """
        Read a MinBin-encoded float.

        Tag meanings
        ============
        0  → 0.0
        1  → int8  / divisor
        2  → int16 / divisor
        3  → int32 / divisor
        4  → raw IEEE-754 float32
        """
        tag = self.read_byte()
        if tag == 0:
            return 0.0
        elif tag == 1:
            val = struct.unpack_from('<b', self.data, self.offset)[0]
            self.offset += 1
            return val / divisor
        elif tag == 2:
            val = struct.unpack_from('<h', self.data, self.offset)[0]
            self.offset += 2
            return val / divisor
        elif tag == 3:
            return float(self.read_int()) / divisor
        elif tag == 4:
            return float(self.read_int()) / divisor
        else:
            log.warning("Unknown FloatMin tag %d at offset %d – defaulting to 0.0",
                        tag, self.offset - 1)
            return 0.0

    # ── Navigation ────────────────────────────────────────────────────────────

    def peek_bytes(self, length: int) -> bytes:
        return self.data[self.offset: self.offset + length]

    def tell(self) -> int:
        return self.offset

    def seek(self, new_offset: int) -> None:
        self.offset = max(0, min(new_offset, self.length))

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def is_printable_ascii(raw: bytes) -> bool:
        """Return True if *raw* decodes to printable ASCII (file-name safe)."""
        try:
            text = raw.decode('utf-8', errors='ignore')
        except Exception:
            return False
        return bool(text) and all(ch in _ALLOWED_STRING_CHARS for ch in text)
