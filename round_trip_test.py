"""
round_trip_test.py
------------------
Parse a RawBin file, rebuild it, and verify the MD5 is identical.

Usage
-----
    python round_trip_test.py path/to/file.bin
    python round_trip_test.py path/to/file.bin --save rebuilt.bin
"""

import sys
import argparse
import hashlib

from rawbin_writer import parse_rawbin_full, write_rawbin


def _hex_dump(data: bytes, center: int, radius: int = 16) -> str:
    start = max(0, center - radius)
    end   = min(len(data), center + radius + 1)
    lines = []
    for base in range(start, end, 16):
        chunk   = data[base: base + 16]
        hex_str = ' '.join(f'{b:02x}' for b in chunk)
        marker  = ' <-- DIFF' if base <= center < base + 16 else ''
        lines.append(f'  {base:08x}  {hex_str:<47}{marker}')
    return '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser(description='RawBin round-trip MD5 test')
    ap.add_argument('bin', help='Path to .bin file')
    ap.add_argument('--save', metavar='OUT', help='Write rebuilt bytes to this path')
    args = ap.parse_args()

    with open(args.bin, 'rb') as fh:
        original = fh.read()

    try:
        parsed  = parse_rawbin_full(original)
        rebuilt = write_rawbin(parsed)
    except Exception as exc:
        print(f'ERROR: {exc}')
        sys.exit(2)

    if args.save:
        with open(args.save, 'wb') as fh:
            fh.write(rebuilt)
        print(f'Saved rebuilt bytes to {args.save}')

    orig_md5    = hashlib.md5(original).hexdigest()
    rebuilt_md5 = hashlib.md5(rebuilt).hexdigest()

    if orig_md5 == rebuilt_md5:
        print(f'PASS  MD5 {orig_md5}  {args.bin}')
        sys.exit(0)

    # ── Failure diagnostics ───────────────────────────────────────────────────
    print(f'FAIL')
    print(f'  original MD5 : {orig_md5}')
    print(f'  rebuilt  MD5 : {rebuilt_md5}')
    print(f'  original size: {len(original)} bytes')
    print(f'  rebuilt  size: {len(rebuilt)} bytes')

    min_len    = min(len(original), len(rebuilt))
    diff_count = sum(1 for i in range(min_len) if original[i] != rebuilt[i])
    diff_count += abs(len(original) - len(rebuilt))
    print(f'  differing bytes: {diff_count}')

    first_diff = next((i for i in range(min_len) if original[i] != rebuilt[i]),
                      min_len if len(original) != len(rebuilt) else None)
    if first_diff is not None:
        print(f'\n  First difference at byte 0x{first_diff:08x} ({first_diff}):')
        print(f'    original : 0x{original[first_diff]:02x}  ({original[first_diff]})')
        print(f'    rebuilt  : 0x{rebuilt[first_diff]:02x}  ({rebuilt[first_diff]})')
        print(f'\n  Original around diff:')
        print(_hex_dump(original, first_diff))
        print(f'\n  Rebuilt around diff:')
        print(_hex_dump(rebuilt, first_diff))

    sys.exit(1)


if __name__ == '__main__':
    main()
