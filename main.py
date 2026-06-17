"""
main.py
-------
Single entry point for the project. Launches the animation player on a
(.bin, .pvr) pair.

    python main.py                              # interactive — pops up file pickers
    python main.py --bin char.bin --pvr char.pvr   # scripted, both files explicit
    python main.py --bin char.bin               # auto-pairs char.pvr from the same folder
    python main.py --pvr char.pvr               # auto-pairs char.bin from the same folder

When only one flag is given, the other is found by looking for a file with
the same stem and the matching extension next to it. If the sibling is
missing, a file picker opens for it.

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


def _sibling_with_suffix(path_str: str, suffix: str) -> str | None:
    """If `path_str` exists, look for a file with the same stem and the given
    suffix in the same folder. Returns its path string or None.

        _sibling_with_suffix("…/1111111.bin", ".pvr") -> "…/1111111.pvr"
    """
    if not path_str:
        return None
    p = Path(path_str)
    if not p.exists():
        return None
    sibling = p.with_suffix(suffix)
    return str(sibling) if sibling.exists() else None


def _resolve_inputs(args: argparse.Namespace) -> tuple[Path, Path]:
    """Return (bin_path, pvr_path). For each missing CLI flag:
       1. try to find a same-stem sibling next to the file that WAS provided;
       2. otherwise fall back to a tkinter file picker.
    """
    start_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "samples")
    if not os.path.isdir(start_dir):
        start_dir = os.path.dirname(os.path.abspath(__file__))

    bin_str = args.bin
    pvr_str = args.pvr

    # Auto-pair by stem when only one flag is supplied.
    if bin_str and not pvr_str:
        guess = _sibling_with_suffix(bin_str, ".pvr")
        if guess:
            print(f"Auto-paired .pvr: {guess}")
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
                    "Pass only one flag and the other is auto-paired by "
                    "matching the stem (foo.bin <-> foo.pvr) in the same "
                    "folder. Pass neither and a file picker opens for both.")
    p.add_argument("--bin", metavar="PATH",
                   help="Path to .bin animation file (FBIN or RawBin). "
                        "If omitted, the sibling .pvr's stem is used to "
                        "find it, otherwise a file picker opens.")
    p.add_argument("--pvr", metavar="PATH",
                   help="Path to .pvr texture file. If omitted, the sibling "
                        ".bin's stem is used to find it, otherwise a file "
                        "picker opens.")
    args = p.parse_args()

    bin_path, pvr_path = _resolve_inputs(args)

    if not bin_path.exists() or not pvr_path.exists():
        print(f"Error: file not found ({bin_path if not bin_path.exists() else pvr_path}).")
        sys.exit(1)

    run_player(bin_path, pvr_path)


if __name__ == "__main__":
    main()
