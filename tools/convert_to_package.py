"""
convert_to_package.py
---------------------
Convert a Cocos2d-x FBIN / RawBin animation (bin + pvr/png atlas) into the
PvZ ".package" format used by the Plant/Zombie resource bundles.

Two layout flavors:
    --version 4  ->  PlantPeashooter_4.package layout
    --version 5  ->  PlantPeashooter_5.package layout

Both produce the same XFL tree inside:
    <PKG>/resource/images/initial/<TYPE_PATH>/<CHAR>/{,<CHAR>/}DOMDocument.xml
    .../library/{image,sprite,label,media}/

Usage:
    python convert_to_package.py --bin char.bin --pvr char.pvr        # both layouts
    python convert_to_package.py --bin char.bin --pvr char.pvr --version 4
    python convert_to_package.py --bin char.bin --pvr char.pvr --version 5

By default both v4 and v5 are emitted side-by-side. Pass --version to pick one.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import _paths  # noqa: F401


# ─────────────────────────────────────────────────────────────────────────────
# Atlas loader (same magic-byte sniffing as main.py)
# ─────────────────────────────────────────────────────────────────────────────

_PNG_MAGIC = b'\x89PNG\r\n\x1a\n'


def _load_atlas_pil(atlas_path: str):
    """Return a PIL.Image (RGBA) for the atlas, regardless of extension.
    PNG bytes are loaded directly; everything else goes through the project's
    PVR decoder which returns a pygame.Surface that we convert to PIL."""
    from PIL import Image

    with open(atlas_path, 'rb') as fh:
        head = fh.read(16)

    if head.startswith(_PNG_MAGIC):
        return Image.open(atlas_path).convert("RGBA")

    # PVR — decode via pygame then move pixels into PIL.
    import pygame
    pygame.init()
    try:
        pygame.display.set_mode((1, 1), pygame.NOFRAME)
    except pygame.error:
        pass
    from pvr_loader import load_pvr_texture
    surf = load_pvr_texture(atlas_path)
    if surf is None:
        raise RuntimeError(f"Could not decode PVR '{atlas_path}'")
    w, h = surf.get_width(), surf.get_height()
    raw = pygame.image.tostring(surf, "RGBA")
    return Image.frombytes("RGBA", (w, h), raw)


# ─────────────────────────────────────────────────────────────────────────────
# Matrix conversion: FBIN (Cocos Y-up) -> Flash XFL (Y-down)
#   a' = a, b' = -b, c' = -c, d' = d, tx' = tx, ty' = -ty
# ─────────────────────────────────────────────────────────────────────────────

def _flash_matrix(m):
    a, b, c, d, tx, ty = m
    return (a, -b, -c, d, tx, -ty)


def _fmt6(x: float) -> str:
    return f"{x:.6f}"


# ─────────────────────────────────────────────────────────────────────────────
# Name sanitisation
# ─────────────────────────────────────────────────────────────────────────────

_SAFE_CHARS = re.compile(r'[^A-Za-z0-9_]+')


def _safe_name(name: str, fallback: str) -> str:
    """Return a Flash-safe symbol name. Strips non-identifier chars and
    prepends `_` when the result would start with a digit (Flash CS5 and the
    PvZ packer reject symbol names that begin with `0-9`)."""
    s = _SAFE_CHARS.sub('_', (name or '').strip())
    if not s:
        return fallback
    if s[0].isdigit():
        s = '_' + s
    return s


def _ident_id(prefix: str, name: str) -> str:
    return prefix + name.upper()


# ─────────────────────────────────────────────────────────────────────────────
# Image XML / sprite XML / label XML emitters
# ─────────────────────────────────────────────────────────────────────────────

_XFL_HEAD = ('<DOMSymbolItem xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
             'xmlns="http://ns.adobe.com/xfl/2008/" '
             'name="{name}" symbolType="graphic">\n')


def _emit_image_symbol(symbol_name: str, media_name: str,
                       offset_x: float, offset_y: float) -> str:
    """image/NAME.xml — one DOMLayer with a single DOMBitmapInstance positioned
    so the symbol's (0,0) is the Flash registration point.

    Per the renderer (render/renderer.py:391-394) the bitmap CENTER in Cocos
    Y-up local space is `(offset_x + w/2, -offset_y - h/2)`. Y-flipping to
    Flash Y-down gives `(offset_x + w/2, offset_y + h/2)`, so the bitmap's
    top-left in symbol-local must be `(+offset_x, +offset_y)`. The previous
    `-offset_x, -offset_y` placed the bitmap on the opposite side of the
    registration whenever offsets were negative (e.g. applemortar IMG[3]
    `ox=-49.2`), which made nested sprites land far from their intended spot.
    """
    out = [
        _XFL_HEAD.format(name=f"image/{symbol_name}"),
        '    <timeline>\n',
        f'        <DOMTimeline name="{symbol_name}">\n',
        '            <layers>\n',
        '                <DOMLayer>\n',
        '                    <frames>\n',
        '                        <DOMFrame index="0">\n',
        '                            <elements>\n',
        f'                                <DOMBitmapInstance libraryItemName="media/{media_name}">\n',
        '                                    <matrix>\n',
        '                                        <Matrix a="1.000000" b="0.000000" c="0.000000" d="1.000000" '
        f'tx="{_fmt6(offset_x)}" ty="{_fmt6(offset_y)}"/>\n',
        '                                    </matrix>\n',
        '                                </DOMBitmapInstance>\n',
        '                            </elements>\n',
        '                        </DOMFrame>\n',
        '                    </frames>\n',
        '                </DOMLayer>\n',
        '            </layers>\n',
        '        </DOMTimeline>\n',
        '    </timeline>\n',
        '</DOMSymbolItem>\n',
    ]
    return ''.join(out)


def _element_payload(elem, images, movie_clips,
                     img_symbol_names, mc_symbol_names,
                     is_rawbin: bool = False):
    """Convert one element to (library_item_name,) — or None to skip.
    libraryItemName is prefixed with 'sprite/' or 'image/'.

    For RawBin the parser flags every element `is_mc=True` and stores the
    on-disk `mc_id` byte in `id`, but `mc_id` is a *dispatch route*, not a
    direct MC index. The real target lives in `frame_index`. Mirrors the
    renderer's RawBin branch (render/renderer.py:263-288):
        eid == 1 + fi in MC range   -> sprite for movie_clips[fi]
        eid == 1 + fi in image range -> image for images[fi]
        eid != 1 + fi in image range -> image for images[fi]
        else (1-frame MC reuse)      -> sprite for movie_clips[fi]
    Without this, every RawBin element is treated as `sprite/MC[mc_id]`,
    which routinely produces self-referential sprites (e.g. MC[1]
    `coconut_cloud_front` referencing `sprite/coconut_cloud_front`) and
    Flash CS5 hard-crashes trying to expand the cycle.
    """
    eid = elem.get('id', -1)
    if eid < 0:
        return None

    if is_rawbin:
        fi = elem.get('frame_index', -1)
        if eid == 1 and fi >= 0:
            if fi < len(movie_clips):
                sym = mc_symbol_names.get(fi)
                if sym is None:
                    return None
                return (f"sprite/{sym}",)
            if fi < len(images):
                sym = img_symbol_names.get(fi)
                if sym is None:
                    return None
                return (f"image/{sym}",)
            return None
        if 0 <= fi < len(images):
            sym = img_symbol_names.get(fi)
            if sym is None:
                return None
            return (f"image/{sym}",)
        if (0 <= eid < len(movie_clips)
                and len(movie_clips[eid].get('frames', [])) == 1
                and 0 <= fi < len(movie_clips)):
            sym = mc_symbol_names.get(fi)
            if sym is None:
                return None
            return (f"sprite/{sym}",)
        return None

    if elem.get('is_mc'):
        if 0 <= eid < len(movie_clips):
            sym = mc_symbol_names.get(eid)
            if sym is None:
                return None
            return (f"sprite/{sym}",)
        return None
    if 0 <= eid < len(images):
        sym = img_symbol_names.get(eid)
        if sym is None:
            return None
        return (f"image/{sym}",)
    return None


def _emit_dom_frame(index: int, duration: int, elem,
                    libname: str, is_image: bool) -> str:
    """One <DOMFrame> with a single child <DOMSymbolInstance>."""
    a, b, c, d, tx, ty = _flash_matrix(elem['matrix'])
    alpha = elem.get('alpha', 1.0)
    if alpha is None:
        alpha = 1.0
    alpha = max(0.0, min(1.0, float(alpha)))

    # Sprite vs image: image XMLs use plain DOMSymbolInstance without
    # firstFrame attr in the example, but sprite-instances include firstFrame.
    extra = '' if is_image else ' firstFrame="0"'
    out = [
        f'                        <DOMFrame index="{index}" duration="{duration}">\n',
        '                            <elements>\n',
        f'                                <DOMSymbolInstance libraryItemName="{libname}"{extra} symbolType="graphic" loop="loop">\n',
        '                                    <matrix>\n',
        '                                        <Matrix '
        f'a="{_fmt6(a)}" b="{_fmt6(b)}" c="{_fmt6(c)}" d="{_fmt6(d)}" '
        f'tx="{_fmt6(tx)}" ty="{_fmt6(ty)}"/>\n',
        '                                    </matrix>\n',
        '                                    <color>\n',
        f'                                        <Color redMultiplier="1.000000" greenMultiplier="1.000000" '
        f'blueMultiplier="1.000000" alphaMultiplier="{_fmt6(alpha)}"/>\n',
        '                                    </color>\n',
        '                                </DOMSymbolInstance>\n',
        '                            </elements>\n',
        '                        </DOMFrame>\n',
    ]
    return ''.join(out)


def _empty_dom_frame(index: int, duration: int) -> str:
    return (f'                        <DOMFrame index="{index}" duration="{duration}">\n'
            f'                            <elements/>\n'
            f'                        </DOMFrame>\n')


def _build_layers_xml(frames_subset: list,
                      images, movie_clips,
                      img_symbol_names, mc_symbol_names,
                      is_rawbin: bool = False) -> str:
    """Build the <layers>...</layers> block for a timeline whose frame list is
    `frames_subset` (a slice of mc['frames']).

    Element-slot tracking: position i in frame N corresponds to position i in
    frame N+1. Each slot becomes one DOMLayer. Consecutive identical frames in
    a layer are merged with `duration=N`. Layers in XML are emitted highest
    layer name first (Flash convention puts the topmost layer at the top of the
    XML)."""
    n_frames = len(frames_subset)
    if n_frames == 0:
        # Empty timeline: emit one placeholder layer with one empty frame so the
        # symbol is still well-formed (some XFL loaders reject <layers/>).
        return ('            <layers>\n'
                '                <DOMLayer name="1">\n'
                '                    <frames>\n'
                '                        <DOMFrame index="0" duration="1">\n'
                '                            <elements/>\n'
                '                        </DOMFrame>\n'
                '                    </frames>\n'
                '                </DOMLayer>\n'
                '            </layers>\n')

    max_slots = max((len(f) for f in frames_subset), default=0)

    if max_slots == 0:
        # Frames exist but all are empty (no elements) — emit a single layer with
        # one empty frame spanning the whole duration so loaders don't see
        # <layers/> or zero layers.
        return ('            <layers>\n'
                '                <DOMLayer name="1">\n'
                '                    <frames>\n'
                f'                        <DOMFrame index="0" duration="{n_frames}">\n'
                '                            <elements/>\n'
                '                        </DOMFrame>\n'
                '                    </frames>\n'
                '                </DOMLayer>\n'
                '            </layers>\n')

    def _payload_for(slot, fi):
        if slot >= len(frames_subset[fi]):
            return None
        elem = frames_subset[fi][slot]
        info = _element_payload(elem, images, movie_clips,
                                img_symbol_names, mc_symbol_names,
                                is_rawbin)
        if info is None:
            return None
        libname = info[0]
        is_image = libname.startswith('image/')
        # Key used to detect "identical frame" (same instance + matrix + alpha)
        a, b, c, d, tx, ty = _flash_matrix(elem['matrix'])
        alpha = elem.get('alpha', 1.0)
        key = (libname, round(a, 6), round(b, 6), round(c, 6), round(d, 6),
               round(tx, 4), round(ty, 4), round(float(alpha or 1.0), 4))
        return (libname, is_image, elem, key)

    layer_blocks = []
    for slot in range(max_slots):
        # Pre-compute payloads per frame for this slot
        payloads = [_payload_for(slot, fi) for fi in range(n_frames)]

        frame_xml_parts = []
        fi = 0
        last_keyframe_idx = -1
        last_was_empty = False
        while fi < n_frames:
            payload = payloads[fi]

            # Find run-length of identical payload starting at fi
            run = 1
            while fi + run < n_frames:
                nxt = payloads[fi + run]
                # Empty matches empty; non-empty must match exactly
                if payload is None and nxt is None:
                    run += 1
                elif payload is not None and nxt is not None and payload[3] == nxt[3]:
                    run += 1
                else:
                    break

            if payload is None:
                # Empty span: only emit if the slot is going to come back
                # later or has come before. Otherwise trim trailing empties.
                # We always emit interior empties so frame indices stay in sync.
                has_later = any(p is not None for p in payloads[fi:])
                if last_keyframe_idx >= 0 or has_later:
                    frame_xml_parts.append(_empty_dom_frame(fi, run))
                    last_was_empty = True
            else:
                libname, is_image, elem, _key = payload
                frame_xml_parts.append(
                    _emit_dom_frame(fi, run, elem, libname, is_image))
                last_keyframe_idx = fi
                last_was_empty = False
            fi += run

        # Skip slot entirely if it has no content
        if not any(p is not None for p in payloads):
            continue

        layer_blocks.append((slot + 1, ''.join(frame_xml_parts)))

    # Highest layer number first in XML
    layer_blocks.sort(key=lambda lb: -lb[0])

    if not layer_blocks:
        # Every element resolved to None (e.g. all references were to filtered
        # or invalid MCs). Emit a placeholder so the symbol stays well-formed.
        return ('            <layers>\n'
                '                <DOMLayer name="1">\n'
                '                    <frames>\n'
                f'                        <DOMFrame index="0" duration="{n_frames}">\n'
                '                            <elements/>\n'
                '                        </DOMFrame>\n'
                '                    </frames>\n'
                '                </DOMLayer>\n'
                '            </layers>\n')

    out = ['            <layers>\n']
    for layer_name, frames_xml in layer_blocks:
        out.append(f'                <DOMLayer name="{layer_name}">\n')
        out.append('                    <frames>\n')
        out.append(frames_xml)
        out.append('                    </frames>\n')
        out.append('                </DOMLayer>\n')
    out.append('            </layers>\n')
    return ''.join(out)


def _emit_sprite_symbol(symbol_name: str, mc: dict,
                        images, movie_clips,
                        img_symbol_names, mc_symbol_names,
                        is_rawbin: bool = False) -> str:
    layers_xml = _build_layers_xml(mc['frames'], images, movie_clips,
                                   img_symbol_names, mc_symbol_names,
                                   is_rawbin)
    return (
        _XFL_HEAD.format(name=f"sprite/{symbol_name}") +
        '    <timeline>\n' +
        f'        <DOMTimeline name="{symbol_name}">\n' +
        layers_xml +
        '        </DOMTimeline>\n' +
        '    </timeline>\n' +
        '</DOMSymbolItem>\n'
    )


def _emit_label_symbol(symbol_name: str, frames_subset: list,
                       images, movie_clips,
                       img_symbol_names, mc_symbol_names,
                       is_rawbin: bool = False) -> str:
    layers_xml = _build_layers_xml(frames_subset, images, movie_clips,
                                   img_symbol_names, mc_symbol_names,
                                   is_rawbin)
    return (
        _XFL_HEAD.format(name=f"label/{symbol_name}") +
        '    <timeline>\n' +
        f'        <DOMTimeline name="{symbol_name}">\n' +
        layers_xml +
        '        </DOMTimeline>\n' +
        '    </timeline>\n' +
        '</DOMSymbolItem>\n'
    )


# ─────────────────────────────────────────────────────────────────────────────
# Bounding-box probe — figure out where on stage to plant the character
# ─────────────────────────────────────────────────────────────────────────────

class _BBox:
    __slots__ = ('minx', 'miny', 'maxx', 'maxy', 'valid')
    def __init__(self):
        self.minx = self.miny = float('inf')
        self.maxx = self.maxy = float('-inf')
        self.valid = False
    def expand(self, x: float, y: float):
        if x < self.minx: self.minx = x
        if x > self.maxx: self.maxx = x
        if y < self.miny: self.miny = y
        if y > self.maxy: self.maxy = y
        self.valid = True
    def union(self, other: '_BBox'):
        if not other.valid: return
        if other.minx < self.minx: self.minx = other.minx
        if other.miny < self.miny: self.miny = other.miny
        if other.maxx > self.maxx: self.maxx = other.maxx
        if other.maxy > self.maxy: self.maxy = other.maxy
        self.valid = True


def _probe_frame_bbox(mc_idx: int, frame_idx: int,
                      images: list, movie_clips: list,
                      is_rawbin: bool,
                      parent_matrix: tuple = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0),
                      depth: int = 0,
                      visited: frozenset = frozenset()) -> _BBox:
    """Walk the MC tree applying Flash-space matrices and accumulate the union
    bbox of every image draw. Pure-Python, no pygame needed. Used to find a
    sensible stage-center offset so the character actually lands on the canvas.
    """
    bbox = _BBox()
    if depth > 32 or not (0 <= mc_idx < len(movie_clips)) or mc_idx in visited:
        return bbox
    visited = visited | {mc_idx}
    mc = movie_clips[mc_idx]
    frames = mc.get('frames', [])
    if not frames:
        return bbox
    fi_clamped = max(0, min(frame_idx, len(frames) - 1))
    pa, pb, pc, pd, ptx, pty = parent_matrix

    for elem in frames[fi_clamped]:
        eid = elem.get('id', -1)
        if eid < 0:
            continue
        target_fi = elem.get('frame_index', -1)
        la, lb, lc, ld, ltx, lty = _flash_matrix(elem['matrix'])
        na  = pa * la + pc * lb
        nb  = pb * la + pd * lb
        nc  = pa * lc + pc * ld
        nd  = pb * lc + pd * ld
        ntx = pa * ltx + pc * lty + ptx
        nty = pb * ltx + pd * lty + pty
        child_matrix = (na, nb, nc, nd, ntx, nty)

        if is_rawbin:
            if eid == 1 and 0 <= target_fi < len(movie_clips):
                bbox.union(_probe_frame_bbox(target_fi, 0, images, movie_clips,
                                             is_rawbin, child_matrix,
                                             depth + 1, visited))
            elif 0 <= target_fi < len(images):
                bbox.union(_image_world_rect(target_fi, images, child_matrix))
        else:
            if elem.get('is_mc') and 0 <= eid < len(movie_clips):
                child_fi = target_fi if target_fi >= 0 else 0
                bbox.union(_probe_frame_bbox(eid, child_fi, images, movie_clips,
                                             is_rawbin, child_matrix,
                                             depth + 1, visited))
            elif 0 <= eid < len(images):
                bbox.union(_image_world_rect(eid, images, child_matrix))
    return bbox


def _image_world_rect(img_idx: int, images: list, matrix: tuple) -> _BBox:
    """Map the four corners of an image bitmap into Flash world coords and
    return their bbox. Bitmap top-left is at local (+offset_x, +offset_y);
    matches `_emit_image_symbol`'s convention."""
    img = images[img_idx]
    w  = float(img.get('width', 0))
    h  = float(img.get('height', 0))
    ox = float(img.get('offset_x', 0))
    oy = float(img.get('offset_y', 0))
    a, b, c, d, tx, ty = matrix
    bbox = _BBox()
    for (lx, ly) in ((ox, oy), (ox + w, oy), (ox + w, oy + h), (ox, oy + h)):
        bbox.expand(a * lx + c * ly + tx, b * lx + d * ly + ty)
    return bbox


