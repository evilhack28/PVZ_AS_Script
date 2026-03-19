"""
xfl_media.py
------------
Step 1: cut individual sprite PNGs from the atlas into library/media/.
Returns sprite_info = {img_idx: base_name}.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)


def _build_image_name_map(images: list, movie_clips: list) -> dict:
    """
    Build {img_idx: mc_name} for every leaf MC.

    RawBin leaf MC: 1 frame, 1 element, is_mc=True, frame_index = image index.
      e.g. MC 'zombie_king_xiaobi_wai' has frame_index=16 → image[16]

    FBIN leaf MC: 1 frame, 1 element, is_mc=False, id = image index.
      e.g. MC 'body1' has id=0 → image[0]

    Images not covered by any leaf MC keep their raw FBIN name as fallback.
    """
    name_map: dict = {}
    for mc in movie_clips:
        frames = mc.get("frames", [])
        if len(frames) != 1 or len(frames[0]) != 1:
            continue
        elem    = frames[0][0]
        mc_name = mc.get("name", "").strip()
        if not mc_name or mc_name.startswith("<BINARY"):
            continue

        if elem.get("is_mc", False):
            # RawBin: frame_index is the image index
            img_idx = elem.get("frame_index", -1)
        else:
            # FBIN: id is the image index
            img_idx = elem.get("id", -1)

        if not (0 <= img_idx < len(images)):
            continue
        if img_idx not in name_map:
            name_map[img_idx] = mc_name

    return name_map


def export_media(images: list, atlas, stem: str, media_dir: str,
                 movie_clips: list = None) -> dict:
    """
    Crop each image out of *atlas* and save to *media_dir*.

    Returns {img_idx: base_name} where base_name is the image's own name
    from the parser (e.g. "legend_zombie_Sprinter_118x86"), falling back to
    "{stem}_{W}x{H}" for unnamed images.
    Duplicate names get a _2, _3 … suffix.
    None entries mean the image was skipped (invalid / out-of-bounds).
    """
    tw, th     = atlas.size
    result:    dict = {}
    used_names: dict = {}

    # Build MC-name → img_idx map so leaf sprites get proper names
    mc_name_map = _build_image_name_map(images, movie_clips or [])

    for idx, img in enumerate(images):
        tx = int(img.get("tex_x", 0))
        ty = int(img.get("tex_y", 0))
        w  = int(img.get("width",  0))
        h  = int(img.get("height", 0))

        # ── validity checks ───────────────────────────────────────────────────
        if w <= 0 or h <= 0:
            result[idx] = None; continue
        if tx < 0 or ty < 0 or tx + w > tw or ty + h > th:
            result[idx] = None; continue
        if tx == 0 and ty == 0 and w <= 4 and h <= 4:
            result[idx] = None; continue  # tiny top-left placeholder

        # ── derive name ───────────────────────────────────────────────────────
        if idx in mc_name_map:
            # Leaf MC name wins: e.g. "zombie_king_xiaobi_wai"
            base = mc_name_map[idx]
        else:
            # Fallback: strip leading "NNN_" prefix from raw FBIN name
            # e.g. "016_96x112" -> use stem+dimensions instead
            # e.g. "001_131x4"  -> "Zombie_Greetwall_King_131x4"
            raw = img.get("name", "").strip()
            import re
            raw_stripped = re.sub(r'^\d+_', '', raw)  # remove "016_" prefix
            if raw_stripped and not re.match(r'^\d+x\d+$', raw_stripped):
                # Has a real name after stripping prefix
                base = raw_stripped
            else:
                # Pure dimension name or empty — use stem + dimensions
                # PascalCase the stem: "zombie_greetwall_king" -> "Zombie_Greetwall_King"
                pascal_stem = "_".join(w.capitalize() for w in stem.replace("-","_").split("_"))
                base = f"{pascal_stem}_{w}x{h}"

        base = base.replace("/", "_").replace("\\", "_").replace(":", "_").strip()

        count = used_names.get(base, 0) + 1
        used_names[base] = count
        if count > 1:
            base = f"{base}_{count}"

        # ── crop & save ───────────────────────────────────────────────────────
        png_path = os.path.join(media_dir, base + ".png")
        if not os.path.exists(png_path):
            atlas.crop((tx, ty, tx + w, ty + h)).save(png_path, "PNG")
            log.debug("Saved media/%s.png", base)

        result[idx] = base

    log.info("Media: %d images exported (%d skipped)",
             sum(1 for v in result.values() if v),
             sum(1 for v in result.values() if not v))
    return result
