"""
rawbin_parser.py
----------------
Parses the no-magic raw-float32 RawBin animation format used by some
Cocos2d-x games (no 'FBIN' magic bytes at the start).

Field layout per image record (8 × raw float32):
    offset_x, offset_y, width, height, tex_x, tex_y, origin_x, origin_y
"""

import struct
import logging

log = logging.getLogger(__name__)

MAX_IMAGES      = 1024
MAX_MOVIE_CLIPS = 2000
MAX_ACTIONS     = 5000
MAX_FRAMES      = 8000
MAX_ELEMENTS    = 4096
_ELEM_SIZE      = 38

# Populated by the successful parse path so callers (e.g. scripts/main.py) can
# show the detected variant in their summary.  Reset on each parse call.
LAST_INFO: dict = {}


def parse_rawbin(bin_path: str):
    """Parse a RawBin file from disk. Returns (images, mcs, actions, True)."""
    try:
        with open(bin_path, 'rb') as fh:
            data = fh.read()
    except OSError as exc:
        log.error("Cannot open RawBin '%s': %s", bin_path, exc)
        return None, None, None, True
    try:
        return parse_rawbin_from_bytes(data)
    except Exception as exc:
        log.error("RawBin parse failed: %s", exc)
        return None, None, None, True


def parse_rawbin_from_bytes(data: bytes):
    """Parse RawBin from an already-loaded bytes object."""
    return _parse_impl(data)


# ── Internal ──────────────────────────────────────────────────────────────────

def _probe(data: bytes, off: int) -> bool:
    if off + 4 > len(data): return False
    nim = struct.unpack_from('<H', data, off)[0]
    if not (1 <= nim <= MAX_IMAGES): return False
    slen_off = off + 2
    if slen_off >= len(data): return False
    slen = data[slen_off]
    if not (1 <= slen <= 64): return False
    name = data[slen_off + 1: slen_off + 1 + slen]
    try:
        return name.decode('ascii').isprintable()
    except Exception:
        return False


def _parse_impl(data: bytes):
    LAST_INFO.clear()
    if _probe(data, 0):
        offset = 0
    elif _probe(data, 12):
        offset = 12
    else:
        raise ValueError('Cannot locate image table — unrecognised RawBin layout')
    start_offset = offset

    # Images: pascal_string + 8×float32
    # Layout: offset_x, offset_y, width, height, tex_x, tex_y, origin_x, origin_y
    num_images = struct.unpack_from('<h', data, offset)[0]; offset += 2
    if not (0 <= num_images <= MAX_IMAGES):
        raise ValueError(f"Implausible num_images={num_images}")

    images = []
    for _ in range(num_images):
        slen   = data[offset]; offset += 1
        name   = data[offset: offset + slen].decode('utf-8', errors='replace')
        offset += slen
        vals   = struct.unpack_from('<8f', data, offset); offset += 32
        off_x, off_y = vals[0], vals[1]
        w,     h     = vals[2], vals[3]
        # offset_x/offset_y are Flash registration points (same as FBIN); they
        # can legitimately exceed sprite dimensions (e.g. body parts pivoted at
        # a joint far from the sprite bounds). A re-export of the same
        # character as FBIN (v33 of zombie_JourneyWest_tieguo) confirms the
        # raw values match the FBIN registration points byte-for-byte.
        log.debug("RawBin image '%s': tex=(%g,%g) size=(%g×%g) offset=(%g,%g)",
                  name, vals[4], vals[5], w, h, off_x, off_y)
        images.append({
            'name': name,
            'offset_x': off_x, 'offset_y': off_y,
            'width': w,        'height': h,
            'tex_x': vals[4],  'tex_y': vals[5],
            'origin_x': vals[6], 'origin_y': vals[7],
        })

    # Export table
    num_mc_names = struct.unpack_from('<h', data, offset)[0]; offset += 2
    if not (0 <= num_mc_names <= MAX_MOVIE_CLIPS):
        raise ValueError(f"Implausible num_mc_names={num_mc_names}")
    export_table = []
    for _ in range(num_mc_names):
        slen = data[offset]; offset += 1
        export_table.append(data[offset: offset + slen].decode('utf-8', errors='replace'))
        offset += slen

    # Actions
    num_actions = struct.unpack_from('<h', data, offset)[0]; offset += 2
    if not (0 <= num_actions <= MAX_ACTIONS):
        raise ValueError(f"Implausible num_actions={num_actions}")
    actions = []
    for _ in range(num_actions):
        slen  = data[offset]; offset += 1
        aname = data[offset: offset + slen].decode('utf-8', errors='replace')
        offset += slen
        v1, v2, v3, v4 = struct.unpack_from('<4h', data, offset); offset += 8
        actions.append({'name': aname, 'start': v1, 'end': v2, 'mc_idx': v3, 'p4': v4})

    # Movie clips — probe 6-byte vs 4-byte clip header
    parsed = None
    for hdr_size in (6, 4):
        result = _try_parse_clips(data, offset, num_mc_names, export_table, hdr_size)
        if result is not None:
            clips, end_offset = result
            if abs(end_offset - len(data)) < 16:
                parsed = clips
                offset = end_offset
                log.debug('RawBin clip header size: %d bytes', hdr_size)
                break

    if parsed is None:
        raise ValueError('Could not determine RawBin clip header size')

    LAST_INFO.update({
        "format":            "RawBin",
        "clip_header_size":  hdr_size,
        "start_offset":      start_offset,
        "consumed":          offset,
        "total":             len(data),
    })
    log.debug('RawBin: %d images, %d clips, %d actions (consumed %d/%d bytes)',
              len(images), len(parsed), len(actions), offset, len(data))
    return images, parsed, actions, True


def _try_parse_clips(data, start, num_mc, export_table, hdr_size):
    off   = start
    clips = []
    for ci in range(num_mc):
        if off + hdr_size > len(data): return None
        nf      = struct.unpack_from('<H', data, off)[0]
        # The next u16 is empirically a sequential clip index (matches `ci`)
        # in all observed samples — NOT a frame rate. Skip it.
        off    += hdr_size
        if nf > MAX_FRAMES: return None
        frames = []
        for _fi in range(nf):
            if off + 4 > len(data): return None
            ne = struct.unpack_from('<H', data, off + 2)[0]; off += 4
            if ne > MAX_ELEMENTS: return None
            elems = []
            for _ei in range(ne):
                if off + _ELEM_SIZE > len(data): return None
                mc_id       = data[off]
                frame_in_mc = data[off + 1]
                _extra, sx, ky, kx, sy, tx, ty = struct.unpack_from('<7f', data, off + 2)
                color_mult  = data[off + 30: off + 34]
                color_add   = data[off + 34: off + 38]
                off        += _ELEM_SIZE
                elems.append({
                    'is_mc': True, 'id': mc_id, 'frame_index': frame_in_mc,
                    'matrix': (sx, ky, kx, sy, tx, ty),
                    'alpha': color_mult[3] / 255.0,
                    'color_mult': bytes(color_mult), 'color_add': bytes(color_add),
                })
            frames.append(elems)
        mc_name = export_table[ci] if ci < len(export_table) else f"MC_{ci}"
        clips.append({'name': mc_name, 'frames': frames, 'frame_rate': 0})
    return clips, off
