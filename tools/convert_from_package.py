"""
convert_from_package.py
-----------------------
Reverse of convert_to_package.py: take a Flash CS5 .package (or just the
XFL project folder containing main.xfl) and emit a RawBin .bin + PVR atlas
(RGBA8888, iOS PVR v2) pair playable by main.py.

Usage:
    # single-character .package
    python convert_from_package.py --package PlantApplemortar3_5.package

    # group .package — fans out, one .bin/.pvr pair per character
    python convert_from_package.py --package ZombieFooGroup_5.package

    # or point at the XFL folder directly (parent of main.xfl)
    python convert_from_package.py --package path/to/applemortar_3

    # custom output folder
    python convert_from_package.py --package foo.package --out out/

Outputs `<stem>.bin` + `<stem>.pvr` in the output folder (defaults to the
input's parent).
"""
from __future__ import annotations

import argparse
import os
import struct
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import _paths  # noqa: F401

from PIL import Image

XFL_NS = '{http://ns.adobe.com/xfl/2008/}'

# Sprite symbol names that are trigger-only overlays: not drawn by default
# in-game, injected by the runtime when a state activates (a buttered zombie;
# the Atlantis zombie's squid-ink cloud and glowing red-eye rage). Source XFLs
# reference them on their own label layers in every frame, which bakes them in
# as always-visible. We drop those references from action (label) timelines
# only — the sprite MC itself stays in the library so a trigger can show it.
# Matched against the lowercased sprite symbol name.
_TRIGGER_OVERLAY_NAMES = frozenset({'butter', 'ink', 'red_eyes'})


# ─────────────────────────────────────────────────────────────────────────────
# XFL root resolution
# ─────────────────────────────────────────────────────────────────────────────

def _find_xfl_roots(input_path: Path) -> list[Path]:
    """Return one or more directories that contain a main.xfl + DOMDocument.xml.
    Accepts either an XFL folder directly, or a .package wrapper (in which
    case every character's XFL folder is returned)."""
    if (input_path / 'main.xfl').exists():
        return [input_path]
    if not input_path.is_dir():
        raise FileNotFoundError(f"Not a directory: {input_path}")
    # Walk and find every main.xfl
    roots = []
    for dirpath, _dirs, files in os.walk(input_path):
        if 'main.xfl' in files:
            roots.append(Path(dirpath))
    if not roots:
        raise FileNotFoundError(
            f"No main.xfl found under '{input_path}'. Point at the XFL folder "
            f"or a .package containing one.")
    return roots


# ─────────────────────────────────────────────────────────────────────────────
# Matrix helpers (Flash convention: (a, b, c, d, tx, ty))
# ─────────────────────────────────────────────────────────────────────────────

def _read_flash_matrix(mat_el: ET.Element | None) -> tuple:
    """Read a <Matrix> XML element into a Flash (a,b,c,d,tx,ty) tuple."""
    if mat_el is None:
        return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    attrs = mat_el.attrib
    return (float(attrs.get('a',  1.0)),
            float(attrs.get('b',  0.0)),
            float(attrs.get('c',  0.0)),
            float(attrs.get('d',  1.0)),
            float(attrs.get('tx', 0.0)),
            float(attrs.get('ty', 0.0)))


def _mat_mul(m1: tuple, m2: tuple) -> tuple:
    """Flash matrix composition: result = m1 * m2 (m2 applied first)."""
    a1, b1, c1, d1, tx1, ty1 = m1
    a2, b2, c2, d2, tx2, ty2 = m2
    return (a1*a2 + c1*b2,
            b1*a2 + d1*b2,
            a1*c2 + c1*d2,
            b1*c2 + d1*d2,
            a1*tx2 + c1*ty2 + tx1,
            b1*tx2 + d1*ty2 + ty1)


def _flash_to_cocos(m: tuple) -> tuple:
    """Flash Y-down (a,b,c,d,tx,ty) -> Cocos Y-up (sx,ky,kx,sy,tx,ty).
    Inverse of convert_to_package._flash_matrix; the formula is symmetric."""
    a, b, c, d, tx, ty = m
    return (a, -b, -c, d, tx, -ty)


