"""
debug_anim.py
-------------
Diagnostic tool — prints every image drawn for frame 0 of every action,
showing the full MC call tree with depths and world positions.

Usage
-----
    python debug_anim.py --bin animation.bin [--action idle] [--frame 0]
    python debug_anim.py --bin animation.bin --dump-mc 17 [--frame 0]

Flags
-----
--bin       PATH   FBIN / RawBin animation file (required)
--action    NAME   Action name to inspect (default: all actions)
--frame     INT    Frame index to inspect (default: 0)
--define    STR    Character define key for default_settings lookup
--dump-mc   INT    Dump the raw element list for a specific MC index (no dedup)
--no-dedup         Show draw tree WITHOUT deduplication (shows the bug)
"""

import argparse
import sys
import math
from collections import Counter


def _parse_args():
    p = argparse.ArgumentParser(description="FBIN draw-tree debugger")
    p.add_argument("--bin",      required=True,          help="Path to .bin / FBIN file")
    p.add_argument("--action",   default=None,           help="Action name to inspect (default: all)")
    p.add_argument("--frame",    type=int, default=0,    help="Frame index (default: 0)")
    p.add_argument("--define",   default=None,           help="Character define key")
    p.add_argument("--dump-mc",  type=int, default=None, metavar="IDX",
                   help="Dump raw element list for MC index (no walk, no dedup)")
    p.add_argument("--scan",     action="store_true",
                   help="Scan all frames of the action and report image counts per frame")
    p.add_argument("--no-dedup", action="store_true",
                   help="Disable display-list deduplication (shows the raw duplicates)")
    return p.parse_args()


# ── Minimal affine helpers ────────────────────────────────────────────────────

def _concat(parent, local):
    pa, pb, pc, pd, ptx, pty = parent
    la, lb, lc, ld, ltx, lty = local
    return (
        pa*la + pc*lb,
        pb*la + pd*lb,
        pa*lc + pc*ld,
        pb*lc + pd*ld,
        pa*ltx + pc*lty + ptx,
        pb*ltx + pd*lty + pty,
    )


def _world_pos(matrix, img_def):
    na, nb, nc, nd, ntx, nty = matrix
    lcx =  img_def['offset_x'] + img_def['width']  * 0.5
    lcy = -img_def['offset_y'] - img_def['height'] * 0.5
    return na*lcx + nc*lcy + ntx, nb*lcx + nd*lcy + nty


# ── Raw MC dump (no dedup, no walk) ──────────────────────────────────────────

def _dump_mc(images, movie_clips, mc_idx, frame):
    if mc_idx < 0 or mc_idx >= len(movie_clips):
        print(f"mc_idx {mc_idx} out of range (0..{len(movie_clips)-1})")
        return
    mc   = movie_clips[mc_idx]
    fidx = frame % max(1, len(mc['frames']))
    elements = mc['frames'][fidx] if mc['frames'] else []
    print(f"RAW DUMP  MC[{mc_idx}] '{mc['name']}'  "
          f"frame={fidx}/{len(mc['frames'])}  elements={len(elements)}")
    print("-" * 72)

    eid_counts = Counter(e['id'] for e in elements)
    for i, elem in enumerate(elements):
        eid   = elem['id']
        fi    = elem.get('frame_index', -1)
        mat   = elem['matrix']
        is_mc = elem['is_mc']
        kind  = "MC " if is_mc else "IMG"
        name  = ""
        if is_mc and 0 <= eid < len(movie_clips):
            name = movie_clips[eid]['name']
        elif not is_mc and 0 <= eid < len(images):
            name = images[eid]['name']
        dup_flag = f"  *** eid appears {eid_counts[eid]}x ***" if eid_counts[eid] > 1 else ""
        print(f"  [{i:3d}] {kind} eid={eid:4d}  frame_index={fi:4d}  "
              f"name={name!r:35s}  "
              f"tx={mat[4]:7.1f} ty={mat[5]:7.1f}{dup_flag}")
    print()
    dup_eids = {eid for eid, c in eid_counts.items() if c > 1}
    if dup_eids:
        print(f"  Duplicate eids in this frame's element list:")
        for eid in sorted(dup_eids):
            print(f"      eid={eid}  appears {eid_counts[eid]}x  "
                  f"-> display-list: only the LAST entry should render")
        print()
        print("  The renderer fix: iterate elements, keep last index per eid,")
        print("  then draw only those. Earlier entries are superseded placements.")
    else:
        print("  No duplicate eids -- no display-list issue in this MC/frame.")

    # ── Simulate dedup strategy ───────────────────────────────────────────────
    print()
    print("  DEDUP SIMULATION")
    print("  Strategy A — key=(eid) — keep last per eid:")
    seen_a = {}
    for i, elem in enumerate(elements):
        seen_a[elem['id']] = i
    for i in sorted(seen_a.values()):
        e = elements[i]
        name = movie_clips[e['id']]['name'] if e['is_mc'] and 0 <= e['id'] < len(movie_clips) \
               else (images[e['id']]['name'] if not e['is_mc'] and 0 <= e['id'] < len(images) else "?")
        print(f"    keep [{i:3d}] eid={e['id']:4d}  fi={e.get('frame_index',-1):4d}  "
              f"tx={e['matrix'][4]:7.1f}  name={name!r}")

    print()
    print("  Strategy C — key=(frame_index) — keep last per frame_index [ACTIVE]:")
    seen_c = {}
    for i, elem in enumerate(elements):
        seen_c[elem.get('frame_index', -1)] = i
    for i in sorted(seen_c.values()):
        e = elements[i]
        name = movie_clips[e['id']]['name'] if e['is_mc'] and 0 <= e['id'] < len(movie_clips) \
               else (images[e['id']]['name'] if not e['is_mc'] and 0 <= e['id'] < len(images) else "?")
        print(f"    keep [{i:3d}] eid={e['id']:4d}  fi={e.get('frame_index',-1):4d}  "
              f"tx={e['matrix'][4]:7.1f}  name={name!r}")


