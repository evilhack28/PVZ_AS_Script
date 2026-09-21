"""Reverse of convert_to_package.py: take a Flash CS5 .package"""
from __future__ import annotations

import argparse
import math
import os
import struct
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import _paths  # noqa: F401

from PIL import Image

XFL_NS = '{http://ns.adobe.com/xfl/2008/}'

# Sprite symbol names that are trigger-only overlays
_TRIGGER_OVERLAY_NAMES = frozenset({'butter', 'ink', 'red_eyes'})


# ─────────────────────────────────────────────────────────────────────────────
# XFL root resolution
# ─────────────────────────────────────────────────────────────────────────────

def _find_xfl_roots(input_path: Path) -> list[Path]:
    """Return one or more directories that contain a main.xfl + DOMDocument.xml."""
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
    """Flash Y-down (a,b,c,d,tx,ty) -> Cocos Y-up (sx,ky,kx,sy,tx,ty)."""
    a, b, c, d, tx, ty = m
    return (a, -b, -c, d, tx, -ty)


def _parse_instance(instance: ET.Element,
                    image_sym_xforms: dict) -> dict | None:
    """Convert a <DOMSymbolInstance> into an intermediate dict {target: 'sprite/foo'|'image/bar', matrix"""
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
    first_frame = 0
    ff = instance.get('firstFrame')
    if ff is not None:
        try:
            first_frame = max(0, int(ff))
        except ValueError:
            first_frame = 0
    # An instance blend mode of "add" is the game's additive blend flag.
    blend = (instance.get('blendMode') == 'add')
    return {'target': libname, 'matrix': matrix, 'alpha': alpha,
            'first_frame': first_frame, 'blend': blend}


# ─────────────────────────────────────────────────────────────────────────────
# Timeline (sprite or label) -> per-frame element lists
# ─────────────────────────────────────────────────────────────────────────────

def _timeline_frames(symbol_xml: Path,
                     image_sym_xforms: dict) -> list[list[dict]]:
    """Parse a sprite/<x>.xml or label/<x>.xml and return frames[i] = list of intermediate element dicts"""
    tree = ET.parse(symbol_xml)
    root = tree.getroot()
    timeline = root.find(f'.//{XFL_NS}DOMTimeline')
    if timeline is None:
        return [[]]
    layers_node = timeline.find(f'{XFL_NS}layers')
    if layers_node is None:
        return [[]]

    # Determine total frame count from the maximum (index + duration) across all layers' DOMFrames.
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
    # convert_to_package emits highest layer number first.
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
    """Read the full <Matrix a b c d tx ty/> from an image symbol."""
    tree = ET.parse(image_xml)
    root = tree.getroot()
    return _read_flash_matrix(root.find(f'.//{XFL_NS}Matrix'))


# ─────────────────────────────────────────────────────────────────────────────
# DOMDocument -> actions
# ─────────────────────────────────────────────────────────────────────────────

def _parse_actions(domdoc_xml: Path) -> list[dict]:
    """Read the root timeline's `label` layer for named action markers."""
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
# PVR v2 RGBA8888 encoder (matches pvr/pvr_loader.py's `_FMT_RGBA8888 = 0x12`) Header layout (52 bytes)
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


# Transparent gutter (px) between packed sprites.
_ATLAS_PAD = 2


def _pack_atlas(media_dir: Path, image_names: list[str]) -> tuple[Image.Image, dict]:
    """Shelf-pack each PNG in `media_dir` into a single atlas."""
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

    # Pick atlas width: roughly the next-pow2 above the widest sprite OR √(total area) — whichever's larger.
    max_w = max((img.width for _n, img in sources), default=1)
    total_area = sum((img.width + _ATLAS_PAD) * (img.height + _ATLAS_PAD)
                     for _n, img in sources)
    width = max(_next_pow2(max_w), _next_pow2(int(math.sqrt(total_area) * 1.1)))
    width = max(min(width, 4096), _next_pow2(max_w))

    # Shelf placement.
    placements = {}
    x = y = shelf_h = 0
    for name, img in sources:
        if x + img.width > width and x > 0:
            x = 0
            y += shelf_h + _ATLAS_PAD
            shelf_h = 0
        placements[name] = (x, y, img.width, img.height, img)
        x += img.width + _ATLAS_PAD
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