def _parse_instance(instance: ET.Element,
                    image_sym_xforms: dict) -> dict | None:
    """Convert a <DOMSymbolInstance> into an intermediate dict
    {target: 'sprite/foo'|'image/bar', matrix: 6-tuple cocos, alpha: float}.

    For image targets we COMBINE the outer matrix with the image symbol's
    internal matrix (which often carries a non-unity scale in real PvZ
    packages, e.g. a=d=0.781250). The combined matrix becomes the element's
    matrix in the bin so the renderer reproduces Flash's geometry exactly —
    and the image's offset_x/offset_y in the bin can stay 0 because the
    combined matrix already places the bitmap correctly."""
    libname = instance.get('libraryItemName')
    if not libname or not (libname.startswith('sprite/') or libname.startswith('image/')):
        return None

    outer_flash = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    mnode = instance.find(f'{XFL_NS}matrix')
    if mnode is not None:
        outer_flash = _read_flash_matrix(mnode.find(f'{XFL_NS}Matrix'))

    if libname.startswith('image/'):
        sym = libname[len('image/'):]
        img_flash = image_sym_xforms.get(sym, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0))
        combined = _mat_mul(outer_flash, img_flash)
        matrix = _flash_to_cocos(combined)
    else:
        matrix = _flash_to_cocos(outer_flash)

    alpha = 1.0
    cnode = instance.find(f'{XFL_NS}color')
    if cnode is not None:
        col = cnode.find(f'{XFL_NS}Color')
        if col is not None:
            try:
                alpha = float(col.get('alphaMultiplier', '1.0'))
            except ValueError:
                pass

    # `firstFrame` selects which frame of a referenced sprite symbol is shown.
    # RawBin output ignores it (its mc_id=1 redirect drives sub-frames a
    # different way), but FBIN output encodes it directly as the element's
    # frame_index. Default 0.
    first_frame = 0
    ff = instance.get('firstFrame')
    if ff is not None:
        try:
            first_frame = max(0, int(ff))
        except ValueError:
            first_frame = 0
    return {'target': libname, 'matrix': matrix, 'alpha': alpha,
            'first_frame': first_frame}


# ─────────────────────────────────────────────────────────────────────────────
# Timeline (sprite or label) -> per-frame element lists
# ─────────────────────────────────────────────────────────────────────────────

def _timeline_frames(symbol_xml: Path,
                     image_sym_xforms: dict) -> list[list[dict]]:
    """Parse a sprite/<x>.xml or label/<x>.xml and return frames[i] = list of
    intermediate element dicts (in z-order: bottom layer first)."""
    tree = ET.parse(symbol_xml)
    root = tree.getroot()
    timeline = root.find(f'.//{XFL_NS}DOMTimeline')
    if timeline is None:
        return [[]]
    layers_node = timeline.find(f'{XFL_NS}layers')
    if layers_node is None:
        return [[]]

    # Determine total frame count from the maximum (index + duration) across
    # all layers' DOMFrames.
    layers = layers_node.findall(f'{XFL_NS}DOMLayer')
    total = 0
    for layer in layers:
        for df in layer.findall(f'.//{XFL_NS}DOMFrame'):
            idx = int(df.get('index', '0'))
            dur = int(df.get('duration', '1'))
            total = max(total, idx + dur)
    if total == 0:
        return [[]]

    frames = [[] for _ in range(total)]
    # convert_to_package emits highest layer number first. Reverse so bottom
    # layer's elements come first per frame (matches normal z-order).
    for layer in reversed(layers):
        for df in layer.findall(f'.//{XFL_NS}DOMFrame'):
            idx = int(df.get('index', '0'))
            dur = int(df.get('duration', '1'))
            elements_node = df.find(f'{XFL_NS}elements')
            if elements_node is None:
                continue
            for instance in elements_node.findall(f'{XFL_NS}DOMSymbolInstance'):
                payload = _parse_instance(instance, image_sym_xforms)
                if payload is None:
                    continue
                for f in range(idx, min(idx + dur, total)):
                    frames[f].append(payload)
    return frames


# ─────────────────────────────────────────────────────────────────────────────
# Image symbol parsing
# ─────────────────────────────────────────────────────────────────────────────

def _parse_image_symbol_matrix(image_xml: Path) -> tuple:
    """Read the full <Matrix a b c d tx ty/> from an image symbol. Real PvZ
    packages put a scale here (e.g. a=d=0.781250) that must be folded into
    every reference's outer matrix to render at the right size."""
    tree = ET.parse(image_xml)
    root = tree.getroot()
    return _read_flash_matrix(root.find(f'.//{XFL_NS}Matrix'))


# ─────────────────────────────────────────────────────────────────────────────
# DOMDocument -> actions
# ─────────────────────────────────────────────────────────────────────────────

def _parse_actions(domdoc_xml: Path) -> list[dict]:
    """Read the root timeline's `label` layer for named action markers.
    Returns [{name, start, duration}, ...] in document order."""
    tree = ET.parse(domdoc_xml)
    root = tree.getroot()
    out = []
    for layer in root.findall(f'.//{XFL_NS}DOMLayer'):
        if layer.get('name') != 'label':
            continue
        for df in layer.findall(f'.//{XFL_NS}DOMFrame'):
            if df.get('labelType') == 'name' and df.get('name'):
                out.append({
                    'name': df.get('name'),
                    'start': int(df.get('index', '0')),
                    'duration': int(df.get('duration', '1')),
                })
        break
    return out


