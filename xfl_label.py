"""
xfl_label.py
------------
Step 4: write one animated graphic XML per action into library/label/.

Uses the same correct matrix formula as xfl_sprite.py:
    flash_tx = na*lcx + nc*lcy + ntx   (blit centre X)
    flash_ty = nb*lcx + nd*lcy + nty   (blit centre Y)
    flash_a/b/c/d = world_a/b/c/d      (rotation/scale unchanged)

See xfl_sprite.py for full derivation.
"""
from __future__ import annotations

import logging
import os
from xml.etree import ElementTree as ET

from xfl_helpers import sym_root, make_layers, write_xml, write_color, mc_safe_name
from xfl_sprite  import (_collect_draws_from_frame, _flash_matrix,
                          _write_matrix_el, _round_mat)

log = logging.getLogger(__name__)


def _clamp_action_range(action: dict, last_frame: int):
    """
    Mirror player.py _clamp_action_range exactly.
    Global frame numbers (start/end) are mapped back to MC-local frame indices.
    """
    raw_start = action.get('start', 0)
    raw_end   = action.get('end',   last_frame)
    duration  = raw_end - raw_start
    # If start>0 and duration covers the whole MC, it's a global offset — reset to 0
    is_global = (duration > last_frame) or (raw_start > 0 and duration >= last_frame)
    if is_global:
        return 0, min(duration, last_frame)
    cs = max(0, min(raw_start, last_frame))
    ce = max(0, min(raw_end,   last_frame))
    if ce <= cs:
        return 0, last_frame
    return cs, ce




def _base_transform_from_meta(anim_meta, define_key: str, action_name: str) -> tuple:
    """
    Build the base affine transform from animaction.txt meta, mirroring player._base_transform.
    Returns (sx, 0, 0, -s, offset_x, offset_y) in Flash/screen coordinates.
    Without meta returns Y-flip identity (1, 0, 0, -1, 0, 0).
    """
    if anim_meta is None or not define_key:
        return (1.0, 0.0, 0.0, -1.0, 0.0, 0.0)

    try:
        cfg = anim_meta.action_config(define_key, action_name)
    except Exception:
        cfg = None

    if cfg is None:
        return (1.0, 0.0, 0.0, -1.0, 0.0, 0.0)

    s  = cfg.scale if cfg.scale > 0 else 1.0
    # In the player: tx = cx - offset_x*s, ty = cy + offset_y*s
    # For XFL we use 0,0 as origin so: tx = -offset_x*s, ty = offset_y*s
    tx = -cfg.offset_x * s
    ty =  cfg.offset_y * s
    sx = -s if cfg.flip else s
    return (sx, 0.0, 0.0, -s, tx, ty)


def export_label_symbols(
    movie_clips: list,
    actions:     list,
    images:      list,
    sprite_info: dict,
    label_dir:   str,
    anim_meta=None,
    define_key:  str = "",
    scale:       float = 1.0,
) -> None:
    """Write library/label/<action_name>.xml for every action."""
    rawbin = bool(movie_clips and movie_clips[0].get('frames') and
                  movie_clips[0]['frames'][0] and
                  all(e.get('is_mc', False) for e in movie_clips[0]['frames'][0]))

    for action in actions:
        act_name = action.get("name", "action")
        mc_idx   = action.get("mc_idx", 0)

        if not (0 <= mc_idx < len(movie_clips)):
            log.warning("Label '%s': mc_idx %d out of range, skipping", act_name, mc_idx)
            continue

        mc         = movie_clips[mc_idx]
        all_frames = mc.get("frames", [])
        last_frame = max(0, len(all_frames) - 1)

        a_start, a_end = _clamp_action_range(action, last_frame)
        frames = all_frames[a_start: a_end + 1]

        # Build base transform from meta (scale + offset)
        base = _base_transform_from_meta(anim_meta, define_key, act_name)

        root   = sym_root(f"label/{act_name}", "graphic")
        layers = make_layers(root, act_name)

        _fill_label_layers(layers, frames, movie_clips, images, sprite_info,
                           base_transform=base, scale=scale)
        write_xml(root, os.path.join(label_dir, act_name + ".xml"))

    log.info("Label: wrote %d action XMLs", len(actions))


