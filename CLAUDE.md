# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Python toolset for reading, playing, and re-exporting **Cocos2d-x FBIN / RawBin** animation files from Plants vs. Zombies (and similar games). Built with pygame, numpy (optional), and Pillow.

Install dependencies: `pip install pygame numpy Pillow`

Three top-level scripts:
- `main.py` — pygame player.
- `tools/convert_to_package.py` — `.bin + atlas` → Flash CS5 `.package` (XFL project).
- `tools/convert_from_package.py` — `.package` → `.bin + .pvr` (round-trip back to game-loadable RawBin + RGBA8888 PVR atlas).
- `tools/bin_diff.py` — structural diff between two `.bin` files (dev utility).

---

## Layout

```
PVZ_AS_Script/
├── _paths.py              registers parsers/render/pvr on sys.path
├── main.py                pygame player entry point
├── tools/
│   ├── convert_to_package.py     .bin -> .package
│   ├── convert_from_package.py   .package -> .bin + .pvr
│   └── bin_diff.py               compare two .bin files
├── parsers/               fbin_parser.py (parse_fbin + parse_binary), rawbin_parser.py, input_buffer.py
├── render/                renderer.py + player/ subpackage (core, hud, input, export)
├── pvr/                   pvr_loader.py (load_pvr_texture + convert_pvr_to_png), pypvr.py
└── samples/               example .bin / .pvr files
```

Imports stay flat (`from fbin_parser import parse_binary`). Each entry-point script registers paths before importing project modules:
- `main.py` (at repo root): `sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))` then `import _paths`.
- `tools/*.py`: same pattern but one level up — `os.path.join(os.path.dirname(__file__), '..')` — so `_paths.py` is found from the repo root.

> The legacy XFL exporter, RawBin writer, debug scripts, round-trip tests, resolution-conversion tool, and per-character display-tweak tables (`config/anim_meta.py` + `config/default_settings.py`) are gone. `tools/convert_to_package.py` is the replacement XFL exporter; `tools/convert_from_package.py` is the new round-trip back to RawBin.

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
- **FBIN ground-swatch skip** (`_GROUND_PLANE_NAMES` = `ground_swatch`, `ground_swatch_plane`, `_ground`): in the FBIN draw path these lawn-alignment placeholder MCs are skipped entirely. They draw a thin ground strip (e.g. `001_105x3`, a band of atlas/padding garbage) that a parent stretches tens of times to mark the tile (`zombie_snai` walk: `ground_swatch` scales `001_105x3` by `sy=43.33` → 105×130 of colored stripes). The game never renders them (the lawn is separate). **FBIN-only** — in RawBin `ground_swatch` is MC[1], used as a dispatch-route redirect that is never recursed into by name, so the skip can't interfere there.
- **RawBin element dispatch** — `mc_id` (eid) determines how `frame_index` (fi) is interpreted:
  - `mc_id=1` → **always redirect to body-part MC[fi]**. MC[1] is universally a 1-frame redirect-wrapper (named `ground_swatch`, `zombie_imp_pirate_hand1`, etc.) whose fi value is the *target MC index*, not an image index. The target MC then draws its own sub-sprites (e.g. MC[15]=zombie_basic_eye draws image 14).
  - `mc_id≠1` (eid=0 ground, eid=2 image-pointer, etc.) → **draw image fi directly** when fi < len(images). This is the terminal draw for all body-part sub-elements.
- **Transform cache**: LRU, up to 2048 pre-scaled/rotated surfaces (`_NAME_OVERRIDES` applies hardcoded flip/size corrections for `jaw`, `flag`, `31-031`).
- **Shear: guarded affine path**. `_draw_image`'s fast path extracts `(scale_x, scale_y, rotation, flip_y)` from the cumulative matrix and uses `pygame.transform.scale` + `pygame.transform.rotate`, which **drops 2D shear** (matrices where the X- and Y-axes rotate by different angles — Flash walk-cycle limb bends, attack motion-smear trails). When the cumulative matrix shears the axes by more than `_SHEAR_AFFINE_DEG` (5°, via `_affine_shear_deg`), `_draw_image_affine` warps the true parallelogram with a PIL affine instead (texture→screen linear map `A=[[na,-nc],[nb,-nd]]`, warped about the sprite centre and blitted so the centre lands at the same `(wcx,wcy)` as the fast path). Below the threshold the fast path is visually identical and stays in use — that guard is why this doesn't regress no-shear content (an earlier *unconditional* PIL path did). Falls back to the fast path if PIL is missing or the matrix is degenerate. Example: `zombie_snai` `attack3` has faint low-alpha sheared jaw-bitmap smear trails that rendered as rigid floating jaws before this.
- **Alpha is leaf-only**: `_draw_image()` reads `elem['alpha']` from the leaf image element. Alpha set on an `is_mc=True` parent is NOT propagated to children. (Known limitation — relevant when a transparency effect is authored on a MovieClip instance rather than a leaf image, e.g. `zombie_kungfu_hammer`.)

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