# One RawBin element (38 bytes), exactly what CCFlashDefine::ParseBin reads
_RAW_ELEM = struct.Struct('<BhBh6f4s4s')


def _elem_colors(elem: dict) -> tuple:
    cm = elem.get('color_mult')
    if cm is None or len(cm) != 4:
        a255 = max(0, min(255, int(round(elem.get('alpha', 1.0) * 255))))
        cm = bytes([255, 255, 255, a255])
    ca = elem.get('color_add')
    if ca is None or len(ca) != 4:
        ca = bytes([0, 0, 0, 0])
    return bytes(cm), bytes(ca)


def _write_rawbin(images: list[dict],
                  movie_clips: list[dict],
                  actions: list[dict]) -> bytes:
    """Build the RawBin byte stream the game's ParseBin reads (see parsers/game_bin.py)"""
    if len(images) > 0x7FFF or len(movie_clips) > 0x7FFF:
        raise ValueError("RawBin holds at most 32767 images / movie-clips")
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

    # Actions: frames played are (p4 + i) % mc_frames for i in 0..end-start
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
        # Clip header: u16 num_frames + u16 id (4 bytes).
        buf += struct.pack('<HH', len(frames), ci)
        for frame in frames:
            buf += struct.pack('<HH', 0, len(frame))    # unused + num_elements
            for elem in frame:
                cm, ca = _elem_colors(elem)
                buf += _RAW_ELEM.pack(
                    int(elem['type']), int(elem['id']),
                    1 if elem.get('blend') else 0,
                    int(elem.get('frame_index', 0)),
                    *elem['matrix'], cm, ca)
    return bytes(buf)


# ─────────────────────────────────────────────────────────────────────────────
# FBIN serializer
# ─────────────────────────────────────────────────────────────────────────────
# Optional (`--format fbin`).

def _write_float_min(buf: bytearray, value: float, divisor: float) -> None:
    """Append a MinBin float, inverse of the game's InputBuffer::ReadFloatMin"""
    scaled = int(round(value * divisor))
    if scaled == 0:
        buf.append(0)
    elif 0 < scaled <= 127:
        buf.append(1); buf.append(scaled)
    elif -32768 <= scaled <= 32767:
        buf.append(2); buf += struct.pack('<h', scaled)
    else:
        scaled = max(-2**31, min(2**31 - 1, scaled))
        buf.append(4); buf += struct.pack('<i', scaled)


def _q(value: float, divisor: float) -> float:
    """`value` after a MinBin round trip."""
    return round(value * divisor) / divisor


def _write_fbin_element(buf: bytearray, elem: dict) -> None:
    is_mc = bool(elem.get('is_mc'))
    eid   = int(elem['id'])
    fidx  = int(elem.get('frame_index', 0))
    a, b, c, d, tx, ty = elem['matrix']
    cm, ca = _elem_colors(elem)

    flags = 0
    if elem.get('blend'):                 flags |= 0x01
    if fidx != 0:                         flags |= 0x02
    if _q(a, 10000.0) != 1.0:             flags |= 0x04
    if _q(b, 10000.0) != 0.0:             flags |= 0x08
    if _q(c, 10000.0) != 0.0:             flags |= 0x10
    if _q(d, 10000.0) != 1.0:             flags |= 0x20
    if _q(tx, 100.0) != 0.0 or _q(ty, 100.0) != 0.0:
        flags |= 0x40
    if cm != bytes((255, 255, 255, 255)) or ca != bytes(4):
        flags |= 0x80

    buf.append(flags)
    buf.append(1 if is_mc else 2)
    buf += struct.pack('<h', eid)
    if flags & 0x02: buf += struct.pack('<h', fidx)
    if flags & 0x04: _write_float_min(buf, a, 10000.0)
    if flags & 0x08: _write_float_min(buf, b, 10000.0)
    if flags & 0x10: _write_float_min(buf, c, 10000.0)
    if flags & 0x20: _write_float_min(buf, d, 10000.0)
    if flags & 0x40:
        _write_float_min(buf, tx, 100.0)
        _write_float_min(buf, ty, 100.0)
    if flags & 0x80:
        buf += cm
        buf += ca