def _fill_label_layers(
    layers:         ET.Element,
    frames:         list,
    movie_clips:    list,
    images:         list,
    sprite_info:    dict,
    base_transform: tuple = None,
    scale:          float = 1.0,
) -> None:
    if not frames:
        layer = ET.SubElement(layers, "DOMLayer")
        layer.set("name", "1")
        f_el  = ET.SubElement(layer, "frames")
        dom_f = ET.SubElement(f_el, "DOMFrame")
        dom_f.set("index", "0"); dom_f.set("duration", "1")
        ET.SubElement(dom_f, "elements")
        return

    # Detect rawbin
    rawbin = frames and frames[0] and all(e.get("is_mc", False) for e in frames[0])

    # Base transform: meta scale+offset baked in, or Y-flip identity
    identity = base_transform if base_transform else (1.0, 0.0, 0.0, -1.0, 0.0, 0.0)

    # Collect draw list per frame
    all_draws = []
    for fi, frame_elems in enumerate(frames):
        draws = _collect_draws_from_frame(frame_elems, movie_clips, images,
                                          rawbin, identity, fi)
        # Tag each draw with a slot key: (img_idx, occurrence_n)
        # so multiple instances of the same image each get their own layer
        counts: dict = {}
        tagged = []
        for d in draws:
            ii = d['img_idx']
            n  = counts.get(ii, 0)
            counts[ii] = n + 1
            tagged.append(dict(d, _slot=(ii, n)))
        all_draws.append(tagged)

    if not any(all_draws):
        layer = ET.SubElement(layers, "DOMLayer")
        layer.set("name", "1")
        f_el  = ET.SubElement(layer, "frames")
        dom_f = ET.SubElement(f_el, "DOMFrame")
        dom_f.set("index", "0"); dom_f.set("duration", str(len(frames)))
        ET.SubElement(dom_f, "elements")
        return

    # Unique slots in draw order — each slot becomes one DOMLayer
    seen_slots: list = []
    seen_set:   set  = set()
    for draws in all_draws:
        for d in draws:
            slot = d['_slot']
            if slot not in seen_set:
                seen_slots.append(slot)
                seen_set.add(slot)

    total    = len(frames)
    seen_slots = list(reversed(seen_slots))

    for layer_idx, slot in enumerate(seen_slots):
        img_idx = slot[0]
        base    = sprite_info.get(img_idx)
        if not base:
            continue

        img   = images[img_idx]
        layer = ET.SubElement(layers, "DOMLayer")
        layer.set("name", str(layer_idx + 1))
        f_el  = ET.SubElement(layer, "frames")

        _UNSET = object()
        runs      = []
        prev_key  = _UNSET
        run_start = 0
        run_fmat  = None
        run_alpha = 1.0

        for fi, draws in enumerate(all_draws):
            found = next((d for d in draws if d['_slot'] == slot), None)
            cur_fmat  = _flash_matrix(found["world_matrix"], img, found.get("local_matrix"), scale) if found else None
            cur_alpha = found["alpha"] if found else 1.0
            cur_key   = (_round_mat(cur_fmat), round(cur_alpha, 4))

            if cur_key != prev_key:
                if prev_key is not _UNSET:
                    runs.append((run_start, fi, run_fmat, run_alpha))
                run_start = fi
                run_fmat  = cur_fmat
                run_alpha = cur_alpha
                prev_key  = cur_key

        if prev_key is not _UNSET:
            runs.append((run_start, total, run_fmat, run_alpha))

        for start, end, fmat, alpha in runs:
            dur   = max(1, end - start)
            dom_f = ET.SubElement(f_el, "DOMFrame")
            dom_f.set("index",    str(start))
            dom_f.set("duration", str(dur))
            elems_el = ET.SubElement(dom_f, "elements")

            if fmat is not None:
                inst = ET.SubElement(elems_el, "DOMSymbolInstance")
                inst.set("libraryItemName", f"sprite/{base}")
                inst.set("firstFrame",  "0")
                inst.set("symbolType",  "graphic")
                inst.set("loop",        "loop")
                _write_matrix_el(inst, fmat)
                write_color(inst, alpha)

        if len(f_el) == 0:
            dom_f = ET.SubElement(f_el, "DOMFrame")
            dom_f.set("index", "0"); dom_f.set("duration", str(total))
            ET.SubElement(dom_f, "elements")
