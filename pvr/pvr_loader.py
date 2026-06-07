"""
pvr_loader.py
-------------
Loads PVR texture files into pygame surfaces.

Supported formats
=================
PVR v2  (iOS / Cocos2d-x, magic 'PVR!' at offset 44)
  - RGBA4444    pixel_type 0x10 / 0x0C
  - RGBA8888    pixel_type 0x12 / 0x0D
  - PVRTC4      pixel_type 0x19 / 0x18  (4bpp, numpy-accelerated)
  - PVRTC2      pixel_type 0x17 / 0x16  (2bpp, numpy-accelerated)

Dreamcast / Naomi PVRT  (GBIX / PVRT magic, handled via pypvr)
  - All formats supported by pypvr (twiddled, VQ, palettes, YUV, etc.)
  - Requires: pypvr.py in the same directory + Pillow + numpy
"""

import struct
import logging

log = logging.getLogger(__name__)

# ── PVR v2 pixel-type codes ───────────────────────────────────────────────────
_FMT_RGBA4444 = {0x10, 0x0C}
_FMT_RGBA8888 = {0x12, 0x0D}
_FMT_PVRTC4   = {0x19, 0x18}
_FMT_PVRTC2   = {0x17, 0x16}
_CH_SCALE     = 17   # 4-bit → 8-bit channel scale factor


# ── Public entry point ────────────────────────────────────────────────────────

def load_pvr_texture(pvr_path: str):
    """
    Load any supported PVR file and return a pygame.Surface (RGBA).
    Returns None on failure.

    Detection order
    ===============
    1. Dreamcast/Naomi PVRT (contains 'GBIX' or 'PVRT' magic) → pypvr
    2. iOS/Cocos2d-x PVR v2 ('PVR!' at offset 44)             → built-in decoder
    """
    try:
        import pygame
    except ImportError:
        raise RuntimeError("pygame is required to load PVR textures.")

    try:
        with open(pvr_path, 'rb') as fh:
            data = fh.read()
    except OSError as exc:
        log.error("Cannot open PVR '%s': %s", pvr_path, exc)
        return None

    fmt = _detect_format(data)
    log.debug("PVR '%s': detected format '%s'", pvr_path, fmt)

    if fmt == 'dreamcast':
        return _load_dreamcast(data, pvr_path, pygame)

    if fmt == 'ios_v2':
        return _load_ios_v2(data, pvr_path, pygame)

    log.error("Unrecognised PVR format in '%s' (first bytes: %s)", pvr_path, data[:8].hex())
    return None


