"""
xfl_sprite.py
-------------
Step 3: write one animated graphic XML per FBIN movie-clip into library/sprite/.

Matrix formula (derived from player JSON dump)
==============================================
The player renderer blits each sprite at a BLIT CENTER computed as:

    lcx = img.offset_x + img.width  / 2   (pivot offset in local space)
    lcy = -img.offset_y - img.height / 2

    blit_cx = na*lcx + nc*lcy + ntx       (world_matrix applied to pivot)
    blit_cy = nb*lcx + nd*lcy + nty

The XFL image symbol places the bitmap at tx=-w/2, ty=-h/2, so its
registration point (0,0) is at the bitmap centre.

The Flash instance matrix must map (0,0) → (blit_cx, blit_cy) with the
same rotation/scale as the world_matrix:

    flash.a  = world.a       flash.b  = world.b
    flash.c  = world.c       flash.d  = world.d
    flash.tx = blit_cx       flash.ty = blit_cy

world_matrix is already in screen (Y-down) space because the player base
transform bakes in the Y-flip (d=-1).  No extra negation is needed.
"""
from __future__ import annotations

import logging
import os
from xml.etree import ElementTree as ET

from xfl_helpers import (
    sym_root, make_layers, make_single_layer, write_xml, write_color, mc_safe_name
)

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def export_sprite_symbols(
    images:      list,
    movie_clips: list,
    sprite_info: dict,
    sprite_dir:  str,
    scale:       float = 1.0,
) -> None:
    """
    Write library/sprite/<name>.xml — one per image, NOT per movie-clip.

    Each sprite XML is a simple 1-frame graphic that wraps the corresponding
    image symbol at the origin.  The label XML then references these sprite
    symbols (with firstFrame="0") and applies the per-frame matrices.

    Structure:
        <DOMSymbolItem name="sprite/zombie_foot_inner_toe" symbolType="graphic">
          <timeline><DOMTimeline name="zombie_foot_inner_toe"><layers>
            <DOMLayer name="1"><frames>
              <DOMFrame index="0">
                <elements>
                  <DOMSymbolInstance libraryItemName="image/zombie_foot_inner_toe"
                                     firstFrame="0" symbolType="graphic" loop="loop">
                    <matrix><Matrix a="1" b="0" c="0" d="1" tx="0" ty="0"/></matrix>
                  </DOMSymbolInstance>
                </elements>
              </DOMFrame>
            </frames></DOMLayer>
          </layers></DOMTimeline></timeline>
        </DOMSymbolItem>
    """
    count = 0
    for idx in range(len(images)):
        base = sprite_info.get(idx)
        if not base:
            continue

        root  = sym_root(f"sprite/{base}", "graphic")
        layer = make_single_layer(root, base)

        f_el  = ET.SubElement(layer, "frames")
        dom_f = ET.SubElement(f_el, "DOMFrame")
        dom_f.set("index", "0")
        elems = ET.SubElement(dom_f, "elements")

        inst = ET.SubElement(elems, "DOMSymbolInstance")
        inst.set("libraryItemName", f"image/{base}")
        inst.set("firstFrame", "0")
        inst.set("symbolType", "graphic")
        inst.set("loop", "loop")

        mx_el = ET.SubElement(inst, "matrix")
        m     = ET.SubElement(mx_el, "Matrix")
        m.set("a", "1.000000"); m.set("b", "0.000000")
        m.set("c", "0.000000"); m.set("d", "1.000000")
        m.set("tx", "0.000000"); m.set("ty", "0.000000")

        write_xml(root, os.path.join(sprite_dir, base + ".xml"))
        count += 1

    log.info("Sprite: wrote %d symbol XMLs", count)


# ─────────────────────────────────────────────────────────────────────────────
# Matrix helpers
# ─────────────────────────────────────────────────────────────────────────────

