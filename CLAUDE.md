# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Python toolset for reading, playing, and exporting **Cocos2d-x FBIN / RawBin** animation files from Plants vs. Zombies (and similar games). Built with pygame, numpy (optional), and Pillow (optional).

Install dependencies: `pip install pygame numpy Pillow`

---

## Layout

```
PVZ_AS_Script/
├── _paths.py         registers library subfolders on sys.path (imported by entry points)
├── scripts/          entry points (main.py, xfl_main.py, debug_anim.py, test_crop.py)
├── parsers/          fbin_parser.py, rawbin_parser.py, input_buffer.py
├── render/           renderer.py + player/ subpackage (core, hud, input, export)
├── pvr/              pvr_loader.py, pypvr.py
├── xfl/              xfl_helpers / _sprite / _label / _image / _media / _document / _exporter
├── config/           default_settings.py, anim_meta.py
├── writer/           rawbin_writer.py
├── tests/            round_trip_test.py
├── tools/            convert_1200_to_1536.py
└── samples/          example .bin / .pvr / .png files
```

Imports stay flat (`from fbin_parser import parse_fbin`); each entry point reaches back
to `_paths.py` to register the library folders before importing project modules.

## Commands

```bash
# Play animation (PVR texture or pre-decoded PNG atlas)
python scripts/main.py --bin samples/char.bin --pvr samples/char.pvr
python scripts/main.py --bin samples/char.bin --atlas char.png
python scripts/main.py --bin samples/char.bin --atlas char.png --meta animaction.txt --define zombie_pirate_imp

# Export to Adobe Animate XFL
python scripts/xfl_main.py --bin samples/char.bin --atlas char.png
python scripts/xfl_main.py --bin samples/char.bin --atlas char.png --out ./output --stem mychar

# Debug draw tree (no pygame required)
python scripts/debug_anim.py --bin samples/char.bin                        # all actions, frame 0
python scripts/debug_anim.py --bin samples/char.bin --action idle --frame 2
python scripts/debug_anim.py --bin samples/char.bin --dump-mc 17           # raw MC element list, no dedup
python scripts/debug_anim.py --bin samples/char.bin --scan                 # image counts per frame

# Verify sprite atlas coordinates — FBIN + RawBin, no pygame required
python scripts/test_crop.py --bin samples/char.bin --atlas char.png --out crops

# RawBin round-trip MD5 test
python tests/round_trip_test.py samples/char.bin
```

**Logging:** pass `--log-level DEBUG` to any entry point for verbose parse traces.

---

## Architecture

### Entry points
| Script | Role |
|---|---|
| `scripts/main.py` | Animation player (pygame window) |
| `scripts/xfl_main.py` | XFL exporter (no display needed) |
| `scripts/debug_anim.py` | Print MC draw tree to stdout |
| `scripts/test_crop.py` | Standalone sprite coordinate verifier |
| `tests/round_trip_test.py` | RawBin parse/write MD5 round-trip |

### Parse pipeline
`fbin_parser.parse_fbin()` is the single entry point for both formats:
1. Checks for `FBIN` magic bytes at offset 0.
2. **FBIN path** — tries 4 parse variants (`has_transform` × `order_variant A/B`), validates by checking trailing unconsumed bytes (>16 = wrong variant) and mc_idx range.
3. **RawBin path** — delegates to `rawbin_parser.parse_rawbin_from_bytes()`. Probes at offset 0 or 12, then tries 4-byte or 6-byte clip headers.
4. Falls back to a minimal synthetic clip if all variants fail.

Returns `(images, movie_clips, actions, is_rawbin)` — the shared data contract used by every downstream module.

After a successful parse, both modules populate a module-level `LAST_INFO` dict that callers can read for the summary:
- `fbin_parser.LAST_INFO`: `version_ints`, `version_tag` (e.g. `"v1.0"`), `has_transform`, `order`, `num_versions`, `variant_tag`.
- `rawbin_parser.LAST_INFO`: `clip_header_size` (4 or 6), `start_offset` (0 or 12), `consumed`, `total`.

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
- **FBIN dedup**: stale keyframe placements — same image id appears **exactly twice** per frame (old keyframe + new). Only the last of the two is kept. Three or more copies = intentional multi-instance (e.g. vine thorns) — keep all.
- **RawBin dedup**: suppress identical `(frame_index, tx, ty)` triples per frame.
- **RawBin plane suppression**: images drawn via `mc_id=0` (ground_swatch_plane) suppress `mc_id=1` draws of the same image.
- **RawBin element dispatch** — `mc_id` (eid) determines how `frame_index` (fi) is interpreted:
  - `mc_id=1` → **always redirect to body-part MC[fi]**. MC[1] is universally a 1-frame redirect-wrapper (named `ground_swatch`, `zombie_imp_pirate_hand1`, etc.) whose fi value is the *target MC index*, not an image index. The target MC then draws its own sub-sprites (e.g. MC[15]=zombie_basic_eye draws image 14).
  - `mc_id≠1` (eid=0 ground, eid=2 image-pointer, etc.) → **draw image fi directly** when fi < len(images). This is the terminal draw for all body-part sub-elements.
