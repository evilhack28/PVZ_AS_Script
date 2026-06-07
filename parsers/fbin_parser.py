"""
fbin_parser.py
--------------
Parses Cocos2d-x FBIN / MinBin animation data files.
Delegates no-magic RawBin files to rawbin_parser.py.

FBIN image record — 8 MinBin floats, all divisor=100
=====================================================
  [0] offset_x   registration X  (sub-pixel, can exceed sprite bounds)
  [1] offset_y   registration Y  (sub-pixel, can exceed sprite bounds)
  [2] width      sprite width  in pixels
  [3] height     sprite height in pixels
  [4] tex_x      atlas X in pixels
  [5] tex_y      atlas Y in pixels  (tag=4 near-zero == 0, int() handles it)
  [6] hint_x     tex_x + width  + padding  (ignored)
  [7] hint_y     tex_y + height + padding  (ignored)

NOTE: offset_x/offset_y can legitimately be larger than width/height in
FBIN — do NOT clamp them.  They are Flash registration points that may
sit far outside the sprite boundary.
"""

import os
import struct
import logging

from input_buffer import InputBuffer, BufferError

log = logging.getLogger(__name__)

MAX_IMAGES         = 1024
MAX_MOVIE_CLIPS    = 2000
MAX_ACTIONS        = 5000
MAX_FRAMES         = 8000
MAX_ELEMENTS       = 4096
DEFAULT_FRAME_RATE = 30

# Populated by the successful parse path so callers (e.g. scripts/main.py) can
# show the file's encoded version + variant in their summary.  Reset on each
# parse_fbin() call.
LAST_INFO: dict = {}


# ── Public API ────────────────────────────────────────────────────────────────

def parse_fbin(bin_path: str):
    """
    Auto-detect FBIN vs RawBin and parse.
    Returns (images, movie_clips, actions, is_rawbin).
    """
    LAST_INFO.clear()
    try:
        with open(bin_path, 'rb') as fh:
            data = fh.read()
    except OSError as exc:
        log.error("Cannot open '%s': %s", bin_path, exc)
        return None, None, None, False

    if len(data) < 4 or data[0:4] != b'FBIN':
        log.debug("No FBIN magic — delegating to rawbin_parser.")
        try:
            from rawbin_parser import parse_rawbin_from_bytes
            result = parse_rawbin_from_bytes(data)
            if result[0] is not None:
                return result
        except Exception as exc:
            log.debug("RawBin parse failed: %s", exc)
        log.warning("RawBin parse failed — file unrecognised.")
        return None, None, None, False

    # Silence input_buffer warnings while probing: a rejected variant misreads
    # bytes as MinBin tags, but the outer validator throws the result away anyway.
    ib_log = logging.getLogger("input_buffer")
    prev_level = ib_log.level
    ib_log.setLevel(logging.ERROR)
    try:
        for num_versions in (2, 1):
            for has_transform in (True, False):
                for order in ('A', 'B'):
                    result = _parse_impl(data, has_transform=has_transform,
                                         order_variant=order,
                                         num_versions=num_versions)
                    if result is not None:
                        ib_log.setLevel(prev_level)
                        images, mcs, actions = result
                        return images, mcs, actions, False
    finally:
        ib_log.setLevel(prev_level)

    log.warning("All FBIN variants failed — attempting minimal fallback.")
    result = _minimal_fallback(data, bin_path)
    if result[0] is not None:
        return result[0], result[1], result[2], False

    return None, None, None, False


# ── FBIN parse ────────────────────────────────────────────────────────────────