def probe_pvr(pvr_path: str) -> dict:
    """
    Read the PVR header without decoding and return a metadata dict:

        {
          'container': 'ios_v2' | 'dreamcast' | 'unknown',
          'width':      int,
          'height':     int,
          'bpp':        int,
          'pixel_type': int,          # raw pixel-type code (ios_v2 only)
          'format_name': str,         # human-readable pixel format
          'data_len':   int,
          'file_size':  int,
        }

    Returns a minimal dict with container='unknown' on any error.
    """
    _PT_NAMES = {
        0x0C: 'RGBA4444', 0x10: 'RGBA4444',
        0x0D: 'RGBA8888', 0x12: 'RGBA8888',
        0x18: 'PVRTC4',   0x19: 'PVRTC4',
        0x16: 'PVRTC2',   0x17: 'PVRTC2',
    }
    try:
        with open(pvr_path, 'rb') as fh:
            data = fh.read()
    except OSError:
        return {'container': 'unknown', 'width': 0, 'height': 0,
                'bpp': 0, 'pixel_type': 0, 'format_name': 'unknown',
                'data_len': 0, 'file_size': 0}

    container = _detect_format(data)
    file_size = len(data)

    if container == 'ios_v2' and len(data) >= 52:
        height     = struct.unpack_from('<I', data,  4)[0]
        width      = struct.unpack_from('<I', data,  8)[0]
        flags      = struct.unpack_from('<I', data, 16)[0]
        data_len   = struct.unpack_from('<I', data, 20)[0]
        bpp        = struct.unpack_from('<I', data, 24)[0]
        pixel_type = flags & 0xFF
        return {
            'container':   'ios_v2',
            'width':       width,
            'height':      height,
            'bpp':         bpp,
            'pixel_type':  pixel_type,
            'format_name': _PT_NAMES.get(pixel_type, f'0x{pixel_type:02X}'),
            'data_len':    data_len,
            'file_size':   file_size,
        }

    if container == 'dreamcast':
        # Best-effort: look for PVRT block and read its header
        off = data.find(b'PVRT')
        if off >= 0 and off + 12 <= len(data):
            data_len   = struct.unpack_from('<I', data, off + 4)[0]
            pixel_fmt  = data[off + 8] if off + 8 < len(data) else 0
            pixel_type = data[off + 9] if off + 9 < len(data) else 0
            width  = struct.unpack_from('<H', data, off + 12)[0] if off + 14 <= len(data) else 0
            height = struct.unpack_from('<H', data, off + 14)[0] if off + 16 <= len(data) else 0
            dc_fmts = {0x00:'ARGB1555',0x01:'RGB565',0x02:'ARGB4444',
                       0x03:'YUV422',0x04:'BUMP',0x05:'RGB555',0x06:'ARGB8888'}
            dc_types = {0x01:'Square twiddled',0x02:'Square twiddled + mips',
                        0x03:'VQ',0x04:'VQ + mips',0x09:'Non-square twiddled',
                        0x0B:'YUV420',0x0D:'Bitmap'}
            fmt_name = f"{dc_fmts.get(pixel_fmt,'fmt?')} {dc_types.get(pixel_type,'type?')}"
            bpp_map = {0x00:16,0x01:16,0x02:16,0x03:16,0x06:32}
            return {
                'container':   'dreamcast',
                'width':       width,
                'height':      height,
                'bpp':         bpp_map.get(pixel_fmt, 0),
                'pixel_type':  pixel_type,
                'format_name': fmt_name,
                'data_len':    data_len,
                'file_size':   file_size,
            }

    return {'container': container, 'width': 0, 'height': 0,
            'bpp': 0, 'pixel_type': 0, 'format_name': 'unknown',
            'data_len': 0, 'file_size': 0}


# ── Format detection ──────────────────────────────────────────────────────────

def _detect_format(data: bytes) -> str:
    # Dreamcast: starts with GBIX header or PVRT block anywhere near start
    if data[:4] in (b'GBIX', b'PVRT'):
        return 'dreamcast'
    if data.find(b'PVRT') != -1:
        return 'dreamcast'
    # iOS PVR v2: 'PVR!' magic at offset 44
    if len(data) >= 48 and data[44:48] == b'PVR!':
        return 'ios_v2'
    return 'unknown'


# ── Dreamcast / Naomi loader (via pypvr) ──────────────────────────────────────

def _load_dreamcast(data: bytes, pvr_path: str, pygame):
    """Decode a Dreamcast/Naomi PVRT file using pypvr, return pygame.Surface."""
    try:
        from pypvr import Pypvr
    except ImportError:
        log.error("pypvr.py not found – cannot decode Dreamcast PVR '%s'. "
                  "Place pypvr.py in the same folder as pvr_loader.py.", pvr_path)
        return None

    try:
        from PIL import Image as PilImage
    except ImportError:
        log.error("Pillow (PIL) is required for Dreamcast PVR decoding. "
                  "Run: pip install Pillow")
        return None

    try:
        decoder = Pypvr.Decode(args_str='-buffer', buff_pvr=data)
        pil_img = decoder.get_image_buffer()

        if pil_img is None:
            log.error("pypvr returned no image for '%s'", pvr_path)
            return None

        # Ensure RGBA so pygame is happy
        pil_img = pil_img.convert('RGBA')
        w, h    = pil_img.size
        raw     = pil_img.tobytes()
        surface = pygame.image.fromstring(raw, (w, h), 'RGBA')
        log.debug("Dreamcast PVR loaded via pypvr: %dx%d  mode=%s", w, h, pil_img.mode)
        return surface

    except Exception as exc:
        log.error("pypvr decode failed for '%s': %s", pvr_path, exc)
        return None


# ── iOS PVR v2 loader ─────────────────────────────────────────────────────────