# ─────────────────────────────────────────────────────────────────────────────
# PVR v2 RGBA8888 encoder (matches pvr/pvr_loader.py's `_FMT_RGBA8888 = 0x12`)
# Header layout (52 bytes):
#   off  0 : header_size  u32  = 52
#   off  4 : height       u32
#   off  8 : width        u32
#   off 12 : mip_count    u32  = 0
#   off 16 : flags        u32  = 0x12 (RGBA8888 pixel_type, no extras)
#   off 20 : data_size    u32  = w*h*4
#   off 24 : bit_count    u32  = 32
#   off 28 : red_mask     u32  = 0x000000FF
#   off 32 : green_mask   u32  = 0x0000FF00
#   off 36 : blue_mask    u32  = 0x00FF0000
#   off 40 : alpha_mask   u32  = 0xFF000000
#   off 44 : magic        'PVR!'
#   off 48 : num_surfaces u32  = 1
# Pixel data follows, raw width*height*4 RGBA bytes (row-major, top-to-bottom).
# ─────────────────────────────────────────────────────────────────────────────

def _encode_pvr_rgba8888(atlas: Image.Image) -> bytes:
    w, h = atlas.size
    pixels = atlas.convert('RGBA').tobytes()  # row-major RGBA
    data_size = w * h * 4
    header = struct.pack(
        '<11I4sI',
        52,                 # header_size
        h, w,               # height, width  (note: height comes first)
        0,                  # mip_count
        0x12,               # flags / pixel_type = RGBA8888
        data_size,          # data_size
        32,                 # bit_count
        0x000000FF,         # red_mask
        0x0000FF00,         # green_mask
        0x00FF0000,         # blue_mask
        0xFF000000,         # alpha_mask
        b'PVR!',            # magic at offset 44
        1,                  # num_surfaces
    )
    assert len(header) == 52
    return header + pixels


# ─────────────────────────────────────────────────────────────────────────────
# Atlas packing (shelf algorithm)
# ─────────────────────────────────────────────────────────────────────────────

def _next_pow2(n: int) -> int:
    p = 1
    while p < n:
        p <<= 1
    return p


def _pack_atlas(media_dir: Path, image_names: list[str]) -> tuple[Image.Image, dict]:
    """Shelf-pack each PNG in `media_dir` into a single atlas. Returns the
    composed PIL.Image plus a dict {name: (tex_x, tex_y, width, height)}.
    """
    sources = []
    for name in image_names:
        png_path = media_dir / f"{name}.png"
        if not png_path.exists():
            # Synthesize a 1×1 transparent placeholder
            sources.append((name, Image.new('RGBA', (1, 1), (0, 0, 0, 0))))
        else:
            sources.append((name, Image.open(png_path).convert('RGBA')))

    # Sort by descending height for better shelf packing
    sources.sort(key=lambda s: -s[1].height)

    # Pick atlas width: roughly the next-pow2 above the widest sprite OR
    # √(total area) — whichever's larger. Cap at 4096.
    max_w = max((img.width for _n, img in sources), default=1)
    total_area = sum(img.width * img.height for _n, img in sources)
    import math
    width = max(_next_pow2(max_w), _next_pow2(int(math.sqrt(total_area) * 1.1)))
    width = min(width, 4096)

    # Shelf placement
    placements = {}
    x = y = shelf_h = 0
    for name, img in sources:
        if x + img.width > width:
            x = 0
            y += shelf_h
            shelf_h = 0
        placements[name] = (x, y, img.width, img.height, img)
        x += img.width
        shelf_h = max(shelf_h, img.height)
    total_h = _next_pow2(y + shelf_h)

    atlas = Image.new('RGBA', (width, total_h), (0, 0, 0, 0))
    rects = {}
    for name, (px, py, pw, ph, img) in placements.items():
        atlas.paste(img, (px, py))
        rects[name] = (px, py, pw, ph)
    return atlas, rects


# ─────────────────────────────────────────────────────────────────────────────
# RawBin serializer
# ─────────────────────────────────────────────────────────────────────────────

def _pascal(s: str) -> bytes:
    b = s.encode('utf-8')[:255]
    return bytes([len(b)]) + b