`_base_transform()` maps from Cocos Y-up space to pygame screen space (Y-flip is built in),
placing the world origin at the screen centre with zoom/pan applied. It does **no**
content re-centring — bins are pre-centred on the world origin at conversion time
(`convert_from_package._center_actions`), so there is no runtime auto-centre probe
(the former RawBin-only `_probe_rawbin_center` was removed). Older bins not re-exported
through the current converter will sit wherever their source anchor is.
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

## XFL exporter (`tools/convert_to_package.py`)

Emits a PvZ `.package` that wraps a Flash CS5 XFL project. Single-character OR group bundle (multiple PopAnims in one subgroup). Output reads in Adobe Animate via `<package>/.../main.xfl`.

### CLI

```bash
# single character (emits Foo_4.package + Foo_5.package)
python tools/convert_to_package.py --bin char.bin --pvr char.pvr

# group bundle — single top-level subgroup with multiple resources
python tools/convert_to_package.py --group ZombieKungfuGroup \
    --bin zombie_kungfu_basic.bin --bin zombie_kungfu_flag.bin

# auto-group — a lone --bin auto-bundles its `<stem>_*.bin` siblings
python tools/convert_to_package.py --bin zombie_slingshot.bin
#   folder also has zombie_slingshot_bullet.bin + zombie_slingshot_re.bin
#   -> ZombieSlingshotGroup_{4,5}.package (base in zombie/, the two in effects/)
```

`--bin`/`--pvr` are repeatable; sibling `.pvr` auto-pairs when `--pvr` is omitted. `--resolution` defaults to `1536` (matches every reference package: PlantPeashooter, ZombieTutorialGroup, ZombieEgyptTombRaiserGroup); pass `768` for SD.