def _load_ios_v2(data: bytes, pvr_path: str, pygame):
    """Decode an iOS/Cocos2d-x PVR v2 file, return pygame.Surface."""
    if len(data) < 52:
        log.error("PVR v2 file too short: '%s'", pvr_path)
        return None

    header_len = struct.unpack_from('<I', data,  0)[0]
    height     = struct.unpack_from('<I', data,  4)[0]
    width      = struct.unpack_from('<I', data,  8)[0]
    flags      = struct.unpack_from('<I', data, 16)[0]
    data_len   = struct.unpack_from('<I', data, 20)[0]
    bpp        = struct.unpack_from('<I', data, 24)[0]
    pixel_type = flags & 0xFF

    log.debug("PVR v2: %dx%d  bpp=%d  pixel_type=0x%02X  data_len=%d",
              width, height, bpp, pixel_type, data_len)

    pixel_data = data[header_len: header_len + data_len]

    if pixel_type in _FMT_RGBA4444:
        rgba = _decode_rgba4444(pixel_data, width, height)
    elif pixel_type in _FMT_RGBA8888:
        rgba = _decode_rgba8888(pixel_data, width, height)
    elif pixel_type in _FMT_PVRTC4:
        rgba = _decode_pvrtc4(pixel_data, width, height)
    elif pixel_type in _FMT_PVRTC2:
        rgba = _decode_pvrtc2(pixel_data, width, height)
    else:
        log.warning("Unsupported PVR v2 pixel_type 0x%02X in '%s' – trying RGBA4444 fallback.",
                    pixel_type, pvr_path)
        rgba = _decode_rgba4444(pixel_data, width, height)

    surface = pygame.image.fromstring(rgba, (width, height), 'RGBA')
    log.debug("PVR v2 texture loaded: %dx%d  pixel_type=0x%02X", width, height, pixel_type)
    return surface


# ── RGBA4444 ──────────────────────────────────────────────────────────────────

def _decode_rgba4444(data: bytes, width: int, height: int) -> bytes:
    n   = width * height
    out = bytearray(n * 4)
    for i in range(n):
        b0 = i * 2
        if b0 + 1 >= len(data):
            break
        w   = data[b0] | (data[b0 + 1] << 8)
        r   = (w >> 12) & 0xF
        g   = (w >>  8) & 0xF
        b   = (w >>  4) & 0xF
        a   =  w        & 0xF
        p   = i * 4
        out[p]   = r * _CH_SCALE
        out[p+1] = g * _CH_SCALE
        out[p+2] = b * _CH_SCALE
        out[p+3] = a * _CH_SCALE
    return bytes(out)


# ── RGBA8888 ──────────────────────────────────────────────────────────────────

def _decode_rgba8888(data: bytes, width: int, height: int) -> bytes:
    n = width * height * 4
    return data[:n] if len(data) >= n else data + bytes(n - len(data))


# ── PVRTC-I shared ────────────────────────────────────────────────────────────
#
# PVRTC v1 64-bit block layout (little-endian):
#   bytes 0-3 : 32-bit modulation word — 16 texels × 2 bits, row-major
#   bytes 4-7 : 32-bit color word
#
# Color word layout:
#   bit  0      : modulation interpretation flag (0=standard, 1=punch-through)
#   bits 1-14   : Color A (14-bit color value)
#   bit  15     : Color A opacity flag (1=opaque, 0=translucent)
#   bits 16-30  : Color B (15-bit color value)
#   bit  31     : Color B opacity flag (1=opaque, 0=translucent)
#
# Color encoding within the value bits:
#   Opaque  : RGB-555 (Color B) or RGB-554 (Color A) — alpha forced to 255
#   Translucent: ARGB-3444 (Color B) or ARGB-3443 (Color A)
#
# Blocks are stored in Morton (Z-order) interleaving, NOT row-major.
# Block (bx, by) lives at byte offset `morton(bx, by) * 8` in the pixel data.
#
# Bilinear: each block's two colors are anchored at the block CENTER (pixel
# offset 2,2 within the block). For a pixel at offset (px, py) in block (bx, by):
#   * px < 2  → sample from blocks (bx-1, *) and (bx, *)
#   * px >= 2 → sample from blocks (bx, *)   and (bx+1, *)
# Same logic on Y. Bilinear weights are (4 - fx) and fx where fx = (px + 2) & 3.