def _max_quads(movie_clips: list) -> list:
    """Per movie clip: the most images any single frame flattens to."""
    memo: dict = {}

    def count(mc_idx: int, fi: int, depth: int = 0) -> int:
        key = (mc_idx, fi)
        if key in memo:
            return memo[key]
        frames = movie_clips[mc_idx]['frames']
        if depth > 32 or not frames:
            return 0
        memo[key] = 0                                    # cycle guard
        n = 0
        for el in frames[fi % len(frames)]:
            if el['id'] < 0:
                continue
            n += (count(el['id'], el.get('frame_index', 0), depth + 1)
                  if el.get('is_mc') else 1)
        memo[key] = n
        return n

    return [max((count(i, f) for f in range(len(mc['frames']))), default=0)
            for i, mc in enumerate(movie_clips)]


def _write_fbin(images: list[dict],
                movie_clips: list[dict],
                actions: list[dict]) -> bytes:
    if len(images) > 0x7FFF or len(movie_clips) > 0x7FFF:
        raise ValueError("FBIN holds at most 32767 images / movie-clips")
    buf = bytearray()
    buf += b'FBIN'
    buf += struct.pack('<ii', 1, 0)            # version ints
    buf += struct.pack('<i', 0)                # ignored int
    _write_float_min(buf, 1.0, 100.0)          # world scale = 1.0

    # Images: int16 count + per image (pascal name + 8 MinBin floats /100)
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
    quads = _max_quads(movie_clips)
    for ci, mc in enumerate(movie_clips):
        frames = mc['frames']
        buf += struct.pack('<hhh', len(frames), ci,
                           max(1, min(0x7FFF, quads[ci])))
        for frame in frames:
            buf += struct.pack('<hh', 0, len(frame))
            for elem in frame:
                _write_fbin_element(buf, elem)
    return bytes(buf)


# ─────────────────────────────────────────────────────────────────────────────
# World-space bbox + content centring
# ─────────────────────────────────────────────────────────────────────────────
# Centre each action on the world origin (the source anchor sits ~200 px off-centre).

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


def _world_bbox(mc_idx: int, frame_idx: int,
                images: list, movie_clips: list,
                parent: tuple, acc: list,
                depth: int = 0, visited: frozenset = frozenset()) -> None:
    """Union bbox of every image drawn by MC `mc_idx` at `frame_idx`."""
    if depth > 32 or not (0 <= mc_idx < len(movie_clips)) or mc_idx in visited:
        return
    visited = visited | {mc_idx}
    frames = movie_clips[mc_idx]['frames']
    if not frames:
        return
    fi = max(0, min(frame_idx, len(frames) - 1))
    for el in frames[fi]:
        if el['id'] < 0:
            continue
        child = _compose(parent, el['matrix'])
        if el['is_mc']:
            _world_bbox(el['id'], el.get('frame_index', 0),
                        images, movie_clips, child, acc, depth + 1, visited)
        else:
            _expand_image_bbox(el['id'], images, child, acc)


def _center_actions(images: list, movie_clips: list,
                    actions: list) -> None:
    """Centre each action on the world origin, baked into the bin."""
    walk = _world_bbox
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

