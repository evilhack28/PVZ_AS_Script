"""
bin_diff.py
-----------
Structural diff between two FBIN / RawBin animation files.

Compares header (format, version, ext_float), image list, movie-clip list,
actions, and (optionally) the per-MC element references. Output focuses on
content changes - new sprites, renamed body parts, longer/shorter animations,
added/removed actions - not visual/atlas differences.

    python bin_diff.py a.bin b.bin
    python bin_diff.py a.bin b.bin --verbose          # per-MC element refs
    python bin_diff.py a.bin b.bin --by-index         # match MCs by index instead of name
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _paths  # noqa: F401

from fbin_parser import parse_binary


def _img_sig(im: dict) -> tuple:
    """Identity for an image: name + dimensions. Atlas coords ignored (atlas
    repack between versions is not a content change)."""
    return (im['name'], round(im['width'], 1), round(im['height'], 1))


def _mc_refs(mc: dict) -> tuple[Counter, Counter]:
    """Count (is_mc, id) references in this MC's frames. Returns (mc_refs, img_refs)."""
    mc_refs, img_refs = Counter(), Counter()
    for frame in mc['frames']:
        for e in frame:
            if e['is_mc']:
                mc_refs[e['id']] += 1
            else:
                img_refs[e['id']] += 1
    return mc_refs, img_refs


def _print_header(label_a: str, label_b: str, da: dict, db: dict) -> None:
    print(f"\n=== {label_a}  vs  {label_b} ===\n")
    print(f"  format:   {da['format']:8s} -> {db['format']}")
    ia, ib = da.get('info', {}), db.get('info', {})
    for key in ('version_tag', 'has_transform', 'order', 'ext_float',
                'clip_header_size', 'start_offset'):
        if key in ia or key in ib:
            va, vb = ia.get(key, '-'), ib.get(key, '-')
            mark = '' if va == vb else '   <- CHANGED'
            print(f"  {key:14s} {str(va):10s} -> {vb}{mark}")
    print(f"  images:   {len(da['images']):5d}    -> {len(db['images'])}")
    print(f"  mcs:      {len(da['movie_clips']):5d}    -> {len(db['movie_clips'])}")
    print(f"  actions:  {len(da['actions']):5d}    -> {len(db['actions'])}")


def _diff_images(da: dict, db: dict) -> None:
    a = {im['name']: im for im in da['images']}
    b = {im['name']: im for im in db['images']}
    added   = sorted(b.keys() - a.keys())
    removed = sorted(a.keys() - b.keys())
    resized = []
    for name in sorted(a.keys() & b.keys()):
        if _img_sig(a[name]) != _img_sig(b[name]):
            resized.append((name, a[name], b[name]))

    if not (added or removed or resized):
        print("\nImages: identical (by name + size)")
        return

    print(f"\nImages: +{len(added)}  -{len(removed)}  ~{len(resized)}")
    for name in added:
        im = b[name]
        print(f"  + {name!r:30s}  {int(im['width'])}x{int(im['height'])}")
    for name in removed:
        im = a[name]
        print(f"  - {name!r:30s}  {int(im['width'])}x{int(im['height'])}")
    for name, oa, ob in resized:
        print(f"  ~ {name!r:30s}  {int(oa['width'])}x{int(oa['height'])} -> "
              f"{int(ob['width'])}x{int(ob['height'])}")