def _flash_matrix(world_matrix: tuple, img: dict,
                  local_matrix: tuple = None,
                  scale: float = 1.0) -> tuple:
    """
    Build the Flash instance matrix for a sprite.
    scale multiplies all position (tx/ty) and rotation/scale (a/b/c/d) components.
    """
    na, nb, nc, nd, ntx, nty = world_matrix
    w   = img.get("width",    0)
    h   = img.get("height",   0)
    ox  = img.get("offset_x", 0.0)
    oy  = img.get("offset_y", 0.0)

    lcx      =  ox + w * 0.5
    lcy      = -oy - h * 0.5
    flash_tx = (na * lcx + nc * lcy + ntx) * scale
    flash_ty = (nb * lcx + nd * lcy + nty) * scale

    if local_matrix is not None:
        la, lb, lc, ld = local_matrix[0], local_matrix[1], local_matrix[2], local_matrix[3]
        flash_a =  la * scale
        flash_b = -lb * scale
        flash_c = -lc * scale
        flash_d =  ld * scale
    else:
        flash_a =  na * scale
        flash_b =  nb * scale
        flash_c =  nc * scale
        flash_d = -nd * scale

    return (flash_a, flash_b, flash_c, flash_d, flash_tx, flash_ty)


def _write_matrix_el(parent: ET.Element, flash_mat: tuple) -> None:
    """Write a <matrix><Matrix .../></matrix> element from a flash matrix tuple."""
    a, b, c, d, tx, ty = flash_mat

    def fmt(v: float) -> str:
        return f"{v:.6f}" if abs(v) > 1e-9 else "0.000000"

    mx_el = ET.SubElement(parent, "matrix")
    m     = ET.SubElement(mx_el, "Matrix")
    m.set("a",  fmt(a))
    m.set("b",  fmt(b))
    m.set("c",  fmt(c))
    m.set("d",  fmt(d))
    m.set("tx", fmt(tx))
    m.set("ty", fmt(ty))


# ─────────────────────────────────────────────────────────────────────────────
# MC tree traversal (mirrors renderer.draw exactly)
# ─────────────────────────────────────────────────────────────────────────────

def _collect_draws(mc_idx, frame_num, parent_mat, movie_clips, images,
                   rawbin, depth=0, visited=None):
    """
    Walk the MC tree exactly as renderer.draw() does and return a flat list of:
        { img_idx, img, world_matrix, alpha }
    """
    if depth > 32:
        return []
    if visited is None:
        visited = frozenset()
    if mc_idx in visited:
        return []
    visited = visited | {mc_idx}

    if mc_idx < 0 or mc_idx >= len(movie_clips):
        return []

    mc = movie_clips[mc_idx]
    if not mc.get("frames"):
        return []

    idx      = frame_num % len(mc["frames"])
    elements = list(mc["frames"][idx])
    pa, pb, pc, pd, ptx, pty = parent_mat

    # ── dedup (mirrors renderer) ──────────────────────────────────────────────
    if rawbin:
        seen = {}
        for i, elem in enumerate(elements):
            tx_r = round(elem['matrix'][4], 1)
            ty_r = round(elem['matrix'][5], 1)
            key  = (elem.get('frame_index', -1), tx_r, ty_r)
            seen[key] = i
        elements = [elements[i] for i in sorted(seen.values())]
    else:
        from collections import Counter
        counts = Counter(e["id"] for e in elements if not e["is_mc"])
        if any(c > 1 for c in counts.values()):
            seen_img = {}
            for i, elem in enumerate(elements):
                if not elem["is_mc"]:
                    seen_img[elem["id"]] = i
            kept = set(seen_img.values())
            elements = [e for i, e in enumerate(elements)
                        if e["is_mc"] or i in kept]

    results = []
    for elem in elements:
        eid = elem["id"]
        if eid < 0:
            continue

        la, lb, lc, ld, ltx, lty = elem["matrix"]
        na  = pa*la + pc*lb
        nb  = pb*la + pd*lb
        nc  = pa*lc + pc*ld
        nd  = pb*lc + pd*ld
        ntx = pa*ltx + pc*lty + ptx
        nty = pb*ltx + pd*lty + pty
        world = (na, nb, nc, nd, ntx, nty)

        if elem["is_mc"]:
            child_frame = elem.get("frame_index", -1)
            if eid >= len(movie_clips):
                continue
            child_mc = movie_clips[eid]

            if rawbin:
                if eid == 1 and child_frame >= 0:
                    if child_frame < len(movie_clips):
                        results.extend(_collect_draws(child_frame, frame_num, world,
                                                      movie_clips, images, rawbin,
                                                      depth+1, visited))
                    elif child_frame < len(images):
                        img = images[child_frame]
                        if not (int(img.get("tex_x",0))==0 and int(img.get("tex_y",0))==0
                                and int(img.get("width",0))<=4 and int(img.get("height",0))<=4):
                            results.append({
                                "img_idx":      child_frame,
                                "img":          img,
                                "world_matrix": world,
                                "local_matrix": elem["matrix"],
                                "alpha":        float(elem.get("alpha", 1.0)),
                            })
                elif child_frame >= 0 and child_frame < len(images):
                    img = images[child_frame]
                    if not (int(img.get("tex_x",0))==0 and int(img.get("tex_y",0))==0
                            and int(img.get("width",0))<=4 and int(img.get("height",0))<=4):
                        results.append({
                            "img_idx":      child_frame,
                            "img":          img,
                            "world_matrix": world,
                            "local_matrix": elem["matrix"],
                            "alpha":        float(elem.get("alpha", 1.0)),
                        })
                elif len(child_mc["frames"]) == 1 and child_frame >= 0:
                    if child_frame < len(movie_clips):
                        results.extend(_collect_draws(child_frame, frame_num, world,
                                                      movie_clips, images, rawbin,
                                                      depth+1, visited))
                else:
                    nf = child_frame if child_frame >= 0 else frame_num
                    results.extend(_collect_draws(eid, nf, world,
                                                  movie_clips, images, rawbin,
                                                  depth+1, visited))
            else:
                nf = child_frame if child_frame >= 0 else frame_num
                results.extend(_collect_draws(eid, nf, world,
                                              movie_clips, images, rawbin,
                                              depth+1, visited))
        else:
            if eid < len(images):
                img = images[eid]
                if not (int(img.get("tex_x",0))==0 and int(img.get("tex_y",0))==0
                        and int(img.get("width",0))<=4 and int(img.get("height",0))<=4):
                    results.append({
                        "img_idx":      eid,
                        "img":          img,
                        "world_matrix": world,
                        "local_matrix": elem["matrix"],
                        "alpha":        float(elem.get("alpha", 1.0)),
                    })
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Layer filling
# ─────────────────────────────────────────────────────────────────────────────

