"""Binary stream reader with bounds-checked primitives and MinBin float decoding."""

import os
import struct
import logging

log = logging.getLogger(__name__)

DEFAULT_FRAME_RATE = 30

# Tag 1 of a MinBin float is ONE byte.
TAG1_SIGNED = os.environ.get('PVZ_GAME_QUIRKS', '') not in ('1', 'true', 'yes')


def set_game_quirks(enabled: bool) -> None:
    """True -> read MinBin tag 1 like the game (unsigned, buggy)"""
    global TAG1_SIGNED
    TAG1_SIGNED = not enabled


class BufferError(Exception):
    """Raised when a read would exceed the buffer bounds."""


class InputBuffer:
    """Wraps a bytes object and provides sequential, bounds-checked reads."""

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

    def read_float(self) -> float:
        """Read a raw little-endian float32 (InputBuffer::ReadFloat, RawBin)."""
        if self.offset + 4 > self.length:
            raise BufferError("End of buffer reading float")
        val = struct.unpack_from('<f', self.data, self.offset)[0]
        self.offset += 4
        return val

    def read_float_min(self, divisor: float) -> float:
        """MinBin float — exact port of the game's InputBuffer::ReadFloatMin (libcocos2dcpp.so 1.0.105, 0x00ac828a)."""
        tag = self.read_byte()
        if tag == 1:
            b = self.read_byte()
            if TAG1_SIGNED and b >= 128:
                b -= 256
            return b / divisor
        if tag == 2:
            return self.read_short() / divisor
        if tag == 4:
            return self.read_int() / divisor
        if tag not in (0,):
            log.debug("FloatMin tag %d at offset %d -> 0.0 (game behaviour)",
                      tag, self.offset - 1)
        return 0.0

    # ── Navigation ────────────────────────────────────────────────────────────

    def tell(self) -> int:
        return self.offset
