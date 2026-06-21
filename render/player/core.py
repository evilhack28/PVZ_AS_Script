"""
Player core: PlayerConfig, _PlayerCore (run loop, base transform, meta resolution).
"""

import logging
import math
import os
import re
from dataclasses import dataclass
from typing import Optional, List

import pygame

from renderer import Renderer, BoundingBox
from fbin_parser import DEFAULT_FRAME_RATE

# Plant/zombie costume MCs follow a `_custom*` / `custom_NN*` naming convention
# (e.g. `_custom_left`, `custom_01_left`, `wuxh_wdss_custom_03`). `_CUSTOM_PAT`
# detects any costume MC; `_VARIANT_PAT` pulls out the trailing variant number
# from numbered ones — when the number is absent the MC is the "base" slot.
_CUSTOM_PAT  = re.compile(r'custom', re.IGNORECASE)
_VARIANT_PAT = re.compile(r'custom[_\W]?(\d+)', re.IGNORECASE)

# Helmet/armor MCs ship as `<family>_<state>` where state ∈
# {norm, norm_wu, damage_NN, plantfood}. The picker (M key) lists every
# matching MC as its own checkbox so the user can hide individual
# helmet states (e.g. show only damage_02 of the cone).
_HELMET_STATE_PAT = re.compile(
    r'^(.+?)_(norm_wu|norm|damage_\d+|plantfood)$', re.IGNORECASE)
_HELMET_STATE_ORDER = {
    'norm':      0,
    'norm_wu':   1,
    'damage_01': 2,
    'damage_02': 3,
    'damage_03': 4,
    'plantfood': 5,
}

log = logging.getLogger(__name__)


# ── Config ────────────────────────────────────────────────────────────────────

# Valid fps_mode values:
#   'source'  - use the raw frame_rate stored in the MC
#   'custom'  - use fps_custom value regardless of source

@dataclass
class PlayerConfig:
    window_width:   int   = 1024
    window_height:  int   = 768
    fps_cap:        int   = 60
    start_action:   int   = 0
    loop:           bool  = True
    background_rgb: tuple = (40, 40, 40)
    hud_font_size:  int   = 16
    pvr_name:       str   = "sprites"   # stem of the .pvr file, used as export folder name
    output_dir:     str   = "."         # base directory for all exports (GIF/sprites/atlas/JSON)
    fps_mode:       str   = "source"    # 'source' | 'custom'
    fps_custom:     int   = 30          # fps used when fps_mode == 'custom'
    show_help:      bool  = False       # ? overlay
    fullscreen:     bool  = False       # F11 toggle
    zoom:           float = 1.0         # mouse wheel zoom
    pan_x:          float = 0.0         # right-drag pan (screen pixels)
    pan_y:          float = 0.0


# ── Player core ───────────────────────────────────────────────────────────────