def convert_one(xfl_root: Path, out_dir: Path, stem: str,
                fmt: str = 'rawbin') -> None:
    lib = xfl_root / 'library'
    image_dir  = lib / 'image'
    sprite_dir = lib / 'sprite'
    label_dir  = lib / 'label'
    media_dir  = lib / 'media'

    if not image_dir.is_dir() or not media_dir.is_dir():
        raise FileNotFoundError(f"Missing library/image or library/media in {xfl_root}")

    # ── 1. Build the image table + capture each image symbol's inner matrix ──
    image_names_unsorted = sorted(p.stem for p in image_dir.glob('*.xml'))
    image_index = {name: i for i, name in enumerate(image_names_unsorted)}
    image_sym_xforms = {
        name: _parse_image_symbol_matrix(image_dir / f"{name}.xml")
        for name in image_names_unsorted
    }
    # width/height/tex_x/tex_y are filled in by the atlas packer below, which opens each PNG exactly once
    images = [{
        'name':     name,
        'offset_x': 0.0, 'offset_y': 0.0,  # carried by element matrices
        'width':    1.0, 'height': 1.0,
        'tex_x':    0.0, 'tex_y':  0.0,
        'origin_x': 0.0, 'origin_y': 0.0,
    } for name in image_names_unsorted]

    # ── 2. Pack the atlas, fill in size + tex_x / tex_y ──────────────────────
    atlas, rects = _pack_atlas(media_dir, image_names_unsorted)
    for img in images:
        rect = rects.get(img['name'])
        if rect is not None:
            tx, ty, w, h = rect
            img['tex_x'] = float(tx)
            img['tex_y'] = float(ty)
            img['width'] = float(w)
            img['height'] = float(h)

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

    # ── 4. Lower intermediate elements -> the game's element model ─────────
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
                    # Strip armor-4 wrapper from label timelines — the game injects it at runtime when armor 4 is equipped.
                    if is_label_mc and armor4_idx is not None and idx == armor4_idx:
                        continue
                    # Strip trigger-only overlay sprites (butter / ink / red_eyes) from action timelines.
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

                if elem_id > 0x7FFF:
                    continue                      # int16 id limit
                lowered.append({
                    'type':        1 if is_mc_elem else 2,
                    'is_mc':       is_mc_elem,
                    'id':          elem_id,
                    'frame_index': (min(0x7FFF, payload.get('first_frame', 0))
                                    if is_mc_elem else 0),
                    'blend':       payload.get('blend', False),
                    'matrix':      payload['matrix'],
                    'alpha':       payload['alpha'],
                })
            lowered_frames.append(lowered)
        mc['frames'] = lowered_frames
        del mc['frames_im']

    # ── 5. Actions: one per label. Each label MC holds exactly the frames of
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

    # ── 5b. Centre content on the world origin ──────────────────────────────
    _center_actions(images, movie_clips, actions)

    # ── 6. Write outputs ─────────────────────────────────────────────────────
    out_dir.mkdir(parents=True, exist_ok=True)
    bin_path = out_dir / f"{stem}.bin"
    pvr_path = out_dir / f"{stem}.pvr"
    if fmt == 'fbin':
        bin_path.write_bytes(_write_fbin(images, movie_clips, actions))
        fmt_name = "FBIN"
    else:
        bin_path.write_bytes(_write_rawbin(images, movie_clips, actions))
        fmt_name = "RawBin"
    pvr_path.write_bytes(_encode_pvr_rgba8888(atlas))
    print(f"  -> {bin_path.name}  [{fmt_name}]  ({len(images)} images, "
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
    p.add_argument('--format', choices=('rawbin', 'fbin'), default='rawbin',
                   help="Bin encoding (default: rawbin, the format known to "
                        "load in-game; both hold up to 32767 MCs / images).")
    args = p.parse_args()

    inp = Path(args.package).resolve()
    if not inp.exists():
        print(f"Error: no such path '{inp}'"); sys.exit(1)

    xfl_roots = _find_xfl_roots(inp)
    out_dir = Path(args.out).resolve() if args.out else inp.parent

    for root in xfl_roots:
        stem = root.name
        print(f"Converting -> {stem}")
        convert_one(root, out_dir, stem, args.format)


if __name__ == '__main__':
    main()
