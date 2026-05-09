"""
xfl_main.py
-----------
Entry point for the FBIN → XFL export pipeline.

Usage
=====
    python xfl_main.py --bin char.bin --atlas char.png
    python xfl_main.py --bin char.bin --atlas char.png --out ./output --stem mychar
"""
from __future__ import annotations

import argparse
import logging
import os
import sys


def main():
    p = argparse.ArgumentParser(description="Export FBIN / RawBin animation to Adobe Animate XFL")
    p.add_argument("--bin",   required=True,  help="Path to .bin animation file")
    p.add_argument("--atlas", required=True,  help="Path to decoded PNG atlas")
    p.add_argument("--out",   default=None,   help="Output directory (default: same folder as --bin)")
    p.add_argument("--stem",  default=None,   help="Project name stem (default: bin filename without extension)")
    p.add_argument("scale", nargs="?", default=None,
                   help="Scale factor: 1.28 (bigger) or 0.78 (smaller). "
                        "Negative value like -0.78 also accepted.")
    p.add_argument("--fps",        default=None, type=int, help="Frame rate override")
    p.add_argument("--resolution", default=1536, type=int, choices=[1200, 1536],
                   help="Output resolution: 1200 (native sprites, scale=1.0) or "
                        "1536 (upscaled ×1.28, scale=0.78125) [default: 1536]")
    p.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    log = logging.getLogger(__name__)

    # ── resolve paths ─────────────────────────────────────────────────────────
    bin_path   = os.path.abspath(args.bin)
    atlas_path = os.path.abspath(args.atlas)
    stem       = args.stem or os.path.splitext(os.path.basename(bin_path))[0]
    out_dir    = os.path.abspath(args.out) if args.out else os.path.dirname(bin_path)

    # ── validate inputs ───────────────────────────────────────────────────────
    if not os.path.isfile(bin_path):
        print(f"ERROR: bin file not found: {bin_path}"); sys.exit(1)
    if not os.path.isfile(atlas_path):
        print(f"ERROR: atlas PNG not found: {atlas_path}"); sys.exit(1)

    # ── add script directory to path so all modules are importable ────────────
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    # ── step 1: parse the .bin file ───────────────────────────────────────────
    print("=" * 60)
    print("Step 1/6 — Parsing .bin file ...")
    from fbin_parser import parse_fbin
    images, movie_clips, actions, rawbin = parse_fbin(bin_path)
    if images is None:
        print(f"ERROR: failed to parse {bin_path}"); sys.exit(1)

    print(f"  Images     : {len(images)}")
    print(f"  Movie clips: {len(movie_clips)}")
    print(f"  Actions    : {len(actions)}")
    print(f"  Format     : {'RawBin' if rawbin else 'FBIN'}")

    # ── determine fps ─────────────────────────────────────────────────────────
    fps = args.fps
    if fps is None:
        if actions:
            midx = actions[0].get("mc_idx", -1)
            if 0 <= midx < len(movie_clips):
                fps = movie_clips[midx].get("frame_rate", 0) or 0
        fps = fps or 24
    print(f"  FPS        : {fps}")

    # ── step 2-6: export XFL ─────────────────────────────────────────────────
    print()
    print("Step 2/6 — Cutting sprites from atlas (media/) ...")
    print("Step 3/6 — Writing image symbols (image/) ...")
    print("Step 4/6 — Writing sprite symbols (sprite/) ...")
    print("Step 5/6 — Writing label symbols  (label/) ...")
    print("Step 6/6 — Writing DOMDocument.xml + zipping .fla ...")
    print()

    # Parse scale — accept "1.28", "-0.78", "0.78" etc.
    scale = 1.0
    if args.scale is not None:
        try:
            scale = float(args.scale)
            if scale < 0:
                scale = -scale  # treat -0.78 same as 0.78
            print(f"  Scale      : ×{scale}")
        except ValueError:
            print(f"WARNING: invalid scale '{args.scale}', using 1.0")

    from xfl_exporter import export_xfl
    xfl_path = export_xfl(
        images=images,
        movie_clips=movie_clips,
        actions=actions,
        texture_png=atlas_path,
        out_dir=out_dir,
        stem=stem,
        fps=fps,
        rawbin=rawbin,
        scale=scale,
        resolution=args.resolution,
    )

    fla_path = os.path.join(out_dir, stem + ".fla")

    print("=" * 60)
    print("Done!")
    print(f"  XFL folder : {xfl_path}")
    print(f"  FLA file   : {fla_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
