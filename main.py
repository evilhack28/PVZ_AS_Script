"""
main.py
-------
Single entry point for the project. Launches the animation player on a
(.bin, .pvr) pair.

    python main.py                                                # interactive — pops up file pickers
    python main.py --bin path/to/char.bin --pvr path/to/char.pvr  # scripted

The parser (`parsers/fbin_parser.parse_binary`) and the PVR decoder
(`pvr/pvr_loader.load_pvr_texture` / `convert_pvr_to_png`) are still
importable on their own — this script just wires them together with the
player and a small CLI.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Register library subfolders (parsers/, pvr/, render/) on sys.path so the
# flat project imports (`from fbin_parser import ...`) resolve.
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

    root = tk.Tk()
    root.withdraw()                 # hide the empty root window
    root.attributes("-topmost", True)
    path = filedialog.askopenfilename(title=title,
                                      filetypes=filetypes,
                                      initialdir=start_dir)
    root.destroy()
    return path or None


def _resolve_inputs(args: argparse.Namespace) -> tuple[Path, Path]:
    """Return (bin_path, pvr_path), prompting interactively for whichever
    one wasn't passed on the CLI."""
    start_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "samples")
    if not os.path.isdir(start_dir):
        start_dir = os.path.dirname(os.path.abspath(__file__))

    bin_str = args.bin
    pvr_str = args.pvr

    if not bin_str:
        bin_str = _pick_file("Select .bin animation file",
                             [("Animation .bin", "*.bin"), ("All files", "*.*")],
                             start_dir)
    if not pvr_str:
        pvr_str = _pick_file("Select .pvr texture file",
                             [("PVR texture", "*.pvr"), ("All files", "*.*")],
                             start_dir)

    if not bin_str or not pvr_str:
        print("Error: both a .bin and a .pvr file are required.")
        sys.exit(1)

    return Path(bin_str).resolve(), Path(pvr_str).resolve()


# ─────────────────────────────────────────────────────────────────────────────
# Player runner
# ─────────────────────────────────────────────────────────────────────────────

def run_player(bin_path: Path, pvr_path: Path) -> None:
    """Launch the project's animation player on (bin, pvr)."""
    try:
        import pygame
    except ImportError:
        print("Error: pygame is required.  Run:  pip install pygame")
        sys.exit(1)

    pygame.init()
    pygame.display.set_mode((1, 1), pygame.NOFRAME)

    from fbin_parser import parse_fbin
    from pvr_loader  import load_pvr_texture
    from player      import Player, PlayerConfig

    images, movie_clips, actions, is_rawbin = parse_fbin(str(bin_path))
    if images is None or movie_clips is None:
        print(f"Error: failed to parse '{bin_path}'"); pygame.quit(); sys.exit(1)

    texture = load_pvr_texture(str(pvr_path))
    if texture is None:
        print(f"Error: failed to load PVR '{pvr_path}'"); pygame.quit(); sys.exit(1)

    cfg = PlayerConfig(pvr_name=pvr_path.stem, output_dir=str(bin_path.parent))
    try:
        player = Player(images, movie_clips, actions, texture, cfg,
                        rawbin=is_rawbin, define_key=bin_path.stem)
        player.run()
    except RuntimeError as exc:
        print(f"Player error: {exc}"); pygame.quit(); sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        description="Launch the animation player on a .bin + .pvr pair. "
                    "Omit either flag to be prompted with a file picker.")
    p.add_argument("--bin", metavar="PATH",
                   help="Path to .bin animation file (FBIN or RawBin). "
                        "If omitted, a file picker opens.")
    p.add_argument("--pvr", metavar="PATH",
                   help="Path to .pvr texture file. If omitted, a file picker opens.")
    args = p.parse_args()

    bin_path, pvr_path = _resolve_inputs(args)

    if not bin_path.exists() or not pvr_path.exists():
        print(f"Error: file not found ({bin_path if not bin_path.exists() else pvr_path}).")
        sys.exit(1)

    run_player(bin_path, pvr_path)


if __name__ == "__main__":
    main()