def _parse_impl(data: bytes, *, has_transform: bool, order_variant: str,
                num_versions: int = 2):
    buf = InputBuffer(data)
    tag = f"has_transform={has_transform}, order={order_variant}, versions={num_versions}"
    try:
        if buf.read_bytes(4) != b'FBIN':
            return None
        version_ints = []
        for _ in range(max(0, num_versions)):
            version_ints.append(buf.read_int())

        if has_transform:
            saved = buf.tell()
            try:
                _ext_v1    = buf.read_int()
                _ext_float = buf.read_float_min(100.0)
            except BufferError:
                buf.seek(saved)

        images                = _read_images(buf)
        export_table, actions = _read_export_and_actions(buf, order_variant)
        movie_clips           = _read_movie_clips(buf, len(export_table),
                                                  export_table, has_transform)

        if not movie_clips and images:
            movie_clips = [_make_pseudo_clip("PseudoClip", images)]

        # ── Validate: buffer should be mostly consumed ────────────────────────
        # If the wrong variant was selected (e.g. has_transform=True on a file
        # that has no per-clip frame_rate field), the extra reads shift all
        # subsequent positions, leaving unconsumed bytes at the end.
        # More than 16 trailing bytes strongly indicates the wrong variant.
        remaining = buf.length - buf.tell()
        if remaining > 16:
            log.debug("Parse attempt (%s) rejected: %d trailing bytes unconsumed.",
                      tag, remaining)
            return None

        # ── Validate: all action mc_idx must be in range ──────────────────────
        for action in actions:
            if not (0 <= action.get('mc_idx', -1) < len(movie_clips)):
                log.debug("Parse attempt (%s) rejected: action '%s' mc_idx=%d out of range "
                          "(have %d clips).", tag, action.get('name', '?'),
                          action.get('mc_idx', -1), len(movie_clips))
                return None

        # Build a human-readable version tag from the encoded ints.  Most files
        # store [1, 0] (or just [1] under num_versions=1) — we expose this so
        # the summary can print "FBIN v1.0" / "FBIN v1".
        if version_ints:
            version_tag = "v" + ".".join(str(v) for v in version_ints)
        else:
            version_tag = "v?"
        LAST_INFO.update({
            "format":        "FBIN",
            "version_ints":  tuple(version_ints),
            "version_tag":   version_tag,
            "has_transform": has_transform,
            "order":         order_variant,
            "num_versions":  num_versions,
            "variant_tag":   tag,
        })
        log.debug("[%s] Parsed %d images, %d clips, %d actions.",
                  tag, len(images), len(movie_clips), len(actions))
        return images, movie_clips, actions

    except (BufferError, struct.error, Exception) as exc:
        log.debug("Parse attempt (%s) failed: %s", tag, exc)
        return None


# ── Image reader ──────────────────────────────────────────────────────────────

def _read_images(buf: InputBuffer) -> list:
    raw_count = buf.read_short()
    count     = max(0, min(raw_count, MAX_IMAGES))
    if raw_count != count:
        log.debug("Image count clamped %d → %d", raw_count, count)

    images = []
    for _ in range(count):
        name = buf.read_pascal_string()

        off_x  = buf.read_float_min(100.0)   # [0] registration X
        off_y  = buf.read_float_min(100.0)   # [1] registration Y
        w      = buf.read_float_min(100.0)   # [2] width
        h      = buf.read_float_min(100.0)   # [3] height
        tex_x  = buf.read_float_min(100.0)   # [4] atlas X
        tex_y  = buf.read_float_min(100.0)   # [5] atlas Y
        _hx    = buf.read_float_min(100.0)   # [6] hint: tex_x+w+pad (ignored)
        _hy    = buf.read_float_min(100.0)   # [7] hint: h+pad       (ignored)

        log.debug("FBIN image '%s': tex=(%g,%g) size=(%g×%g) offset=(%g,%g)",
                  name, tex_x, tex_y, w, h, off_x, off_y)

        images.append({
            "name":     name,
            "offset_x": off_x,
            "offset_y": off_y,
            "width":    w,
            "height":   h,
            "tex_x":    tex_x,
            "tex_y":    tex_y,
            "origin_x": _hx,
            "origin_y": _hy,
        })
    return images


# ── Export table + actions ────────────────────────────────────────────────────

def _read_export_and_actions(buf: InputBuffer, order_variant: str):
    if order_variant == 'A':
        return _read_export_table(buf), _read_actions(buf)
    elif order_variant == 'B':
        actions = _read_actions_with_probe(buf)
        return _read_export_table(buf), actions
    raise ValueError(f"Unknown order_variant '{order_variant}'")


def _read_export_table(buf: InputBuffer) -> list:
    count = max(0, min(buf.read_short(), MAX_MOVIE_CLIPS))
    names = []
    for i in range(count):
        try:
            names.append(buf.read_pascal_string())
        except BufferError:
            names.append(f"MC_{i}")
    return names


def _read_actions(buf: InputBuffer) -> list:
    count = max(0, min(buf.read_short(), MAX_ACTIONS))
    return [_read_single_action(buf) for _ in range(count)]


def _read_actions_with_probe(buf: InputBuffer) -> list:
    count = max(0, min(buf.read_short(), MAX_ACTIONS))
    if count == 0:
        return []
    probe_pos  = buf.tell()
    name_len   = buf.read_byte()
    name_bytes = buf.read_bytes(min(name_len, 64))
    if not InputBuffer.is_printable_ascii(name_bytes):
        raise BufferError("Non-printable action name in variant-B probe")
    buf.seek(probe_pos)
    return [_read_single_action(buf) for _ in range(count)]