# ─────────────────────────────────────────────────────────────────────────────
# DOMDocument.xml — root timeline that strings labels together
# ─────────────────────────────────────────────────────────────────────────────

def _emit_dom_document(media_names, image_symbols, sprite_symbols, label_actions,
                       frame_rate: int, doc_w: int = 390, doc_h: int = 390,
                       stage_offset: tuple = (0.0, 0.0)) -> str:
    """Root document. Labels are concatenated on the timeline; each gets a
    DOMSymbolInstance of label/NAME and a label marker on the label layer.

    label_actions: list of (label_name, duration). The label name is also the
    symbol name (label/<name>).
    """
    parts = []
    parts.append(
        f'<DOMDocument xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        f'xmlns="http://ns.adobe.com/xfl/2008/" backgroundColor="#999999" '
        f'frameRate="{frame_rate}" width="{doc_w:.6f}" height="{doc_h:.6f}" '
        f'xflVersion="2.971">\n')
    parts.append('    <folders>\n')
    parts.append('        <DOMFolderItem name="media" isExpanded="true"/>\n')
    parts.append('        <DOMFolderItem name="image" isExpanded="true"/>\n')
    parts.append('        <DOMFolderItem name="sprite" isExpanded="true"/>\n')
    parts.append('        <DOMFolderItem name="label" isExpanded="true"/>\n')
    parts.append('    </folders>\n')

    parts.append('    <media>\n')
    for m in media_names:
        parts.append(f'        <DOMBitmapItem name="media/{m}" href="media/{m}.png"/>\n')
    parts.append('    </media>\n')

    parts.append('    <symbols>\n')
    for s in image_symbols:
        parts.append(f'        <Include href="image/{s}.xml"/>\n')
    for s in sprite_symbols:
        parts.append(f'        <Include href="sprite/{s}.xml"/>\n')
    for label, _dur in label_actions:
        parts.append(f'        <Include href="label/{label}.xml"/>\n')
    parts.append('    </symbols>\n')

    parts.append('    <timelines>\n')
    parts.append('        <DOMTimeline name="animation">\n')
    parts.append('            <layers>\n')

    # Label layer: name markers at action start frames
    parts.append('                <DOMLayer name="label">\n')
    parts.append('                    <frames>\n')
    cursor = 0
    for label, dur in label_actions:
        parts.append(
            f'                        <DOMFrame index="{cursor}" duration="{dur}" '
            f'name="{label}" labelType="name">\n'
            f'                            <elements/>\n'
            f'                        </DOMFrame>\n')
        cursor += dur
    parts.append('                    </frames>\n')
    parts.append('                </DOMLayer>\n')

    # Action layer: stop(); at end of each segment.
    # For dur>=2 we split into a body frame (dur-1) plus a trailing stop
    # keyframe. For a 1-frame label the body+stop split would advance cursor
    # by only 1 while occupying 2 indices — colliding with the next label's
    # first frame. Emit just the stop keyframe in that case.
    parts.append('                <DOMLayer name="action">\n')
    parts.append('                    <frames>\n')
    cursor = 0
    for i, (_label, dur) in enumerate(label_actions):
        if dur >= 2:
            body_dur = dur - 1
            parts.append(
                f'                        <DOMFrame index="{cursor}" duration="{body_dur}">\n'
                f'                            <elements/>\n'
                f'                        </DOMFrame>\n')
            parts.append(
                f'                        <DOMFrame index="{cursor + body_dur}">\n'
                f'                            <Actionscript>\n'
                f'                                <script><![CDATA[stop();]]></script>\n'
                f'                            </Actionscript>\n'
                f'                            <elements/>\n'
                f'                        </DOMFrame>\n')
        else:
            parts.append(
                f'                        <DOMFrame index="{cursor}">\n'
                f'                            <Actionscript>\n'
                f'                                <script><![CDATA[stop();]]></script>\n'
                f'                            </Actionscript>\n'
                f'                            <elements/>\n'
                f'                        </DOMFrame>\n')
        cursor += dur
    parts.append('                    </frames>\n')
    parts.append('                </DOMLayer>\n')

    # Instance layer: one DOMSymbolInstance per label, shifted by stage_offset
    # so the character bounding box lands on-canvas instead of jammed in the
    # top-left corner (Cocos world origin is wherever the source picks it).
    sox, soy = stage_offset
    parts.append('                <DOMLayer name="instance">\n')
    parts.append('                    <frames>\n')
    cursor = 0
    for label, dur in label_actions:
        parts.append(
            f'                        <DOMFrame index="{cursor}" duration="{dur}">\n'
            f'                            <elements>\n'
            f'                                <DOMSymbolInstance libraryItemName="label/{label}" '
            f'symbolType="graphic" loop="loop">\n'
            f'                                    <matrix>\n'
            f'                                        <Matrix tx="{_fmt6(sox)}" ty="{_fmt6(soy)}"/>\n'
            f'                                    </matrix>\n'
            f'                                </DOMSymbolInstance>\n'
            f'                            </elements>\n'
            f'                        </DOMFrame>\n')
        cursor += dur
    parts.append('                    </frames>\n')
    parts.append('                </DOMLayer>\n')

    parts.append('            </layers>\n')
    parts.append('        </DOMTimeline>\n')
    parts.append('    </timelines>\n')
    parts.append('</DOMDocument>\n')
    return ''.join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# JSON data emitters
