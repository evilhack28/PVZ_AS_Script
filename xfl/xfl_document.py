"""
xfl_document.py
---------------
Step 5: write DOMDocument.xml — the project root that lists all folders,
media bitmaps, symbol includes, and the master animation timeline.
"""
from __future__ import annotations

import logging
import os
from xml.etree import ElementTree as ET

from xfl_helpers import _NS, _XFLV, _XSI, mc_safe_name, write_xml

log = logging.getLogger(__name__)


def export_domdocument(
    images:      list,
    movie_clips: list,
    actions:     list,
    sprite_info: dict,
    stem:        str,
    fps:         int,
    atlas_w:     int,
    atlas_h:     int,
    xfl_dir:     str,
) -> None:
    root = ET.Element("DOMDocument")
    root.set("xmlns:xsi",       _XSI)
    root.set("xmlns",           _NS)
    root.set("backgroundColor", "#999999")
    root.set("frameRate",       str(fps))
    root.set("width",           f"{atlas_w}.000000")
    root.set("height",          f"{atlas_h}.000000")
    root.set("xflVersion",      _XFLV)

    # ── folders ───────────────────────────────────────────────────────────────
    folders = ET.SubElement(root, "folders")
    for fname in ("media", "image", "sprite", "label"):
        fi = ET.SubElement(folders, "DOMFolderItem")
        fi.set("name", fname)
        fi.set("isExpanded", "true")

    # ── media ─────────────────────────────────────────────────────────────────
    media_el    = ET.SubElement(root, "media")
    seen_bases: set = set()
    for idx in range(len(images)):
        base = sprite_info.get(idx)
        if not base or base in seen_bases:
            continue
        seen_bases.add(base)
        bm = ET.SubElement(media_el, "DOMBitmapItem")
        bm.set("name", f"media/{base}")
        bm.set("href", f"media/{base}.png")

    # ── symbols ───────────────────────────────────────────────────────────────
    symbols_el  = ET.SubElement(root, "symbols")
    seen_image: set = set()
    for idx in range(len(images)):
        base = sprite_info.get(idx)
        if not base or base in seen_image:
            continue
        seen_image.add(base)
        ET.SubElement(symbols_el, "Include").set("href", f"image/{base}.xml")
        ET.SubElement(symbols_el, "Include").set("href", f"sprite/{base}.xml")

    for action in actions:
        act_name = action.get("name", "action")
        ET.SubElement(symbols_el, "Include").set("href", f"label/{act_name}.xml")

    # ── timelines ─────────────────────────────────────────────────────────────
    act_info = []
    cursor   = 0
    for action in actions:
        name   = action.get("name", "action")
        mc_idx = action.get("mc_idx", -1)
        count  = 1
        if 0 <= mc_idx < len(movie_clips):
            count = max(1, len(movie_clips[mc_idx].get("frames", [])))
        act_info.append((name, count, cursor))
        cursor += count

    if act_info:
        timelines_el = ET.SubElement(root, "timelines")
        tl = ET.SubElement(timelines_el, "DOMTimeline")
        tl.set("name", "animation")
        layers_el = ET.SubElement(tl, "layers")

        # label layer
        lbl_layer = ET.SubElement(layers_el, "DOMLayer")
        lbl_layer.set("name", "label")
        lbl_frames = ET.SubElement(lbl_layer, "frames")
        for name, count, start in act_info:
            f = ET.SubElement(lbl_frames, "DOMFrame")
            f.set("index",     str(start))
            f.set("duration",  str(count))
            f.set("name",      name)
            f.set("labelType", "name")
            ET.SubElement(f, "elements")

        # action layer
        act_layer = ET.SubElement(layers_el, "DOMLayer")
        act_layer.set("name", "action")
        act_frames_el = ET.SubElement(act_layer, "frames")
        for name, count, start in act_info:
            if count > 1:
                f = ET.SubElement(act_frames_el, "DOMFrame")
                f.set("index",    str(start))
                f.set("duration", str(count - 1))
                ET.SubElement(f, "elements")
            last_f = ET.SubElement(act_frames_el, "DOMFrame")
            last_f.set("index", str(start + count - 1))
            as_el = ET.SubElement(last_f, "Actionscript")
            sc_el = ET.SubElement(as_el, "script")
            sc_el.text = "stop();"
            ET.SubElement(last_f, "elements")

        # instance layer
        inst_layer = ET.SubElement(layers_el, "DOMLayer")
        inst_layer.set("name", "instance")
        inst_frames = ET.SubElement(inst_layer, "frames")
        for name, count, start in act_info:
            f = ET.SubElement(inst_frames, "DOMFrame")
            f.set("index",    str(start))
            f.set("duration", str(count))
            elems = ET.SubElement(f, "elements")
            sym = ET.SubElement(elems, "DOMSymbolInstance")
            sym.set("libraryItemName", f"label/{name}")
            sym.set("symbolType",      "graphic")
            sym.set("loop",            "loop")

    write_xml(root, os.path.join(xfl_dir, "DOMDocument.xml"))
    log.info("DOMDocument.xml written")