class _PlayerCore:
    def __init__(self, images: list, movie_clips: list, actions: list,
                 texture_surf: pygame.Surface,
                 config: Optional[PlayerConfig] = None,
                 rawbin: bool = False,
                 define_key: str = "") -> None:

        self.images      = images
        self.movie_clips = movie_clips
        self.texture     = texture_surf
        self.cfg         = config or PlayerConfig()
        self.define_key  = define_key
        self.renderer    = Renderer(images, movie_clips, texture_surf, rawbin=rawbin)

        self.playlist = self._build_playlist(actions)
        if not self.playlist:
            raise RuntimeError("No animations to play.")

        self.current_idx   = max(0, min(self.cfg.start_action, len(self.playlist) - 1))
        self.speed         = 1.0
        self.loop          = self.cfg.loop
        self.paused        = False

        # FPS mode: 'source' | 'meta' | 'custom'
        self.fps_mode      = self.cfg.fps_mode
        self.fps_custom    = self.cfg.fps_custom

        self.show_list     = False
        self.list_selected = self.current_idx
        self.show_hud      = True

        # K key toggles this. When True, any sprite whose name contains
        # 'butter' is suppressed during draw — useful on the kungfu zombies
        # whose butter sprite covers the head.
        self.hide_butter = False

        # Helmet picker (M key). Built from MC names matching
        # `<family>_<state>` where state is norm/damage_NN/plantfood/etc.
        # User checks/unchecks rows; unchecked names go into
        # renderer.hidden_parts via _apply_filters().
        self._init_helmets()

        # Costume picker state. C key cycles through self.costume_cycle which
        # is built from MC names containing 'custom' at startup. _apply_costume()
        # pushes the resulting hidden-MC set to the renderer.
        self._init_costumes()

        self._apply_filters()

        # Temporary status message shown after GIF export
        self._gif_msg      = ""
        self._gif_msg_ttl  = 0

        # Used by N/B step keys
        self._step_frame_idx = None

        # Frame-number input mode (press F, type digits, Enter to jump)
        self._frame_input_active = False
        self._frame_input_buf    = ""
        # Custom fps input mode (press 4, type digits, Enter to confirm)
        self._fps_input_active   = False
        self._fps_input_buf      = ""

        # View state (zoom/pan/fullscreen/help) — seeded from config, mutated by InputMixin
        self.show_help    = bool(self.cfg.show_help)
        self.fullscreen   = bool(self.cfg.fullscreen)
        self.zoom         = float(self.cfg.zoom)
        self.pan_x        = float(self.cfg.pan_x)
        self.pan_y        = float(self.cfg.pan_y)
        # Stored windowed (w, h) before fullscreen, so F11 can restore it
        self._windowed_size: tuple = (self.cfg.window_width, self.cfg.window_height)
        # Scrub bar geometry — set by HudMixin each frame, read by InputMixin click handler
        self._scrub_bar_rect = None
        # Right-button drag-pan state
        self._pan_origin = None   # (mouse_x, mouse_y, pan_x_start, pan_y_start) | None
        # Left-button scrub-drag state (True while held inside the scrub bar)
        self._scrub_dragging = False

    # ── Helmet picker ─────────────────────────────────────────────────────────

    def _init_helmets(self) -> None:
        """Scan MC names for `<family>_<state>` patterns and build a flat
        ordered list of helmet variants. Each row is a checkbox in the M
        picker. By default every variant is visible — the user unchecks the
        states they want hidden (e.g. keep only damage_02 of the cone)."""
        # family -> list of {mc_idx, mc_name, state}
        groups: dict = {}
        for i, mc in enumerate(self.movie_clips):
            name = mc.get('name', '')
            if 'armor' not in name.lower():
                continue
            m = _HELMET_STATE_PAT.match(name)
            if not m:
                continue
            family = m.group(1)
            state  = m.group(2).lower()
            groups.setdefault(family, []).append({
                'mc_idx':  i,
                'mc_name': name,
                'state':   state,
            })

        rows: list = []
        for family in sorted(groups.keys()):
            variants = groups[family]
            variants.sort(key=lambda v: (_HELMET_STATE_ORDER.get(v['state'], 99),
                                          v['state']))
            for v in variants:
                rows.append({'family': family, **v})

        self.helmet_rows:    list = rows
        self.helmet_visible: dict = {r['mc_name']: True for r in rows}
        self.show_helmets:   bool = False
        self.helmet_sel:     int  = 0

    def _apply_filters(self) -> None:
        """Push the union of all hidden-name sources to the renderer.
        Sources: K-key butter toggle + helmet picker unchecks. Substring
        matching in the renderer means each MC name acts as its own filter."""
        parts: set = set()
        if getattr(self, 'hide_butter', False):
            parts.add('butter')
        for name, visible in getattr(self, 'helmet_visible', {}).items():
            if not visible:
                parts.add(name.lower())
        self.renderer.hidden_parts = frozenset(parts)

    # ── Costume picker ────────────────────────────────────────────────────────

    def _init_costumes(self) -> None:
        """Build costume swap slots from MC names + action-tree references.

        A "slot" is a group of MCs sharing the same name stem around `custom`
        (e.g. `_custom_left` and `custom_01_left` both share stem `left`).
        A numbered MC (`custom_NN_*`) only counts as a real costume variant
        when it's never referenced from any action MC's frames — those are
        the alternates the game swaps in at runtime. MCs whose names happen
        to contain "custom" but are actively drawn (chizhenhua's `custom_01`
        and `custom_02`, which dispatch the entire body) are ignored.

        The slot's base — the MC variant N replaces — is the referenced
        unnumbered sibling if one exists (CherryBomb `_custom_left` →
        `custom_01_left`); otherwise the unnumbered slot member (peashooter
        `wuxh_wdss__custom`) which may itself be unreferenced (the picker
        becomes a no-op in that case but the cycle still surfaces).
        """
        ref_counts = self._action_mc_ref_counts()

        # bucket by stem -> list of (variant_key, mc_idx)
        by_stem: dict = {}
        for i, mc in enumerate(self.movie_clips):
            name = mc.get('name', '')
            if not _CUSTOM_PAT.search(name):
                continue
            stem  = self._costume_stem(name)
            m     = _VARIANT_PAT.search(name)
            var   = int(m.group(1)) if m else 'base'
            by_stem.setdefault(stem, []).append((var, i))

        slots: list = []
        for stem, members in by_stem.items():
            numbered  = [(v, idx) for v, idx in members if isinstance(v, int)]
            base_mem  = [idx for v, idx in members if v == 'base']
            # Filter out numbered variants that are actively drawn — they're
            # body parts misnamed as "custom", not swap targets.
            numbered  = [(v, idx) for v, idx in numbered
                         if ref_counts.get(idx, 0) == 0]
            if not numbered:
                continue
            # Prefer a referenced unnumbered MC as the base (the active body
            # part the variant replaces). Fall back to any unnumbered, else
            # the lowest-numbered variant.
            base_mc = next((idx for idx in base_mem if ref_counts.get(idx, 0) > 0), None)
            if base_mc is None and base_mem:
                base_mc = base_mem[0]
            if base_mc is None:
                base_mc = min(numbered, key=lambda vi: vi[0])[1]
            slots.append({
                'stem':     stem,
                'base_mc':  base_mc,
                'variants': {v: idx for v, idx in numbered},
            })

        self.costume_slots: list = slots
        all_variants: set = set()
        for s in slots:
            all_variants.update(s['variants'].keys())

        cycle: list = ['all']
        if slots:
            cycle.append('none')
            cycle.extend(sorted(all_variants))
        self.costume_cycle: list = cycle
        self.costume_mode_idx: int = 0
        self.costume_mode = cycle[0]
        self._apply_costume()

    def _action_mc_ref_counts(self) -> dict:
        """Count direct MC-element references inside each action MC's frame
        tree. Used to tell active body parts from inert swap targets."""
        roots = {a['mc_idx'] for a in self.playlist
                 if 0 <= a.get('mc_idx', -1) < len(self.movie_clips)}
        counts: dict = {}
        for ai in roots:
            for frame in self.movie_clips[ai]['frames']:
                for e in frame:
                    if e['is_mc']:
                        counts[e['id']] = counts.get(e['id'], 0) + 1
        return counts

    @staticmethod
    def _costume_stem(name: str) -> str:
        """Strip the `custom` marker (and its variant number) from a costume
        MC name to derive the shared slot key. `_custom_left` and
        `custom_01_left` both reduce to `left`; `wuxh_wdss__custom` and
        `wuxh_wdss_custom_03` both reduce to `wuxh_wdss`."""
        n   = name.lower()
        idx = n.find('custom')
        if idx < 0:
            return ''
        prefix = n[:idx].rstrip('_')
        rest   = n[idx + 6:]                       # past 'custom'
        rest   = re.sub(r'^[_]?\d+', '', rest)     # drop _NN
        rest   = rest.lstrip('_')
        if prefix and rest:
            return prefix + '_' + rest
        return prefix or rest

    def _apply_costume(self) -> None:
        """Resolve self.costume_mode to a renderer mc_remap dict."""
        mode  = self.costume_mode
        remap: dict = {}
        if mode == 'all' or not self.costume_slots:
            pass
        elif mode == 'none':
            for s in self.costume_slots:
                remap[s['base_mc']] = None
        else:
            for s in self.costume_slots:
                tgt = s['variants'].get(mode)
                # Slot has this variant → swap base for it. Slot lacks the
                # variant → drop the base so mixing characters with different
                # variant numbers (CherryBomb#1 + chizhenhua-style) stays sane.
                remap[s['base_mc']] = tgt if tgt is not None else None
        self.renderer.mc_remap = dict(remap)

    @property
    def costume_all_mcs(self) -> set:
        """Set of every MC index touched by the costume picker (HUD uses this
        to know whether to show the pill)."""
        out: set = set()
        for s in getattr(self, 'costume_slots', []):
            out.add(s['base_mc'])
            out.update(s['variants'].values())
        return out

    def _costume_mode_label(self) -> str:
        """Human-readable label for the current costume mode (HUD/messages)."""
        m = self.costume_mode
        if m == 'all':  return "ALL"
        if m == 'none': return "NONE"
        return f"#{m}"

    def _base_transform(self, screen_w: int, screen_h: int) -> tuple:
        """
        Build the root affine transform (a, b, c, d, tx, ty) that maps from
        Cocos Y-up space to pygame screen space. Identity + Y-flip centred on
        the screen, with zoom/pan/RawBin-recentre applied.
        """
        cx = screen_w * 0.5
        cy = screen_h * 0.5
        z  = self.zoom
        # Content is pre-centred on the world origin at conversion time (see
        # convert_from_package._center_actions), so the origin maps straight to
        # the screen centre and zoom stays anchored on the character.
        return (z, 0.0, 0.0, -z,
                cx + self.pan_x,
                cy + self.pan_y)

    def _resolve_fps(self, action: dict, mc=None) -> int:
        """
        Return the playback fps for *action*.

        'custom' → self.fps_custom
        otherwise → MC `frame_rate` if > 0, else DEFAULT_FRAME_RATE
        """
        if self.fps_mode == 'custom':
            return max(1, self.fps_custom)

        if mc is None:
            midx = action.get('mc_idx', -1)
            mc   = self.movie_clips[midx] if 0 <= midx < len(self.movie_clips) else None
        mc_fps = mc.get('frame_rate', 0) if mc else 0
        return mc_fps if mc_fps > 0 else DEFAULT_FRAME_RATE

    # ── View helpers (zoom/pan/fullscreen/screenshot) ─────────────────────────

    def _reset_view(self) -> None:
        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0

    def _toggle_fullscreen(self) -> None:
        self.fullscreen = not self.fullscreen
        if self.fullscreen:
            self._windowed_size = self.screen.get_size()
            self.screen = pygame.display.set_mode(
                (0, 0), pygame.FULLSCREEN | pygame.RESIZABLE)
        else:
            self.screen = pygame.display.set_mode(
                self._windowed_size, pygame.RESIZABLE)

    def _screenshot(self, action_name: str, frame_idx: int) -> None:
        """Save the current screen surface as PNG next to the .bin."""
        stem = self.cfg.pvr_name or "player"
        safe_action = "".join(ch if ch.isalnum() or ch in "._-" else "_"
                              for ch in action_name)
        path = os.path.join(
            self.cfg.output_dir,
            f"{stem}_{safe_action}_f{frame_idx:04d}.png",
        )
        try:
            pygame.image.save(self.screen, path)
            self._gif_msg     = f"Screenshot saved: {os.path.basename(path)}"
            self._gif_msg_ttl = 180
            log.info("Screenshot saved: %s", path)
        except Exception as exc:
            self._gif_msg     = f"Screenshot failed: {exc}"
            self._gif_msg_ttl = 180
            log.error("Screenshot failed: %s", exc)

    # ── Entry point ───────────────────────────────────────────────────────────

    def run(self) -> None:
        pygame.display.set_caption("Cocos Animation Player")
        flags = pygame.RESIZABLE | (pygame.FULLSCREEN if self.fullscreen else 0)
        size  = (0, 0) if self.fullscreen else (self.cfg.window_width, self.cfg.window_height)
        self.screen = pygame.display.set_mode(size, flags)
        self.clock    = pygame.time.Clock()
        self.font     = pygame.font.SysFont("Arial", self.cfg.hud_font_size)
        self.font_big = pygame.font.SysFont("Arial", self.cfg.hud_font_size + 4, bold=True)

        running = True
        while running:
            running = self._play_action()

        pygame.quit()

    # ── Action loop ───────────────────────────────────────────────────────────

    def _play_action(self) -> bool:
        action = self.playlist[self.current_idx]
        mc_idx = action['mc_idx']

        if not (0 <= mc_idx < len(self.movie_clips)):
            log.warning("Action '%s' invalid mc_idx %d - skipping.", action['name'], mc_idx)
            self.current_idx = (self.current_idx + 1) % len(self.playlist)
            return True

        mc         = self.movie_clips[mc_idx]
        last_frame = max(0, len(mc['frames']) - 1)
        action_start, action_end = self._clamp_action_range(action, last_frame)

        frame_rate = self._resolve_fps(action, mc)
        play_loop  = self.loop

        frame_dur   = 1000.0 / frame_rate
        frame_idx   = action_start
        timer       = 0.0
        anim_active = True
        self._step_frame_idx = None

        render_cap = max(self.cfg.fps_cap, frame_rate)

        log.info("Playing '%s'  [MC: %s]  frames %d-%d  @%dfps",
                 action['name'], mc['name'], action_start, action_end, frame_rate)

        while anim_active:
            dt = self.clock.tick(render_cap)
            if not self.paused:
                timer += dt

            quit_requested, anim_active = self._handle_events(
                anim_active, frame_idx, action_start, action_end)
            if quit_requested:
                return False

            # Apply frame step from N/B keys
            if self._step_frame_idx is not None:
                frame_idx = self._step_frame_idx
                self._step_frame_idx = None

            # Advance frame
            effective_dur = frame_dur / max(0.01, self.speed)
            if not self.paused and timer >= effective_dur:
                timer = 0.0
                frame_idx += 1
                if frame_idx > action_end:
                    if play_loop:
                        frame_idx = action_start
                    else:
                        anim_active = False

            frame_bounds = BoundingBox()
            self._render(mc_idx, frame_idx, frame_bounds,
                         action_start, action_end)

        return True

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _render(self, mc_idx: int, frame_idx: int, frame_bounds: BoundingBox,
                action_start: int, action_end: int) -> None:
        sw, sh = self.screen.get_size()
        base   = self._base_transform(sw, sh)
        self.screen.fill(self.cfg.background_rgb)
        self.renderer.draw(self.screen, mc_idx, frame_idx, base, frame_bounds)
        self._draw_hud(mc_idx, frame_idx, action_start, action_end)
        pygame.display.flip()

    # ── Playlist + frame range helpers ────────────────────────────────────────

    @staticmethod
    def _clamp_action_range(action: dict, last_frame: int):
        raw_start = action.get('start', 0)
        raw_end   = action.get('end',   last_frame)
        duration  = raw_end - raw_start
        is_global = (duration > last_frame) or (raw_start > 0 and duration >= last_frame)
        if is_global:
            return 0, min(duration, last_frame)
        cs = max(0, min(raw_start, last_frame))
        ce = max(0, min(raw_end,   last_frame))
        if ce <= cs:
            return 0, last_frame
        return cs, ce

    def _build_playlist(self, actions: list) -> list:
        if actions:
            valid = [a for a in actions if 0 <= a.get('mc_idx', -1) < len(self.movie_clips)]
            if valid:
                return valid
        log.info("No valid actions - building one entry per movie-clip.")
        return [
            {"name": mc['name'], "mc_idx": i,
             "start": 0, "end": max(0, len(mc['frames']) - 1), "p4": 0}
            for i, mc in enumerate(self.movie_clips) if mc['frames']
        ]
