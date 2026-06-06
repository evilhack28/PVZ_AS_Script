"""
test_crop.py
------------
Standalone test: parse a FBIN or RawBin file, crop every sprite from the
atlas PNG (or PVR), and save them to a folder. No renderer, no pygame
animation — just raw coordinate verification.

Usage:
    python test_crop.py --bin zombie_sheep_hurt.bin --atlas zombie_sheep_hurt.png --out crops
    python test_crop.py --bin zombie_sheep_hurt.bin --pvr  zombie_sheep_hurt.pvr  --out crops

Requires: Pillow  (pip install Pillow)
"""

import argparse
import os
import struct
import sys

# Register library subfolders on sys.path so flat project imports resolve.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401

from fbin_parser import parse_fbin


def parse_images(bin_path):
    images, _mcs, _actions, is_raw = parse_fbin(bin_path)
    if images is None:
        print(f"ERROR: failed to parse {bin_path}")
        sys.exit(1)
    fmt = "RawBin" if is_raw else "FBIN"
    print(f"Format: {fmt}   num_images = {len(images)}")
    for i, img in enumerate(images):
        tx = int(img['tex_x']); ty = int(img['tex_y'])
        wi = int(img['width']); hi = int(img['height'])
        print(f"  [{i:2d}] {img.get('name', ''):20s}  "
              f"tex=({tx:4d},{ty:4d})  size=({wi}x{hi})")
    return images


def load_atlas_png(path):
    from PIL import Image
    return Image.open(path).convert('RGBA')


def load_atlas_pvr(path):
    """Minimal RGBA4444 PVR loader using only stdlib + Pillow."""
    from PIL import Image
    with open(path, 'rb') as f:
        data = f.read()

    if data[44:48] != b'PVR!':
        print("ERROR: not a PVR v2 file")
        sys.exit(1)

    header_len = struct.unpack_from('<I', data,  0)[0]
    height     = struct.unpack_from('<I', data,  4)[0]
    width      = struct.unpack_from('<I', data,  8)[0]
    flags      = struct.unpack_from('<I', data, 16)[0]
    data_len   = struct.unpack_from('<I', data, 20)[0]
    pixel_type = flags & 0xFF
    pixel_data = data[header_len: header_len + data_len]

    print(f"PVR: {width}x{height}  pixel_type=0x{pixel_type:02X}")

    if pixel_type in (0x10, 0x0C):  # RGBA4444
        n   = width * height
        out = bytearray(n * 4)
        for i in range(n):
            b0 = i * 2
            w16 = pixel_data[b0] | (pixel_data[b0+1] << 8)
            r = ((w16 >> 12) & 0xF) * 17
            g = ((w16 >>  8) & 0xF) * 17
            b = ((w16 >>  4) & 0xF) * 17
            a = ( w16        & 0xF) * 17
            p = i * 4
            out[p]=r; out[p+1]=g; out[p+2]=b; out[p+3]=a
        return Image.frombytes('RGBA', (width, height), bytes(out))
    else:
        print(f"ERROR: unsupported PVR pixel_type 0x{pixel_type:02X} - use --atlas instead")
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bin',   required=True)
    ap.add_argument('--atlas', default=None, help='Pre-decoded PNG atlas')
    ap.add_argument('--pvr',   default=None, help='PVR texture file')
    ap.add_argument('--out',   default='crops')
    args = ap.parse_args()

    if not args.atlas and not args.pvr:
        print("ERROR: supply --atlas <png> or --pvr <pvr>")
        sys.exit(1)

    print(f"\nParsing {args.bin} ...")
    images = parse_images(args.bin)

    print(f"\nLoading atlas ...")
    atlas = load_atlas_png(args.atlas) if args.atlas else load_atlas_pvr(args.pvr)
    aw, ah = atlas.size
    print(f"Atlas size: {aw}x{ah}")

    os.makedirs(args.out, exist_ok=True)
    saved = skipped = 0

    print(f"\nCropping sprites -> {args.out}/")
    for img in images:
        tx, ty  = int(img['tex_x']), int(img['tex_y'])
        w,  h   = int(img['width']), int(img['height'])
        name    = img['name'].replace('/', '_').replace('\\', '_')

        if w <= 0 or h <= 0:
            print(f"  SKIP  {name}  (zero size)")
            skipped += 1
            continue
        if tx < 0 or ty < 0 or tx + w > aw or ty + h > ah:
            print(f"  SKIP  {name}  tex=({tx},{ty}) size=({w}x{h}) OUT OF BOUNDS")
            skipped += 1
            continue

        crop = atlas.crop((tx, ty, tx + w, ty + h))
        out_path = os.path.join(args.out, f"{name}.png")
        crop.save(out_path)
        print(f"  SAVED {name}  tex=({tx},{ty}) size=({w}x{h})")
        saved += 1

    print(f"\nDone: {saved} saved, {skipped} skipped")


if __name__ == '__main__':
    main()