- **Transform cache**: LRU, up to 2048 pre-scaled/rotated surfaces (`_NAME_OVERRIDES` applies hardcoded flip/size corrections for `jaw`, `flag`, `31-031`).

### Player
`render/player/` is a 4-file subpackage. `__init__.py` composes the final class:
`class Player(InputMixin, HudMixin, ExportMixin, _PlayerCore)`. Each module owns one
concern:

| Module | Contents |
|---|---|
| `core.py` | `PlayerConfig`, `_PlayerCore` — `__init__`, run loop, `_base_transform`, `_meta_for_action`, `_resolve_fps`, `_build_playlist` |
| `hud.py` | `HudMixin` — `_draw_hud`, `_draw_action_list` |
| `input.py` | `InputMixin` — `_handle_events`, `_handle_key` |
| `export.py` | `ExportMixin` — GIF / sprites / atlas / JSON / XFL exports |

`_base_transform()` maps from Cocos Y-up space to pygame screen space (Y-flip is built in).
With `AnimMeta`, it additionally applies per-action scale, offset, flip, and frame-range overrides.

FPS resolution priority (`_resolve_fps`), per `fps_mode`:
- `custom` → `fps_custom`
- `meta`   → meta file → MC `frame_rate` → `DEFAULT_FRAME_RATE` (30)
- `source` → MC `frame_rate` → meta file → `DEFAULT_FRAME_RATE` (30)

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
| R | Cycle fps mode (source → meta → custom) |
| 1 / 2 / 3 | Set fps mode directly |
| 4 | Enter custom fps value |
| M | Hot-reload metadata file |
| G | Export current action to GIF |
| A | Export all actions as GIFs |
| Z | Export all actions as no-background GIFs |
| S | Export individual sprites |
| T | Export atlas as PNG |
| X | Export XFL / .fla |
| J | Dump frame data as JSON |
| H | Toggle HUD |
| ? | Toggle help overlay (full key list) |
| 0 | Reset zoom and pan |
| F11 | Toggle fullscreen |
| PrtScr | Save PNG screenshot of current frame |
| Mouse wheel | Zoom in / out |
| Right-drag | Pan the canvas |
| Left-click scrub bar | Seek to that frame (drag to scrub) |

### Optional metadata (`animaction.txt`)
A TSV/CSV file providing per-action scale, offset, fps, flip, and frame-range overrides. Auto-discovered in the `.bin` folder → `cwd` → script folder. Loaded via `config/anim_meta.py` (`AnimMeta.load()`). Override with `--meta PATH` or disable with `--no-meta`.

`config/anim_meta.py` is a minimal TSV/CSV reader exposing `AnimMeta`, `ActionConfig`, and `ParticleConfig`. The action table is the default; a `[particles]` header switches to the particle table. Returns an empty `AnimMeta` (whose `is_empty()` is True) when the file is missing or unparseable, so callers fall back to `default_settings.py` or raw FBIN values.

### `config/default_settings.py`
Hardcoded fallback per-character display settings. **Only for FBIN files** — RawBin files already store absolute world positions and must NOT have entries here. Each character entry maps a define key (= `.bin` stem) to `{offset_x, offset_y, scale, fps, flip, actions}`.

---

## Critical constraints

- **FBIN `offset_x/y`** — Flash registration points. **Never clamp** them; they can legitimately exceed sprite dimensions.
- **RawBin `offset_x/y`** — Also Flash registration points. **Never clamp** them either: when the same character is re-exported as FBIN (e.g. `zombie_JourneyWest_tieguo` v32 RawBin vs v33 FBIN) the raw float values match byte-for-byte. An earlier `abs(offset) >= dimension → 0` clamp destroyed legitimate registrations for body parts pivoted at joints.
- **RawBin world positions are absolute** — do not add character-level scale/offset on top.
- **`default_settings.py` is FBIN-only** — adding entries for RawBin characters breaks rendering.
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
