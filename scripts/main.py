"""
main.py
-------
Entry point for Cocos Animation Player v2 (refactored).

Usage
-----
    python main.py --bin animation.bin --pvr texture.pvr [options]
    python main.py --bin animation.bin --atlas texture.png [options]

Options
-------
--bin        PATH   Path to the FBIN animation file  (required)
--pvr        PATH   Path to the PVR texture file      (required unless --atlas used)
--atlas      PATH   Path to a PNG/BMP/etc. atlas image (overrides --pvr)
--meta       PATH   Path to the game's animation metadata TSV/CSV file (optional)
                    Provides per-action scale, offset, fps, flip, and frame-range
                    overrides read from the game data.  If omitted the animation
                    is played using the raw FBIN values.
--define     STR    Character define key used to look up rows in the meta file,
                    e.g. "zombie_pirate_imp".  Defaults to the --bin filename stem.
--width      INT    Window width   (default: 1024)
--height     INT    Window height  (default: 768)
--fps        INT    FPS cap        (default: 60)
--action     INT    Starting action index (default: 0)
--no-loop           Start with looping disabled
--log-level  LVL    Logging level: DEBUG, INFO, WARNING, ERROR (default: INFO)
"""

import argparse
import logging
import os
import sys

# Register library subfolders on sys.path so flat project imports resolve.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cocos Animation Player v2",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--bin",       required=True,  metavar="PATH",
                        help="Path to .bin / FBIN animation file")
    parser.add_argument("--pvr",       required=False, metavar="PATH", default=None,
                        help="Path to .pvr texture file")
    parser.add_argument("--atlas",     required=False, metavar="PATH", default=None,
                        help="Path to a pre-decoded atlas image (PNG/BMP/etc.) – overrides --pvr")
    parser.add_argument("--meta",      required=False, metavar="PATH", default=None,
                        help="Path to the game metadata TSV/CSV file (optional). "
                             "If omitted, animaction.txt in the script folder is used automatically.")
    parser.add_argument("--no-meta",   action="store_true",
                        help="Disable automatic animaction.txt loading")
    parser.add_argument("--define",    required=False, metavar="STR",  default=None,
                        help="Character define key for meta lookup (default: bin filename stem)")
    parser.add_argument("--width",     type=int, default=1024, metavar="INT",
                        help="Window width  (default: 1024)")
    parser.add_argument("--height",    type=int, default=768,  metavar="INT",
                        help="Window height (default: 768)")
    parser.add_argument("--fps",       type=int, default=60,   metavar="INT",
                        help="FPS cap (default: 60)")
    parser.add_argument("--action",    type=int, default=0,    metavar="INT",
                        help="Starting action index (default: 0)")
    parser.add_argument("--no-loop",   action="store_true",
                        help="Disable looping on start-up")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        help="Logging verbosity (default: INFO)")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    # Must supply at least one of --pvr or --atlas
    if not args.pvr and not args.atlas:
        print("Error: supply --pvr <texture.pvr>  or  --atlas <atlas.png>")
        sys.exit(1)

    # Common mistake: passing the .bin as both arguments
    tex_path = args.atlas or args.pvr or ""
    if tex_path.lower().endswith(".bin"):
        print(f"Error: '{tex_path}' looks like an animation file, not a texture.")
        print("  --pvr   expects a .pvr file")
        print("  --atlas expects a .png / .bmp image file")
        sys.exit(1)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)-8s %(name)s: %(message)s",
    )
    log = logging.getLogger(__name__)

    try:
        import pygame
    except ImportError:
        print("Error: pygame is not installed.  Run:  pip install pygame")
        sys.exit(1)

    pygame.init()

    from fbin_parser import parse_fbin
    from player      import Player, PlayerConfig

    # ── Derive the bin stem (used as export folder and default define key) ────
    bin_stem = os.path.splitext(os.path.basename(args.bin))[0]

    if not os.path.isfile(args.bin):
        print(f"Error: bin file not found: {args.bin}")
        pygame.quit()
        sys.exit(1)

    log.info("Loading animation data: %s", args.bin)
    images, movie_clips, actions, is_rawbin = parse_fbin(args.bin)
    if images is None or movie_clips is None:
        print(f"Error: failed to parse '{args.bin}'")
        pygame.quit()
        sys.exit(1)

    # ── Terminal summary ──────────────────────────────────────────────────────
    # Meta status (needed for the summary, computed before the meta-load block)
    _meta_status = "none"
    if not getattr(args, 'no_meta', False):
        if getattr(args, 'meta', None):
            _meta_status = args.meta
        else:
            _bin_dir_p    = os.path.dirname(os.path.abspath(args.bin))
            _script_dir_p = os.path.dirname(os.path.abspath(__file__))
            for _d in dict.fromkeys([_bin_dir_p, os.getcwd(), _script_dir_p]):
                if os.path.isfile(os.path.join(_d, "animaction.txt")):
                    _meta_status = f"animaction.txt ({_d})"
                    break

    fmt_tag = "RawBin" if is_rawbin else "FBIN"
    print()
    print(f"Code_Name  : {bin_stem}")
    print(f"Format     : {fmt_tag}  |  Images: {len(images)}  Clips: {len(movie_clips)}  Actions: {len(actions)}")
    print(f"Meta       : {_meta_status}")

    if actions:
        print()
        for act in actions:
            midx = act.get('mc_idx', -1)
            mc   = movie_clips[midx] if 0 <= midx < len(movie_clips) else None
            mcn  = mc['name'] if mc else '?'
            print(f"  Action : {act.get('name', '?'):<20s}  →  Movie_Clip : {mcn}")

    print()

    # ── Load optional metadata ────────────────────────────────────────────────
    # Priority: --meta path > auto-discovered animaction.txt > nothing
    # Use --no-meta to suppress automatic loading entirely.
    anim_meta  = None
    meta_source = None

    if not args.no_meta:
        if args.meta:
            meta_source = args.meta
        else:
            # Auto-discover animaction.txt — check these locations in order:
            #   1. folder containing the .bin file
            #   2. current working directory
            #   3. folder containing main.py
            _bin_dir    = os.path.dirname(os.path.abspath(args.bin))
            _cwd        = os.getcwd()
            _script_dir = os.path.dirname(os.path.abspath(__file__))
            for _search_dir in dict.fromkeys([_bin_dir, _cwd, _script_dir]):
                _auto_path = os.path.join(_search_dir, "animaction.txt")
                if os.path.isfile(_auto_path):
                    meta_source = _auto_path
                    log.info("Auto-detected metadata: %s", _auto_path)
                    break
            if not meta_source:
                log.debug("animaction.txt not found in: %s",
                          ', '.join(dict.fromkeys([_bin_dir, _cwd, _script_dir])))

    if meta_source:
        try:
            from anim_meta import AnimMeta
        except ImportError:
            log.warning("anim_meta module not found – metadata support disabled.")
            meta_source = None
            AnimMeta = None

    if meta_source and AnimMeta is not None:
        log.info("Loading animation metadata: %s", meta_source)
        anim_meta = AnimMeta.load(action_tsv=meta_source, particle_tsv=meta_source)
        if anim_meta.is_empty():
            log.warning("Metadata file loaded but contained no recognised tables.")
            anim_meta = None

    # The define key used for meta lookups: explicit --define wins, else bin stem
    define_key = args.define or bin_stem

    # ── Init display before loading textures (required for PNG via pygame) ───
    pygame.display.set_mode((1, 1), pygame.NOFRAME)

    # ── Load texture ──────────────────────────────────────────────────────────
    texture   = None
    tex_stem  = bin_stem
    _pvr_info = None

    if args.atlas:
        log.info("Loading atlas image: %s", args.atlas)
        try:
            texture  = pygame.image.load(args.atlas).convert_alpha()
            w, h     = texture.get_size()
            tex_stem = os.path.splitext(os.path.basename(args.atlas))[0]
            log.info("Atlas loaded: %dx%d", w, h)
            _pvr_info = None
        except Exception as exc:
            log.error("Failed to load atlas '%s': %s", args.atlas, exc)
            _pvr_info = None

    if texture is None and args.pvr:
        from pvr_loader import load_pvr_texture, probe_pvr
        _pvr_info = probe_pvr(args.pvr)
        log.info("Loading texture: %s", args.pvr)
        texture  = load_pvr_texture(args.pvr)
        tex_stem = os.path.splitext(os.path.basename(args.pvr))[0]

    # ── Texture summary ───────────────────────────────────────────────────────
    if args.pvr and _pvr_info is not None:
        p      = _pvr_info
        status = "OK" if texture is not None else "FAILED"
        print(f"Texture   : {args.pvr}")
        print(f"           {p['container']}  {p['width']}x{p['height']}  "
              f"{p['format_name']} ({p['bpp']}bpp)  {status}")
        print()

    if not images or not movie_clips or texture is None:
        log.error("Failed to load required resources – aborting.")
        pygame.quit()
        sys.exit(1)

    # ── Launch player ─────────────────────────────────────────────────────────
    cfg = PlayerConfig(
        window_width  = args.width,
        window_height = args.height,
        fps_cap       = args.fps,
        start_action  = args.action,
        loop          = not args.no_loop,
        pvr_name      = tex_stem,
        output_dir    = os.path.dirname(os.path.abspath(args.bin)),
    )

    try:
        player = Player(
            images, movie_clips, actions, texture, cfg,
            rawbin      = is_rawbin,
            anim_meta   = anim_meta,
            define_key  = define_key,
            meta_source = meta_source or "",
        )
        player.run()
    except RuntimeError as exc:
        log.error("Player error: %s", exc)
        pygame.quit()
        sys.exit(1)


if __name__ == "__main__":
    main()