def _diff_mcs(da: dict, db: dict, *, by_index: bool, verbose: bool) -> None:
    if by_index:
        # Index-matched diff: same slot, what changed
        amcs, bmcs = da['movie_clips'], db['movie_clips']
        max_i = max(len(amcs), len(bmcs))
        added, removed, changed, renamed = [], [], [], []
        for i in range(max_i):
            ma = amcs[i] if i < len(amcs) else None
            mb = bmcs[i] if i < len(bmcs) else None
            if ma is None: added.append((i, mb))
            elif mb is None: removed.append((i, ma))
            else:
                fa, fb = len(ma['frames']), len(mb['frames'])
                fra, frb = ma.get('frame_rate', 0), mb.get('frame_rate', 0)
                if ma['name'] != mb['name']:
                    renamed.append((i, ma, mb))
                elif fa != fb or fra != frb:
                    changed.append((i, ma, mb))
                elif verbose:
                    # Same name+frames - check ref counts
                    if _mc_refs(ma) != _mc_refs(mb):
                        changed.append((i, ma, mb))
    else:
        # Name-matched diff: what MCs were added/removed by name
        a = {mc['name']: (i, mc) for i, mc in enumerate(da['movie_clips'])}
        b = {mc['name']: (i, mc) for i, mc in enumerate(db['movie_clips'])}
        added   = [(b[n][0], b[n][1]) for n in sorted(b.keys() - a.keys())]
        removed = [(a[n][0], a[n][1]) for n in sorted(a.keys() - b.keys())]
        changed, renamed = [], []
        for name in sorted(a.keys() & b.keys()):
            ia, ma = a[name]
            ib, mb = b[name]
            fa, fb = len(ma['frames']), len(mb['frames'])
            fra, frb = ma.get('frame_rate', 0), mb.get('frame_rate', 0)
            if fa != fb or fra != frb:
                changed.append((ib, ma, mb))
            elif verbose and _mc_refs(ma) != _mc_refs(mb):
                changed.append((ib, ma, mb))

    if not (added or removed or changed or renamed):
        mode = "by index" if by_index else "by name"
        print(f"\nMovie Clips ({mode}): identical")
        return

    mode = "by index" if by_index else "by name"
    print(f"\nMovie Clips ({mode}): +{len(added)}  -{len(removed)}  "
          f"~{len(changed)}  renamed={len(renamed)}")
    for i, mc in added:
        print(f"  + [{i:3}] {mc['name']!r:45s} frames={len(mc['frames'])} "
              f"fps={mc.get('frame_rate', 0)}")
    for i, mc in removed:
        print(f"  - [{i:3}] {mc['name']!r:45s} frames={len(mc['frames'])} "
              f"fps={mc.get('frame_rate', 0)}")
    for i, ma, mb in renamed:
        print(f"  R [{i:3}] {ma['name']!r:30s} -> {mb['name']!r}")
    for i, ma, mb in changed:
        fa, fb = len(ma['frames']), len(mb['frames'])
        fra, frb = ma.get('frame_rate', 0), mb.get('frame_rate', 0)
        bits = []
        if fa != fb:    bits.append(f"frames {fa}->{fb}")
        if fra != frb:  bits.append(f"fps {fra}->{frb}")
        if not bits:    bits.append("refs changed")
        print(f"  ~ [{i:3}] {mb['name']!r:45s} " + ", ".join(bits))
        if verbose:
            ra_mc, ra_img = _mc_refs(ma)
            rb_mc, rb_img = _mc_refs(mb)
            for ref_label, ra, rb in [('MC', ra_mc, rb_mc), ('IMG', ra_img, rb_img)]:
                add_keys = sorted(set(rb) - set(ra))
                rm_keys  = sorted(set(ra) - set(rb))
                for k in add_keys: print(f"        + {ref_label}[{k}] refs={rb[k]}")
                for k in rm_keys: print(f"        - {ref_label}[{k}] refs={ra[k]}")


def _diff_actions(da: dict, db: dict) -> None:
    a = {ac['name']: ac for ac in da['actions']}
    b = {ac['name']: ac for ac in db['actions']}
    added   = sorted(b.keys() - a.keys())
    removed = sorted(a.keys() - b.keys())
    changed = []
    for name in sorted(a.keys() & b.keys()):
        if (a[name]['mc_idx'] != b[name]['mc_idx']
                or a[name]['start'] != b[name]['start']
                or a[name]['end']   != b[name]['end']):
            changed.append((name, a[name], b[name]))

    if not (added or removed or changed):
        print("\nActions: identical")
        return

    print(f"\nActions: +{len(added)}  -{len(removed)}  ~{len(changed)}")
    for name in added:
        ac = b[name]
        print(f"  + {name!r:25s} mc={ac['mc_idx']} frames {ac['start']}-{ac['end']}")
    for name in removed:
        ac = a[name]
        print(f"  - {name!r:25s} mc={ac['mc_idx']} frames {ac['start']}-{ac['end']}")
    for name, oa, ob in changed:
        bits = []
        if oa['mc_idx'] != ob['mc_idx']: bits.append(f"mc {oa['mc_idx']}->{ob['mc_idx']}")
        if oa['start'] != ob['start'] or oa['end'] != ob['end']:
            bits.append(f"range {oa['start']}-{oa['end']} -> {ob['start']}-{ob['end']}")
        print(f"  ~ {name!r:25s} " + ", ".join(bits))


def main() -> None:
    p = argparse.ArgumentParser(
        description="Diff two FBIN/RawBin files structurally.")
    p.add_argument("a", help="Path to first .bin")
    p.add_argument("b", help="Path to second .bin")
    p.add_argument("--by-index", action="store_true",
                   help="Match MCs by slot index instead of by name.")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Show per-MC element reference deltas.")
    args = p.parse_args()

    pa, pb = Path(args.a).resolve(), Path(args.b).resolve()
    if not pa.exists(): sys.exit(f"Missing: {pa}")
    if not pb.exists(): sys.exit(f"Missing: {pb}")

    da = parse_binary(str(pa))
    db = parse_binary(str(pb))
    if da is None: sys.exit(f"Parse failed: {pa}")
    if db is None: sys.exit(f"Parse failed: {pb}")

    _print_header(pa.name, pb.name, da, db)
    _diff_images(da, db)
    _diff_mcs(da, db, by_index=args.by_index, verbose=args.verbose)
    _diff_actions(da, db)
    print()


if __name__ == "__main__":
    main()
