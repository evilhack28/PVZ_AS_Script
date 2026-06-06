"""
xfl_exporter.py
---------------
Exports a Cocos2d-x FBIN / RawBin animation as an Adobe Animate XFL project.

Output structure
================
  <stem>.xfl/
  ├── main.xfl                  ← "PROXY-CS5" marker file
  ├── DOMDocument.xml           ← project root
  └── library/
      ├── media/                ← individual sprite PNGs
      ├── image/                ← one 1-frame graphic XML per bitmap
      ├── sprite/               ← one graphic XML per FBIN movie-clip
      └── label/                ← one animated graphic XML per action

Modules
=======
  xfl_helpers.py   — shared XML helpers and constants
  xfl_media.py     — Step 1: cut PNGs from atlas → library/media/
  xfl_image.py     — Step 2: write image symbols → library/image/
  xfl_sprite.py    — Step 3: write sprite symbols → library/sprite/
  xfl_label.py     — Step 4: write label symbols  → library/label/
  xfl_document.py  — Step 5: write DOMDocument.xml
"""
from __future__ import annotations

import logging
import os
import zipfile

from xfl_media    import export_media
from xfl_image    import export_image_symbols
from xfl_sprite   import export_sprite_symbols
from xfl_label    import export_label_symbols
from xfl_document import export_domdocument

log = logging.getLogger(__name__)


def export_xfl(
    images:      list,
    movie_clips: list,
    actions:     list,
    texture_png: str,
    out_dir:     str  = ".",
    stem:        str  = "character",
    fps:         int  = 24,
    rawbin:      bool = False,
    anim_meta         = None,
    define_key:  str  = "",
    scale:       float = 1.0,
    resolution:  int  = 1536,
) -> str:
    """Export the animation and return the path to the .xfl project folder."""
    if not os.path.isfile(texture_png):
        raise FileNotFoundError(f"Texture PNG not found: {texture_png!r}")

    from PIL import Image as PilImage
    atlas = PilImage.open(texture_png).convert("RGBA")

    # Build PascalCase name for the .package folder
    stem_lower  = stem.lower().replace("-", "_")
    pascal_name = "".join(w.capitalize() for w in stem_lower.split("_"))

    # Package root: <out_dir>/<PascalCase>.package/
    pkg_dir    = os.path.join(out_dir, pascal_name + ".package")
    # XFL lives at: <package>/resource/images/<stem_lower>/
    xfl_dir    = os.path.join(pkg_dir, "resource", "images", stem_lower)
    lib_dir    = os.path.join(xfl_dir, "library")
    media_dir  = os.path.join(lib_dir, "media")
    image_dir  = os.path.join(lib_dir, "image")
    sprite_dir = os.path.join(lib_dir, "sprite")
    label_dir  = os.path.join(lib_dir, "label")

    for d in (xfl_dir, lib_dir, media_dir, image_dir, sprite_dir, label_dir):
        os.makedirs(d, exist_ok=True)

    with open(os.path.join(xfl_dir, "main.xfl"), "w") as fh:
        fh.write("PROXY-CS5")

    # 1536: sprites are upscaled ×1.28 and image XML scale set to 0.78125
    # 1200: native sprite sizes and scale 1.0
    img_scale = 0.781250 if resolution == 1536 else 1.0
    upscale   = (1.0 / img_scale) if resolution == 1536 else 1.0

    sprite_info = export_media(images, atlas, stem, media_dir, movie_clips, upscale=upscale)
    export_image_symbols(images, sprite_info, image_dir, img_scale=img_scale)
    export_sprite_symbols(images, movie_clips, sprite_info, sprite_dir, scale=scale)
    export_label_symbols(movie_clips, actions, images, sprite_info, label_dir,
                         anim_meta=anim_meta, define_key=define_key, scale=scale)

    atlas_w, atlas_h = atlas.size
    export_domdocument(
        images, movie_clips, actions, sprite_info,
        stem, fps, atlas_w, atlas_h, xfl_dir,
    )

    _write_data_json(images, sprite_info, xfl_dir, resolution=resolution)

    _write_resource_bundle(stem, pkg_dir, resolution=resolution)

    log.info("XFL → %s", xfl_dir)
    return xfl_dir


