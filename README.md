# PVZ_AS_Script

A Python toolset for reading, playing, and exporting **Cocos2d-x FBIN / RawBin** animation files from *Plants vs. Zombies* (and other Cocos2d-x games that use the same format).

You can use it to:

- Play a `.bin` animation in a window with full playback controls.
- Export animations to **GIF**, **PNG sprites**, **atlases**, **JSON frame data**, or **Adobe Animate XFL / .fla** projects.
- Inspect the internal draw tree for debugging or research.
- Round-trip RawBin files (parse and re-write byte-for-byte).

---

## Supported formats

| Format | Magic | Notes |
|---|---|---|
| **FBIN** | `FBIN` at offset 0 | Newer PvZ format. Parser tries 4 variants and auto-detects. |
| **RawBin** | none (legacy) | Older PvZ format. Parser probes offsets 0/12 and 4/6-byte clip headers. |

Both produce the same in-memory data structure, so every downstream tool (player, exporter, debugger) works on either.

---

## Install

Requires **Python 3.8+**.

```bash
pip install pygame numpy Pillow
```

- `pygame` — required (window + rendering).
- `numpy` — optional, speeds up texture decode.
- `Pillow` — optional, needed for PNG / GIF / atlas export.

---

## Quick start

### Play an animation

```bash
# Using a PVR texture
python scripts/main.py --bin samples/char.bin --pvr samples/char.pvr

# Using a pre-decoded PNG atlas
python scripts/main.py --bin samples/char.bin --atlas char.png

# With per-action metadata overrides
python scripts/main.py --bin samples/char.bin --atlas char.png --meta animaction.txt --define zombie_pirate_imp
```

### Export to Adobe Animate (XFL)

```bash
python scripts/xfl_main.py --bin samples/char.bin --atlas char.png
python scripts/xfl_main.py --bin samples/char.bin --atlas char.png --out ./output --stem mychar
```

### Inspect the draw tree (no window)

```bash
python scripts/debug_anim.py --bin samples/char.bin                  # all actions, frame 0
python scripts/debug_anim.py --bin samples/char.bin --action idle --frame 2
python scripts/debug_anim.py --bin samples/char.bin --dump-mc 17     # raw MC element list
python scripts/debug_anim.py --bin samples/char.bin --scan           # image counts per frame
```

### Verify sprite atlas coordinates

```bash
python scripts/test_crop.py --bin samples/char.bin --atlas char.png --out crops
```

### RawBin round-trip MD5 test

```bash
python tests/round_trip_test.py samples/char.bin
```

Add `--log-level DEBUG` to any entry point for verbose parse traces.

---

## In-player keyboard controls

| Key | Action |
|---|---|
| `ESC` / `Q` | Quit |
| `←` / `→` | Previous / next action |
| `↑` / `↓` | Speed +0.1x / -0.1x |
| `SPACE` | Pause / resume |
| `N` / `B` | Step one frame forward / back |
| `F` | Jump to frame (type number, Enter) |
| `L` | Toggle loop |
| `I` | Open action picker |
| `R` | Cycle fps mode (source → meta → custom) |
| `1` / `2` / `3` | Set fps mode directly |
| `4` | Enter custom fps value |
| `M` | Hot-reload metadata file |
| `G` | Export current action to GIF |
| `A` | Export all actions as GIFs |
| `Z` | Export all actions as no-background GIFs |
| `S` | Export individual sprites |
| `T` | Export atlas as PNG |
| `X` | Export XFL / .fla |
| `J` | Dump frame data as JSON |
| `H` | Toggle HUD |
| `?` | Toggle full help overlay |
| `0` | Reset zoom and pan |
| `F11` | Toggle fullscreen |
| `PrtScr` | Save PNG screenshot |
| Mouse wheel | Zoom in / out |
| Right-drag | Pan canvas |
| Left-click scrub bar | Seek (drag to scrub) |

---

## Project layout

```
PVZ_AS_Script/
├── _paths.py         registers library subfolders on sys.path
├── scripts/          entry points (main, xfl_main, debug_anim, test_crop)
├── parsers/          fbin_parser, rawbin_parser, input_buffer
├── render/           renderer + player/ subpackage (core, hud, input, export)
├── pvr/              pvr_loader, pypvr (PVR texture decoder)
├── xfl/              XFL/.fla exporter modules
├── config/           default_settings, anim_meta
├── writer/           rawbin_writer (re-emits RawBin bytes)
├── tests/            round-trip test
├── tools/            convert_1200_to_1536
└── samples/          example .bin / .pvr / .png files
```

Imports stay flat (`from fbin_parser import parse_fbin`); each entry point reaches
back to `_paths.py` to register the library folders before importing project
modules.

---

## How the pipeline works

```
   .bin file
      │
      ▼
┌──────────────────────────┐
│ fbin_parser.parse_fbin() │   ← single entry for both formats
└──────────────────────────┘
      │
      │  FBIN magic at offset 0?
      │
      ├── yes ──► tries 4 FBIN variants, picks the one with clean trailing bytes
      │
      └── no  ──► rawbin_parser.parse_rawbin_from_bytes()
                     probes offset 0 or 12, 4-byte or 6-byte clip headers
      │
      ▼
   (images, movie_clips, actions, is_rawbin)
      │
      ├──► Renderer (pygame window)
      ├──► XFL exporter (.fla project)
      ├──► GIF / PNG / atlas / JSON exporters
      └──► RawBin writer (round-trip)
```

### Shared data contract

Every downstream module receives the same four-tuple:

```python
images      # [{name, offset_x, offset_y, width, height, tex_x, tex_y, origin_x, origin_y}, ...]
movie_clips # [{name, frames: [[element, ...], ...], frame_rate}, ...]
actions     # [{name, start, end, mc_idx, p4}, ...]
is_rawbin   # bool
```

Each frame is a list of `element` dicts:

```python
{is_mc, id, frame_index, matrix:(sx,ky,kx,sy,tx,ty), alpha, color_mult, color_add}
```

---

## Optional metadata (`animaction.txt`)

A small TSV/CSV file that overrides per-action scale, offset, fps, flip, and
frame ranges. It's auto-discovered in:

1. the `.bin` folder, then
2. the current working directory, then
3. the script folder.

Override the path with `--meta PATH` or skip discovery with `--no-meta`.

---

## Notes on the format

- **FBIN `offset_x/y`** are Flash registration points — they can legitimately
  exceed sprite dimensions and must never be clamped.
- **RawBin** stores absolute world positions; character-level scale / offset is
  not added on top.
- The renderer skips sprites at `tex_x=0, tex_y=0` with `size ≤ 4×4` (Flash
  pivot / registration markers, not real images).
- PVRTC decoding requires power-of-two textures and wraps blocks (modulo).

---

## License

This project is for educational and research purposes. Game assets are
property of their respective owners.
