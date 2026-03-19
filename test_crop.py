"""
test_crop.py
------------
Standalone test: parse the FBIN, crop every sprite from the atlas PNG,
and save them to a folder. No renderer, no pygame animation — just raw
coordinate verification.

Usage:
    python test_crop.py --bin zombie_sheep_hurt.bin --atlas zombie_sheep_hurt.png --out crops
    python test_crop.py --bin zombie_sheep_hurt.bin --pvr  zombie_sheep_hurt.pvr  --out crops

Requires: Pillow  (pip install Pillow)
"""

import argparse
import os
import struct
import sys


def read_float_min(data, offset, divisor):
    tag = data[offset]; offset += 1
    if tag == 0:   return 0.0, offset
    elif tag == 1:
        v = struct.unpack_from('<b', data, offset)[0]; offset += 1
        return v / divisor, offset
    elif tag == 2:
        v = struct.unpack_from('<h', data, offset)[0]; offset += 2
        return v / divisor, offset
    elif tag == 3:
        v = struct.unpack_from('<i', data, offset)[0]; offset += 4
        return float(v) / divisor, offset
    elif tag == 4:
        # tag=4 = raw IEEE-754 float32 — value is near-zero garbage for
        # coordinates that are truly 0; int() of the result is still 0.
        v = struct.unpack_from('<f', data, offset)[0]; offset += 4
        return v, offset
    else:
        print(f"  WARNING: unknown MinBin tag {tag} at offset {offset-1}")
        return 0.0, offset


def parse_images(bin_path):
    with open(bin_path, 'rb') as f:
        data = f.read()

    if data[0:4] != b'FBIN':
        print("ERROR: not an FBIN file")
        sys.exit(1)

    offset = 4
    _ver1 = struct.unpack_from('<i', data, offset)[0]; offset += 4
    _ver2 = struct.unpack_from('<i', data, offset)[0]; offset += 4

    # Extended header: int32 + MinBin float
    ext_int = struct.unpack_from('<i', data, offset)[0]; offset += 4
    _ext_f, offset = read_float_min(data, offset, 100.0)

    num_images = struct.unpack_from('<h', data, offset)[0]; offset += 2
    print(f"num_images = {num_images}")

    images = []
    for i in range(num_images):
        slen = data[offset]; offset += 1
        name = data[offset:offset+slen].decode('utf-8', 'replace'); offset += slen

        off_x,  offset = read_float_min(data, offset, 100.0)
        off_y,  offset = read_float_min(data, offset, 100.0)
        w,      offset = read_float_min(data, offset, 100.0)
        h,      offset = read_float_min(data, offset, 100.0)
        tex_x,  offset = read_float_min(data, offset, 100.0)
        tex_y,  offset = read_float_min(data, offset, 100.0)
        orig_x, offset = read_float_min(data, offset, 100.0)
        orig_y, offset = read_float_min(data, offset, 100.0)

        # int() rounds near-zero floats (e.g. 5.84e-41) to 0 correctly
        tx, ty, wi, hi = int(tex_x), int(tex_y), int(w), int(h)

        images.append({
            'name': name, 'index': i,
            'tex_x': tx, 'tex_y': ty,
            'width': wi, 'height': hi,
            'offset_x': off_x, 'offset_y': off_y,
        })
        print(f"  [{i:2d}] {name:20s}  tex=({tx:4d},{ty:4d})  size=({wi}x{hi})")

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
        print(f"ERROR: unsupported PVR pixel_type 0x{pixel_type:02X} — use --atlas instead")
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

    print(f"\nCropping sprites → {args.out}/")
    for img in images:
        tx, ty  = img['tex_x'], img['tex_y']
        w,  h   = img['width'], img['height']
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
