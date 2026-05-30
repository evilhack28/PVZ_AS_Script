"""
rawbin_writer.py
----------------
Parse a RawBin file preserving every byte (including fields the normal parser
discards), then re-serialise it back to bytes.  Used by round_trip_test.py to
verify that the parser has a complete understanding of the file format.

Public API
----------
parse_rawbin_full(data: bytes) -> dict
write_rawbin(parsed: dict) -> bytes
"""

import struct
import logging

log = logging.getLogger(__name__)

_MAX_IMAGES      = 1024
_MAX_CLIPS       = 2000
_MAX_ACTIONS     = 5000
_MAX_FRAMES      = 8000
_MAX_ELEMENTS    = 4096
_ELEM_SIZE       = 38


# ── Probe (identical to rawbin_parser._probe) ─────────────────────────────────

def _probe(data: bytes, off: int) -> bool:
    if off + 4 > len(data):
        return False
    nim = struct.unpack_from('<H', data, off)[0]
    if not (1 <= nim <= _MAX_IMAGES):
        return False
    slen_off = off + 2
    if slen_off >= len(data):
        return False
    slen = data[slen_off]
    if not (1 <= slen <= 64):
        return False
    name = data[slen_off + 1: slen_off + 1 + slen]
    try:
        return name.decode('ascii').isprintable()
    except Exception:
        return False


# ── Full parser ───────────────────────────────────────────────────────────────

def parse_rawbin_full(data: bytes) -> dict:
    """
    Parse a RawBin binary, preserving ALL fields including those the normal
    parser discards (_extra per element, val_a per frame, fr / _hdr_extra per
    clip, raw float values before offset clamping, and the leading file header).

    Returns a dict suitable for write_rawbin().
    """
    if _probe(data, 0):
        header_offset = 0
    elif _probe(data, 12):
        header_offset = 12
    else:
        raise ValueError('Cannot locate image table — unrecognised RawBin layout')

    header_bytes = data[:header_offset]
    off = header_offset

    # ── Images ────────────────────────────────────────────────────────────────
    num_images = struct.unpack_from('<h', data, off)[0]; off += 2
    if not (0 <= num_images <= _MAX_IMAGES):
        raise ValueError(f'Implausible num_images={num_images}')

    images = []
    for _ in range(num_images):
        slen       = data[off]; off += 1
        name_bytes = data[off: off + slen]; off += slen
        raw_vals   = struct.unpack_from('<8f', data, off); off += 32
        images.append({
            'name_bytes': bytes(name_bytes),
            'name':       name_bytes.decode('utf-8', errors='replace'),
            'raw_vals':   raw_vals,  # 8 floats, NOT clamped — written as-is
        })

    # ── Export table ──────────────────────────────────────────────────────────
    num_mc_names = struct.unpack_from('<h', data, off)[0]; off += 2
    if not (0 <= num_mc_names <= _MAX_CLIPS):
        raise ValueError(f'Implausible num_mc_names={num_mc_names}')

    export_table = []
    for _ in range(num_mc_names):
        slen       = data[off]; off += 1
        name_bytes = data[off: off + slen]; off += slen
        export_table.append({
            'name_bytes': bytes(name_bytes),
            'name':       name_bytes.decode('utf-8', errors='replace'),
        })

    # ── Actions ───────────────────────────────────────────────────────────────
    num_actions = struct.unpack_from('<h', data, off)[0]; off += 2
    if not (0 <= num_actions <= _MAX_ACTIONS):
        raise ValueError(f'Implausible num_actions={num_actions}')

    actions = []
    for _ in range(num_actions):
        slen       = data[off]; off += 1
        name_bytes = data[off: off + slen]; off += slen
        v1, v2, v3, v4 = struct.unpack_from('<4h', data, off); off += 8
        actions.append({
            'name_bytes': bytes(name_bytes),
            'name':       name_bytes.decode('utf-8', errors='replace'),
            'start': v1, 'end': v2, 'mc_idx': v3, 'p4': v4,
        })

    # ── Movie clips — probe 6-byte vs 4-byte header ───────────────────────────
    parsed_clips = None
    used_hdr_size = None
    for hdr_size in (6, 4):
        result = _try_parse_clips_full(data, off, num_mc_names, export_table, hdr_size)
        if result is not None:
            clips, end_off = result
            if abs(end_off - len(data)) < 16:
                parsed_clips = clips
                used_hdr_size = hdr_size
                off = end_off
                break

    if parsed_clips is None:
        raise ValueError('Could not determine RawBin clip header size')

    log.info('RawBin full: %d images, %d clips, %d actions, hdr_size=%d (%d/%d bytes)',
             len(images), len(parsed_clips), len(actions),
             used_hdr_size, off, len(data))

    return {
        'header_bytes':  header_bytes,
        'header_offset': header_offset,
        'hdr_size':      used_hdr_size,
        'images':        images,
        'export_table':  export_table,
        'actions':       actions,
        'clips':         parsed_clips,
    }