def _fill_sprite_layers(
    layers:      ET.Element,
    frames:      list,
    movie_clips: list,
    images:      list,
    sprite_info: dict,
    scale:       float = 1.0,
) -> None:
    if not frames:
        layer = ET.SubElement(layers, "DOMLayer")
        layer.set("name", "1")
        f_el  = ET.SubElement(layer, "frames")
        dom_f = ET.SubElement(f_el, "DOMFrame")
        dom_f.set("index", "0"); dom_f.set("duration", "1")
        ET.SubElement(dom_f, "elements")
        return

    # We need the MC index to traverse — infer from movie_clips by finding
    # which MC owns these frames (passed directly, so mc_idx isn't available here).
    # Instead, collect draws for frame 0 to know which img_idx slots appear,
    # then build layers keyed by draw-order slot.
    #
    # Strategy: for each frame, collect the flat draw list (img_idx, world_matrix, alpha).
    # Build layers by DRAW ORDER SLOT: slot 0 = first sprite drawn, etc.
    # This mirrors what the player actually renders.
    # We need to know rawbin — detect by checking if all elements are is_mc=True.

    # Detect rawbin from frame structure
    rawbin = False
    if frames and frames[0]:
        rawbin = all(e.get("is_mc", False) for e in frames[0])

    # Identity base — the MC's own coordinate system (no global transform)
    # The XFL sprite symbol is in local MC space, not screen space.
    identity = (1.0, 0.0, 0.0, -1.0, 0.0, 0.0)

    # We need the mc_idx. Collect frames list → find owner mc_idx.
    # Since we don't have it here, re-derive by collecting from frames directly.
    # For the sprite-level XML we use the MC's own transform chain starting at
    # a Y-flip identity (Cocos Y-up → Flash Y-down).

    # Collect draw lists for all frames
    # We work directly on the frames list using a local traversal.
    all_draws: list = []
    for fi, frame_elems in enumerate(frames):
        draws = _collect_draws_from_frame(frame_elems, movie_clips, images,
                                          rawbin, identity, fi)
        all_draws.append(draws)

    if not any(all_draws):
        layer = ET.SubElement(layers, "DOMLayer")
        layer.set("name", "1")
        f_el  = ET.SubElement(layer, "frames")
        dom_f = ET.SubElement(f_el, "DOMFrame")
        dom_f.set("index", "0"); dom_f.set("duration", str(len(frames)))
        ET.SubElement(dom_f, "elements")
        return

    # Find all unique (img_idx) slots across all frames, in first-appearance order
    seen_slots: list  = []
    seen_set:   set   = set()
    for draws in all_draws:
        for d in draws:
            if d["img_idx"] not in seen_set:
                seen_slots.append(d["img_idx"])
                seen_set.add(d["img_idx"])

    total    = len(frames)
    # Reverse: last drawn (frontmost) = first DOMLayer in XML = Flash top layer.
    # Flash z-order is XML position order, not the name attribute.
    seen_slots = list(reversed(seen_slots))

    mc_id_for: dict = {}
    for draws in all_draws:
        for d in draws:
            if d["img_idx"] not in mc_id_for:
                mc_id_for[d["img_idx"]] = d.get("mc_id", 1)

    n_layers = len(seen_slots)

    for layer_idx, img_idx in enumerate(seen_slots):
        base = sprite_info.get(img_idx)
        if not base:
            continue

        mc_id = mc_id_for.get(img_idx, 1)
        img = images[img_idx]
        layer = ET.SubElement(layers, "DOMLayer")
        layer.set("name", str(layer_idx + 1))
        f_el  = ET.SubElement(layer, "frames")

        _UNSET = object()
        runs   = []
        prev_fmat  = _UNSET
        prev_alpha = _UNSET
        run_start  = 0
        run_fmat   = None
        run_alpha  = 1.0

        for fi, draws in enumerate(all_draws):
            found = next((d for d in draws if d["img_idx"] == img_idx), None)
            cur_fmat  = _flash_matrix(found["world_matrix"], img, found.get("local_matrix"), scale) if found else None
            cur_alpha = found["alpha"] if found else 1.0

            # Round for stable comparison
            cur_key = (_round_mat(cur_fmat), round(cur_alpha, 4))
            prev_key = (_round_mat(prev_fmat), round(prev_alpha, 4)) \
                       if prev_fmat is not _UNSET else _UNSET

            if cur_key != prev_key:
                if prev_fmat is not _UNSET:
                    runs.append((run_start, fi, run_fmat, run_alpha))
                run_start = fi
                run_fmat  = cur_fmat
                run_alpha = cur_alpha
                prev_fmat  = cur_fmat
                prev_alpha = cur_alpha

        if prev_fmat is not _UNSET:
            runs.append((run_start, total, run_fmat, run_alpha))

        for start, end, fmat, alpha in runs:
            dur   = max(1, end - start)
            dom_f = ET.SubElement(f_el, "DOMFrame")
            dom_f.set("index",    str(start))
            dom_f.set("duration", str(dur))
            elems_el = ET.SubElement(dom_f, "elements")

            if fmat is not None:
                inst = ET.SubElement(elems_el, "DOMSymbolInstance")
                inst.set("libraryItemName", f"image/{base}")
                inst.set("firstFrame",  "0")
                inst.set("symbolType",  "graphic")
                inst.set("loop",        "loop")
                _write_matrix_el(inst, fmat)
                write_color(inst, alpha)

        if len(f_el) == 0:
            dom_f = ET.SubElement(f_el, "DOMFrame")
            dom_f.set("index", "0"); dom_f.set("duration", str(total))
            ET.SubElement(dom_f, "elements")


