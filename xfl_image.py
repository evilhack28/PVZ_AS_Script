"""
xfl_image.py
------------
Step 2: write one 1-frame graphic XML per bitmap into library/image/.
Each symbol centres the bitmap on the registration point (tx=-w/2, ty=-h/2).
"""
from __future__ import annotations

import logging
import os
from xml.etree import ElementTree as ET

from xfl_helpers import sym_root, make_single_layer, write_xml

log = logging.getLogger(__name__)


def export_image_symbols(images: list, sprite_info: dict, image_dir: str,
                          img_scale: float = 1.0) -> None:
    """Write library/image/<name>.xml for every valid image."""
    count = 0
    for idx in range(len(images)):
        base = sprite_info.get(idx)
        if not base:
            continue

        img = images[idx]
        w   = int(img.get("width",  0))
        h   = int(img.get("height", 0))

        root  = sym_root(f"image/{base}", "graphic")
        layer = make_single_layer(root, base)

        f_el  = ET.SubElement(layer, "frames")
        dom_f = ET.SubElement(f_el, "DOMFrame")
        dom_f.set("index", "0")
        elems = ET.SubElement(dom_f, "elements")

        bm = ET.SubElement(elems, "DOMBitmapInstance")
        bm.set("libraryItemName", f"media/{base}")

        mx = ET.SubElement(bm, "matrix")
        m  = ET.SubElement(mx, "Matrix")
        m.set("a",  f"{img_scale:.6f}")
        m.set("b",  "0.000000")
        m.set("c",  "0.000000")
        m.set("d",  f"{img_scale:.6f}")
        m.set("tx", f"{-w / 2:.6f}")
        m.set("ty", f"{-h / 2:.6f}")

        write_xml(root, os.path.join(image_dir, base + ".xml"))
        count += 1

    log.info("Image: wrote %d symbol XMLs", count)