# ─────────────────────────────────────────────────────────────────────────────

def _top_data_v4(subgroup_name: str, resource_id: str, resource_path: str,
                 resolution: int = 1536) -> dict:
    return _top_data_v4_multi(subgroup_name,
                              [(resource_id, resource_path)],
                              resolution=resolution)


def _top_data_v5(subgroup_name: str, resource_id: str, resource_path: str,
                 resolution: int = 1536) -> dict:
    return _top_data_v5_multi(subgroup_name,
                              [(resource_id, resource_path)],
                              resolution=resolution)


def _top_data_v4_multi(subgroup_name: str,
                       resources: list,
                       resolution: int = 1536) -> dict:
    """v4 group/multi-resource top-data. `resources` is a list of
    (resource_id, resource_path) tuples — same layout as a single-character
    package, just with multiple entries in the resource dict."""
    return {
        "#expand_method": "advanced",
        "version": 4,
        "texture_format_category": 1,
        "composite": True,
        "category": {"resolution": [resolution], "format": 0},
        "subgroup": {
            subgroup_name: {
                "category": {"common_type": True, "locale": None, "compression": 3},
                "resource": {
                    rid: {"type": "PopAnim", "path": rpath}
                    for rid, rpath in resources
                },
            }
        },
    }


def _top_data_v5_multi(subgroup_name: str,
                       resources: list,
                       resolution: int = 1536) -> dict:
    """v5 group/multi-resource top-data. Mirrors ZombieTutorialGroup.package's
    layout: single subgroup, resource is a list of PopAnim entries."""
    return {
        "version": 5,
        "expand_data": True,
        "composite": True,
        "category": {"resolution": [resolution], "format": 0, "texture_format_category": 1},
        "subgroups": {
            subgroup_name: {
                "compression": 3,
                "common_type": True,
                "resource": [
                    {"type": "PopAnim", "id": rid, "path": rpath}
                    for rid, rpath in resources
                ],
            }
        },
    }