**Auto-grouping** (`_discover_group_siblings`): a single `--bin` whose folder also holds `<stem>_*.bin` files (the character's effects/variants) is bundled into one `<Stem>Group.package` automatically — matching how real PvZ packages ship a zombie with its effects. The siblings reuse the group effect-detection (any stem extending another with `_` → `effects/`). Skipped when the user is explicit (`--group`, `--pvr`, `--subgroup`/`--char-name`/`--id-prefix`) or passes `--no-auto-group`. If no siblings exist (e.g. an iOS dump with only the base bin), it falls back to a single-character package.

### Path / ID routing (`_derive_defaults`)

| stem | type_path | resource ID prefix |
|---|---|---|
| contains `zombie` | `zombie`  | `POPANIM_ZOMBIE_<STEM>` |
| contains `plant`  | `plant`   | `POPANIM_PLANT_<STEM>` |
| effect (see below)| `effects` | `POPANIM_EFFECTS_<STEM>` |
| anything else     | *(empty)* | `POPANIM_<STEM>`        |

Empty `type_path` drops the category subfolder: `images/initial/<stem>`. The PopAnim folder layout is **single-nested** (`<stem>` once) for both v4 and v5 — early versions used double-nested `<stem>/<stem>` for v5, but this was simplified after the user confirmed the runtime accepts the flat form.

**Effect detection** lives in the CLI loop, NOT in `_derive_defaults` (which only flips on the explicit `force_effect` arg). In group mode the CLI flags any bin whose stem strictly extends another `--bin`'s stem with `_` as an effect of that bin. So `legend_zombie_Sprinter.bin` + `legend_zombie_Sprinter_bullet.bin` → one `zombie/` + one `effects/`. Works for arbitrary suffix words (`_bullet`, `_attack`, `_re`, `_fire`, `_bo`, anything). No hard-coded suffix list. Single-bin mode has nothing to compare against, so effects there require an explicit `--type-path effects`.

### Architecture

| Function | Role |
|---|---|
| `convert()` | Single-character package (back-compat). Calls `_write_character_assets` + writes single-resource top-level data.json. |
| `convert_group()` | Group package. Loops chars through `_write_character_assets`, then writes one multi-resource top-level data.json. |
| `_write_character_assets()` | Shared workhorse. Writes inner data.json, main.xfl, library/{image,sprite,label,media}, DOMDocument.xml for one character. Returns `(resource_id, resource_path)`. Caller writes top-level data.json. |
| `_top_data_v4_multi` / `_top_data_v5_multi` | Multi-resource top-level data.json builders. Single-char helpers wrap these with a one-element list. v4 keys the resource dict by ID; v5 lists resources as an array. |
| `_inner_data_v4` / `_inner_data_v5` | Inner `data.json` per character. Image table (id, dimension, additional) + `sprite` field. **`sprite.""` is populated ONLY for effect characters** — zombies/plants leave it `{}` (matches both reference convention; the PvZ packer is picky here). |
| `_emit_image_symbol` | Bitmap is positioned at local `(+offset_x, +offset_y)`, NOT `(-offset_x, -offset_y)` — derived from the renderer's `lcx=offset_x+w/2, lcy=-offset_y-h/2` (after Y-flip). Inverted sign placed bitmaps on the wrong side of the registration when offsets were negative. |
| `_emit_sprite_symbol` / `_emit_label_symbol` | Build XFL sprites/labels by walking MC frames and emitting one `<DOMLayer>` per element slot. Particle labels are sorted to the end of the timeline (`_is_particle`) — source FBINs often place them mid-list. |
| `_element_payload` | RawBin-aware dispatch mirroring `render/renderer.py:263-288`. Without this the converter treats `mc_id` as a direct MC index, producing self-referential sprites (e.g. MC[1] `coconut_cloud_front` → itself) — Flash CS5 hard-crashes on cycles. |
| `_flash_matrix` | Cocos Y-up → Flash Y-down: `(sx, ky, kx, sy, tx, ty) → (sx, -ky, -kx, sy, tx, -ty)`. |
| `_probe_frame_bbox` + `_image_world_rect` | Pure-Python matrix walker (no pygame). Computes the union bbox of all image draws in Flash space for the first action's middle frame. The offset `(stage_center - bbox_center)` is baked into each root-timeline label instance so the character lands on-canvas. |
| `_emit_dom_document` | Root timeline with `label`, `action`, `instance` layers. **`frameRate` is hard-coded to 30** regardless of MC `frame_rate`. Action layer handles `dur==1` labels by emitting only a stop keyframe (no body+stop split — that would duplicate frame indices). |

### Hard-learned constraints

- **Action `start`/`end` are GLOBAL playlist indices, not local MC frame ranges.** `_clamp_action_range`-style heuristic: if `raw_start > last_frame`, `raw_end > last_frame`, `duration > last_frame`, or `raw_start > 0 and duration >= last_frame` → use the full MC range `(0, last_frame)`. The naïve `min(start, last_frame)` clamp produced single-frame labels for everything after `idle` (e.g. `attack` collapsed to 1 frame for applemortar_3). The `raw_end > last_frame` term is essential for a **walk whose global span is shorter than its MC** (e.g. `zombie_primitive` walk: `start=63 end=127 last_frame=69` → `duration 64 < 69`; the duration-only checks miss it and play only frames 63-69 of the 70-frame cycle). Both the player (`_clamp_action_range`) and the exporter carry the identical heuristic.
- **Image symbol matrix sign**: `tx=+offset_x, ty=+offset_y`. Working PlantPeashooter reference uses negative tx/ty because its source offsets are positive; ours can be negative (applemortar IMG[3] `ox=-49.2`), and the sign must match the renderer's geometry.
- **No duplicate `<DOMFrame index="N">` within one `<DOMLayer>`** — Flash CS5 crashes. The action layer's 1-frame label edge case must emit only the stop keyframe.
- **No symbol cycles** — `sprite/X` containing `libraryItemName="sprite/X"` crashes Flash CS5. Always validate with a cycle scan after rebuilding.
- **Particle labels last** — source FBINs (e.g. `zombie_kungfu_basic.bin`: idle, walk, die, particles, attack, …) often place `particles` mid-list, but Flash projects expect it as a trailing label. Stable partition pushes any `particle*` label to the end.
- **Symbol identifiers can't start with a digit** — `_safe_name` prepends `_` so source MC names like `'0001'` become `_0001`. Flash and the PvZ packer both reject `0001` as a library item name.
- **`id_prefix` is `IMAGE_<CAT>_<CHAR>_` (single CHAR)** — the symbol name is already `<char>_<wxh>`, so the final ID is `IMAGE_<CAT>_<CHAR>_<CHAR>_<wxh>` (2× CHAR + size). Doubling CHAR in the prefix produced a triple-repeat (`IMAGE_APPLEMORTAR_3_APPLEMORTAR_3_APPLEMORTAR_3_130X122`).

---

## XFL importer (`tools/convert_from_package.py`)

Round-trip back: reads any `.package` (or a bare XFL folder) and writes a `.bin` + an iOS PVR v2 RGBA8888 `.pvr` per character. Output plays in `main.py` and (with the 4-byte clip-header fix) loads in the actual PvZ runtime.

**Output format is auto-selected (`use_fbin`).** RawBin addresses each element's MC/image index with a single byte, so it can only reference 256 movie-clips / images. Characters with **more than 256 MCs OR images** (e.g. MegaGatling: 373 MCs / 317 images; ~34% of its references overflowed and were silently dropped — the "lots of missing assets" bug) are written as **FBIN** instead, whose `elem_id`/`frame_index` are 2-byte shorts (`_write_fbin` / `_write_float_min` / `_write_fbin_element`). Smaller characters keep the proven RawBin output. The two formats use different element models, so lowering branches on `use_fbin`:
- **RawBin element**: `mc_id` dispatch route (1=sprite redirect, 2=image) + `frame_in_mc` = target index.
- **FBIN element**: `is_mc` + `id` = MC/image index directly + `frame_index` = the referenced sprite's sub-frame, taken from the XFL instance's `firstFrame` (captured in `_parse_instance`).
The FBIN writer emits the `num_versions=2, has_transform=True, order=A` variant (`ext_float=1.0`, per-MC `frame_rate=30`) — the one `fbin_parser._parse_impl` tries first. Verified by round-tripping through `parse_binary` (all 373 MCs / 26 540 references preserved, 0 dropped).

### CLI

```bash
# .package (single character or group — group fans out per character)
python tools/convert_from_package.py --package Foo_5.package --out samples/test/

# or point at an XFL folder directly (the one containing main.xfl)
python tools/convert_from_package.py --package Foo_5.package/resource/images/initial/foo --out samples/test/
```

### Architecture

| Function | Role |
|---|---|
| `_find_xfl_roots` | Accepts a `.package` directory (walks for any `main.xfl`) OR a direct XFL folder. Group packages return multiple roots — caller emits one `.bin/.pvr` pair per character. |
| `_read_flash_matrix` / `_mat_mul` / `_flash_to_cocos` | Matrix helpers in Flash convention. `_flash_to_cocos` is the inverse of `convert_to_package._flash_matrix` (self-symmetric: `(a, -b, -c, d, tx, -ty)`). |
| `_parse_image_symbol_matrix` | Reads the full `<Matrix a b c d tx ty/>` from each `library/image/<name>.xml`. Real PvZ packages put a scale here (typically `a=d=0.781250`) that maps the authoring resolution to the atlas resolution — must be folded into every reference's outer matrix. |
| `_parse_instance` | For `libraryItemName="image/..."` references, composes `combined_flash = outer_matrix * image_symbol_matrix` then converts to cocos. For `sprite/...` references, the outer matrix is used as-is (nested sprite's elements will fold in their own image scales). Result: image's `offset_x/y` in the bin can stay `0` — the combined matrix carries all positioning. Also captures the instance's `firstFrame` (sub-frame index), used only by FBIN output. |
| `_timeline_frames` | Walks a sprite/label XML's `<DOMLayer>`s (reversed so bottom-layer-first matches z-order), expands `<DOMFrame index/duration>` into a per-frame element list. |
| `_parse_actions` | Reads the root timeline's `label` layer markers → `(name, start, duration)`. |
| Element lowering | Branches on `use_fbin`. **RawBin**: `sprite/foo` → `mc_id=1, frame_index=<MC index of foo>`; `image/bar` → `mc_id=2, frame_index=<image index of bar>` (matches `render/renderer.py:263-288`); references whose index > 255 are dropped. **FBIN**: `sprite/foo` → `is_mc=True, id=<MC index>, frame_index=<firstFrame>`; `image/bar` → `is_mc=False, id=<image index>` (no 256 limit). |
| `_write_fbin` / `_write_fbin_element` / `_write_float_min` | FBIN serializer used when `use_fbin` (>256 MCs or images). MinBin floats, 2-byte element ids. Emits the parser's preferred variant (`versions=[1,0]`, `has_transform=True`, `order A`, `ext_float=1.0`, per-MC `frame_rate=30`). |
| `_center_actions` / `_fbin_world_bbox` / `_rawbin_world_bbox` / `_expand_image_bbox` / `_compose` | Content centring, baked into the bin for **both** formats. A character's source anchor sits ~200 px off its artwork centre, so it renders off-centre. For each action-root (label) MC, the matching walker (FBIN: id = direct index; RawBin: mc_id dispatch — mirrors `render/renderer.py:263-297`) computes the world bbox over **all** the action's frames; the action's frames are then shifted so that union centres on the world origin. Per-action (not one global offset) so a wide `walk` doesn't drag the resting `idle` off-centre. The player maps the origin to the screen centre, so the character lands centred with **no runtime auto-centring** (the old `_probe_rawbin_center` was removed). |
| `_pack_atlas` | Shelf-packs every PNG from `library/media/` into one power-of-two atlas. Records `(tex_x, tex_y, w, h)` per image. |
| `_encode_pvr_rgba8888` | Writes an iOS PVR v2 header (52 bytes, magic `PVR!` at offset 44, `pixel_type = 0x12` = RGBA8888 — what `pvr/pvr_loader.py:_FMT_RGBA8888` decodes) followed by raw RGBA bytes. No PVRTC encoder needed. |
| `_write_rawbin` | Pure `struct.pack`. **Clip header is 4 bytes** (`u16 num_frames + u16 pad`), NOT 6 — the parser accepts both but the PvZ runtime only loads the 4-byte form. Matches `samples/1.0.4/zombie_tutorial.bin` (a known-good in-game file). Element layout: 38 bytes = `mc_id(1) + frame_in_mc(1) + extra_float(4) + 6 matrix floats(24) + color_mult(4) + color_add(4)`. `color_mult = (255, 255, 255, alpha*255)` by default; `color_add = (0, 0, 0, 0)`. |

### Hard-learned constraints

- **Clip header must be 4 bytes**, not 6. Writing 6 makes the parser accept the file but crashes the actual game.
- **PIL is required** to compose the atlas (no fallback).
- **Image symbol scale must be folded into the outer matrix** for `image/...` refs — otherwise sprites render `1/scale` larger than intended ("super close" but wrong).
- **Group packages fan out** to multiple output files (one per character); single-character packages emit one pair.
- **Trigger-only overlays are stripped from action timelines** — sprites named `butter`, `ink`, `red_eyes` (`_TRIGGER_OVERLAY_NAMES`) are NOT drawn by default in-game; the runtime injects them on a state trigger (a buttered zombie; the Atlantis zombie's ink-cloud / glowing-eye rage). Source XFLs reference them on dedicated label layers in every frame, which would bake them in as always-visible. We drop those references from label (action-root) timelines only — the sprite MC stays in the library so a trigger can still show it. Same convention as the armor-4 / `hermit_crab` wrapper strip.
- **RawBin's 1-byte element index caps a character at 256 MCs / images.** Big characters (MegaGatling's `megagatling`/`megagatling1`/`megagatling2` plant variants — 330-376 MCs, 317-365 images) overflow it and lose ~⅓ of their sprite references. `use_fbin` switches those to FBIN output. FBIN's element model differs (id = direct index + `firstFrame` sub-frame, vs RawBin's mc_id dispatch), so capturing `firstFrame` and the separate lowering branch are both required — without `firstFrame`, every sub-sprite would show frame 0. **No real FBIN reference file exists in the repo**, so the writer targets the project's own parser variant; in-game compatibility of the emitted FBIN is unverified (it plays correctly in `main.py`).

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
