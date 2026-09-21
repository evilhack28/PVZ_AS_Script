"""Convert every .pvr in a folder to .png, then delete each .pvr once its PNG is verified."""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import _paths  # noqa: F401

from PIL import Image


def _pick_folder() -> str | None:
    """Ask for a folder (dialog, or the console when there is no display)."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askdirectory(title="Folder with .pvr files")
        root.destroy()
        return path or None
    except Exception:                                   # noqa: BLE001
        return input("Folder with .pvr files: ").strip().strip('"').strip("'") or None


def convert_one(pvr: Path, overwrite: bool, keep: bool, dry_run: bool) -> tuple[str, str]:
    """Convert one file. Returns (status, detail); the PVR is deleted only on 'ok'."""
    from pvr_loader import load_pvr_texture
    import pygame

    png = pvr.with_suffix('.png')
    if png.exists() and not overwrite:
        return 'skipped', 'png already exists (use --overwrite)'
    if dry_run:
        return 'dry-run', ''
    if pvr.stat().st_size < 52:
        return 'failed', f'not a valid PVR ({pvr.stat().st_size} bytes)'

    surf = load_pvr_texture(str(pvr))
    if surf is None:
        return 'failed', 'could not decode'
    size = surf.get_size()
    raw = pygame.image.tobytes(surf, 'RGBA')
    Image.frombytes('RGBA', size, raw).save(png)

    # Re-read the PNG and compare with the decode before touching the source.
    with Image.open(png) as check:
        ok = check.convert('RGBA').size == size and check.convert('RGBA').tobytes() == raw
    if not ok:
        png.unlink(missing_ok=True)
        return 'failed', 'written PNG did not match the decode'
    if not keep:
        pvr.unlink()
    return 'ok', f'{size[0]}x{size[1]}'


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('folder', nargs='?', help="Folder to process (asks if omitted).")
    p.add_argument('-r', '--recursive', action='store_true', help="Include subfolders.")
    p.add_argument('--keep', action='store_true', help="Keep the .pvr files.")
    p.add_argument('--overwrite', action='store_true', help="Replace an existing .png of the same name.")
    p.add_argument('--dry-run', action='store_true', help="List what would be converted, change nothing.")
    args = p.parse_args()

    folder = Path(args.folder or _pick_folder() or '')
    if not folder.is_dir():
        print(f"Error: '{folder}' is not a folder.")
        return 1

    files = sorted(folder.rglob('*.pvr') if args.recursive else folder.glob('*.pvr'))
    if not files:
        print(f"No .pvr files in {folder}")
        return 0
    print(f"{len(files)} .pvr files in {folder}"
          + ("  (dry run)" if args.dry_run else "  (PVRs are deleted after a verified conversion)"
             if not args.keep else "  (keeping PVRs)"))

    counts: dict = {}
    problems = []
    t0 = time.time()
    for i, pvr in enumerate(files, 1):
        try:
            status, detail = convert_one(pvr, args.overwrite, args.keep, args.dry_run)
        except Exception as exc:                        # noqa: BLE001
            status, detail = 'failed', repr(exc)[:120]
        counts[status] = counts.get(status, 0) + 1
        if status in ('failed', 'skipped'):
            problems.append((pvr.name, status, detail))
        if i % 50 == 0 or i == len(files):
            print(f"  {i}/{len(files)}  {time.time() - t0:.0f}s")

    print("\n" + ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    for name, status, detail in problems:
        print(f"  {status.upper():8s} {name}: {detail}")
    if any(s == 'failed' for _n, s, _d in problems):
        print("Failed files were left untouched.")
    return 1 if counts.get('failed') else 0


if __name__ == '__main__':
    sys.exit(main())
