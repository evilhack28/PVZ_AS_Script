# PVZ_AS_Script — Project Overview

A Python toolset for reading, playing, and exporting **Cocos2d-x FBIN / RawBin** animation files from Plants vs. Zombies (and similar games). Built with pygame, numpy (optional), and Pillow (optional).

---

## Entry Points

| Script | Purpose |
|---|---|
| `main.py` | **Animation player** — preview `.bin` + `.pvr`/`.png` atlas in a pygame window |
| `xfl_main.py` | **XFL exporter** — convert `.bin` + `.png` atlas to an Adobe Animate XFL project |
| `debug_anim.py` | **Diagnostic tool** — print the MC draw tree for any action/frame |

---

## Core Modules

### `fbin_parser.py`
Parses **FBIN** files (magic bytes `FBIN` at offset 0).

- Auto-detects format variant: tries 4 combinations (`has_transform` × `order_variant A/B`).
- Falls back to `rawbin_parser.py` if no `FBIN` magic found.
- Falls back to a minimal synthetic clip if all variants fail.
- Key output: `(images, movie_clips, actions, is_rawbin)`

**FBIN image record** (8 MinBin floats, divisor=100):

| Index | Field | Notes |
|---|---|---|
| 0 | `offset_x` | Flash registration X — do NOT clamp, can exceed sprite bounds |
| 1 | `offset_y` | Flash registration Y |
| 2 | `width` | Sprite width in pixels |
| 3 | `height` | Sprite height in pixels |
| 4 | `tex_x` | Atlas X in pixels |
| 5 | `tex_y` | Atlas Y in pixels |
| 6 | `hint_x` | tex_x + width + padding (ignored) |
| 7 | `hint_y` | tex_y + height + padding (ignored) |

**Element flags** (bit-field in each frame element):

| Bit | Meaning |
|---|---|
| 0x02 | `frame_index` present |
| 0x04 | scale X (`sx`) present |
| 0x08 | skew X (`kx`) present |
| 0x10 | skew Y (`ky`) present |
| 0x20 | scale Y (`sy`) present |
| 0x40 | translation `(x, y)` present |
| 0x80 | color/alpha data present |

### `rawbin_parser.py`
Parses **RawBin** files (no magic bytes — raw float32 data).

- Image records: 8 × `float32` (`offset_x, offset_y, width, height, tex_x, tex_y, origin_x, origin_y`).
- Probes at offset 0 or 12 to find the image table.
- Tries 4-byte and 6-byte clip headers.
- Element size: **38 bytes** fixed.

> **RawBin element matrices store complete world positions** — do NOT add scale/offset on top.

### `input_buffer.py`
Bounds-checked binary stream reader. Implements **MinBin float** decoding (tag + compressed int).

| MinBin Tag | Encoding |
|---|---|
| 0 | 0.0 |
| 1 | int8 / divisor |
| 2 | int16 / divisor |
| 3 | int32 / divisor |
| 4 | raw IEEE-754 float32 |

### `pvr_loader.py`
Loads PVR texture files into `pygame.Surface` (RGBA).

Supported formats:
- **iOS PVR v2** (`PVR!` magic at offset 44): RGBA4444, RGBA8888, PVRTC4 (4bpp), PVRTC2 (2bpp)
- **Dreamcast/Naomi PVRT** (`GBIX`/`PVRT` magic): decoded via `pypvr.py` + Pillow + numpy

### `renderer.py`
Stateful pygame-based renderer. Walks the MC tree recursively (max depth 32).

Key behaviours:
- **FBIN dedup**: stale duplicate image placements per frame → keep last by `id`.
- **RawBin dedup**: suppress identical `(frame_index, tx, ty)` placements.
- **RawBin plane suppression**: images drawn via `mc_id=0` (ground_swatch_plane) suppress redundant `mc_id=1` draws of the same image.
- **Hidden parts**: `renderer.hidden_parts` (frozenset of substrings) skips matching sprite names — set by Player for particle actions.
- **Transform cache**: LRU cache of up to 2048 pre-scaled/rotated surfaces.
- **Per-sprite name overrides** (`_NAME_OVERRIDES`): hardcoded flip/size corrections for `jaw`, `flag`, `31-031`.

### `player.py`
Interactive pygame animation player on top of `Renderer`.

**Controls:**