def _try_parse_clips_full(data, start, num_mc, export_table, hdr_size):
    off   = start
    clips = []
    for ci in range(num_mc):
        if off + hdr_size > len(data):
            return None
        nf  = struct.unpack_from('<H', data, off)[0]
        fr  = struct.unpack_from('<H', data, off + 2)[0]
        hdr_extra = None
        if hdr_size == 6:
            hdr_extra = struct.unpack_from('<H', data, off + 4)[0]
        off += hdr_size
        if nf > _MAX_FRAMES:
            return None

        frames = []
        for _fi in range(nf):
            if off + 4 > len(data):
                return None
            val_a = struct.unpack_from('<H', data, off)[0]
            ne    = struct.unpack_from('<H', data, off + 2)[0]
            off  += 4
            if ne > _MAX_ELEMENTS:
                return None

            elems = []
            for _ei in range(ne):
                if off + _ELEM_SIZE > len(data):
                    return None
                mc_id       = data[off]
                frame_in_mc = data[off + 1]
                _extra, sx, ky, kx, sy, tx, ty = struct.unpack_from('<7f', data, off + 2)
                color_mult  = data[off + 30: off + 34]
                color_add   = data[off + 34: off + 38]
                off        += _ELEM_SIZE
                elems.append({
                    'id':          mc_id,
                    'frame_index': frame_in_mc,
                    '_extra':      _extra,
                    'matrix':      (sx, ky, kx, sy, tx, ty),
                    'color_mult':  bytes(color_mult),
                    'color_add':   bytes(color_add),
                })
            frames.append({'val_a': val_a, 'elements': elems})

        mc_name = export_table[ci]['name'] if ci < len(export_table) else f'MC_{ci}'
        clips.append({
            'name':       mc_name,
            '_fr':        fr,
            '_hdr_extra': hdr_extra,
            'frames':     frames,
        })
    return clips, off


# ── Serialiser ────────────────────────────────────────────────────────────────

def write_rawbin(parsed: dict) -> bytes:
    """
    Serialise a dict returned by parse_rawbin_full() back to bytes.
    Intended to produce output that is byte-identical to the original file.
    """
    out      = bytearray()
    hdr_size = parsed['hdr_size']

    # Header
    out += parsed['header_bytes']

    # Images
    images = parsed['images']
    out   += struct.pack('<h', len(images))
    for img in images:
        nb = img['name_bytes']
        out += bytes([len(nb)]) + nb
        out += struct.pack('<8f', *img['raw_vals'])

    # Export table
    export_table = parsed['export_table']
    out += struct.pack('<h', len(export_table))
    for entry in export_table:
        nb   = entry['name_bytes']
        out += bytes([len(nb)]) + nb

    # Actions
    actions = parsed['actions']
    out    += struct.pack('<h', len(actions))
    for act in actions:
        nb   = act['name_bytes']
        out += bytes([len(nb)]) + nb
        out += struct.pack('<4h', act['start'], act['end'], act['mc_idx'], act['p4'])

    # Movie clips
    for clip in parsed['clips']:
        frames = clip['frames']
        nf     = len(frames)
        fr     = clip['_fr']
        if hdr_size == 6:
            out += struct.pack('<3H', nf, fr, clip['_hdr_extra'])
        else:
            out += struct.pack('<2H', nf, fr)

        for frame in frames:
            elems = frame['elements']
            out  += struct.pack('<HH', frame['val_a'], len(elems))
            for elem in elems:
                out += struct.pack('<BB', elem['id'], elem['frame_index'])
                out += struct.pack('<7f', elem['_extra'], *elem['matrix'])
                out += elem['color_mult'] + elem['color_add']

    return bytes(out)