def _write_rawbin(images: list[dict],
                  movie_clips: list[dict],
                  actions: list[dict]) -> bytes:
    """Build the RawBin byte stream. Layout mirrors parsers/rawbin_parser.py
    with clip_header_size=6, frame header = 4 bytes (2 pad + uint16 ne),
    element = 38 bytes."""
    buf = bytearray()

    # Images: int16 count + per-image (pascal name + 8 floats)
    buf += struct.pack('<h', len(images))
    for img in images:
        buf += _pascal(img['name'])
        buf += struct.pack('<8f',
                           img['offset_x'], img['offset_y'],
                           img['width'],    img['height'],
                           img['tex_x'],    img['tex_y'],
                           img.get('origin_x', 0.0),
                           img.get('origin_y', 0.0))

    # Export table (MC names)
    buf += struct.pack('<h', len(movie_clips))
    for mc in movie_clips:
        buf += _pascal(mc['name'])

    # Actions
    buf += struct.pack('<h', len(actions))
    for a in actions:
        buf += _pascal(a['name'])
        buf += struct.pack('<4h',
                           int(a['start']),
                           int(a['end']),
                           int(a['mc_idx']),
                           int(a.get('p4', 0)))

    # Movie clips
    for ci, mc in enumerate(movie_clips):
        frames = mc['frames']
        # Clip header: 4 bytes total = u16 num_frames + u16 pad.
        # The PvZ runtime expects the SHORTER 4-byte header (matches
        # samples/1.0.4/zombie_tutorial.bin, which loads in-game). The parser
        # also accepts a 6-byte variant, but writing 6 bytes here crashes the
        # game.
        buf += struct.pack('<HH', len(frames), 0)
        for frame in frames:
            # Frame header: 2 bytes pad + u16 num_elements
            buf += struct.pack('<HH', 0, len(frame))
            for elem in frame:
                mc_id = elem['mc_id']
                frame_in_mc = elem['frame_index']
                # Element: mc_id (1B) + frame_in_mc (1B) + extra (float 4B) +
                # matrix (6 floats 24B) + color_mult (4B) + color_add (4B)
                buf += bytes([mc_id, frame_in_mc])
                buf += struct.pack('<f', 0.0)  # _extra
                buf += struct.pack('<6f', *elem['matrix'])
                cm = elem.get('color_mult')
                if cm is None or len(cm) != 4:
                    a255 = max(0, min(255, int(round(elem.get('alpha', 1.0) * 255))))
                    cm = bytes([255, 255, 255, a255])
                buf += bytes(cm)
                ca = elem.get('color_add')
                if ca is None or len(ca) != 4:
                    ca = bytes([0, 0, 0, 0])
                buf += bytes(ca)
    return bytes(buf)


# ─────────────────────────────────────────────────────────────────────────────
# FBIN serializer
# ─────────────────────────────────────────────────────────────────────────────
# RawBin stores the element's MC/image index in a single byte (`frame_in_mc`),
# so it can only address 256 movie-clips / images. Big characters like
# MegaGatling (373 MCs, 317 images) overflow it — ~34% of their sprite
# references get silently dropped, which is the "lots of missing assets" bug.
# FBIN stores `elem_id` and `frame_index` as 2-byte shorts (matches
# parsers/fbin_parser.py:_read_element), so it has no such limit. We emit FBIN
# only when a character exceeds RawBin's 256 limit; smaller characters keep the
# proven 4-byte-header RawBin output the game is known to accept.
#
# Layout written here (the `num_versions=2, has_transform=True, order=A`
# variant the parser tries first — see fbin_parser._parse_impl):
#   'FBIN' + 2×int32 version[1,0]
#   + int32 ext_v1(0) + minfloat ext_float(1.0, divisor 100)
#   + images: int16 count, per image (pascal name + 8 minfloats, divisor 100)
#   + export table: int16 count, per MC pascal name
#   + actions: int16 count, per action (pascal name + 4×int16 start/end/mc/p4)
#   + per MC: int16 num_frames + int16 clip_index + int16 frame_rate,
#       then per frame: int16 pad + int16 num_elements, then elements.
# Element: u8 flag + u8 type(1=mc) + int16 id [+ int16 frame_index]
#   + 4 linear minfloats(÷10000) + 2 pos minfloats(÷100) + color_mult/add(8B).

def _write_float_min(buf: bytearray, value: float, divisor: float) -> None:
    """Append a MinBin-encoded float (inverse of InputBuffer.read_float_min)."""
    scaled = int(round(value * divisor))
    if scaled == 0:
        buf.append(0)
    elif -128 <= scaled <= 127:
        buf.append(1); buf += struct.pack('<b', scaled)
    elif -32768 <= scaled <= 32767:
        buf.append(2); buf += struct.pack('<h', scaled)
    else:
        buf.append(3); buf += struct.pack('<i', scaled)