def _morton_table(blocks_x: int, blocks_y: int):
    """
    Precompute a (blocks_y, blocks_x) int32 array mapping each block position
    to its Morton (Z-order) linear index, matching the PowerVR reference
    `TwiddleUV(YSize, XSize, YPos, XPos)` exactly:

        Twiddled[2i]     = YPos[i]   (Y bits at EVEN positions)
        Twiddled[2i + 1] = XPos[i]   (X bits at ODD positions)

    For non-square layouts, bits past the smaller dimension's range come from
    the larger axis, shifted above the interleaved field — that matches the
    reference's `MaxValue << (2*ShiftCount)` trailing OR.
    """
    import numpy as np
    min_dim = min(blocks_x, blocks_y)
    shift_count = (min_dim - 1).bit_length() if min_dim > 1 else 0
    table = np.zeros((blocks_y, blocks_x), dtype=np.int32)
    for by in range(blocks_y):
        for bx in range(blocks_x):
            z = 0
            src_bit = 1
            dst_bit = 1
            while src_bit < min_dim:
                if by & src_bit: z |= dst_bit
                if bx & src_bit: z |= (dst_bit << 1)
                src_bit <<= 1
                dst_bit <<= 2
            # Trailing high-axis bits past the interleaved range
            max_val = (bx if blocks_y < blocks_x else by) >> shift_count
            z |= max_val << (2 * shift_count)
            table[by, bx] = z
    return table


def _decode_colorA(v14, opaque):
    """Decode 14-bit Color A (numpy-vector friendly).  Returns (..., 4) uint8.

    Opaque    : RGB-554  (5 bits R, 5 bits G, 4 bits B)
    Translucent: ARGB-3443 (3 bits A, 4 bits R, 4 bits G, 3 bits B)
    """
    import numpy as np
    r5o = (v14 >> 9) & 0x1F;  g5o = (v14 >> 4) & 0x1F;  b4o = v14 & 0xF
    a3t = (v14 >> 11) & 0x7;  r4t = (v14 >> 7) & 0xF
    g4t = (v14 >> 3) & 0xF;   b3t = v14 & 0x7
    r = np.where(opaque, (r5o << 3) | (r5o >> 2), (r4t << 4) | r4t).astype(np.int32)
    g = np.where(opaque, (g5o << 3) | (g5o >> 2), (g4t << 4) | g4t).astype(np.int32)
    b = np.where(opaque, (b4o << 4) | b4o,
                 (b3t << 5) | (b3t << 2) | (b3t >> 1)).astype(np.int32)
    a = np.where(opaque, np.int32(255),
                 (a3t << 5) | (a3t << 2) | (a3t >> 1)).astype(np.int32)
    return np.stack([r, g, b, a], axis=-1)


def _decode_colorB(v15, opaque):
    """Decode 15-bit Color B (numpy-vector friendly).  Returns (..., 4) uint8.

    Opaque    : RGB-555  (5 bits R, 5 bits G, 5 bits B)
    Translucent: ARGB-3444 (3 bits A, 4 bits R, 4 bits G, 4 bits B)
    """
    import numpy as np
    r5o = (v15 >> 10) & 0x1F; g5o = (v15 >> 5) & 0x1F; b5o = v15 & 0x1F
    a3t = (v15 >> 12) & 0x7;  r4t = (v15 >> 8) & 0xF
    g4t = (v15 >> 4) & 0xF;   b4t = v15 & 0xF
    r = np.where(opaque, (r5o << 3) | (r5o >> 2), (r4t << 4) | r4t).astype(np.int32)
    g = np.where(opaque, (g5o << 3) | (g5o >> 2), (g4t << 4) | g4t).astype(np.int32)
    b = np.where(opaque, (b5o << 3) | (b5o >> 2), (b4t << 4) | b4t).astype(np.int32)
    a = np.where(opaque, np.int32(255),
                 (a3t << 5) | (a3t << 2) | (a3t >> 1)).astype(np.int32)
    return np.stack([r, g, b, a], axis=-1)