def _write_data_json(images: list, sprite_info: dict, xfl_dir: str, resolution: int = 1536) -> None:
    """
    Write data.json into the XFL root folder.

    Format::

        {
            "<sprite_name>": { "width": W, "height": H },
            ...
        }

    One entry per unique sprite exported to library/media/.
    """
    import json

    # Build id prefix from stem: e.g. "zombie_ladder" → "IMAGE_ZOMBIE_LADDER"
    id_prefix = "IMAGE_" + os.path.basename(xfl_dir).replace(".xfl", "").upper()

    image: dict = {}
    for img_idx, base in sprite_info.items():
        if not base:
            continue
        img = images[img_idx]
        w   = int(img.get("width",  0))
        h   = int(img.get("height", 0))
        if w > 0 and h > 0:
            image[base] = {
                "id":        f"{id_prefix}_{base.upper()}",
                "dimension": {"width": w, "height": h},
                "additional": None,
            }

    data = {
        "version":    6,
        "resolution": resolution,
        "position":   {"x": 0, "y": 0},
        "image":      image,
        "sprite":     {},
    }

    out_path = os.path.join(xfl_dir, "data.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent="\t")
    log.info("data.json written (%d entries)", len(image))





def _write_resource_bundle(stem: str, pkg_dir: str, resolution: int = 1536) -> None:
    """
    Create the resource bundle folder and its data.json alongside the XFL.

    Output (next to the .xfl folder):
        <out_dir>/
        └── resource/
            └── <PascalCase>/          e.g. ZombieGreetwallKing/
                └── data.json

    data.json schema::

        {
            "#expand_method": "advanced",
            "version": 4,
            "texture_format_category": 0,
            "composite": true,
            "category": {
                "resolution": [1536, 768],
                "format": 30
            },
            "subgroup": {
                "<PascalCase>": {
                    "category": {
                        "common_type": true,
                        "locale": null,
                        "compression": 1
                    },
                    "resource": {
                        "POPANIM_<STEM_UPPER>": {
                            "type": "PopAnim",
                            "path": "images/<stem_lower>"
                        }
                    }
                }
            }
        }
    """
    import json

    # Derive naming variants from the stem
    stem_lower  = stem.lower().replace("-", "_")
    pascal_name = "".join(w.capitalize() for w in stem_lower.split("_"))
    popanim_key = "POPANIM_" + stem_lower.upper()
    anim_path   = f"images/{stem_lower}"

    # data.json sits at the package root (already created)
    os.makedirs(pkg_dir, exist_ok=True)
    bundle_dir = pkg_dir

    if resolution == 1536:
        tex_fmt_cat = 0
        res_arr     = [1536, 768]
        fmt         = 147
        compression = 3
    else:
        tex_fmt_cat = 1
        res_arr     = [1200, 600]
        fmt         = 147
        compression = 1

    data = {
        "#expand_method": "advanced",
        "version": 4,
        "texture_format_category": tex_fmt_cat,
        "composite": True,
        "category": {
            "resolution": res_arr,
            "format": fmt,
        },
        "subgroup": {
            pascal_name: {
                "category": {
                    "common_type": True,
                    "locale": None,
                    "compression": compression,
                },
                "resource": {
                    popanim_key: {
                        "type": "PopAnim",
                        "path": anim_path,
                    }
                },
            }
        },
    }

    out_path = os.path.join(bundle_dir, "data.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent="\t")
    log.info("Resource bundle → %s", out_path)
if __name__ == "__main__":
    import argparse, sys

    _script_dir = os.path.dirname(os.path.abspath(__file__))
    if _script_dir not in sys.path:
        sys.path.insert(0, _script_dir)

    p = argparse.ArgumentParser(description="Export FBIN animation to XFL / .fla")
    p.add_argument("--bin",   required=True)
    p.add_argument("--atlas", required=True)
    p.add_argument("scale", nargs="?", default=None,
                   help="Scale factor: 1.28 (bigger) or 0.78 (smaller)")
    p.add_argument("--out",        default=None)
    p.add_argument("--stem",       default=None)
    p.add_argument("--resolution", default=1536, type=int, choices=[1200, 1536])
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    bin_path   = os.path.abspath(args.bin)
    atlas_path = os.path.abspath(args.atlas)
    stem       = args.stem or os.path.splitext(os.path.basename(bin_path))[0]
    out_dir    = os.path.abspath(args.out) if args.out else os.path.dirname(bin_path)

    from fbin_parser import parse_fbin
    images, movie_clips, actions, rawbin = parse_fbin(bin_path)
    if images is None:
        print("ERROR: failed to parse", bin_path); sys.exit(1)

    fps = 24
    if actions:
        midx = actions[0].get("mc_idx", -1)
        if 0 <= midx < len(movie_clips):
            fps = movie_clips[midx].get("frame_rate", 24) or 24

    print(f"Parsed : {len(images)} images, {len(movie_clips)} clips, {len(actions)} actions  [rawbin={rawbin}]")
    print(f"Output : {out_dir}  stem={stem}  fps={fps}")

    scale = 1.0
    if args.scale is not None:
        try:
            scale = abs(float(args.scale))
        except ValueError:
            pass

    xfl_path = export_xfl(
        images=images, movie_clips=movie_clips, actions=actions,
        texture_png=atlas_path, out_dir=out_dir, stem=stem, fps=fps,
        rawbin=rawbin, scale=scale, resolution=args.resolution,
    )
    print(f"\nDone!\n  XFL folder : {xfl_path}\n  .fla file  : {os.path.join(out_dir, stem + '.fla')}")