def _write_fbin_element(buf: bytearray, elem: dict) -> None:
    is_mc = bool(elem.get('is_mc'))
    eid   = int(elem['id'])
    fidx  = int(elem.get('frame_index', -1))
    sx, ky, kx, sy, tx, ty = elem['matrix']

    # Always emit the linear terms, position, and colour. Emit frame_index only
    # for MC references (images ignore it; the renderer reads frame 0).
    flag = 0x04 | 0x08 | 0x10 | 0x20 | 0x40 | 0x80
    if is_mc and fidx >= 0:
        flag |= 0x02
    buf.append(flag)
    buf.append(1 if is_mc else 0)
    buf += struct.pack('<h', eid)
    if flag & 0x02:
        buf += struct.pack('<h', fidx)
    _write_float_min(buf, sx, 10000.0)
    _write_float_min(buf, ky, 10000.0)
    _write_float_min(buf, kx, 10000.0)
    _write_float_min(buf, sy, 10000.0)
    _write_float_min(buf, tx, 100.0)
    _write_float_min(buf, ty, 100.0)
    cm = elem.get('color_mult')
    if cm is None or len(cm) != 4:
        a255 = max(0, min(255, int(round(elem.get('alpha', 1.0) * 255))))
        cm = bytes([255, 255, 255, a255])
    buf += bytes(cm)
    ca = elem.get('color_add')
    if ca is None or len(ca) != 4:
        ca = bytes([0, 0, 0, 0])
    buf += bytes(ca)


def _write_fbin(images: list[dict],
                movie_clips: list[dict],
                actions: list[dict]) -> bytes:
    buf = bytearray()
    buf += b'FBIN'
    buf += struct.pack('<ii', 1, 0)            # version ints
    buf += struct.pack('<i', 0)                # ext_v1 (ignored by parser)
    _write_float_min(buf, 1.0, 100.0)          # ext_float = 1.0 (no world scale)

    # Images: int16 count + per image (pascal name + 8 minfloats ÷100)
    buf += struct.pack('<h', len(images))
    for img in images:
        buf += _pascal(img['name'])
        for v in (img['offset_x'], img['offset_y'], img['width'], img['height'],
                  img['tex_x'], img['tex_y'],
                  img.get('origin_x', 0.0), img.get('origin_y', 0.0)):
            _write_float_min(buf, float(v), 100.0)

    # Export table (MC names)
    buf += struct.pack('<h', len(movie_clips))
    for mc in movie_clips:
        buf += _pascal(mc['name'])

    # Actions
    buf += struct.pack('<h', len(actions))
    for a in actions:
        buf += _pascal(a['name'])
        buf += struct.pack('<4h', int(a['start']), int(a['end']),
                           int(a['mc_idx']), int(a.get('p4', 0)))

    # Movie clips
    for ci, mc in enumerate(movie_clips):
        frames = mc['frames']
        buf += struct.pack('<hhh', len(frames), ci, 30)   # count, index, fps
        for frame in frames:
            buf += struct.pack('<hh', 0, len(frame))       # pad + num_elements
            for elem in frame:
                _write_fbin_element(buf, elem)
    return bytes(buf)


# ─────────────────────────────────────────────────────────────────────────────
# World-space bbox + content centring (both formats)
# ─────────────────────────────────────────────────────────────────────────────
# A character's source anchor (the lawn tile / spawn origin) usually sits
# ~200 px off the centre of its artwork, so the bin renders off-centre. We bake
# a one-time translation into the action-root (label) MCs so the content is
# centred on the world origin — the player maps the origin to the screen centre,
# so the character lands centred without any runtime auto-centring. The walkers
# mirror renderer.draw's compose + dispatch (FBIN: id is a direct index;
# RawBin: mc_id is a dispatch route — see render/renderer.py:263-297).

def _expand_image_bbox(img_idx: int, images: list,
                       matrix: tuple, acc: list) -> None:
    if not (0 <= img_idx < len(images)):
        return
    na, nb, nc, nd, ntx, nty = matrix
    img = images[img_idx]
    w  = float(img.get('width', 0));   h  = float(img.get('height', 0))
    ox = float(img.get('offset_x', 0)); oy = float(img.get('offset_y', 0))
    for lx, ly in ((ox, -oy), (ox + w, -oy), (ox + w, -oy - h), (ox, -oy - h)):
        wx = na * lx + nc * ly + ntx
        wy = nb * lx + nd * ly + nty
        if wx < acc[0]: acc[0] = wx
        if wy < acc[1]: acc[1] = wy
        if wx > acc[2]: acc[2] = wx
        if wy > acc[3]: acc[3] = wy
        acc[4] = True


def _compose(parent: tuple, local: tuple) -> tuple:
    pa, pb, pc, pd, ptx, pty = parent
    la, lb, lc, ld, ltx, lty = local
    return (pa * la + pc * lb, pb * la + pd * lb,
            pa * lc + pc * ld, pb * lc + pd * ld,
            pa * ltx + pc * lty + ptx, pb * ltx + pd * lty + pty)


