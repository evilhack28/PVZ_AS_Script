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
    log.info("PVR '%s': detected format '%s'", pvr_path, fmt)

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
        log.info("Dreamcast PVR loaded via pypvr: %dx%d  mode=%s", w, h, pil_img.mode)
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

    log.info("PVR v2: %dx%d  bpp=%d  pixel_type=0x%02X  data_len=%d",
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
    log.info("PVR v2 texture loaded: %dx%d  pixel_type=0x%02X", width, height, pixel_type)
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

def _dec14(raw14: int, punch_through: bool):
    """Decode a 14-bit PVRTC colour endpoint → (R, G, B, A) 0-255."""
    opaque = bool((raw14 >> 13) & 1)
    r4 = (raw14 >> 9) & 0xF
    g5 = (raw14 >> 4) & 0x1F
    b4 =  raw14       & 0xF
    r8 = (r4 << 4) | r4
    g8 = (g5 << 3) | (g5 >> 2)
    b8 = (b4 << 4) | b4
    a8 = 255 if (opaque or not punch_through) else 0
    return r8, g8, b8, a8


def _bilinear4(c00, c10, c01, c11, wx0, wx1, wy0, wy1):
    return tuple(
        min(255, (c00[i]*wx0*wy0 + c10[i]*wx1*wy0 +
                  c01[i]*wx0*wy1 + c11[i]*wx1*wy1) >> 4)
        for i in range(4)
    )


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
    import numpy as np
    blks  = np.frombuffer(data[:blocks_x*blocks_y*8], dtype='<u4').reshape(-1, 2)
    mod_g = blks[:, 0].reshape(blocks_y, blocks_x)
    col_g = blks[:, 1].reshape(blocks_y, blocks_x)
    pt    = (col_g & 1).astype(bool)
    rawA  = ((col_g >>  1) & 0x3FFF).astype(np.int32)
    rawB  = ((col_g >> 15) & 0x3FFF).astype(np.int32)

    def vec_dec(raw, pt_arr):
        r4 = (raw >> 9) & 0xF
        g5 = (raw >> 4) & 0x1F
        b4 =  raw       & 0xF
        r8 = (r4 << 4) | r4
        g8 = (g5 << 3) | (g5 >> 2)
        b8 = (b4 << 4) | b4
        op = ((raw >> 13) & 1).astype(bool)
        a8 = np.where(op, np.int32(255), np.where(pt_arr, np.int32(0), np.int32(255)))
        return np.stack([r8, g8, b8, a8], axis=-1)

    ca = vec_dec(rawA, pt);   cb = vec_dec(rawB, pt)
    ca_r = np.roll(ca,-1,axis=1); ca_d = np.roll(ca,-1,axis=0)
    ca_rd = np.roll(ca_d,-1,axis=1)
    cb_r = np.roll(cb,-1,axis=1); cb_d = np.roll(cb,-1,axis=0)
    cb_rd = np.roll(cb_d,-1,axis=1)

    out = np.zeros((height, width, 4), dtype=np.uint8)
    for py in range(4):
        for px in range(4):
            wx1=px+1; wx0=4-wx1; wy1=py+1; wy0=4-wy1
            fa = (ca*wx0*wy0 + ca_r*wx1*wy0 + ca_d*wx0*wy1 + ca_rd*wx1*wy1) >> 4
            fb = (cb*wx0*wy0 + cb_r*wx1*wy0 + cb_d*wx0*wy1 + cb_rd*wx1*wy1) >> 4
            mod = ((mod_g >> ((py*4+px)*2)) & 3)[:,:,np.newaxis]
            c = np.where(mod==0,fa, np.where(mod==1,(fa*5+fb*3)>>3,
                         np.where(mod==2,(fa*3+fb*5)>>3,fb)))
            out[py::4, px::4] = np.clip(c,0,255).astype(np.uint8)[:blocks_y,:blocks_x]
    return out.tobytes()


def _pvrtc4_pure(data, width, height, blocks_x, blocks_y):
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
                for px in range(4):
                    wx1=px+1;wx0=4-wx1;wy1=py+1;wy0=4-wy1
                    fa=_bilinear4(a00,a10,a01,a11,wx0,wx1,wy0,wy1)
                    fb=_bilinear4(b00,b10,b01,b11,wx0,wx1,wy0,wy1)
                    mod=(m00>>((py*4+px)*2))&3
                    c=fa if mod==0 else fb if mod==3 else \
                      tuple(min(255,(fa[i]*5+fb[i]*3)>>3) for i in range(4)) if mod==1 else \
                      tuple(min(255,(fa[i]*3+fb[i]*5)>>3) for i in range(4))
                    ox=bx*4+px; oy=by*4+py
                    if ox<width and oy<height:
                        p=(oy*width+ox)*4
                        out[p]=c[0];out[p+1]=c[1];out[p+2]=c[2];out[p+3]=c[3]
    return bytes(out)


# ── PVRTC-I 2bpp ─────────────────────────────────────────────────────────────

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