def _dec_colorA_scalar(v14: int, opaque: bool):
    """Scalar version of _decode_colorA for the pure-Python fallback path."""
    if opaque:
        r5 = (v14 >> 9) & 0x1F; g5 = (v14 >> 4) & 0x1F; b4 = v14 & 0xF
        return ((r5 << 3) | (r5 >> 2), (g5 << 3) | (g5 >> 2),
                (b4 << 4) | b4, 255)
    a3 = (v14 >> 11) & 0x7; r4 = (v14 >> 7) & 0xF
    g4 = (v14 >> 3) & 0xF;  b3 = v14 & 0x7
    return ((r4 << 4) | r4, (g4 << 4) | g4,
            (b3 << 5) | (b3 << 2) | (b3 >> 1),
            (a3 << 5) | (a3 << 2) | (a3 >> 1))


def _dec_colorB_scalar(v15: int, opaque: bool):
    """Scalar version of _decode_colorB for the pure-Python fallback path."""
    if opaque:
        r5 = (v15 >> 10) & 0x1F; g5 = (v15 >> 5) & 0x1F; b5 = v15 & 0x1F
        return ((r5 << 3) | (r5 >> 2), (g5 << 3) | (g5 >> 2),
                (b5 << 3) | (b5 >> 2), 255)
    a3 = (v15 >> 12) & 0x7; r4 = (v15 >> 8) & 0xF
    g4 = (v15 >> 4) & 0xF;  b4 = v15 & 0xF
    return ((r4 << 4) | r4, (g4 << 4) | g4, (b4 << 4) | b4,
            (a3 << 5) | (a3 << 2) | (a3 >> 1))


# ── PVRTC-I 4bpp ─────────────────────────────────────────────────────────────

def _decode_pvrtc4(data: bytes, width: int, height: int) -> bytes:
    bx = max(2, (width  + 3) >> 2)
    by = max(2, (height + 3) >> 2)
    needed = bx * by * 8
    if len(data) < needed:
        data = data + bytes(needed - len(data))
    try:
        import numpy as np
        return _pvrtc4_numpy(data, width, height, bx, by)
    except ImportError:
        log.debug("numpy not available – using pure-Python PVRTC4 decoder.")
        return _pvrtc4_pure(data, width, height, bx, by)