def _fbin_world_bbox(mc_idx: int, frame_idx: int,
                     images: list, movie_clips: list,
                     parent: tuple, acc: list,
                     depth: int = 0, visited: frozenset = frozenset()) -> None:
    if depth > 32 or not (0 <= mc_idx < len(movie_clips)) or mc_idx in visited:
        return
    visited = visited | {mc_idx}
    frames = movie_clips[mc_idx]['frames']
    if not frames:
        return
    fi = max(0, min(frame_idx, len(frames) - 1))
    for el in frames[fi]:
        child = _compose(parent, el['matrix'])
        if el['is_mc']:
            cf = el.get('frame_index', -1)
            _fbin_world_bbox(el['id'], cf if cf >= 0 else 0,
                             images, movie_clips, child, acc, depth + 1, visited)
        else:
            _expand_image_bbox(el['id'], images, child, acc)


def _rawbin_world_bbox(mc_idx: int, frame_num: int,
                       images: list, movie_clips: list,
                       parent: tuple, acc: list,
                       depth: int = 0, visited: frozenset = frozenset()) -> None:
    if depth > 32 or not (0 <= mc_idx < len(movie_clips)) or mc_idx in visited:
        return
    visited = visited | {mc_idx}
    frames = movie_clips[mc_idx]['frames']
    if not frames:
        return
    fi = max(0, min(frame_num, len(frames) - 1))
    for el in frames[fi]:
        eid = el['mc_id']
        cf  = el['frame_index']
        child = _compose(parent, el['matrix'])
        if not (0 <= eid < len(movie_clips)):
            continue
        if eid == 1 and cf >= 0:
            if cf < len(movie_clips):
                _rawbin_world_bbox(cf, fi, images, movie_clips, child,
                                   acc, depth + 1, visited)
            elif cf < len(images):
                _expand_image_bbox(cf, images, child, acc)
        elif 0 <= cf < len(images):
            _expand_image_bbox(cf, images, child, acc)
        elif len(movie_clips[eid]['frames']) == 1 and cf >= 0:
            if cf < len(movie_clips):
                _rawbin_world_bbox(cf, fi, images, movie_clips, child,
                                   acc, depth + 1, visited)
        else:
            _rawbin_world_bbox(eid, cf if cf >= 0 else fi, images, movie_clips,
                               child, acc, depth + 1, visited)


def _center_actions(images: list, movie_clips: list,
                    actions: list, use_fbin: bool) -> None:
    """Centre each action on the world origin, baked into the bin. Mirrors the
    player's old per-action auto-centre: for each action-root (label) MC, take
    the union bbox over ALL its frames and shift every frame's top-level
    elements so that union centres on the origin. Per-action (not one global
    offset) so a wide action like `walk` doesn't drag the resting `idle` pose
    off-centre."""
    walk = _fbin_world_bbox if use_fbin else _rawbin_world_bbox
    done = set()
    for a in actions:
        mc = a['mc_idx']
        if not (0 <= mc < len(movie_clips)) or mc in done:
            continue
        done.add(mc)
        frames = movie_clips[mc]['frames']
        if not frames:
            continue
        acc = [float('inf'), float('inf'), float('-inf'), float('-inf'), False]
        for fi in range(len(frames)):
            walk(mc, fi, images, movie_clips,
                 (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), acc)
        if not acc[4]:
            continue
        dx = -(acc[0] + acc[2]) * 0.5
        dy = -(acc[1] + acc[3]) * 0.5
        for frame in frames:
            for el in frame:
                a_, b_, c_, d_, tx, ty = el['matrix']
                el['matrix'] = (a_, b_, c_, d_, tx + dx, ty + dy)


# ─────────────────────────────────────────────────────────────────────────────
# Main per-character conversion
# ─────────────────────────────────────────────────────────────────────────────

