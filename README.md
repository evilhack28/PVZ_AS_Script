# PVZ_AS_Script

A Python tool for **reading and playing Cocos2d-x FBIN / RawBin animation files** from *Plants vs. Zombies* (and other Cocos2d-x games that use the same format).

The current build is intentionally minimal: it decodes the PVR texture, parses the .bin animation, and plays it in a pygame window. The XFL exporter is gone and will be rewritten separately.

---

## Supported formats

| Format | Magic | Notes |
|---|---|---|
| **FBIN** | `FBIN` at offset 0 | Newer PvZ format. Parser tries 4 variants and auto-detects. |
| **RawBin** | none (legacy) | Older PvZ format. Parser probes offsets 0/12 and 4/6-byte clip headers. |

The PVR loader handles **iOS PVR v2** (RGBA4444 / RGBA8888 / PVRTC4) directly and falls back to **Dreamcast/Naomi PVRT** via the bundled `pypvr` decoder.

---

## Install

Requires **Python 3.8+**.

```bash
pip install pygame numpy Pillow
```

- `pygame` — required (window + rendering).
- `numpy` — optional, speeds up PVRTC4 texture decode.
- `Pillow` — optional, needed for GIF / atlas / sprite export.

---

## Run

```bash
# Interactive — pops up file pickers for both
python main.py

# Both files explicit
python main.py --bin samples/zombie_kungfu_torch.bin --pvr samples/zombie_kungfu_torch.pvr

# Half-explicit — the sibling is auto-paired by matching stem in the same folder
python main.py --bin samples/zombie_kungfu_torch.bin     # finds zombie_kungfu_torch.pvr
python main.py --pvr samples/zombie_kungfu_torch.pvr     # finds zombie_kungfu_torch.bin
```

If only one of `--bin` / `--pvr` is given, the script looks next to that file for a sibling with the same stem and the matching extension. If the sibling is missing (or both flags are omitted), a tkinter file dialog opens for whatever is still unresolved.

---

## In-player keyboard controls

| Key | Action |
|---|---|
| `ESC` / `Q` | Quit |
| `←` / `→` | Previous / next action |
| `↑` / `↓` | Speed +0.1× / -0.1× |
| `SPACE` | Pause / resume |
| `N` / `B` | Step one frame forward / back (auto-pauses) |
| `F` | Jump to frame (type number, Enter) |
| `L` | Toggle loop |
| `I` | Open action picker |
| `K` | Toggle "butter" sprite (hides the head accessory on kungfu zombies) |
| `1` / `2` | Set fps mode: source / custom |
| `4` | Enter a custom fps value |
| `G` | Export current action to GIF |
| `A` | Export all actions as GIFs |
| `Z` | Export all actions as transparent-background GIFs |
| `S` | Export individual sprite PNGs |
| `T` | Export atlas as PNG |
| `J` | Dump frame data as JSON |
| `H` | Toggle HUD |
| `?` | Toggle full help overlay |
| `0` | Reset zoom and pan |
| `F11` | Toggle fullscreen |
| `PrtScr` | Save PNG screenshot of the current frame |
| Mouse wheel | Zoom in / out |
| Right-drag | Pan canvas |
| Left-click scrub bar | Seek (drag to scrub) |

---

## Importable API

```python
from fbin_parser import parse_binary             # dict-shaped: format/info/images/movie_clips/actions
from pvr_loader  import load_pvr_texture          # PVR -> pygame.Surface
from pvr_loader  import convert_pvr_to_png        # PVR -> PNG on disk
```

---

## Project layout

```
PVZ_AS_Script/
├── _paths.py        registers library subfolders on sys.path
├── main.py          sole entry point — CLI + run_player(bin, pvr)
├── parsers/         fbin_parser (parse_fbin + parse_binary), rawbin_parser, input_buffer
├── render/          renderer + player/ subpackage (core, hud, input, export)
├── pvr/             pvr_loader (load_pvr_texture + convert_pvr_to_png), pypvr
└── samples/         example .bin / .pvr files
```

Imports stay flat (`from fbin_parser import parse_binary`); `main.py` reaches into `_paths.py` to register the library folders before importing project modules.

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
     ▼
  Player (pygame window)
```

### Shared data contract

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

## Notes on the format

- **FBIN `offset_x/y`** are Flash registration points — they can legitimately exceed sprite dimensions and must never be clamped.
- **RawBin** stores absolute world positions; character-level scale / offset is not added on top.
- The renderer skips sprites at `tex_x=0, tex_y=0` with `size ≤ 4×4` (Flash pivot / registration markers, not real images).
- PVRTC decoding requires power-of-two textures and wraps blocks (modulo).
- Some kungfu-zombie samples wear a `butter` sprite over the face — press **`K`** in the player to hide it.

---

## License

This project is for educational and research purposes. Game assets are property of their respective owners.
