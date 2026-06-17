# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Python toolset for reading and playing **Cocos2d-x FBIN / RawBin** animation files from Plants vs. Zombies (and similar games). Built with pygame, numpy (optional), and Pillow.

Install dependencies: `pip install pygame numpy Pillow`

---

## Layout

```
PVZ_AS_Script/
├── _paths.py         registers library subfolders on sys.path (imported by main.py)
├── main.py           sole entry point — CLI + run_player(bin, pvr)
├── parsers/          fbin_parser.py (parse_fbin + parse_binary), rawbin_parser.py, input_buffer.py
├── render/           renderer.py + player/ subpackage (core, hud, input, export)
├── pvr/              pvr_loader.py (load_pvr_texture + convert_pvr_to_png), pypvr.py
└── samples/          example .bin / .pvr files
```

Imports stay flat (`from fbin_parser import parse_binary`); `main.py` reaches into
`_paths.py` to register the library folders before importing project modules.

> **Note:** The XFL exporter, RawBin writer, debug scripts, round-trip tests, resolution-conversion tool, and the per-character display-tweak tables (`config/anim_meta.py` + `config/default_settings.py`) have been removed. The player now renders directly from raw FBIN/RawBin values. XFL export will be rewritten separately and is out of scope for the current state of this repo.

## Commands

```bash
# Interactive — file pickers appear for both
python main.py

# Scripted — both files explicit
python main.py --bin samples/char.bin --pvr samples/char.pvr

# Half-explicit — sibling is auto-paired by matching stem in the same folder
python main.py --bin samples/char.bin     # finds char.pvr next to it
python main.py --pvr samples/char.pvr     # finds char.bin next to it
```

Resolution order in `main._resolve_inputs`:
1. If the user passed exactly one of `--bin` / `--pvr`, look for a same-stem sibling next to it.
2. For anything still missing, open a tkinter file dialog (anchored at `samples/`). Falls back to `input()` if tkinter is unavailable.

Importable API (no CLI):
```python
from fbin_parser import parse_binary          # dict-shaped wrapper around parse_fbin
from pvr_loader  import load_pvr_texture, convert_pvr_to_png
```

---

## Architecture

### Entry point
`main.py` is the only entry point. It parses the CLI and calls `run_player(bin, pvr)`,
which in turn:
1. Calls `fbin_parser.parse_fbin()` to load images/MCs/actions
2. Calls `pvr_loader.load_pvr_texture()` to decode the atlas
3. Constructs `player.Player` and runs the pygame loop

### Parse pipeline
`fbin_parser` exposes two public functions:
- `parse_fbin(path)` → `(images, movie_clips, actions, is_rawbin)` tuple (raw)
- `parse_binary(path)` → dict with keys `format`, `info`, `images`, `movie_clips`, `actions`, `is_rawbin` (or None on failure)

Internally:
1. Checks for `FBIN` magic bytes at offset 0.
2. **FBIN path** — tries 4 parse variants (`has_transform` × `order_variant A/B`), validates by checking trailing unconsumed bytes (>16 = wrong variant) and mc_idx range.
3. **RawBin path** — delegates to `rawbin_parser.parse_rawbin_from_bytes()`. Probes at offset 0 or 12, then tries 4-byte or 6-byte clip headers.
4. Returns `None` if every variant fails (no synthetic fallback).

Returns `(images, movie_clips, actions, is_rawbin)` — the shared data contract used by every downstream module.

After a successful parse, both modules populate a module-level `LAST_INFO` dict that callers can read for the summary:
- `fbin_parser.LAST_INFO`: `version_ints`, `version_tag` (e.g. `"v1.0"`), `has_transform`, `order`, `num_versions`, `variant_tag`, `ext_float`.
- `rawbin_parser.LAST_INFO`: `clip_header_size` (4 or 6), `start_offset` (0 or 12), `consumed`, `total`.