def convert_one(xfl_root: Path, out_dir: Path, stem: str) -> None:
    lib = xfl_root / 'library'
    image_dir  = lib / 'image'
    sprite_dir = lib / 'sprite'
    label_dir  = lib / 'label'
    media_dir  = lib / 'media'

    if not image_dir.is_dir() or not media_dir.is_dir():
        raise FileNotFoundError(f"Missing library/image or library/media in {xfl_root}")

    # ── 1. Build the image table + capture each image symbol's inner matrix ──
    # The inner matrix (scale + translation inside the image symbol) gets
    # folded into every outer instance matrix in step 3, so the bin's
    # offset_x/y can stay 0 — all positioning is carried by the element
    # matrices the renderer applies directly.
    image_names_unsorted = sorted(p.stem for p in image_dir.glob('*.xml'))
    image_index = {name: i for i, name in enumerate(image_names_unsorted)}
    image_sym_xforms = {
        name: _parse_image_symbol_matrix(image_dir / f"{name}.xml")
        for name in image_names_unsorted
    }
    images = []
    for name in image_names_unsorted:
        png_path = media_dir / f"{name}.png"
        if png_path.exists():
            with Image.open(png_path) as im:
                w, h = im.size
        else:
            w = h = 1
        images.append({
            'name':     name,
            'offset_x': 0.0, 'offset_y': 0.0,  # carried by element matrices
            'width':    float(w), 'height': float(h),
            'tex_x':    0.0, 'tex_y':  0.0,    # filled in by atlas packer
            'origin_x': 0.0, 'origin_y': 0.0,
        })

    # ── 2. Pack the atlas, fill in tex_x / tex_y ─────────────────────────────
    atlas, rects = _pack_atlas(media_dir, image_names_unsorted)
    for img in images:
        rect = rects.get(img['name'])
        if rect is not None:
            tx, ty, _w, _h = rect
            img['tex_x'] = float(tx)
            img['tex_y'] = float(ty)

    # ── 3. Build movie_clips list — sprites first, then labels ──────────────
    sprite_names = sorted(p.stem for p in sprite_dir.glob('*.xml')) if sprite_dir.is_dir() else []
    label_names  = sorted(p.stem for p in label_dir.glob('*.xml'))  if label_dir.is_dir()  else []

    movie_clips = []
    mc_index_by_sprite = {}
    for name in sprite_names:
        mc_index_by_sprite[name] = len(movie_clips)
        frames_intermediate = _timeline_frames(sprite_dir / f"{name}.xml",
                                               image_sym_xforms)
        movie_clips.append({'name': name, 'frames_im': frames_intermediate})

    mc_index_by_label = {}
    for name in label_names:
        mc_index_by_label[name] = len(movie_clips)
        frames_intermediate = _timeline_frames(label_dir / f"{name}.xml",
                                               image_sym_xforms)
        movie_clips.append({'name': name, 'frames_im': frames_intermediate})

    # ── 3b. Armor-4 detection: hermit_crab wrapper ──────────────────────────
    # The PvZ runtime treats armor 4 differently from armors 1-3 / cone /
    # bucket. Reference: samples/zombie_kungfu_basic.bin (FBIN) has a state
    # wrapper named `_zombie_egypt_armor4_states` (underscore prefix, `_states`
    # suffix, theme-prefixed) that is NOT referenced from any action label —
    # the game injects it at runtime when armor 4 is equipped on the zombie.
    #
    # In the atlantis source package the equivalent wrapper is called
    # `hermit_crab` (no prefix, no suffix) AND it IS in every label, so the
    # game treats it as a regular always-visible sprite — the crab shell is
    # always worn. Convert this to the armor-4 convention:
    #   1. Rename the wrapper MC to `_zombie_<theme>_armor4_states`.
    #   2. Strip element references to it from every label MC, so the game
    #      can add it back dynamically when armor 4 is selected.
    # The wrapper's own contents (shells + arms + face layers) stay intact —
    # they render together when the game injects the wrapper.
    armor4_idx = None
    if ('hermit_crab' in mc_index_by_sprite
            and 'hermit_crab_shell_01' in mc_index_by_sprite
            and 'hermit_crab_shell_02' in mc_index_by_sprite
            and 'hermit_crab_shell_03' in mc_index_by_sprite):
        armor4_idx = mc_index_by_sprite['hermit_crab']
        parts = stem.split('_')
        theme = parts[1] if (len(parts) >= 2 and parts[0] == 'zombie') else stem
        wrapper_name = f'_zombie_{theme}_armor4_states'
        movie_clips[armor4_idx]['name'] = wrapper_name
        mc_index_by_sprite[wrapper_name] = armor4_idx

    # ── 4. Lower intermediate elements -> RawBin or FBIN ────────────────────
    # RawBin addresses MC/image indices with a single byte (max 256). When a
    # character has more than 256 movie-clips OR images, those references
    # overflow and get dropped (the "lots of missing assets" bug on big
    # characters like MegaGatling, 373 MCs). Switch to FBIN — whose element
    # ids are 2-byte shorts — for those. Smaller characters keep RawBin, whose
    # in-game compatibility is proven.
    use_fbin = (len(movie_clips) > 256) or (len(images) > 256)
    label_mc_indices = set(mc_index_by_label.values())
    for mc_idx, mc in enumerate(movie_clips):
        is_label_mc = mc_idx in label_mc_indices
        lowered_frames = []
        for frame in mc['frames_im']:
            lowered = []
            for payload in frame:
                target = payload['target']
                kind, _, sym = target.partition('/')
                if kind == 'sprite':
                    if sym not in mc_index_by_sprite and sym not in mc_index_by_label:
                        continue  # dangling — skip
                    idx = mc_index_by_sprite.get(sym, mc_index_by_label.get(sym))
                    # Strip armor-4 wrapper from label timelines — the game
                    # injects it at runtime when armor 4 is equipped. Leaving
                    # it in every label frame makes the zombie wear the crab
                    # always.
                    if is_label_mc and armor4_idx is not None and idx == armor4_idx:
                        continue
                    # Strip trigger-only overlay sprites (butter / ink /
                    # red_eyes) from action timelines. In-game these aren't
                    # drawn by default — the runtime injects them when a state
                    # triggers (a buttered zombie, the ink-cloud / glowing-eye
                    # rage state). The source XFL references them on dedicated
                    # label layers in every frame; keeping that bakes them in as
                    # always-visible. The MC stays in the library so a trigger
                    # can still show it; we only drop the default reference.
                    if is_label_mc and sym.lower() in _TRIGGER_OVERLAY_NAMES:
                        continue
                    is_mc_elem = True
                    elem_id    = idx
                elif kind == 'image':
                    if sym not in image_index:
                        continue
                    is_mc_elem = False
                    elem_id    = image_index[sym]
                else:
                    continue

                if use_fbin:
                    # FBIN: id is the MC/image index directly; frame_index is
                    # the referenced sprite's sub-frame (firstFrame).
                    lowered.append({
                        'is_mc':       is_mc_elem,
                        'id':          elem_id,
                        'frame_index': payload.get('first_frame', 0) if is_mc_elem else -1,
                        'matrix':      payload['matrix'],
                        'alpha':       payload['alpha'],
                    })
                else:
                    if elem_id > 255:
                        # RawBin only has 1 byte for the index — skip overflow.
                        continue
                    lowered.append({
                        'mc_id':       1 if is_mc_elem else 2,
                        'frame_index': elem_id,
                        'matrix':      payload['matrix'],
                        'alpha':       payload['alpha'],
                    })
            lowered_frames.append(lowered)
        mc['frames'] = lowered_frames
        del mc['frames_im']

    # ── 5. Actions: map label name -> mc_idx; start/end are GLOBAL  ─────────
    raw_actions = _parse_actions(xfl_root / 'DOMDocument.xml')
    actions = []
    for a in raw_actions:
        mci = mc_index_by_label.get(a['name'])
        if mci is None:
            continue
        actions.append({
            'name':   a['name'],
            'start':  a['start'],
            'end':    a['start'] + a['duration'] - 1,
            'mc_idx': mci,
            'p4':     0,
        })

    # ── 5b. Centre content on the world origin (both formats) ───────────────
    # Bake the centring into the action-root MCs so the character renders
    # centred without any runtime auto-centring in the player.
    _center_actions(images, movie_clips, actions, use_fbin)

    # ── 6. Write outputs ─────────────────────────────────────────────────────
    out_dir.mkdir(parents=True, exist_ok=True)
    bin_path = out_dir / f"{stem}.bin"
    pvr_path = out_dir / f"{stem}.pvr"
    if use_fbin:
        bin_path.write_bytes(_write_fbin(images, movie_clips, actions))
        fmt = "FBIN"
    else:
        bin_path.write_bytes(_write_rawbin(images, movie_clips, actions))
        fmt = "RawBin"
    pvr_path.write_bytes(_encode_pvr_rgba8888(atlas))
    print(f"  -> {bin_path.name}  [{fmt}]  ({len(images)} images, "
          f"{len(movie_clips)} clips, {len(actions)} actions)")
    print(f"  -> {pvr_path.name}  ({atlas.size[0]}x{atlas.size[1]} RGBA8888)")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Convert a .package (or XFL folder) back to RawBin + PVR "
                    "atlas (iOS PVR v2, RGBA8888), playable in main.py. Group "
                    "packages with multiple characters get one .bin/.pvr pair "
                    "per character.")
    p.add_argument('--package', required=True,
                   help="Path to a .package directory, or to the XFL folder "
                        "directly (the one containing main.xfl).")
    p.add_argument('--out', help="Output directory (default: alongside input)")
    args = p.parse_args()

    inp = Path(args.package).resolve()
    if not inp.exists():
        print(f"Error: no such path '{inp}'"); sys.exit(1)

    xfl_roots = _find_xfl_roots(inp)
    out_dir = Path(args.out).resolve() if args.out else inp.parent

    for root in xfl_roots:
        stem = root.name
        print(f"Converting -> {stem}")
        convert_one(root, out_dir, stem)


if __name__ == '__main__':
    main()
