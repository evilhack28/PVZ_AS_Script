"""Faithful port of the game's animation-bin loader, recovered from libcocos2dcpp.so 1.0.105"""

import logging
import struct

from input_buffer import InputBuffer, BufferError, DEFAULT_FRAME_RATE

log = logging.getLogger(__name__)

MAGIC = b'FBIN'
DEFAULT_DRAW_NUM = 500          # game default when the layout has no draw_num
ELEM_TYPE_MC = 1                # any other type value is an image

# Populated by every successful parse (shared with fbin_parser).
LAST_INFO: dict = {}

_RAW_ELEM = struct.Struct('<BhBh6f4s4s')      # 38 bytes


class GameBinError(Exception):
    """The file is not loadable by the game's parser."""


# ── Public entry ─────────────────────────────────────────────────────────────

def parse_game_bin(data: bytes):
    """Parse `data` exactly like CCFlashDefine::LoadFile."""
    LAST_INFO.clear()
    if data[:4] == MAGIC:
        return _parse(data, fbin=True)
    return _parse(data, fbin=False)


def is_extended_fbin(data: bytes) -> bool:
    return len(data) >= 13 and not any(data[5:13])


def is_extended_raw(data: bytes) -> bool:
    return len(data) >= 8 and not any(data[0:8])


# ── Body ─────────────────────────────────────────────────────────────────────

def _parse(data: bytes, *, fbin: bool):
    buf = InputBuffer(data)
    info = {"format": "FBIN" if fbin else "RawBin"}
    try:
        if fbin:
            buf.read_int()                              # magic, as an int
            v1 = buf.read_int()
            extended = is_extended_fbin(data)
            ints = [v1]
            ext = 1.0
            if extended:
                ints.append(buf.read_int())
                buf.read_int()                          # ignored
                ext = buf.read_float_min(100.0)
            info.update(version_ints=tuple(ints),
                        version_tag="v" + ".".join(str(v) for v in ints),
                        order='A', header_size=buf.tell())
            read_num = lambda: buf.read_float_min(100.0)    # noqa: E731
            read_elem = _read_elem_fbin
        else:
            extended = is_extended_raw(data)
            ext = 1.0
            if extended:
                buf.read_int()
                buf.read_int()
                ext = buf.read_float()
            info.update(header_size=buf.tell(), start_offset=buf.tell())
            read_num = buf.read_float
            read_elem = _read_elem_raw

        info["ext_float"] = ext
        info["extended"] = extended
        info["has_transform"] = extended                # legacy alias
        info["clip_header_size"] = 6 if extended else 4

        # ── images ───────────────────────────────────────────────────────────
        n_img = buf.read_short()
        if n_img < 0:
            raise GameBinError(f"negative image count {n_img}")
        images = []
        for _ in range(n_img):
            name = buf.read_pascal_string()
            v = [read_num() for _ in range(8)]
            images.append({
                "name": name,
                "offset_x": v[0], "offset_y": v[1],
                "width": v[2], "height": v[3],
                "tex_x": v[4], "tex_y": v[5],
                "origin_x": v[6], "origin_y": v[7],       # hint fields
            })

        # ── movie-clip names ─────────────────────────────────────────────────
        n_mc = buf.read_short()
        if n_mc < 0:
            raise GameBinError(f"negative movie-clip count {n_mc}")
        mc_names = [buf.read_pascal_string() for _ in range(n_mc)]

        # ── actions ──────────────────────────────────────────────────────────
        n_act = buf.read_short()
        if n_act < 0:
            raise GameBinError(f"negative action count {n_act}")
        actions = []
        for _ in range(n_act):
            name = buf.read_pascal_string()
            start, end, mc_idx, p4 = (buf.read_short() for _ in range(4))
            actions.append({"name": name, "start": start, "end": end,
                            "mc_idx": mc_idx, "p4": p4})

        # ── movie-clip frame data ────────────────────────────────────────────
        movie_clips = []
        for i in range(n_mc):
            n_frames = buf.read_short()
            mc_id    = buf.read_short()
            draw_num = buf.read_short() if extended else DEFAULT_DRAW_NUM
            if n_frames < 0:
                raise GameBinError(f"MC {i}: negative frame count {n_frames}")
            frames = []
            for _f in range(n_frames):
                buf.read_short()                        # per-frame word (unused)
                n_elem = buf.read_short()
                if n_elem < 0:
                    raise GameBinError(f"MC {i}: negative element count")
                frames.append([read_elem(buf, n_mc, n_img) for _e in range(n_elem)])
            movie_clips.append({
                "name": mc_names[i], "frames": frames, "id": mc_id,
                "draw_num": draw_num,
                # The file carries no frame rate; CCFlashMovieclip's ctor hard-codes 30
                "frame_rate": DEFAULT_FRAME_RATE,
            })
    except (BufferError, struct.error) as exc:
        raise GameBinError(str(exc)) from exc

    finalize_actions(actions, movie_clips)

    if ext and ext != 1.0:
        _apply_world_scale(images, movie_clips, ext)

    info["consumed"] = buf.tell()
    info["total"] = len(data)
    LAST_INFO.clear()
    LAST_INFO.update(info)
    return images, movie_clips, actions, (not fbin)


