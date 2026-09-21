"""Single entry point for the project."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Register library subfolders (parsers/, pvr/, render/) on sys.path so the flat project imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _paths  # noqa: F401


# ─────────────────────────────────────────────────────────────────────────────
# Interactive file picker (used when --bin / --pvr are omitted)
# ─────────────────────────────────────────────────────────────────────────────

def _pick_file(title: str, filetypes: list, start_dir: str) -> str | None:
    """Open a tkinter file-open dialog. Returns the chosen path or None."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        # tkinter unavailable — fall back to plain input()
        path = input(f"{title} (path): ").strip().strip('"').strip("'")
        return path or None

    try:
        root = tk.Tk()
    except tk.TclError:
        # Headless / no display — fall back to plain input()
        path = input(f"{title} (path): ").strip().strip('"').strip("'")
        return path or None
    root.withdraw()                 # hide the empty root window
    root.attributes("-topmost", True)
    try:
        path = filedialog.askopenfilename(title=title,
                                          filetypes=filetypes,
                                          initialdir=start_dir)
    finally:
        root.destroy()
    return path or None


def _sibling_with_suffix(path_str: str, suffixes) -> str | None:
    """If `path_str` exists, look for a file with the same stem and ONE of the given suffixes"""
    if not path_str:
        return None
    p = Path(path_str)
    if not p.exists():
        return None
    if isinstance(suffixes, str):
        suffixes = (suffixes,)
    for suf in suffixes:
        sibling = p.with_suffix(suf)
        if sibling.exists():
            return str(sibling)
    return None


def _resolve_inputs(args: argparse.Namespace) -> tuple[Path, Path]:
    """Return (bin_path, pvr_path)."""
    start_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "samples")
    if not os.path.isdir(start_dir):
        start_dir = os.path.dirname(os.path.abspath(__file__))

    bin_str = args.bin
    pvr_str = args.pvr

    # Auto-pair by stem when only one flag is supplied.
    if bin_str and not pvr_str:
        guess = _sibling_with_suffix(bin_str, (".pvr", ".png"))
        if guess:
            print(f"Auto-paired atlas: {guess}")
            pvr_str = guess
    elif pvr_str and not bin_str:
        guess = _sibling_with_suffix(pvr_str, ".bin")
        if guess:
            print(f"Auto-paired .bin: {guess}")
            bin_str = guess

    if not bin_str:
        bin_str = _pick_file("Select .bin animation file",
                             [("Animation .bin", "*.bin"), ("All files", "*.*")],
                             start_dir)
    if not pvr_str:
        pvr_str = _pick_file("Select atlas (.pvr or .png)",
                             [("Atlas texture", ("*.pvr", "*.png")),
                              ("PVR texture",   "*.pvr"),
                              ("PNG atlas",     "*.png"),
                              ("All files",     "*.*")],
                             start_dir)

    if not bin_str or not pvr_str:
        print("Error: both a .bin and a .pvr file are required.")
        sys.exit(1)

    return Path(bin_str).resolve(), Path(pvr_str).resolve()


# ─────────────────────────────────────────────────────────────────────────────
# Atlas loading (extension-agnostic — sniffs file magic)
# ─────────────────────────────────────────────────────────────────────────────

_PNG_MAGIC = b'\x89PNG\r\n\x1a\n'

def _load_atlas(atlas_path: str, pygame):
    """Load an atlas texture as a pygame.Surface"""
    from pvr_loader import load_pvr_texture
    try:
        with open(atlas_path, 'rb') as fh:
            head = fh.read(16)
    except OSError as exc:
        print(f"Error: cannot open atlas '{atlas_path}': {exc}")
        return None

    if head.startswith(_PNG_MAGIC):
        try:
            return pygame.image.load(atlas_path).convert_alpha()
        except Exception as exc:
            print(f"Error: failed to load PNG atlas '{atlas_path}': {exc}")
            return None
    # Anything else is treated as PVR (PVR2 'PVR!' at offset 44, PVR3 'PVR\x03' at offset 0
    surf = load_pvr_texture(atlas_path)
    return surf.convert_alpha() if surf is not None else None


# ─────────────────────────────────────────────────────────────────────────────
# Player runner
# ─────────────────────────────────────────────────────────────────────────────

def run_player(bin_path: Path, pvr_path: Path) -> None:
    """Launch the project's animation player on (bin, atlas)."""
    try:
        import pygame
    except ImportError:
        print("Error: pygame is required.  Run:  pip install pygame")
        sys.exit(1)

    pygame.init()
    pygame.display.set_mode((1, 1), pygame.NOFRAME)

    from fbin_parser import parse_fbin
    from player      import Player, PlayerConfig

    images, movie_clips, actions, is_rawbin = parse_fbin(str(bin_path))
    if images is None or movie_clips is None:
        print(f"Error: failed to parse '{bin_path}'"); pygame.quit(); sys.exit(1)

    texture = _load_atlas(str(pvr_path), pygame)
    if texture is None:
        print(f"Error: failed to load atlas '{pvr_path}'"); pygame.quit(); sys.exit(1)

    cfg = PlayerConfig(pvr_name=pvr_path.stem, output_dir=str(bin_path.parent))
    try:
        player = Player(images, movie_clips, actions, texture, cfg,
                        rawbin=is_rawbin, define_key=bin_path.stem,
                        loader=lambda: parse_fbin(str(bin_path)))
        player.run()
    except RuntimeError as exc:
        print(f"Player error: {exc}"); pygame.quit(); sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        description="Launch the animation player on a .bin + atlas pair. "
                    "Atlas may be .pvr or .png (content sniffed by magic). "
                    "Pass only one flag and the other is auto-paired by "
                    "matching the stem in the same folder. Pass neither and "
                    "a file picker opens for both.")
    p.add_argument("--bin", metavar="PATH",
                   help="Path to .bin animation file (FBIN or RawBin). "
                        "If omitted, the sibling atlas's stem is used to "
                        "find it, otherwise a file picker opens.")
    p.add_argument("--pvr", metavar="PATH",
                   help="Path to atlas texture (.pvr or .png — format detected "
                        "from magic bytes, not extension). If omitted, the "
                        "sibling .bin's stem is used to find it (tries .pvr "
                        "then .png), otherwise a file picker opens.")
    p.add_argument("--game-quirks", action="store_true",
                   help="Reproduce the game's own decoding bug: MinBin tag-1 "
                        "bytes are read unsigned, so small negative values in "
                        "FBIN files come out wrong (trembling helmets, offset "
                        "eyes). Default: decode as the encoder intended.")
    args = p.parse_args()
    if args.game_quirks:
        from input_buffer import set_game_quirks
        set_game_quirks(True)

    bin_path, pvr_path = _resolve_inputs(args)

    if not bin_path.exists() or not pvr_path.exists():
        print(f"Error: file not found ({bin_path if not bin_path.exists() else pvr_path}).")
        sys.exit(1)

    run_player(bin_path, pvr_path)


if __name__ == "__main__":
    main()
