"""
Player core: PlayerConfig, _PlayerCore (run loop, base transform, meta resolution).
"""

import logging
import math
import os
from dataclasses import dataclass
from typing import Optional, List

import pygame

from renderer import Renderer, BoundingBox
from fbin_parser import DEFAULT_FRAME_RATE

log = logging.getLogger(__name__)


# ── Config ────────────────────────────────────────────────────────────────────

# Valid fps_mode values:
#   'source'  - use the raw fps stored in the animation (FBIN) or default 24 (RawBin)
#   'meta'    - use the fps from the loaded metadata file (animaction.txt / --meta)
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
    output_dir:     str   = "."         # base directory for all exports (GIF/XFL/sprites/atlas/JSON)
    fps_mode:       str   = "meta"      # 'source' | 'meta' | 'custom'
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
                 anim_meta=None,       # AnimMeta instance or None
                 define_key: str = "",
                 meta_source: str = "") -> None:

        self.images      = images
        self.movie_clips = movie_clips
        self.texture     = texture_surf
        self.cfg         = config or PlayerConfig()
        self.anim_meta   = anim_meta
        self.define_key  = define_key
        self.meta_source = meta_source   # path to the loaded meta file, for hot-reload
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

        # RawBin auto-centering: cache per mc_idx of (dx, dy) shift
        self._rawbin_offsets: dict  = {}
        self._rawbin_center_offset: tuple = (0.0, 0.0)

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

    # ── Meta helpers ──────────────────────────────────────────────────────────

    def _meta_for_action(self, action: dict):
        """
        Return an ActionConfig for this action.

        Priority order:
          1. External --meta TSV file (anim_meta)     - most authoritative
          2. default_settings.py hardcoded table      - built-in fallback
          3. None - renderer uses raw FBIN values
        """
        action_name = action.get("name", "")

        # 1. TSV file
        if self.anim_meta is not None and self.define_key:
            cfg = self.anim_meta.action_config(self.define_key, action_name)
            if cfg is not None:
                return cfg

        # 2. default_settings fallback
        if self.define_key:
            try:
                from default_settings import get_action_config
                d = get_action_config(self.define_key, action_name)
                # Only return a synthetic ActionConfig if we actually have
                # a known entry (scale != 1 or offset != 0 are the tells)
                if (d["scale"] != 1.0 or d["offset_x"] != 0.0
                        or d["offset_y"] != 0.0 or d["fps"] != 0):
                    from anim_meta import ActionConfig
                    return ActionConfig(
                        define      = self.define_key,
                        action_name = action_name,
                        flip        = bool(d.get("flip", False)),
                        loop        = True,
                        offset_x    = d["offset_x"],
                        offset_y    = d["offset_y"],
                        scale       = d["scale"],
                        fps         = d["fps"],
                    )
            except ImportError:
                pass  # default_settings.py not present - that's fine

        return None

    def _base_transform(self, screen_w: int, screen_h: int,
                        meta_cfg=None) -> tuple:
        """
        Build the root affine transform (a, b, c, d, tx, ty) that maps from
        Cocos Y-up space to pygame screen space.

        Without metadata this is the identity + Y-flip centred on screen:
            (1, 0, 0, -1, cx, cy)

        With an ActionConfig we additionally apply:
            - uniform scale  (from meta_cfg.scale)
            - X/Y world offset  (from meta_cfg.offset_x / offset_y)
        """
        cx = screen_w * 0.5
        cy = screen_h * 0.5

        z = self.zoom
        px, py = self.pan_x, self.pan_y

        if meta_cfg is None:
            ox, oy = self._rawbin_center_offset
            return (z, 0.0, 0.0, -z, cx - ox + px, cy - oy + py)

        s   = meta_cfg.scale if meta_cfg.scale > 0 else 1.0
        # offset_x/y represent the FOOT (ground contact) position within the
        # animation's bounding box in screen pixels at the given scale.
        #   tx = cx - offset_x * s   (LEFT  so foot is horizontally centred)
        #   ty = cy + offset_y * s   (DOWN  so foot sits at screen centre)
        tx  = cx - meta_cfg.offset_x * s
        ty  = cy + meta_cfg.offset_y * s

        # Horizontal flip: negate the X scale column
        sx = -s if meta_cfg.flip else s

        return (sx * z, 0.0, 0.0, -s * z, tx + px, ty + py)

    def _probe_rawbin_center(self, mc_idx: int,
                             a_start: int, a_end: int) -> tuple:
        """
        Probe the union bounding box of a RawBin action to detect whether the
        animation content is far off-centre.  If the content centre deviates
        more than 150 px from the probe canvas centre, return the (dx, dy)
        shift needed to re-centre it; otherwise return (0, 0).
        """
        PROBE = 2048
        cx    = float(PROBE // 2)
        cy    = float(PROBE // 2)
        base  = (1.0, 0.0, 0.0, -1.0, cx, cy)

        probe_surf = pygame.Surface((PROBE, PROBE))
        union      = BoundingBox()

        for f in range(a_start, a_end + 1):
            fb = BoundingBox()
            self.renderer.draw(probe_surf, mc_idx, f, base, fb)
            if fb.valid:
                union.expand(pygame.Rect(
                    int(fb.minx), int(fb.miny),
                    max(1, int(fb.maxx - fb.minx)),
                    max(1, int(fb.maxy - fb.miny))))

        del probe_surf

        if union.valid:
            dx = (union.minx + union.maxx) / 2.0 - cx
            dy = (union.miny + union.maxy) / 2.0 - cy
            if abs(dx) > 150 or abs(dy) > 150:
                log.debug("RawBin auto-centre: shift (%.1f, %.1f) for mc_idx=%d",
                          dx, dy, mc_idx)
                return (dx, dy)
        return (0.0, 0.0)

    def _hidden_parts_for_action(self, action: dict) -> frozenset:
        """
        Return a frozenset of image-name substrings that should be suppressed
        for this action (used by the particle table for detached body parts).
        """
        if self.anim_meta is None or not self.define_key:
            return frozenset()
        action_name = action.get("name", "").lower()
        pcfg = self.anim_meta.particle_config(self.define_key, action_name)
        if pcfg is None:
            return frozenset()
        hidden = set()
        for part in (pcfg.hide_part1, pcfg.hide_part2):
            part = str(part).strip()
            if part and part != "0":
                hidden.add(part.lower())
        return frozenset(hidden)

    def _resolve_fps(self, action: dict, mc=None, meta_cfg=None) -> int:
        """
        Return the playback fps for *action* according to the current fps_mode.

        Per-mode order
        --------------
        'custom' → self.fps_custom
        'meta'   → meta file → MC `frame_rate` → DEFAULT_FRAME_RATE
        'source' → MC `frame_rate` → meta file → DEFAULT_FRAME_RATE

        `meta_cfg` may be supplied by the caller to skip the lookup; if
        omitted it is resolved on demand only when actually consulted.
        """
        if self.fps_mode == 'custom':
            return max(1, self.fps_custom)

        if mc is None:
            midx = action.get('mc_idx', -1)
            mc   = self.movie_clips[midx] if 0 <= midx < len(self.movie_clips) else None
        mc_fps = mc.get('frame_rate', 0) if mc else 0

        _meta_unset = object()
        meta = meta_cfg if meta_cfg is not None else _meta_unset

        def _meta_fps():
            nonlocal meta
            if meta is _meta_unset:
                meta = self._meta_for_action(action)
            return meta.fps if meta and meta.fps > 0 else 0

        if self.fps_mode == 'meta':
            fps = _meta_fps()
            if fps > 0:
                return fps
            if mc_fps > 0:
                return mc_fps
        else:  # 'source'
            if mc_fps > 0:
                return mc_fps
            fps = _meta_fps()
            if fps > 0:
                return fps

        return DEFAULT_FRAME_RATE

    def _reload_meta(self) -> None:
        """Reload the meta file from disk without restarting the player."""
        if not self.meta_source:
            self._gif_msg     = "No meta file loaded - nothing to reload"
            self._gif_msg_ttl = 120
            return
        try:
            from anim_meta import AnimMeta
            fresh = AnimMeta.load(action_tsv=self.meta_source,
                                  particle_tsv=self.meta_source)
            if fresh.is_empty():
                self._gif_msg     = f"Reload failed - no valid table found in {self.meta_source}"
                self._gif_msg_ttl = 180
                return
            self.anim_meta    = fresh
            self._gif_msg     = f"Meta reloaded  ({self.meta_source})"
            self._gif_msg_ttl = 180
            log.info("Meta reloaded: %s", self.meta_source)
        except Exception as exc:
            self._gif_msg     = f"Reload error: {exc}"
            self._gif_msg_ttl = 180
            log.error("Meta reload failed: %s", exc)

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

        # Metadata overrides — resolved once per action entry, reused below.
        meta_cfg     = self._meta_for_action(action)
        hidden_parts = self._hidden_parts_for_action(action)

        # RawBin auto-centering: probe action bounds once per mc_idx
        if self.renderer.rawbin and meta_cfg is None:
            if mc_idx not in self._rawbin_offsets:
                self._rawbin_offsets[mc_idx] = self._probe_rawbin_center(
                    mc_idx, action_start, action_end)
            self._rawbin_center_offset = self._rawbin_offsets[mc_idx]
        else:
            self._rawbin_center_offset = (0.0, 0.0)

        # FPS resolved by current fps_mode (source / meta / custom)
        frame_rate = self._resolve_fps(action, mc, meta_cfg)

        # Frame range: meta wins if both sframe/eframe are non-zero
        if meta_cfg and (meta_cfg.start_frame > 0 or meta_cfg.end_frame > 0):
            meta_start = max(0, min(meta_cfg.start_frame, last_frame))
            meta_end   = max(0, min(meta_cfg.end_frame,   last_frame))
            if meta_end > meta_start:
                action_start, action_end = meta_start, meta_end

        # Loop flag for this action: meta wins if loaded, otherwise the
        # user's L-key toggle (`self.loop`) is authoritative. We keep this
        # local so meta does NOT clobber the user toggle across actions.
        play_loop = meta_cfg.loop if meta_cfg is not None else self.loop

        frame_dur   = 1000.0 / frame_rate
        frame_idx   = action_start
        timer       = 0.0
        anim_active = True
        self._step_frame_idx = None

        # The render clock must run at least as fast as the animation fps.
        render_cap = max(self.cfg.fps_cap, frame_rate)

        # Push hidden_parts into renderer for this action
        self.renderer.hidden_parts = hidden_parts

        log.info("Playing '%s'  [MC: %s]  frames %d-%d  @%dfps  scale=%.2f  meta=%s",
                 action['name'], mc['name'], action_start, action_end, frame_rate,
                 meta_cfg.scale if meta_cfg else 1.0,
                 "yes" if meta_cfg else "no")

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

            # Render - pass meta_cfg so base transform is correct
            frame_bounds = BoundingBox()
            self._render(mc_idx, frame_idx, frame_bounds,
                         action_start, action_end, meta_cfg)

        return True

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _render(self, mc_idx: int, frame_idx: int, frame_bounds: BoundingBox,
                action_start: int, action_end: int,
                meta_cfg=None) -> None:
        sw, sh = self.screen.get_size()
        base   = self._base_transform(sw, sh, meta_cfg)
        self.screen.fill(self.cfg.background_rgb)
        self.renderer.draw(self.screen, mc_idx, frame_idx, base, frame_bounds)
        self._draw_hud(mc_idx, frame_idx, action_start, action_end, meta_cfg)
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