def _inner_data_v4(images: list, img_symbol_names: dict, id_prefix: str,
                   resolution: int = 1536,
                   sprite_names: list = None) -> dict:
    image_map = {}
    for i, img in enumerate(images):
        sym = img_symbol_names.get(i)
        if sym is None:
            continue
        image_map[sym] = {
            "id": _ident_id(id_prefix, sym),
            "dimension": {
                "width":  max(1, int(round(img.get('width', 0)))),
                "height": max(1, int(round(img.get('height', 0)))),
            },
            "additional": None,
        }
    # The PvZ packer reads the sprite field to enumerate sprite-symbol
    # resources for ID registration. Reference packages list every emitted
    # sprite under the empty-string key (default category). Leaving this
    # empty makes the packer skip every sprite — visible breakage on the
    # effect side of ZombieEgyptTombRaiserGroup.
    sprite_field = {"": list(sprite_names)} if sprite_names else {}
    return {
        "version": 6,
        "resolution": resolution,
        "position": {"x": 0, "y": 0},
        "image": image_map,
        "sprite": sprite_field,
    }


def _inner_data_v5(images: list, img_symbol_names: dict, id_prefix: str,
                   resolution: int = 1536,
                   sprite_names: list = None) -> dict:
    image_map = {}
    for i, img in enumerate(images):
        sym = img_symbol_names.get(i)
        if sym is None:
            continue
        image_map[sym] = {
            "id": _ident_id(id_prefix, sym),
            "width":  max(1, int(round(img.get('width', 0)))),
            "height": max(1, int(round(img.get('height', 0)))),
        }
    sprite_field = {"": list(sprite_names)} if sprite_names else {}
    return {
        "version": 6,
        "resolution": resolution,
        "position": {"x": 0, "y": 0},
        "image": image_map,
        "sprite": sprite_field,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Sprite extraction from atlas
# ─────────────────────────────────────────────────────────────────────────────

def _extract_sprite_png(atlas, img: dict):
    """Crop the atlas to the image's atlas-pixel rect. Returns a PIL.Image or
    None if the rect is degenerate / out of bounds."""
    tw, th = atlas.size
    tx = int(img.get('tex_x', 0))
    ty = int(img.get('tex_y', 0))
    w  = int(round(img.get('width', 0)))
    h  = int(round(img.get('height', 0)))
    if w <= 0 or h <= 0:
        return None
    if tx < 0 or ty < 0 or tx + w > tw or ty + h > th:
        return None
    return atlas.crop((tx, ty, tx + w, ty + h))


# ─────────────────────────────────────────────────────────────────────────────
# Main conversion
# ─────────────────────────────────────────────────────────────────────────────

def _write_character_assets(out_dir: Path, parsed: dict, atlas,
                            version: int, *,
                            type_path: str, char_name: str,
                            id_prefix: str, resource_id: str,
                            resolution: int = 1536) -> tuple:
    """Write everything BELOW the package's top-level data.json for one
    character: inner data.json, main.xfl, library/image|sprite|label|media,
    DOMDocument.xml. Returns (resource_id, resource_path) so the caller can
    assemble the top-level data.json (single OR group)."""
    images      = parsed['images']
    movie_clips = parsed['movie_clips']
    actions     = parsed['actions']
    is_rawbin   = bool(parsed.get('is_rawbin', False))

    # Symbol names (de-duplicated, sanitised)
    used = set()

    def _unique(name: str, prefix: str) -> str:
        base = _safe_name(name, prefix)
        candidate = base
        n = 2
        while candidate in used:
            candidate = f"{base}_{n}"
            n += 1
        used.add(candidate)
        return candidate

    # Image symbol names use the canonical "<char>_<W>x<H>" convention so the
    # output never inherits indexed FBIN names like "000_130x122". Duplicates
    # get a "_2", "_3", ... suffix (matches peashooter_21x17 / peashooter_21x17_2).
    img_symbol_names: dict = {}
    used_img: set = set()
    for i, im in enumerate(images):
        w = int(round(im.get('width', 0)))
        h = int(round(im.get('height', 0)))
        base = f"{char_name}_{w}x{h}"
        cand = base
        n = 2
        while cand in used_img:
            cand = f"{base}_{n}"
            n += 1
        used_img.add(cand)
        img_symbol_names[i] = cand

    # MC names — and exclude action-root MCs from sprite generation (they become
    # labels instead).
    action_mc_set = {a['mc_idx'] for a in actions
                     if 0 <= a.get('mc_idx', -1) < len(movie_clips)}

    mc_symbol_names: dict = {}
    used_mc: set = set(img_symbol_names.values())  # image names take precedence
    for i, mc in enumerate(movie_clips):
        if i in action_mc_set:
            continue  # action root MCs render via label/* — no sprite symbol
        base = _safe_name(mc.get('name', ''), f"sprite_{i}")
        cand = base
        n = 2
        while cand in used_mc:
            cand = f"{base}_{n}"
            n += 1
        used_mc.add(cand)
        mc_symbol_names[i] = cand

    # Action labels
    used_lbl: set = set()
    label_records = []  # list of (label_name, mc_idx, start, end)
    for a in actions:
        mc_idx = a.get('mc_idx', -1)
        if not (0 <= mc_idx < len(movie_clips)):
            continue
        base = _safe_name(a.get('name', ''), f"action_{mc_idx}")
        cand = base
        n = 2
        while cand in used_lbl:
            cand = f"{base}_{n}"
            n += 1
        used_lbl.add(cand)
        mc = movie_clips[mc_idx]
        last_frame = max(0, len(mc['frames']) - 1)
        # FBIN/RawBin actions encode start/end as positions in a concatenated
        # global playlist, not as local frame indices into their own MC. Detect
        # that case and collapse to the MC's full local range. Same heuristic
        # as the player's _clamp_action_range, plus `raw_start > last_frame`
        # which is unambiguous (a local start can't exceed the MC's last frame).
        raw_start = a.get('start', 0)
        raw_end   = a.get('end', last_frame)
        duration  = raw_end - raw_start
        is_global = (raw_start > last_frame
                     or duration > last_frame
                     or (raw_start > 0 and duration >= last_frame))
        if is_global:
            s, e = 0, last_frame
        else:
            s = max(0, min(raw_start, last_frame))
            e = max(0, min(raw_end, last_frame))
            if e < s:
                s, e = 0, last_frame
        label_records.append((cand, mc_idx, s, e))

    # Frame rate is always 30 in the output XFL regardless of what the source
    # MC declares — Flash projects in this pipeline are authored at 30 fps.
    frame_rate = 30

    # Make sure label/sprite name spaces don't collide (labels are siblings of
    # sprites in the symbol table — rename labels if they collide)
    seen_symbols = set(img_symbol_names.values()) | set(mc_symbol_names.values())
    final_label_records = []
    for label, mc_idx, s, e in label_records:
        cand = label
        n = 2
        while cand in seen_symbols:
            cand = f"{label}_{n}"
            n += 1
        seen_symbols.add(cand)
        final_label_records.append((cand, mc_idx, s, e))

    # Particle labels always sit at the end of the timeline. Source FBINs
    # commonly place "particles" mid-action list, but Flash projects expect
    # particle FX as a trailing label (matches ZombieTutorialGroup, peashooter).
    def _is_particle(rec):
        return rec[0].lower().startswith('particle')
    label_records = ([r for r in final_label_records if not _is_particle(r)]
                     + [r for r in final_label_records if _is_particle(r)])

    # ── Build directory tree ─────────────────────────────────────────────────
    out_dir.mkdir(parents=True, exist_ok=True)
    # Single-nested for both v4 and v5: images/initial/<type>/<char> (or
    # images/initial/<char> when type_path is empty). Drops the historical
    # v5 double-nest (`<char>/<char>`) so paths stay short.
    parts = ['images', 'initial']
    if type_path:
        parts.append(type_path)
    parts.append(char_name)
    char_root = out_dir.joinpath('resource', *parts)
    resource_path = '/'.join(parts)

    lib = char_root / "library"
    (lib / "image").mkdir(parents=True, exist_ok=True)
    (lib / "sprite").mkdir(parents=True, exist_ok=True)
    (lib / "label").mkdir(parents=True, exist_ok=True)
    (lib / "media").mkdir(parents=True, exist_ok=True)

    # ── Inner data.json (image table) ────────────────────────────────────────
    # Reference packages populate `sprite.""` ONLY for effect characters; the
    # zombie/plant reference leaves it as `{}`. Mirror that convention exactly
    # — the PvZ packer is picky about which characters declare top-level
    # sprite resources.
    if type_path == 'effects':
        sprite_names_for_inner = [mc_symbol_names[i]
                                  for i in range(len(movie_clips))
                                  if mc_symbol_names.get(i) is not None]
    else:
        sprite_names_for_inner = None
    inner = (_inner_data_v4 if version == 4 else _inner_data_v5)(
        images, img_symbol_names, id_prefix,
        resolution=resolution,
        sprite_names=sprite_names_for_inner)
    with open(char_root / "data.json", 'w', encoding='utf-8') as fh:
        json.dump(inner, fh, indent='\t')

    # ── main.xfl ─────────────────────────────────────────────────────────────
    with open(char_root / "main.xfl", 'w', encoding='utf-8') as fh:
        fh.write("PROXY-CS5")

    # ── library/media/<NAME>.png ─────────────────────────────────────────────
    media_names = []
    for i, img in enumerate(images):
        sym = img_symbol_names.get(i)
        if sym is None:
            continue
        sprite_png = _extract_sprite_png(atlas, img)
        if sprite_png is None:
            # Synthesize a 1x1 transparent placeholder so the symbol still loads
            from PIL import Image
            sprite_png = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        sprite_png.save(lib / "media" / f"{sym}.png", "PNG")
        media_names.append(sym)

    # ── library/image/<NAME>.xml ─────────────────────────────────────────────
    image_symbols_out = []
    for i, img in enumerate(images):
        sym = img_symbol_names.get(i)
        if sym is None:
            continue
        xml = _emit_image_symbol(sym, sym,
                                 float(img.get('offset_x', 0.0)),
                                 float(img.get('offset_y', 0.0)))
        with open(lib / "image" / f"{sym}.xml", 'w', encoding='utf-8') as fh:
            fh.write(xml)
        image_symbols_out.append(sym)

    # ── library/sprite/<NAME>.xml ────────────────────────────────────────────
    sprite_symbols_out = []
    for i, mc in enumerate(movie_clips):
        sym = mc_symbol_names.get(i)
        if sym is None:
            continue
        xml = _emit_sprite_symbol(sym, mc, images, movie_clips,
                                  img_symbol_names, mc_symbol_names,
                                  is_rawbin)
        with open(lib / "sprite" / f"{sym}.xml", 'w', encoding='utf-8') as fh:
            fh.write(xml)
        sprite_symbols_out.append(sym)

    # ── library/label/<NAME>.xml ─────────────────────────────────────────────
    label_actions_out = []  # (label_name, duration) for the root timeline
    for label, mc_idx, s, e in label_records:
        mc = movie_clips[mc_idx]
        frames_subset = mc['frames'][s:e + 1]
        xml = _emit_label_symbol(label, frames_subset, images, movie_clips,
                                 img_symbol_names, mc_symbol_names,
                                 is_rawbin)
        with open(lib / "label" / f"{label}.xml", 'w', encoding='utf-8') as fh:
            fh.write(xml)
        label_actions_out.append((label, max(1, len(frames_subset))))

    # ── Stage-centring offset ────────────────────────────────────────────────
    # Cocos world origin is wherever the source picks it (often the character's
    # foot or origin of the spawning system); after Y-flip the content can end
    # up jammed into Flash's top-left corner. Probe the first action's middle
    # frame, find its bbox centre in Flash space, and shift so that centre
    # lands at the stage centre.
    doc_w, doc_h = 390, 390
    sox = soy = 0.0
    if label_records:
        _lbl, probe_mc, probe_s, probe_e = label_records[0]
        probe_frame = (probe_s + probe_e) // 2
        bbox = _probe_frame_bbox(probe_mc, probe_frame, images, movie_clips,
                                 is_rawbin)
        if bbox.valid:
            sox = doc_w * 0.5 - (bbox.minx + bbox.maxx) * 0.5
            soy = doc_h * 0.5 - (bbox.miny + bbox.maxy) * 0.5

    # ── DOMDocument.xml ──────────────────────────────────────────────────────
    doc = _emit_dom_document(media_names, image_symbols_out, sprite_symbols_out,
                             label_actions_out, frame_rate,
                             doc_w=doc_w, doc_h=doc_h,
                             stage_offset=(sox, soy))
    with open(char_root / "DOMDocument.xml", 'w', encoding='utf-8') as fh:
        fh.write(doc)

    print(f"     [{char_name}] {len(images)} images, "
          f"{len(sprite_symbols_out)} sprites, "
          f"{len(label_actions_out)} labels")
    return (resource_id, resource_path)


def convert(bin_path: Path, pvr_path: Path, out_dir: Path,
            version: int, *, subgroup_name: str,
            type_path: str, char_name: str, id_prefix: str,
            resolution: int = 1536) -> None:
    """Single-character package. Top-level subgroup name == package name."""
    from fbin_parser import parse_binary

    parsed = parse_binary(str(bin_path))
    if parsed is None:
        raise RuntimeError(f"Failed to parse '{bin_path}'.")
    atlas = _load_atlas_pil(str(pvr_path))

    resource_id = f"POPANIM_{subgroup_name.upper()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    rid, rpath = _write_character_assets(
        out_dir, parsed, atlas, version,
        type_path=type_path, char_name=char_name,
        id_prefix=id_prefix, resource_id=resource_id,
        resolution=resolution)

    top = (_top_data_v4 if version == 4 else _top_data_v5)(
        subgroup_name, rid, rpath, resolution=resolution)
    with open(out_dir / "data.json", 'w', encoding='utf-8') as fh:
        json.dump(top, fh, indent='\t')
    print(f"  -> wrote v{version} package: {out_dir}")