# ── Recursive draw-tree printer ───────────────────────────────────────────────

def _walk(images, movie_clips, mc_idx, frame_num, matrix,
          depth=0, visited=None, rows=None, rawbin=False, dedup=True):
    if rows is None:
        rows = []
    if visited is None:
        visited = frozenset()

    if depth > 32 or mc_idx < 0 or mc_idx >= len(movie_clips):
        return rows
    if mc_idx in visited:
        rows.append((depth, f"[CYCLE GUARD] mc_idx={mc_idx} already on call stack"))
        return rows
    visited = visited | {mc_idx}

    mc       = movie_clips[mc_idx]
    fidx     = frame_num % max(1, len(mc['frames']))
    elements = mc['frames'][fidx] if mc['frames'] else []

    # ── Display-list dedup (mirrors renderer fix) ─────────────────────────────
    # Each (eid, frame_index) pair can appear multiple times in a RawBin frame
    # as successive "update" commands for the same child at the same pose.
    # Only the last such entry should render.
    # Different frame_index values for the same eid = intentional separate
    # placements (e.g. left arm / right arm) — those are NOT deduplicated.
    if rawbin and dedup:
        seen = {}
        for i, elem in enumerate(elements):
            tx_r = round(elem['matrix'][4], 1)
            ty_r = round(elem['matrix'][5], 1)
            key  = (elem.get('frame_index', -1), tx_r, ty_r)
            seen[key] = i              # last wins per (fi, tx, ty)
        before   = len(elements)
        elements = [elements[i] for i in sorted(seen.values())]
        dropped  = before - len(elements)
        if dropped:
            rows.append((depth, f"[DEDUP-RAWBIN] dropped {dropped} superseded placement(s) "
                                 f"in MC[{mc_idx}] '{mc['name']}'"))
    elif not rawbin and dedup:
        img_id_counts = Counter(elem['id'] for elem in elements if not elem['is_mc'])
        if any(c > 1 for c in img_id_counts.values()):
            seen_img: dict = {}
            for i, elem in enumerate(elements):
                if not elem['is_mc']:
                    seen_img[elem['id']] = i
            kept_img = set(seen_img.values())
            before = len(elements)
            elements = [elem for i, elem in enumerate(elements)
                        if elem['is_mc'] or i in kept_img]
            dropped = before - len(elements)
            if dropped:
                rows.append((depth, f"[DEDUP-FBIN] dropped {dropped} stale image placement(s) "
                                     f"in MC[{mc_idx}] '{mc['name']}'"))

    for elem in elements:
        eid = elem['id']
        if eid < 0:
            continue

        child_matrix = _concat(matrix, elem['matrix'])

        if elem['is_mc']:
            if eid >= len(movie_clips):
                rows.append((depth, f"MC ref eid={eid} OUT OF RANGE (max {len(movie_clips)-1})"))
                continue
            child_mc    = movie_clips[eid]
            child_frame = elem.get('frame_index', -1)

            if rawbin and len(child_mc['frames']) == 1 and child_frame >= 0:
                if child_frame < len(images):
                    img = images[child_frame]
                    wx, wy = _world_pos(child_matrix, img)
                    rows.append((depth,
                        f"IMAGE  idx={child_frame:4d}  name={img['name']!r:40s}  "
                        f"world=({wx:7.1f},{wy:7.1f})  "
                        f"size=({int(img['width'])}x{int(img['height'])})"))
                elif child_frame < len(movie_clips):
                    rows.append((depth,
                        f"MC-REDIRECT  -> mc_idx={child_frame}  "
                        f"name={movie_clips[child_frame]['name']!r}"))
                    _walk(images, movie_clips, child_frame, 0,
                          child_matrix, depth+1, visited, rows, rawbin, dedup)
            else:
                next_frame = child_frame if child_frame >= 0 else frame_num
                rows.append((depth,
                    f"MC  eid={eid:4d}  name={child_mc['name']!r:40s}  "
                    f"frame={next_frame}  nframes={len(child_mc['frames'])}"))
                _walk(images, movie_clips, eid, next_frame,
                      child_matrix, depth+1, visited, rows, rawbin, dedup)
        else:
            if eid < len(images):
                img = images[eid]
                wx, wy = _world_pos(child_matrix, img)
                rows.append((depth,
                    f"IMAGE  idx={eid:4d}  name={img['name']!r:40s}  "
                    f"world=({wx:7.1f},{wy:7.1f})  "
                    f"size=({int(img['width'])}x{int(img['height'])})"))
            else:
                rows.append((depth, f"IMAGE ref eid={eid} OUT OF RANGE"))

    return rows


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    args = _parse_args()

    sys.path.insert(0, '.')
    from fbin_parser import parse_fbin

    images, movie_clips, actions, is_rawbin = parse_fbin(args.bin)
    if images is None:
        print("ERROR: failed to parse", args.bin)
        sys.exit(1)

    print(f"Parsed: {len(images)} images, {len(movie_clips)} movie-clips, "
          f"{len(actions)} actions  [rawbin={is_rawbin}]")
    print()

    # ── --dump-mc mode ────────────────────────────────────────────────────────
    if args.dump_mc is not None:
        _dump_mc(images, movie_clips, args.dump_mc, args.frame)
        return

    # ── Build base transform ──────────────────────────────────────────────────
    import os
    define_key = args.define or os.path.splitext(os.path.basename(args.bin))[0]
    base = (1.0, 0.0, 0.0, -1.0, 512.0, 384.0)
    try:
        from default_settings import get_action_config
        d  = get_action_config(define_key, "")
        s  = d["scale"] if d["scale"] > 0 else 1.0
        tx = 512.0 - d["offset_x"] * s
        ty = 384.0 + d["offset_y"] * s
        sx = -s if d.get("flip") else s
        base = (sx, 0.0, 0.0, -s, tx, ty)
        print(f"default_settings for '{define_key}': "
              f"scale={s}  offset=({d['offset_x']},{d['offset_y']})")
    except (ImportError, KeyError):
        print(f"No default_settings for '{define_key}' -- using identity transform")
    print()

    dedup = not args.no_dedup
    if not dedup:
        print("WARNING: Deduplication DISABLED (--no-dedup) -- showing raw duplicate draw calls")
        print()

    # ── Build playlist ────────────────────────────────────────────────────────
    if actions:
        playlist = actions
    else:
        playlist = [{"name": mc['name'], "mc_idx": i, "start": 0,
                     "end": max(0, len(mc['frames'])-1), "p4": 0}
                    for i, mc in enumerate(movie_clips)]

    if args.action:
        playlist = [a for a in playlist
                    if a['name'].lower() == args.action.lower()]
        if not playlist:
            print(f"No action named '{args.action}'.  Available:")
            for a in (actions or []):
                print(f"  {a['name']}")
            sys.exit(1)

    # ── Print draw tree ───────────────────────────────────────────────────────
    for action in playlist:
        mc_idx = action.get('mc_idx', 0)
        frame  = args.frame
        label  = "[NO DEDUP]" if not dedup else "[dedup ON]"
        print("=" * 72)
        print(f"ACTION: {action['name']!r}   mc_idx={mc_idx}   frame={frame}  {label}")
        if 0 <= mc_idx < len(movie_clips):
            mc = movie_clips[mc_idx]
            print(f"  MC name: {mc['name']!r}   "
                  f"total_frames={len(mc['frames'])}   fps={mc['frame_rate']}")
        print("-" * 72)

        rows = _walk(images, movie_clips, mc_idx, frame, base,
                     rawbin=is_rawbin, dedup=dedup)

        image_counts: Counter = Counter()
        for _, msg in rows:
            if msg.startswith("IMAGE"):
                try:
                    name = msg.split("name=")[1].split("'")[1]
                    image_counts[name] += 1
                except IndexError:
                    pass

        duplicates = {n for n, c in image_counts.items() if c > 1}

        for depth, msg in rows:
            indent = "  " * depth
            flag = ""
            if msg.startswith("IMAGE"):
                try:
                    name = msg.split("name=")[1].split("'")[1]
                    if name in duplicates:
                        flag = f"  *** DUPLICATE x{image_counts[name]} ***"
                except IndexError:
                    pass
            print(f"{indent}{msg}{flag}")

        print()
        if duplicates:
            print(f"  Remaining duplicates after dedup:")
            for name in sorted(duplicates):
                print(f"      '{name}'  drawn {image_counts[name]}x")
            print()
            print("  These may be intentional layering or a deeper structural issue.")
            print("  Use --dump-mc <idx> to inspect the raw element list.")
        else:
            print("  No duplicate images -- draw tree looks correct.")
        print()


if __name__ == "__main__":
    main()
