# PVZ_AS_Script

Read, play, and re-export *Plants vs. Zombies* animation files (Cocos2d-x **FBIN** and **RawBin**).

Three entry points:

| Script | What it does |
|---|---|
| `main.py` | Pygame player. Opens a `.bin` + atlas in a window. |
| `tools/convert_to_package.py` | `.bin + atlas` → Flash CS5 `.package` (XFL project). |
| `tools/convert_from_package.py` | `.package` → `.bin + .pvr`. Round-trip back into a playable / game-loadable pair. |

---

## Install

Python 3.8+

```bash
pip install pygame numpy Pillow
```

---

## Play

```bash
# pick files in a dialog
python main.py

# or pass them
python main.py --bin samples/char.bin --pvr samples/char.pvr

# or pass one and the sibling (same stem) auto-pairs
python main.py --bin samples/char.bin
```

Atlas can be `.pvr` OR `.png` — the loader sniffs the file.

### Keys

| Key | Action |
|---|---|
| `←` `→` | Previous / next action |
| `↑` `↓` | Speed +/- 0.1× |
| `Space` | Pause |
| `N` `B` | Step one frame forward / back |
| `F` | Jump to frame |
| `L` | Toggle loop |
| `I` | Action picker |
| `K` | Hide `butter` accessory (kungfu zombies) |
| `C` | Cycle costumes (plants with `custom_NN_*` variants) |
| `G` / `A` / `Z` | Export current / all / all-no-bg as GIF |
| `S` / `T` / `J` | Export sprites / atlas / frame JSON |
| `H` / `?` | HUD / full help |
| Mouse wheel | Zoom |
| Right-drag | Pan |
| Click scrub bar | Seek |
| `F11` | Fullscreen |
| `Esc` / `Q` | Quit |

---

## Convert `.bin` → `.package`

```bash
# one character (emits Foo_4.package + Foo_5.package by default)
python tools/convert_to_package.py --bin samples/char.bin --pvr samples/char.pvr

# group package — multiple bins under one subgroup
python tools/convert_to_package.py --group ZombieKungfuGroup \
    --bin samples/zombie_kungfu_basic.bin \
    --bin samples/zombie_kungfu_flag.bin
```

**Auto-routing** by filename:

| Stem contains | Folder |
|---|---|
| `zombie` | `images/initial/zombie/<stem>` |
| `plant` | `images/initial/plant/<stem>` |
| none of the above | `images/initial/<stem>` |

**Effects**: in `--group` mode, any bin whose stem extends another bin's stem with `_` is treated as an effect of that bin and routed to `images/initial/effects/`. So `zombie_foo.bin` + `zombie_foo_bullet.bin` → one zombie + one effect (works for any suffix word: `_bullet`, `_re`, `_attack`, `_fire`, anything).

**Useful flags**: `--version 4|5|both` (default `both`), `--resolution 768|1536` (default `1536`).

Open the project in Animate CS5 via `<package>/.../main.xfl`.

---

## Convert `.package` → `.bin + .pvr`

```bash
# any .package (single character or group — group fans out per character)
python tools/convert_from_package.py --package samples/Foo_5.package --out samples/test/

# or point at an XFL folder directly (the one containing main.xfl)
python tools/convert_from_package.py --package samples/Foo_5.package/resource/images/initial/foo --out samples/test/
```

Output: `<stem>.bin` (RawBin, 4-byte clip header — game-loadable) + `<stem>.pvr` (iOS PVR v2, RGBA8888).

Play it back in `main.py` to verify.

---

## Importable API

```python
from fbin_parser import parse_binary           # dict: format/images/movie_clips/actions/is_rawbin
from pvr_loader  import load_pvr_texture       # PVR -> pygame.Surface
from pvr_loader  import convert_pvr_to_png     # PVR -> PNG on disk
```

---

## Layout

```
PVZ_AS_Script/
├── main.py                 player entry point
├── _paths.py               sys.path setup for library subfolders
├── tools/
│   ├── convert_to_package.py     .bin -> .package
│   ├── convert_from_package.py   .package -> .bin + .pvr
│   └── bin_diff.py               compare two .bin files structurally
├── parsers/                FBIN + RawBin parsers
├── render/                 renderer + player subpackage
├── pvr/                    PVR/PVRTC texture loader
└── samples/                example .bin / .pvr pairs
```

For implementation notes — format quirks, RawBin element dispatch, matrix conventions, conversion details — see **`CLAUDE.md`**.

---

## License

Educational and research use. Game assets belong to their respective owners.