| Key | Action |
|---|---|
| ESC / Q | Quit |
| LEFT / RIGHT | Previous / next action |
| UP / DOWN | Speed +0.1x / -0.1x |
| P | Pause / resume |
| N / B | Step one frame forward / back |
| L | Toggle loop |
| I | Open action picker (UP/DOWN, ENTER, ESC) |
| G | Export current action to GIF |
| H | Toggle HUD |

**`PlayerConfig` fields:**

| Field | Default | Notes |
|---|---|---|
| `window_width` | 1024 | |
| `window_height` | 768 | |
| `fps_cap` | 60 | |
| `start_action` | 0 | |
| `loop` | True | |
| `background_rgb` | (40,40,40) | |
| `fps_mode` | `"meta"` | `'source'` / `'meta'` / `'custom'` |
| `fps_custom` | 30 | Used when `fps_mode == 'custom'` |
| `pvr_name` | `"sprites"` | Stem of `.pvr` file, used as export folder |

### `default_settings.py`
Hardcoded per-character display settings (scale, offset, fps, flip, per-action overrides).

- Used as fallback when no `--meta` / `animaction.txt` is available.
- **Only for FBIN** — RawBin files should NOT have entries here (world coords already embedded).
- Key lookup: `define_key` = stem of the `.bin` filename.

### `xfl_main.py` / `xfl_*.py`
FBIN → Adobe Animate XFL export pipeline. Modules:

| Module | Role |
|---|---|
| `xfl_main.py` | CLI entry point |
| `xfl_document.py` | XFL document structure builder |
| `xfl_exporter.py` | Writes the XFL folder/files to disk |
| `xfl_sprite.py` | Sprite/symbol generation |
| `xfl_image.py` | Image asset handling |
| `xfl_media.py` | Media library management |
| `xfl_label.py` | Frame label helpers |
| `xfl_helpers.py` | Shared utilities |

### `debug_anim.py`
Diagnostic CLI tool. Prints the full MC draw tree for any action/frame.

```
python debug_anim.py --bin char.bin [--action idle] [--frame 0]
python debug_anim.py --bin char.bin --dump-mc 17 [--frame 0]
python debug_anim.py --bin char.bin --scan
```

---

## Data Flow

```
.bin file
   └─ fbin_parser.parse_fbin()
        ├─ FBIN magic? → _parse_impl() [4 variants]
        └─ no magic?   → rawbin_parser.parse_rawbin_from_bytes()

.pvr / .png atlas
   └─ pvr_loader.load_pvr_texture()  OR  pygame.image.load()
        └─ pygame.Surface (RGBA)

(images, movie_clips, actions, is_rawbin) + texture
   └─ Player(...)
        └─ Renderer.draw(mc_idx, frame_num, transform_matrix)
             └─ recursive MC tree walk → surface.blit()
```

---

## Optional Metadata (`animaction.txt`)

A TSV/CSV file providing per-action scale, offset, fps, flip, and frame-range overrides.

- Auto-discovered in: directory of `.bin` → `cwd` → script directory.
- Loaded via `anim_meta.AnimMeta` (module not always present).
- Override with `--meta PATH` or disable with `--no-meta`.

---

## Dependencies

| Package | Required | Use |
|---|---|---|
| `pygame` | Yes | Display, input, surface ops |
| `numpy` | Optional | Accelerated PVRTC decoding |
| `Pillow` | Optional | GIF export, Dreamcast PVR via pypvr |
| `pypvr.py` | Optional | Dreamcast/Naomi PVR decoding |

Install: `pip install pygame numpy Pillow`

---

## Usage Examples

```bash
# Play animation with PVR texture
python main.py --bin waterpea.bin --pvr waterpea.pvr

# Play with pre-decoded PNG atlas
python main.py --bin waterpea.bin --atlas waterpea.png

# Play with metadata override
python main.py --bin char.bin --pvr char.pvr --meta animaction.txt --define zombie_pirate_imp

# Export to XFL for Adobe Animate
python xfl_main.py --bin char.bin --atlas char.png --out ./output

# Debug draw tree
python debug_anim.py --bin char.bin --action idle
```

---

## Key Constraints & Gotchas

- `offset_x / offset_y` in FBIN are **Flash registration points** — never clamp them, they can legitimately exceed sprite dimensions.
- RawBin world positions are **absolute** — don't add character-level scale/offset.
- PVRTC decoding requires power-of-two textures and wraps blocks (modulo).
- The renderer skips sprites at `tex_x=0, tex_y=0` with size ≤ 4×4 (Flash pivot markers).
- Flash sometimes exports stale keyframe placements → FBIN dedup keeps only the **last** placement per image id per frame.