def convert_group(bins_pvrs: list, out_dir: Path, version: int, *,
                  group_name: str, resolution: int = 1536) -> None:
    """Bundle multiple bin+pvr pairs into one package under a single subgroup,
    matching the ZombieTutorialGroup.package / ZombieEgyptTombRaiserGroup.package
    layouts.

    `bins_pvrs` is a list of (bin_path, pvr_path, type_path, char_name,
    id_prefix) tuples; one per character. When `type_path == 'effects'` the
    resource lands under `images/initial/effects/`. Empty type_path drops the
    category subfolder entirely.
    """
    from fbin_parser import parse_binary

    out_dir.mkdir(parents=True, exist_ok=True)
    resources = []
    for bin_path, pvr_path, type_path, char_name, id_prefix in bins_pvrs:
        parsed = parse_binary(str(bin_path))
        if parsed is None:
            raise RuntimeError(f"Failed to parse '{bin_path}'.")
        atlas = _load_atlas_pil(str(pvr_path))
        # Per-character resource_id derived from category + char (matches
        # POPANIM_<CATEGORY>_<CHAR> in the reference packages). The group name
        # itself doesn't go into the per-resource ID. Empty type_path means no
        # category prefix.
        cat = type_path.split('/')[0].upper() if type_path else ''
        cat_part = f"{cat}_" if cat else ''
        resource_id = f"POPANIM_{cat_part}{char_name.upper()}"
        rid, rpath = _write_character_assets(
            out_dir, parsed, atlas, version,
            type_path=type_path, char_name=char_name,
            id_prefix=id_prefix, resource_id=resource_id,
            resolution=resolution)
        resources.append((rid, rpath))

    top = (_top_data_v4_multi if version == 4 else _top_data_v5_multi)(
        group_name, resources, resolution=resolution)
    with open(out_dir / "data.json", 'w', encoding='utf-8') as fh:
        json.dump(top, fh, indent='\t')
    print(f"  -> wrote v{version} group package: {out_dir}  "
          f"({len(resources)} characters)")