def _read_single_action(buf: InputBuffer) -> dict:
    name = buf.read_pascal_string()
    v1, v2, v3, v4 = (buf.read_short() for _ in range(4))
    return {"name": name, "start": v1, "end": v2, "mc_idx": v3, "p4": v4}


# ── Movie clips ───────────────────────────────────────────────────────────────

def _read_movie_clips(buf: InputBuffer, num_mc: int,
                      export_table: list, has_transform: bool) -> list:
    movie_clips = []
    for i in range(max(0, num_mc)):
        raw_frame_count = buf.read_short()
        _unused          = buf.read_short()

        frame_rate = DEFAULT_FRAME_RATE
        if has_transform:
            try:
                val = buf.read_short()
                if 1 <= val <= 120:
                    frame_rate = val
            except BufferError:
                pass

        num_frames = max(0, min(raw_frame_count, MAX_FRAMES))
        frames     = [_read_frame(buf) for _ in range(num_frames)]
        name       = export_table[i] if i < len(export_table) else f"MC_{i}"
        movie_clips.append({"name": name, "frames": frames,
                            "frame_rate": frame_rate})
    return movie_clips


def _read_frame(buf: InputBuffer) -> list:
    _val_a       = buf.read_short()
    raw_elements = buf.read_short()
    num_elements = max(0, min(raw_elements, MAX_ELEMENTS))
    if raw_elements != num_elements:
        log.debug("Element count clamped %d → %d", raw_elements, num_elements)
    return [_read_element(buf) for _ in range(num_elements)]


def _read_element(buf: InputBuffer) -> dict:
    flag      = buf.read_byte()
    type_byte = buf.read_byte()
    elem_id   = buf.read_short()

    sx, sy, kx, ky = 1.0, 1.0, 0.0, 0.0
    x, y           = 0.0, 0.0
    alpha          = 1.0
    color_mult     = None
    color_add      = None
    frame_index    = -1

    if flag & 0x02: frame_index = buf.read_short()
    if flag & 0x04: sx = buf.read_float_min(10000.0)   # Flash matrix a
    if flag & 0x08: ky = buf.read_float_min(10000.0)   # Flash matrix b (was incorrectly kx)
    if flag & 0x10: kx = buf.read_float_min(10000.0)   # Flash matrix c (was incorrectly ky)
    if flag & 0x20: sy = buf.read_float_min(10000.0)   # Flash matrix d
    if flag & 0x40:
        x = buf.read_float_min(100.0)
        y = buf.read_float_min(100.0)
    if flag & 0x80:
        try:
            color_mult = buf.read_bytes(4)
            color_add  = buf.read_bytes(4)
            alpha      = color_mult[3] / 255.0
        except BufferError:
            color_mult = color_add = None

    return {
        "is_mc":       (type_byte == 1),
        "id":          elem_id,
        "frame_index": frame_index,
        "matrix":      (sx, ky, kx, sy, x, y),
        "alpha":       alpha,
        "color_mult":  color_mult,
        "color_add":   color_add,
    }


# ── Pseudo-clip builder ───────────────────────────────────────────────────────

def _make_pseudo_clip(name: str, images: list) -> dict:
    return {
        "name": name,
        "frames": [[{
            "is_mc": False, "id": idx, "frame_index": -1,
            "matrix": (1.0, 0.0, 0.0, 1.0, 0.0, 0.0),
            "alpha": 1.0, "color_mult": None, "color_add": None,
        } for idx in range(len(images))]],
        "frame_rate": DEFAULT_FRAME_RATE,
    }


# ── Minimal fallback ──────────────────────────────────────────────────────────

def _minimal_fallback(data: bytes, bin_path: str):
    ib_log = logging.getLogger("input_buffer")
    prev_level = ib_log.level
    ib_log.setLevel(logging.ERROR)
    try:
        return _minimal_fallback_impl(data, bin_path)
    finally:
        ib_log.setLevel(prev_level)


def _minimal_fallback_impl(data: bytes, bin_path: str):
    for num_versions in (2, 1):
        try:
            buf = InputBuffer(data)
            buf.read_bytes(4)
            for _ in range(num_versions):
                buf.read_int()
            saved = buf.tell()
            try:
                buf.read_int(); buf.read_float_min(100.0)
            except BufferError:
                buf.seek(saved)
            images = _read_images(buf)
            if images:
                clip_name = os.path.splitext(os.path.basename(bin_path))[0]
                log.info("Minimal fallback (versions=%d): %d images, 1 synthetic clip.",
                         num_versions, len(images))
                return images, [_make_pseudo_clip(clip_name, images)], []
        except Exception as exc:
            log.debug("Minimal fallback versions=%d failed: %s", num_versions, exc)
    log.error("Minimal fallback: no images found.")
    return None, None, None