def _round_mat(m):
    if m is None:
        return None
    return tuple(round(v, 4) for v in m)


def _collect_draws_from_frame(frame_elems, movie_clips, images, rawbin,
                               parent_mat, frame_num, depth=0, visited=None):
    """Collect draws from a single frame's element list."""
    if depth > 32:
        return []
    if visited is None:
        visited = frozenset()

    pa, pb, pc, pd, ptx, pty = parent_mat
    elements = list(frame_elems)

    # dedup
    if rawbin:
        seen = {}
        for i, elem in enumerate(elements):
            tx_r = round(elem['matrix'][4], 1)
            ty_r = round(elem['matrix'][5], 1)
            key  = (elem.get('frame_index', -1), tx_r, ty_r)
            seen[key] = i
        elements = [elements[i] for i in sorted(seen.values())]
    else:
        from collections import Counter
        counts = Counter(e["id"] for e in elements if not e.get("is_mc", False))
        if any(c > 1 for c in counts.values()):
            seen_img = {}
            for i, elem in enumerate(elements):
                if not elem.get("is_mc", False):
                    seen_img[elem["id"]] = i
            kept = set(seen_img.values())
            elements = [e for i, e in enumerate(elements)
                        if e.get("is_mc", False) or i in kept]

    results = []
    for elem in elements:
        eid = elem.get("id", -1)
        if eid < 0:
            continue

        la, lb, lc, ld, ltx, lty = elem["matrix"]
        na  = pa*la + pc*lb;  nb  = pb*la + pd*lb
        nc  = pa*lc + pc*ld;  nd  = pb*lc + pd*ld
        ntx = pa*ltx + pc*lty + ptx;  nty = pb*ltx + pd*lty + pty
        world = (na, nb, nc, nd, ntx, nty)

        if elem.get("is_mc", False):
            child_frame = elem.get("frame_index", -1)
            if eid >= len(movie_clips):
                continue
            child_mc = movie_clips[eid]
            child_frames = child_mc.get("frames", [])

            if rawbin:
                if eid == 1 and child_frame >= 0:
                    if child_frame < len(movie_clips):
                        if child_frame not in visited:
                            child_f_idx = frame_num % max(1, len(movie_clips[child_frame].get("frames",[[]])))
                            child_elems = movie_clips[child_frame]["frames"][child_f_idx] \
                                          if movie_clips[child_frame].get("frames") else []
                            results.extend(_collect_draws_from_frame(
                                child_elems, movie_clips, images, rawbin, world,
                                frame_num, depth+1, visited | {child_frame}))
                    elif child_frame < len(images):
                        img = images[child_frame]
                        if not _is_placeholder(img):
                            results.append({"img_idx": child_frame, "img": img,
                                            "world_matrix": world,
                                            "local_matrix": elem["matrix"],
                                            "mc_id": eid,
                                            "alpha": float(elem.get("alpha", 1.0))})
                elif child_frame >= 0 and child_frame < len(images):
                    img = images[child_frame]
                    if not _is_placeholder(img):
                        results.append({"img_idx": child_frame, "img": img,
                                        "world_matrix": world,
                                        "local_matrix": elem["matrix"],
                                        "mc_id": eid,
                                        "alpha": float(elem.get("alpha", 1.0))})
                elif len(child_frames) == 1 and child_frame >= 0:
                    if child_frame < len(movie_clips):
                        if child_frame not in visited:
                            child_f_idx = frame_num % max(1, len(movie_clips[child_frame].get("frames",[[]])))
                            child_elems = movie_clips[child_frame]["frames"][child_f_idx] \
                                          if movie_clips[child_frame].get("frames") else []
                            results.extend(_collect_draws_from_frame(
                                child_elems, movie_clips, images, rawbin, world,
                                frame_num, depth+1, visited | {child_frame}))
                else:
                    nf = child_frame if child_frame >= 0 else frame_num
                    child_fi = nf % max(1, len(child_frames)) if child_frames else 0
                    child_elems = child_frames[child_fi] if child_frames else []
                    if eid not in visited:
                        results.extend(_collect_draws_from_frame(
                            child_elems, movie_clips, images, rawbin, world,
                            frame_num, depth+1, visited | {eid}))
            else:
                nf = child_frame if child_frame >= 0 else frame_num
                child_fi = nf % max(1, len(child_frames)) if child_frames else 0
                child_elems = child_frames[child_fi] if child_frames else []
                if eid not in visited:
                    results.extend(_collect_draws_from_frame(
                        child_elems, movie_clips, images, rawbin, world,
                        frame_num, depth+1, visited | {eid}))
        else:
            if eid < len(images):
                img = images[eid]
                if not _is_placeholder(img):
                    results.append({"img_idx": eid, "img": img,
                                    "world_matrix": world,
                                    "local_matrix": elem["matrix"],
                                    "mc_id": eid,
                                    "alpha": float(elem.get("alpha", 1.0))})
    return results


def _is_placeholder(img):
    return (int(img.get("tex_x",0))==0 and int(img.get("tex_y",0))==0
            and int(img.get("width",0))<=4 and int(img.get("height",0))<=4)


def _resolve_lib_name(eid, is_mc, movie_clips, sprite_info, visited=None, frame_index=0):
    """Kept for use by xfl_label.py."""
    base = sprite_info.get(frame_index)
    return f"image/{base}" if base else None