# ─────────────────────────────────────────────────────────────────────────────
# Defaults derivation
# ─────────────────────────────────────────────────────────────────────────────

def _derive_defaults(bin_stem: str, *, force_effect: bool = False):
    """Derive (subgroup_name, type_path, char_name, id_prefix) from the bin
    file stem.

    Routing rules:
      - force_effect=True            -> images/initial/effects/<stem>/<stem>
        (group mode flags any bin whose stem strictly extends another's stem
        with `_`, regardless of what the suffix word is — `_bullet`, `_re`,
        `_attack`, `_fire`, `_bo`, anything)
      - contains 'zombie'            -> images/initial/zombie/<stem>/<stem>
      - contains 'plant'             -> images/initial/plant/<stem>/<stem>
      - otherwise                    -> images/initial/<stem>/<stem>

    char_name keeps the full stem (no prefix stripping) so resource folders
    line up with the source filename, matching the ZombieTutorialGroup /
    ZombieEgyptTombRaiserGroup references.

    In single-bin (non-group) mode there's nothing to detect an effect against,
    so callers must pass `--type-path effects` explicitly for FX bins.
    """
    stem = bin_stem.strip()
    low  = stem.lower()

    if force_effect:
        category = 'effects'
    elif 'zombie' in low:
        category = 'zombie'
    elif 'plant' in low:
        category = 'plant'
    else:
        category = ''  # no category subfolder

    char_name = low or stem
    type_path = category

    # subgroup_name: PascalCase from words in the stem, prefixed by the
    # category (Zombie/Plant/Effects) when one is detected; otherwise just the
    # PascalCase stem.
    words = [p for p in re.split(r'[_\W]+', char_name) if p]
    pascal = ''.join(w.capitalize() for w in words)
    subgroup_name = (category.capitalize() + pascal) if category else pascal

    # id_prefix produces `IMAGE_<CAT>_<CHAR>_` (single CHAR). The image
    # symbol name is already `<char>_<wxh>`, so the final ID concatenates to
    # `IMAGE_<CAT>_<CHAR>_<CHAR>_<wxh>` (2× CHAR + size). Doubling CHAR in
    # the prefix produced a triple-repeat in the final IDs
    # (e.g. IMAGE_APPLEMORTAR_3_APPLEMORTAR_3_APPLEMORTAR_3_130X122).
    cat_part = f"{category.upper()}_" if category else ''
    id_prefix = f"IMAGE_{cat_part}{char_name.upper()}_"
    return subgroup_name, type_path, char_name, id_prefix


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_atlas(bin_path: Path) -> Path:
    for suf in ('.pvr', '.png'):
        cand = bin_path.with_suffix(suf)
        if cand.exists():
            return cand
    raise FileNotFoundError(
        f"Could not auto-pair atlas next to '{bin_path}'. Use --pvr.")