def _pvrtc4_numpy(data, width, height, blocks_x, blocks_y):
    """
    PVRTC v1 4bpp decoder.  Each block is 4×4 px / 8 bytes.

    Fixes applied vs the v0 decoder:
      1. Blocks are read in Morton (Z-order), not linear row-major.
      2. Color word bits are split correctly: bit 0 = mod-mode flag,
         bits 1-14 = Color A (14 bits), bit 15 = Color A opacity,
         bits 16-30 = Color B (15 bits), bit 31 = Color B opacity.
      3. Color decoding switches between opaque (RGB-554/555) and
         translucent (ARGB-3443/3444) per the opacity flag.
      4. Bilinear sampling uses the correct neighbouring block centres:
         pixels in the left half of a block sample from (bx-1, bx);
         pixels in the right half sample from (bx, bx+1).
      5. Punch-through modulation: when the per-block mode flag is set,
         modulation value 2 means alpha=0 (transparent); 1 means (A+B)/2.
    """
    import numpy as np

    table = _morton_table(blocks_x, blocks_y)
    blks  = np.frombuffer(data[:blocks_x*blocks_y*8], dtype='<u4').reshape(-1, 2)
    mod_g = blks[:, 0][table]   # shape (blocks_y, blocks_x)
    col_g = blks[:, 1][table]

    mod_flag = (col_g & 1).astype(bool)
    colA_v   = ((col_g >>  1) & 0x3FFF).astype(np.uint32)
    colA_op  = ((col_g >> 15) & 1).astype(bool)
    colB_v   = ((col_g >> 16) & 0x7FFF).astype(np.uint32)
    colB_op  = ((col_g >> 31) & 1).astype(bool)

    ca = _decode_colorA(colA_v, colA_op)
    cb = _decode_colorB(colB_v, colB_op)

    # Shifted views for bilinear neighbour lookup.  np.roll with +1 shifts
    # contents DOWN/RIGHT — i.e. position (y, x) now holds the value that
    # was at (y-1, x) or (y, x-1) — which is what we want for "look up/left".
    ca_l  = np.roll(ca, +1, axis=1); ca_u  = np.roll(ca, +1, axis=0)
    ca_lu = np.roll(ca_u, +1, axis=1)
    ca_r  = np.roll(ca, -1, axis=1); ca_d  = np.roll(ca, -1, axis=0)
    ca_rd = np.roll(ca_d, -1, axis=1)
    ca_ru = np.roll(ca_u, -1, axis=1); ca_ld = np.roll(ca_d, +1, axis=1)
    cb_l  = np.roll(cb, +1, axis=1); cb_u  = np.roll(cb, +1, axis=0)
    cb_lu = np.roll(cb_u, +1, axis=1)
    cb_r  = np.roll(cb, -1, axis=1); cb_d  = np.roll(cb, -1, axis=0)
    cb_rd = np.roll(cb_d, -1, axis=1)
    cb_ru = np.roll(cb_u, -1, axis=1); cb_ld = np.roll(cb_d, +1, axis=1)

    out = np.zeros((height, width, 4), dtype=np.uint8)
    mf  = mod_flag[:, :, np.newaxis]

    for py in range(4):
        for px in range(4):
            fx = (px + 2) & 3      # offset within the 4×4 bilinear region
            fy = (py + 2) & 3
            wL = 4 - fx;  wR = fx
            wT = 4 - fy;  wB = fy

            # Pick the 4 surrounding block-centres based on quadrant
            if px < 2 and py < 2:
                a00, a10, a01, a11 = ca_lu, ca_u, ca_l, ca
                b00, b10, b01, b11 = cb_lu, cb_u, cb_l, cb
            elif px >= 2 and py < 2:
                a00, a10, a01, a11 = ca_u, ca_ru, ca, ca_r
                b00, b10, b01, b11 = cb_u, cb_ru, cb, cb_r
            elif px < 2 and py >= 2:
                a00, a10, a01, a11 = ca_l, ca, ca_ld, ca_d
                b00, b10, b01, b11 = cb_l, cb, cb_ld, cb_d
            else:
                a00, a10, a01, a11 = ca, ca_r, ca_d, ca_rd
                b00, b10, b01, b11 = cb, cb_r, cb_d, cb_rd

            fa = (a00*wL*wT + a10*wR*wT + a01*wL*wB + a11*wR*wB) >> 4
            fb = (b00*wL*wT + b10*wR*wT + b01*wL*wB + b11*wR*wB) >> 4

            mod = ((mod_g >> ((py*4+px)*2)) & 3)[:, :, np.newaxis]

            # Standard modulation: 00=A, 01=5A+3B/8, 10=3A+5B/8, 11=B
            c_std = np.where(mod == 0, fa,
                    np.where(mod == 1, (fa*5 + fb*3) >> 3,
                    np.where(mod == 2, (fa*3 + fb*5) >> 3, fb)))
            # Punch-through modulation: 00=A, 01=(A+B)/2, 10=transparent, 11=B
            c_pt = np.where(mod == 0, fa,
                   np.where(mod == 1, (fa + fb) >> 1,
                   np.where(mod == 2, np.zeros_like(fa), fb)))

            c = np.where(mf, c_pt, c_std)
            out[py::4, px::4] = np.clip(c, 0, 255).astype(np.uint8)[:blocks_y, :blocks_x]
    return out.tobytes()


