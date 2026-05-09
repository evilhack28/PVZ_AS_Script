#!/usr/bin/env python3
"""
XFL Sprite Converter: 1200 -> 1536
====================================
Converts a complete 1200 package into a 1536 package.

What it does:
  - Copies everything from your 1200 package (sprites, labels, DOMDocument.xml, main.xfl)
  - Upscales each PNG by (scale_1200 / 0.78125) so the sprite appears the same visual size
  - Updates image XMLs: sets a/d scale to 0.781250, keeps tx/ty offsets unchanged
  - Updates root data.json: resolution, texture_format_category, format, compression
  - Updates plant data.json: resolution field 1200 -> 1536

Usage:
  python convert_1200_to_1536.py <1200_package> <output_package>

Example:
  python convert_1200_to_1536.py "PlantPeashooter_1200.package" "PlantPeashooter_1536_custom.package"
"""

import sys
import re
import json
import shutil
from pathlib import Path
from PIL import Image

TARGET_SCALE = 0.781250

ROOT_JSON_1536 = {
    "resolution": [1536, 768],
    "texture_format_category": 0,
    "format": 147,
    "compression": 3,
}

_RE_A = re.compile(r'\ba="([^"]*)"')
_RE_D = re.compile(r'\bd="([^"]*)"')


def plant_dir(root: Path) -> Path:
    for p in root.rglob("DOMDocument.xml"):
        return p.parent
    raise FileNotFoundError(f"DOMDocument.xml not found inside {root}")


def convert_root_json(json_path: Path):
    data = json.loads(json_path.read_text(encoding="utf-8"))
    data["texture_format_category"] = ROOT_JSON_1536["texture_format_category"]
    data["category"]["resolution"]  = ROOT_JSON_1536["resolution"]
    data["category"]["format"]      = ROOT_JSON_1536["format"]
    for sg in data.get("subgroup", {}).values():
        sg["category"]["compression"] = ROOT_JSON_1536["compression"]
    json_path.write_text(json.dumps(data, indent="\t", ensure_ascii=False), encoding="utf-8")


def convert_plant_json(json_path: Path, scale_map: dict):
    """Update resolution and fix image dimensions for images whose scale changed."""
    data = json.loads(json_path.read_text(encoding="utf-8"))
    old = data.get("resolution")
    data["resolution"] = 1536
    fixed = 0
    for name, entry in data.get("image", {}).items():
        if name in scale_map:
            old_scale, new_png_w, new_png_h = scale_map[name]
            if abs(old_scale - TARGET_SCALE) > 0.01:
                new_w = round(new_png_w * TARGET_SCALE)
                new_h = round(new_png_h * TARGET_SCALE)
                entry["dimension"]["width"]  = new_w
                entry["dimension"]["height"] = new_h
                fixed += 1
    json_path.write_text(json.dumps(data, indent="\t", ensure_ascii=False), encoding="utf-8")
    return old, fixed


def get_scale_from_xml(xml_path: Path) -> float:
    text = xml_path.read_text(encoding="utf-8")
    m = _RE_A.search(text)
    return float(m.group(1)) if m else 1.0


def update_image_xml(xml_path: Path) -> float:
    """Set a/d to TARGET_SCALE, keep tx/ty. Returns the old scale."""
    text = xml_path.read_text(encoding="utf-8")
    old_scale = float(_RE_A.search(text).group(1)) if _RE_A.search(text) else 1.0
    text = _RE_A.sub(f'a="{TARGET_SCALE:.6f}"', text)
    text = _RE_D.sub(f'd="{TARGET_SCALE:.6f}"', text)
    xml_path.write_text(text, encoding="utf-8")
    return old_scale


def upscale_png(png_path: Path, factor: float):
    img = Image.open(png_path)
    w, h = img.size
    new_w = round(w * factor)
    new_h = round(h * factor)
    if new_w == w and new_h == h:
        return
    upscaled = img.resize((new_w, new_h), Image.LANCZOS)
    upscaled.save(png_path)


def convert(src: Path, dst: Path):
    print(f"\n{'='*60}")
    print(f"  XFL Converter: 1200 -> 1536")
    print(f"{'='*60}")
    print(f"  Source : {src}")
    print(f"  Output : {dst}")
    print(f"{'='*60}\n")

    # Step 1 — copy everything
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    print(f"[1/3] Copied full package to output")

    # Step 2 — update image XMLs and upscale PNGs
    pdir = plant_dir(dst)
    image_dir = pdir / "library" / "image"
    media_dir = pdir / "library" / "media"

    xml_files = sorted(image_dir.glob("*.xml")) if image_dir.exists() else []
    print(f"[2/3] Converting {len(xml_files)} image XMLs and upscaling PNGs...")

    changed = 0
    scale_map = {}  # image_name -> (old_scale, new_png_w, new_png_h)
    for xml_path in xml_files:
        old_scale = update_image_xml(xml_path)

        png_path = media_dir / (xml_path.stem + ".png")
        if png_path.exists():
            if abs(old_scale - TARGET_SCALE) > 1e-6:
                factor = old_scale / TARGET_SCALE
                upscale_png(png_path, factor)
                changed += 1
            img = Image.open(png_path)
            scale_map[xml_path.stem] = (old_scale, img.width, img.height)

    print(f"  [OK] {changed} PNGs upscaled, all image XMLs set to scale {TARGET_SCALE}")

    # Step 3 — update data.json files
    print(f"[3/3] Updating data.json files...")

    root_json = dst / "data.json"
    if root_json.exists():
        convert_root_json(root_json)
        print(f"  [OK] data.json (root)")
        print(f"       resolution             -> {ROOT_JSON_1536['resolution']}")
        print(f"       texture_format_category -> {ROOT_JSON_1536['texture_format_category']}")
        print(f"       format                 -> {ROOT_JSON_1536['format']}")
        print(f"       compression            -> {ROOT_JSON_1536['compression']}")
    else:
        print(f"  [SKIP] root data.json not found")

    plant_json = pdir / "data.json"
    if plant_json.exists():
        old_res, fixed_dims = convert_plant_json(plant_json, scale_map)
        print(f"  [OK] {plant_json.relative_to(dst)}")
        print(f"       resolution  {old_res} -> 1536")
        if fixed_dims:
            print(f"       {fixed_dims} image dimension(s) updated to 1536 rendered size")
    else:
        print(f"  [SKIP] plant data.json not found")

    total = sum(1 for _ in dst.rglob("*") if _.is_file())
    print(f"\n{'='*60}")
    print(f"  Done! {total} files in output package.")
    print(f"  Location: {dst.resolve()}")
    print(f"{'='*60}\n")


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        print("ERROR: expected exactly 2 arguments.")
        sys.exit(1)

    src = Path(sys.argv[1])
    dst = Path(sys.argv[2])

    if not src.exists():
        print(f"ERROR: source package not found: {src}")
        sys.exit(1)

    convert(src, dst)


if __name__ == "__main__":
    main()
