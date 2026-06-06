"""
xfl_helpers.py
--------------
Shared XML helpers, constants, and low-level utilities used by all XFL modules.
"""
from __future__ import annotations

import re
from xml.etree import ElementTree as ET

_NS   = "http://ns.adobe.com/xfl/2008/"
_XFLV = "2.971"
_XSI  = "http://www.w3.org/2001/XMLSchema-instance"


# ─────────────────────────────────────────────────────────────────────────────
# XML element builders
# ─────────────────────────────────────────────────────────────────────────────

def sym_root(name: str, sym_type: str) -> ET.Element:
    root = ET.Element("DOMSymbolItem")
    root.set("xmlns:xsi", _XSI)
    root.set("xmlns",     _NS)
    root.set("name",      name)
    root.set("symbolType", sym_type)
    return root


def make_single_layer(parent: ET.Element, tl_name: str) -> ET.Element:
    """Create timeline → DOMTimeline → layers → DOMLayer, return DOMLayer."""
    tl_wrap = ET.SubElement(parent, "timeline")
    tl      = ET.SubElement(tl_wrap, "DOMTimeline")
    tl.set("name", tl_name)
    layers  = ET.SubElement(tl, "layers")
    return ET.SubElement(layers, "DOMLayer")


def make_layers(parent: ET.Element, tl_name: str) -> ET.Element:
    """Create timeline → DOMTimeline → layers, return layers element."""
    tl_wrap = ET.SubElement(parent, "timeline")
    tl      = ET.SubElement(tl_wrap, "DOMTimeline")
    tl.set("name", tl_name)
    return ET.SubElement(tl, "layers")


def write_color(parent: ET.Element, alpha: float) -> None:
    color_el = ET.SubElement(parent, "color")
    ct       = ET.SubElement(color_el, "Color")
    ct.set("redMultiplier",   "1.000000")
    ct.set("greenMultiplier", "1.000000")
    ct.set("blueMultiplier",  "1.000000")
    ct.set("alphaMultiplier", f"{alpha:.6f}")


def write_matrix_el(parent: ET.Element, matrix: tuple) -> None:
    """Cocos (a,b,c,d,tx,ty) Y-up → Flash Y-down."""
    a, b, c, d, tx, ty = matrix
    def fmt(v: float) -> str:
        return f"{v:.6f}" if abs(v) > 1e-9 else "0.000000"
    mx_el = ET.SubElement(parent, "matrix")
    m     = ET.SubElement(mx_el, "Matrix")
    m.set("a",  fmt(a))
    m.set("b",  fmt(-b))
    m.set("c",  fmt(-c))
    m.set("d",  fmt(d))
    m.set("tx", fmt(tx))
    m.set("ty", fmt(-ty))


# ─────────────────────────────────────────────────────────────────────────────
# XML serialisation
# ─────────────────────────────────────────────────────────────────────────────

def _indent_xml(elem: ET.Element, level: int = 0, indent: str = "    ") -> None:
    prefix       = "\n" + indent * level
    child_prefix = "\n" + indent * (level + 1)
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = child_prefix
        if not elem.tail or not elem.tail.strip():
            elem.tail = prefix
        for child in elem:
            _indent_xml(child, level + 1, indent)
        if not child.tail or not child.tail.strip():
            child.tail = prefix
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = prefix


def write_xml(root: ET.Element, path: str) -> None:
    """Write an ElementTree to *path* with no XML declaration (Animate requires this)."""
    _indent_xml(root)
    content = ET.tostring(root, encoding="unicode")
    # Collapse <tag></tag> → <tag/>
    content = re.sub(r"<(\w+)([^>]*)></\1>", r"<\1\2/>", content)
    # Wrap <script> text in CDATA
    content = re.sub(
        r"<script>([^<]*)</script>",
        lambda m: f"<script><![CDATA[{m.group(1)}]]></script>",
        content,
    )
    content = content.replace(" />", "/>")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


# ─────────────────────────────────────────────────────────────────────────────
# Name helpers
# ─────────────────────────────────────────────────────────────────────────────

def mc_safe_name(mc: dict, mc_idx: int) -> str:
    """Return a filesystem-safe version of the MC name, preserving it exactly."""
    raw = mc.get("name", "") or f"MC_{mc_idx}"
    # Replace chars illegal in filenames
    for ch in r'/\:*?"<>|':
        raw = raw.replace(ch, "_")
    # Strip leading/trailing whitespace — a space at the start becomes a leading
    # underscore after replacement, turning e.g. " ground_swatch" into "_ground_swatch"
    raw = raw.strip()
    return raw or f"MC_{mc_idx}"