**FBIN `ext_float`** — the MinBin float right after the version header (only present when `has_transform=True`) is a per-character **world unit scale**. It pre-multiplies both every element matrix tx/ty AND every image's `offset_x`/`offset_y` (Flash registration points). Linear matrix terms (sx/ky/kx/sy) and atlas pixel data (width/height/tex_x/tex_y) are NOT scaled — those are in atlas pixels, not FBIN world units. Observed values: `zombie_viking`=0.5, `zombie_horn`=0.8, `zombie_JourneyWest_bullking/zhizhu`=0.7, `zombie_JourneyWest_tudi`=1.0. Without it, files with `ext_float<1.0` render with large vertical gaps between body parts (viking's leg/chest separation) and floating accessory sprites whose huge registration offsets land them far from the body (e.g. horn's eyebrow drifting to the left of the head).

### Shared data structures
```
images      — list of dicts: {name, offset_x, offset_y, width, height, tex_x, tex_y, origin_x, origin_y}
movie_clips — list of dicts: {name, frames, frame_rate}
              frames = list of frames; each frame = list of element dicts
              element = {is_mc, id, frame_index, matrix:(sx,ky,kx,sy,tx,ty), alpha, color_mult, color_add}
actions     — list of dicts: {name, start, end, mc_idx, p4}
```

### Renderer
`Renderer.draw()` walks the MC tree recursively (max depth 32). At each node it concatenates the local affine matrix with the parent's. At leaf image nodes it calls `_draw_image()`.

Key behaviours that span multiple files:
- **FBIN dedup**: position-aware — same `(image id, round(tx,1), round(ty,1))` collapses to last wins (Flash stale keyframe placements at the *same* spot). Same image id at *different* positions is kept (legitimate symmetrical pairs: left/right pupils, paired eye dots on bellis/Breeder_zombie/bush). Earlier count-only rule killed one eye on every such pair.
- **RawBin dedup**: suppress identical `(frame_index, tx, ty)` triples per frame.
- **RawBin plane suppression**: images drawn via `mc_id=0` (ground_swatch_plane) suppress `mc_id=1` draws of the same image.
- **RawBin element dispatch** — `mc_id` (eid) determines how `frame_index` (fi) is interpreted:
  - `mc_id=1` → **always redirect to body-part MC[fi]**. MC[1] is universally a 1-frame redirect-wrapper (named `ground_swatch`, `zombie_imp_pirate_hand1`, etc.) whose fi value is the *target MC index*, not an image index. The target MC then draws its own sub-sprites (e.g. MC[15]=zombie_basic_eye draws image 14).
  - `mc_id≠1` (eid=0 ground, eid=2 image-pointer, etc.) → **draw image fi directly** when fi < len(images). This is the terminal draw for all body-part sub-elements.
- **Transform cache**: LRU, up to 2048 pre-scaled/rotated surfaces (`_NAME_OVERRIDES` applies hardcoded flip/size corrections for `jaw`, `flag`, `31-031`).
- **Alpha is leaf-only**: `_draw_image()` reads `elem['alpha']` from the leaf image element. Alpha set on an `is_mc=True` parent is NOT propagated to children. (This is a known limitation — relevant when a transparency effect is authored on a MovieClip instance rather than a leaf image, e.g. `zombie_kungfu_hammer`.)

### Player
`render/player/` is a 4-file subpackage. `__init__.py` composes the final class:
`class Player(InputMixin, HudMixin, ExportMixin, _PlayerCore)`. Each module owns one
concern:

| Module | Contents |
|---|---|
| `core.py` | `PlayerConfig`, `_PlayerCore` — `__init__`, run loop, `_base_transform`, `_meta_for_action`, `_resolve_fps`, `_build_playlist` |
| `hud.py` | `HudMixin` — `_draw_hud`, `_draw_action_list` |
| `input.py` | `InputMixin` — `_handle_events`, `_handle_key` |
| `export.py` | `ExportMixin` — GIF / sprites / atlas / JSON exports |

`_base_transform()` maps from Cocos Y-up space to pygame screen space (Y-flip is built in).
With `AnimMeta`, it additionally applies per-action scale, offset, flip, and frame-range overrides.

FPS resolution priority (`_resolve_fps`), per `fps_mode`:
- `custom` → `fps_custom`
- otherwise → MC `frame_rate` if > 0, else `DEFAULT_FRAME_RATE` (30)

**In-player keyboard controls:**
| Key | Action |
|---|---|
| ESC / Q | Quit |
| LEFT / RIGHT | Previous / next action |
| UP / DOWN | Speed +0.1x / -0.1x |
| SPACE | Pause / resume |
| N / B | Step one frame forward / back |
| F | Jump to frame (type number, Enter) |
| L | Toggle loop |
| I | Open action picker |
| 1 / 2 | Set fps mode: source / custom |
| 4 | Enter custom fps value |
| K | Toggle "butter" sprite hide (kungfu zombies' head accessory) |
| C | Cycle costume mode (ALL → NONE → 1 → 2 → …) for plants with unreferenced `custom_NN_*` swap variants |
| G | Export current action to GIF |
| A | Export all actions as GIFs |
| Z | Export all actions as no-background GIFs |
| S | Export individual sprites |
| T | Export atlas as PNG |
| J | Dump frame data as JSON |
| H | Toggle HUD |
| ? | Toggle help overlay (full key list) |
| 0 | Reset zoom and pan |
| F11 | Toggle fullscreen |
| PrtScr | Save PNG screenshot of current frame |
| Mouse wheel | Zoom in / out |
| Right-drag | Pan the canvas |
| Left-click scrub bar | Seek to that frame (drag to scrub) |

---

## Critical constraints

- **FBIN `offset_x/y`** — Flash registration points. **Never clamp** them; they can legitimately exceed sprite dimensions.
- **RawBin `offset_x/y`** — Also Flash registration points. **Never clamp** them either: when the same character is re-exported as FBIN (e.g. `zombie_JourneyWest_tieguo` v32 RawBin vs v33 FBIN) the raw float values match byte-for-byte. An earlier `abs(offset) >= dimension → 0` clamp destroyed legitimate registrations for body parts pivoted at joints.
- **RawBin world positions are absolute** — do not add character-level scale/offset on top.
- Renderer skips sprites at `tex_x=0, tex_y=0` with `size ≤ 4×4` (Flash pivot/registration markers; contain PVRTC block garbage).
- PVRTC decoding requires power-of-two textures and wraps blocks (modulo).

### MinBin float encoding (`parsers/input_buffer.py`)
| Tag | Encoding |
|---|---|
| 0 | 0.0 |
| 1 | int8 / divisor |
| 2 | int16 / divisor |
| 3 | int32 / divisor |
| 4 | int32 / divisor (same as tag 3; used when value exceeds int16 range) |

FBIN uses divisor=100 for image coords, 10000 for scale/skew values.