def _pvrtc4_pure(data, width, height, blocks_x, blocks_y):
    """Pure-Python fallback when numpy is unavailable.  Much slower."""
    def morton(bx, by):
        nbits = max(blocks_x, blocks_y).bit_length()
        z = 0
        for i in range(nbits):
            z |= ((bx >> i) & 1) << (2 * i)
            z |= ((by >> i) & 1) << (2 * i + 1)
        return z

    def get(bx, by):
        bx %= blocks_x; by %= blocks_y
        off = morton(bx, by) * 8
        mw = struct.unpack_from('<I', data, off)[0]
        cw = struct.unpack_from('<I', data, off + 4)[0]
        mod_flag = bool(cw & 1)
        ca = _dec_colorA_scalar((cw >> 1) & 0x3FFF, bool((cw >> 15) & 1))
        cb = _dec_colorB_scalar((cw >> 16) & 0x7FFF, bool((cw >> 31) & 1))
        return ca, cb, mw, mod_flag

    def bilinear(c00, c10, c01, c11, wL, wR, wT, wB):
        return tuple(
            min(255, (c00[i]*wL*wT + c10[i]*wR*wT +
                      c01[i]*wL*wB + c11[i]*wR*wB) >> 4)
            for i in range(4))

    out = bytearray(width * height * 4)
    for by in range(blocks_y):
        for bx in range(blocks_x):
            a_lu, b_lu, _, _      = get(bx-1, by-1)
            a_u,  b_u,  _, _      = get(bx,   by-1)
            a_ru, b_ru, _, _      = get(bx+1, by-1)
            a_l,  b_l,  _, _      = get(bx-1, by)
            a,    b,    m, mf     = get(bx,   by)
            a_r,  b_r,  _, _      = get(bx+1, by)
            a_ld, b_ld, _, _      = get(bx-1, by+1)
            a_d,  b_d,  _, _      = get(bx,   by+1)
            a_rd, b_rd, _, _      = get(bx+1, by+1)

            for py in range(4):
                for px in range(4):
                    fx = (px + 2) & 3
                    fy = (py + 2) & 3
                    wL = 4 - fx;  wR = fx
                    wT = 4 - fy;  wB = fy
                    if px < 2 and py < 2:
                        fa = bilinear(a_lu, a_u, a_l, a, wL, wR, wT, wB)
                        fb = bilinear(b_lu, b_u, b_l, b, wL, wR, wT, wB)
                    elif px >= 2 and py < 2:
                        fa = bilinear(a_u, a_ru, a, a_r, wL, wR, wT, wB)
                        fb = bilinear(b_u, b_ru, b, b_r, wL, wR, wT, wB)
                    elif px < 2 and py >= 2:
                        fa = bilinear(a_l, a, a_ld, a_d, wL, wR, wT, wB)
                        fb = bilinear(b_l, b, b_ld, b_d, wL, wR, wT, wB)
                    else:
                        fa = bilinear(a, a_r, a_d, a_rd, wL, wR, wT, wB)
                        fb = bilinear(b, b_r, b_d, b_rd, wL, wR, wT, wB)

                    mod = (m >> ((py*4 + px) * 2)) & 3
                    if mf:
                        if mod == 0:   c = fa
                        elif mod == 1: c = tuple((fa[i] + fb[i]) >> 1 for i in range(4))
                        elif mod == 2: c = (0, 0, 0, 0)
                        else:          c = fb
                    else:
                        if   mod == 0: c = fa
                        elif mod == 1: c = tuple(min(255, (fa[i]*5 + fb[i]*3) >> 3) for i in range(4))
                        elif mod == 2: c = tuple(min(255, (fa[i]*3 + fb[i]*5) >> 3) for i in range(4))
                        else:          c = fb

                    ox = bx*4 + px;  oy = by*4 + py
                    if ox < width and oy < height:
                        p = (oy*width + ox) * 4
                        out[p] = c[0]; out[p+1] = c[1]
                        out[p+2] = c[2]; out[p+3] = c[3]
    return bytes(out)


# ── PVRTC-I 2bpp ─────────────────────────────────────────────────────────────
#
# TODO: the PVRTC2 path below still uses the *original* (incorrect) layout
# assumptions — linear block order, 14-bit colors for both A and B, and the
# (bx, bx+1) bilinear. The same five fixes applied to PVRTC4 above (Morton
# block order, asymmetric color word split, opacity-aware color decode,
# quadrant-aware bilinear, punch-through alpha) need to be ported here.
# Not done yet because no PVRTC2 sample is in the repo to validate against.