# ── Element readers ──────────────────────────────────────────────────────────

def _valid_id(type_, ident, n_mc, n_img):
    limit = n_mc if type_ == ELEM_TYPE_MC else n_img
    return -1 if ident >= limit or ident < 0 else ident


def _elem(type_, ident, frame_index, blend, matrix, cm, ca):
    return {
        "type":        type_,
        "is_mc":       type_ == ELEM_TYPE_MC,
        "id":          ident,
        "frame_index": frame_index,
        "blend":       blend,
        "matrix":      matrix,
        "alpha":       cm[3] / 255.0,
        "color_mult":  cm,
        "color_add":   ca,
    }


_DEF_MULT = bytes((255, 255, 255, 255))
_DEF_ADD  = bytes(4)


def _read_elem_fbin(buf: InputBuffer, n_mc: int, n_img: int) -> dict:
    flags = buf.read_byte()
    type_ = buf.read_byte()
    ident = buf.read_short()
    frame_index = buf.read_short() if flags & 0x02 else 0
    a, b, c, d, tx, ty = 1.0, 0.0, 0.0, 1.0, 0.0, 0.0
    if flags & 0x04: a = buf.read_float_min(10000.0)
    if flags & 0x08: b = buf.read_float_min(10000.0)
    if flags & 0x10: c = buf.read_float_min(10000.0)
    if flags & 0x20: d = buf.read_float_min(10000.0)
    if flags & 0x40:
        tx = buf.read_float_min(100.0)
        ty = buf.read_float_min(100.0)
    cm, ca = _DEF_MULT, _DEF_ADD
    if flags & 0x80:
        cm = buf.read_bytes(4)
        ca = buf.read_bytes(4)
    return _elem(type_, _valid_id(type_, ident, n_mc, n_img), frame_index,
                 bool(flags & 0x01), (a, b, c, d, tx, ty), cm, ca)


def _read_elem_raw(buf: InputBuffer, n_mc: int, n_img: int) -> dict:
    if buf.offset + _RAW_ELEM.size > buf.length:
        raise BufferError("End of buffer reading RawBin element")
    (type_, ident, blend, frame_index,
     a, b, c, d, tx, ty, cm, ca) = _RAW_ELEM.unpack_from(buf.data, buf.offset)
    buf.offset += _RAW_ELEM.size
    return _elem(type_, _valid_id(type_, ident, n_mc, n_img), frame_index,
                 blend == 1, (a, b, c, d, tx, ty), cm, ca)


# ── Post-processing ──────────────────────────────────────────────────────────

def finalize_actions(actions: list, movie_clips: list) -> None:
    """Add `frames`, the exact MC-frame sequence the game plays for each action"""
    for a in actions:
        mc_idx = a["mc_idx"]
        length = a["end"] - a["start"] + 1
        ok = 0 <= mc_idx < len(movie_clips)
        total = len(movie_clips[mc_idx]["frames"]) if ok else 0
        if not ok or total <= 0 or length < 1:
            a["frames"], a["valid"] = [], False
            continue
        a["frames"] = [(a["p4"] + i) % total for i in range(length)]
        a["valid"] = True


def _apply_world_scale(images: list, movie_clips: list, ext: float) -> None:
    """Fold the header scale into offsets + translations (see module doc)."""
    for im in images:
        im["offset_x"] *= ext
        im["offset_y"] *= ext
    for mc in movie_clips:
        for fr in mc["frames"]:
            for el in fr:
                a, b, c, d, tx, ty = el["matrix"]
                el["matrix"] = (a, b, c, d, tx * ext, ty * ext)