def main():
    p = argparse.ArgumentParser(
        description="Convert one or more FBIN/RawBin .bin + atlas pairs to PvZ "
                    ".package format. With --group, bundles all bins into one "
                    "package matching the ZombieTutorialGroup.package layout.")
    p.add_argument("--bin", required=True, action='append',
                   help="Path to .bin animation file. Repeatable (one per "
                        "character in a group).")
    p.add_argument("--pvr", action='append',
                   help="Path to atlas (.pvr or .png). Repeatable; matched "
                        "positionally with --bin. Auto-paired by sibling stem "
                        "when omitted.")
    p.add_argument("--group", help="Bundle all --bin entries into one package "
                                   "with this subgroup name (e.g. "
                                   "'ZombieKungfuGroup'). Without --group each "
                                   "--bin produces its own package.")
    p.add_argument("--version", choices=("4", "5", "both"), default="both",
                   help="Package format layout (default: both)")
    p.add_argument("--resolution", type=int, default=1536,
                   help="Texture-resolution flag written to data.json "
                        "(default: 1536, matches HD PvZ packages; pass 768 "
                        "for SD).")
    p.add_argument("--out", help="Output directory base (default: alongside the first .bin)")
    p.add_argument("--subgroup", help="Subgroup name for single-char mode "
                                       "(default: derived from .bin stem). "
                                       "Ignored with --group.")
    p.add_argument("--type-path", help="Path under images/initial/ (default: derived)")
    p.add_argument("--char-name", help="Character folder name (single-char mode "
                                       "only; default: derived per --bin)")
    p.add_argument("--id-prefix", help="Image-ID prefix (single-char mode only; "
                                       "default: derived per --bin)")
    args = p.parse_args()

    bin_paths = [Path(b).resolve() for b in args.bin]
    for bp in bin_paths:
        if not bp.exists():
            print(f"Error: no such file '{bp}'"); sys.exit(1)

    if args.pvr:
        if len(args.pvr) != len(bin_paths):
            print(f"Error: --pvr count ({len(args.pvr)}) must match --bin "
                  f"count ({len(bin_paths)}); omit --pvr to auto-pair.")
            sys.exit(1)
        pvr_paths = [Path(p).resolve() for p in args.pvr]
    else:
        pvr_paths = [_resolve_atlas(bp) for bp in bin_paths]
    for pp in pvr_paths:
        if not pp.exists():
            print(f"Error: no such atlas '{pp}'"); sys.exit(1)

    out_base = Path(args.out).resolve() if args.out else bin_paths[0].parent
    versions = [4, 5] if args.version == "both" else [int(args.version)]

    if args.group:
        if args.subgroup or args.char_name or args.id_prefix:
            print("Warning: --subgroup/--char-name/--id-prefix are ignored "
                  "with --group (per-bin defaults are used).")
        # Resolve per-bin defaults. In group mode, also treat a bin whose stem
        # strictly extends another bin's stem (e.g. `..._bullet`,
        # `..._bone_hit`) as an effect of that bin — even if the suffix isn't
        # in the known FX list. This catches "<zombie>_<effectname>" pairs.
        stems = [bp.stem.lower() for bp in bin_paths]
        force_effect = [False] * len(bin_paths)
        for i, si in enumerate(stems):
            for j, sj in enumerate(stems):
                if i != j and si.startswith(sj + '_'):
                    force_effect[i] = True
                    break
        chars = []
        for bp, pp, fe in zip(bin_paths, pvr_paths, force_effect):
            _sub, type_def, char_def, idp_def = _derive_defaults(
                bp.stem, force_effect=fe)
            chars.append((bp, pp,
                          args.type_path if args.type_path is not None else type_def,
                          char_def, idp_def))
        for v in versions:
            pkg_dir = out_base / f"{args.group}_{v}.package"
            print(f"Converting -> {pkg_dir.name}")
            convert_group(chars, pkg_dir, v, group_name=args.group,
                          resolution=args.resolution)
        return

    # Single-character mode: one package per --bin. Overrides apply only when
    # exactly one bin was given (otherwise they'd collide).
    if len(bin_paths) > 1 and (args.subgroup or args.char_name or args.id_prefix):
        print("Error: --subgroup/--char-name/--id-prefix can only be used "
              "with a single --bin; use --group to bundle multiple.")
        sys.exit(1)

    for bp, pp in zip(bin_paths, pvr_paths):
        sub_def, type_def, char_def, idp_def = _derive_defaults(bp.stem)
        subgroup_name = args.subgroup or sub_def
        type_path     = args.type_path or type_def
        char_name     = args.char_name or char_def
        id_prefix     = args.id_prefix or idp_def
        for v in versions:
            pkg_dir = out_base / f"{subgroup_name}_{v}.package"
            print(f"Converting -> {pkg_dir.name}")
            convert(bp, pp, pkg_dir, v,
                    subgroup_name=subgroup_name,
                    type_path=type_path,
                    char_name=char_name,
                    id_prefix=id_prefix,
                    resolution=args.resolution)


if __name__ == "__main__":
    main()