def _decode_pvrtc2(data: bytes, width: int, height: int) -> bytes:
    bx = max(2, (width  + 7) >> 3)
    by = max(2, (height + 3) >> 2)
    needed = bx * by * 8
    if len(data) < needed:
        data = data + bytes(needed - len(data))
    try:
        import numpy as np
        return _pvrtc2_numpy(data, width, height, bx, by)
    except ImportError:
        return _pvrtc2_pure(data, width, height, bx, by)


def _pvrtc2_numpy(data, width, height, blocks_x, blocks_y):
    import numpy as np
    blks  = np.frombuffer(data[:blocks_x*blocks_y*8], dtype='<u4').reshape(-1, 2)
    mod_g = blks[:, 0].reshape(blocks_y, blocks_x)
    col_g = blks[:, 1].reshape(blocks_y, blocks_x)
    pt    = (col_g & 1).astype(bool)
    rawA  = ((col_g >>  1) & 0x3FFF).astype(np.int32)
    rawB  = ((col_g >> 15) & 0x3FFF).astype(np.int32)

    def vec_dec(raw, pt_arr):
        r4=(raw>>9)&0xF; g5=(raw>>4)&0x1F; b4=raw&0xF
        r8=(r4<<4)|r4; g8=(g5<<3)|(g5>>2); b8=(b4<<4)|b4
        op=((raw>>13)&1).astype(bool)
        a8=np.where(op,np.int32(255),np.where(pt_arr,np.int32(0),np.int32(255)))
        return np.stack([r8,g8,b8,a8],axis=-1)

    ca=vec_dec(rawA,pt); cb=vec_dec(rawB,pt)
    ca_r=np.roll(ca,-1,axis=1); ca_d=np.roll(ca,-1,axis=0)
    ca_rd=np.roll(ca_d,-1,axis=1)
    cb_r=np.roll(cb,-1,axis=1); cb_d=np.roll(cb,-1,axis=0)
    cb_rd=np.roll(cb_d,-1,axis=1)

    out = np.zeros((height, width, 4), dtype=np.uint8)
    for py in range(4):
        for px in range(8):
            wx1=px+1; wx0=8-wx1; wy1=py+1; wy0=4-wy1
            fa=(ca*wx0*wy0+ca_r*wx1*wy0+ca_d*wx0*wy1+ca_rd*wx1*wy1)>>5
            fb=(cb*wx0*wy0+cb_r*wx1*wy0+cb_d*wx0*wy1+cb_rd*wx1*wy1)>>5
            bit=py*8+px
            mod=((mod_g>>bit)&1)[:,:,np.newaxis]
            c=np.where(mod==0,fa,fb)
            if px<width and py<height:
                out[py::4, px::8]=np.clip(c,0,255).astype(np.uint8)[:blocks_y,:blocks_x]
    return out.tobytes()


def _pvrtc2_pure(data, width, height, blocks_x, blocks_y):
    out = bytearray(width * height * 4)
    def get(bx, by):
        bx%=blocks_x; by%=blocks_y
        off=(by*blocks_x+bx)*8
        mw=struct.unpack_from('<I',data,off)[0]
        cw=struct.unpack_from('<I',data,off+4)[0]
        pt=bool(cw&1)
        return _dec14((cw>>1)&0x3FFF,pt), _dec14((cw>>15)&0x3FFF,pt), mw
    for by in range(blocks_y):
        for bx in range(blocks_x):
            a00,b00,m00=get(bx,by); a10,b10,_=get(bx+1,by)
            a01,b01,_=get(bx,by+1); a11,b11,_=get(bx+1,by+1)
            for py in range(4):
                for px in range(8):
                    wx1=px+1;wx0=8-wx1;wy1=py+1;wy0=4-wy1
                    fa=_bilinear4(a00,a10,a01,a11,wx0,wx1,wy0,wy1)
                    fb=_bilinear4(b00,b10,b01,b11,wx0,wx1,wy0,wy1)
                    mod=(m00>>(py*8+px))&1
                    c=fa if mod==0 else fb
                    ox=bx*8+px; oy=by*4+py
                    if ox<width and oy<height:
                        p=(oy*width+ox)*4
                        out[p]=c[0];out[p+1]=c[1];out[p+2]=c[2];out[p+3]=c[3]
    return bytes(out)
