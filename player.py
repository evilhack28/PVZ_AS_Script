"""
player.py
---------
Animation player built on top of Renderer.

Controls
========
ESC / Q     – quit
LEFT/RIGHT  – previous / next action
UP/DOWN     – speed +0.1x / -0.1x
P           – pause / resume
N / B       – step one frame forward / back (auto-pauses)
L           – toggle loop
I           – open action picker (UP/DOWN navigate, ENTER select, ESC close)
G           – export current action to GIF immediately (no recording step needed)
H           – toggle minimal HUD
"""

import logging
import math
import os
from dataclasses import dataclass
from typing import Optional, List

import pygame

from renderer import Renderer, BoundingBox

log = logging.getLogger(__name__)

# Optional GIF export
try:
    from PIL import Image as PilImage
except ImportError:
    PilImage = None


# ── Config ────────────────────────────────────────────────────────────────────

# Valid fps_mode values:
#   'source'  – use the raw fps stored in the animation (FBIN) or default 24 (RawBin)
#   'meta'    – use the fps from the loaded metadata file (animaction.txt / --meta)
#   'custom'  – use fps_custom value regardless of source

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
    fps_mode:       str   = "meta"      # 'source' | 'meta' | 'custom'
    fps_custom:     int   = 30          # fps used when fps_mode == 'custom' 


# ── Player ────────────────────────────────────────────────────────────────────

class Player:
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

    # ── Meta helpers ──────────────────────────────────────────────────────────

    def _meta_for_action(self, action: dict):
        """
        Return an ActionConfig for this action.

        Priority order:
          1. External --meta TSV file (anim_meta)     ← most authoritative
          2. default_settings.py hardcoded table      ← built-in fallback
          3. None → renderer uses raw FBIN values
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
                pass  # default_settings.py not present — that's fine

        return None

    def _base_transform(self, screen_w: int, screen_h: int,
                        meta_cfg=None) -> tuple:
        """
        Build the root affine transform (a, b, c, d, tx, ty) that maps from
        Cocos Y-up space to pygame screen space.

        Without metadata this is the identity + Y-flip centred on screen:
            (1, 0, 0, -1, cx, cy)

        With an ActionConfig we additionally apply:
            • uniform scale  (from meta_cfg.scale)
            • X/Y world offset  (from meta_cfg.offset_x / offset_y)

        The offset in the game data represents how far from the animation's
        local origin the sprite should appear, in Cocos Y-up pixel units.
        We apply it AFTER the centre-translation so it shifts the whole
        animation relative to the screen centre.
        """
        cx = screen_w * 0.5
        cy = screen_h * 0.5

        if meta_cfg is None:
            ox, oy = self._rawbin_center_offset
            return (1.0, 0.0, 0.0, -1.0, cx - ox, cy - oy)

        s   = meta_cfg.scale if meta_cfg.scale > 0 else 1.0
        # offset_x/y represent the FOOT (ground contact) position within the
        # animation's bounding box in screen pixels at the given scale.
        # The animation's local origin (0,0) is NOT the foot — to put the foot
        # at screen centre we shift the origin by MINUS the foot offset:
        #   tx = cx - offset_x * s   (LEFT  so foot is horizontally centred)
        #   ty = cy + offset_y * s   (DOWN  so foot sits at screen centre)
        # ty uses + because after Y-flip (d=-1), positive offset_y = more pixels
        # from the top = further DOWN on screen = we push the origin up.
        tx  = cx - meta_cfg.offset_x * s
        ty  = cy + meta_cfg.offset_y * s

        # Horizontal flip: negate the X scale column
        sx = -s if meta_cfg.flip else s

        # Full affine: (a, b, c, d, tx, ty)
        #   a=sx, b=0, c=0, d=-s  (Y always flipped for screen), tx, ty
        return (sx, 0.0, 0.0, -s, tx, ty)

    def _probe_rawbin_center(self, mc_idx: int,
                             a_start: int, a_end: int) -> tuple:
        """
        Probe the union bounding box of a RawBin action to detect whether the
        animation content is far off-centre (e.g. particle files that store
        absolute game-world coordinates).  If the content centre deviates more
        than 150 px from the probe canvas centre, return the (dx, dy) shift
        needed to re-centre it; otherwise return (0, 0) (no correction).

        The returned offset is subtracted from cx/cy in _base_transform so the
        animation content lands at the middle of any canvas.
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

    def _resolve_fps(self, action: dict, mc=None) -> int:
        """
        Return the playback fps for *action* according to the current fps_mode.

        Modes
        -----
        'source'  → raw frame_rate from the parsed MC; if 0 (RawBin), falls
                    through to meta, then DEFAULT_FRAME_RATE
        'meta'    → fps from the loaded metadata file; falls back to source
        'custom'  → self.fps_custom (user-set value), no fallback

        The ultimate fallback is DEFAULT_FRAME_RATE from fbin_parser, so 24
        only appears when there is genuinely no other fps information anywhere.
        """
        from fbin_parser import DEFAULT_FRAME_RATE

        if self.fps_mode == 'custom':
            return max(1, self.fps_custom)

        # 'meta' mode: try metadata first
        if self.fps_mode == 'meta':
            meta_cfg = self._meta_for_action(action)
            if meta_cfg and meta_cfg.fps > 0:
                return meta_cfg.fps

        # 'source' / meta-fallback: use MC frame_rate if the binary stored one
        if mc is None:
            midx = action.get('mc_idx', -1)
            mc   = self.movie_clips[midx] if 0 <= midx < len(self.movie_clips) else None
        if mc and mc.get('frame_rate', 0) > 0:
            return mc['frame_rate']

        # MC stored 0 (RawBin) — try meta as a last resort even in source mode
        meta_cfg = self._meta_for_action(action)
        if meta_cfg and meta_cfg.fps > 0:
            return meta_cfg.fps

        # Absolute last resort: the parser default
        return DEFAULT_FRAME_RATE

    def _reload_meta(self) -> None:
        """Reload the meta file from disk without restarting the player."""
        if not self.meta_source:
            self._gif_msg     = "No meta file loaded — nothing to reload"
            self._gif_msg_ttl = 120
            return
        try:
            from anim_meta import AnimMeta
            fresh = AnimMeta.load(action_tsv=self.meta_source,
                                  particle_tsv=self.meta_source)
            if fresh.is_empty():
                self._gif_msg     = f"Reload failed — no valid table found in {self.meta_source}"
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

    # ── Entry point ───────────────────────────────────────────────────────────

    def run(self) -> None:
        pygame.display.set_caption("Cocos Animation Player")
        self.screen = pygame.display.set_mode(
            (self.cfg.window_width, self.cfg.window_height),
            pygame.RESIZABLE,
        )
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
            log.warning("Action '%s' invalid mc_idx %d – skipping.", action['name'], mc_idx)
            self.current_idx = (self.current_idx + 1) % len(self.playlist)
            return True

        mc         = self.movie_clips[mc_idx]
        last_frame = max(0, len(mc['frames']) - 1)
        action_start, action_end = self._clamp_action_range(action, last_frame)

        # ── Metadata overrides ────────────────────────────────────────────────
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
        frame_rate = self._resolve_fps(action, mc)

        # Frame range: meta wins if both sframe/eframe are non-zero
        if meta_cfg and (meta_cfg.start_frame > 0 or meta_cfg.end_frame > 0):
            meta_start = max(0, min(meta_cfg.start_frame, last_frame))
            meta_end   = max(0, min(meta_cfg.end_frame,   last_frame))
            if meta_end > meta_start:
                action_start, action_end = meta_start, meta_end

        # Loop flag: meta wins if meta is loaded
        if meta_cfg is not None:
            self.loop = meta_cfg.loop

        frame_dur   = 1000.0 / frame_rate
        frame_idx   = action_start
        timer       = 0.0
        anim_active = True
        self._step_frame_idx = None

        # The render clock must run at least as fast as the animation fps.
        # If fps_cap < frame_rate the clock throttles dt > frame_dur every
        # tick, capping playback at fps_cap regardless of the requested fps.
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
                    if self.loop:
                        frame_idx = action_start
                    else:
                        anim_active = False

            # Render — pass meta_cfg so base transform is correct
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

    # ── HUD ───────────────────────────────────────────────────────────────────

    def _draw_hud(self, mc_idx: int, frame_idx: int,
                  action_start: int, action_end: int,
                  meta_cfg=None) -> None:

        action  = self.playlist[self.current_idx]
        mc      = self.movie_clips[mc_idx]
        clk_fps = self.clock.get_fps()
        anim_fps = self._resolve_fps(action, mc)
        total   = action_end - action_start + 1
        local   = frame_idx - action_start
        n_act   = len(self.playlist)

        sw, sh = self.screen.get_size()

        # GIF export status message (temporary, centred at bottom)
        if self._gif_msg_ttl > 0:
            self._gif_msg_ttl -= 1
            surf = self.font_big.render(self._gif_msg, True, (80, 255, 100))
            rect = surf.get_rect(center=(sw // 2, sh - 36))
            bg = pygame.Surface((rect.width + 24, rect.height + 12), pygame.SRCALPHA)
            bg.fill((0, 0, 0, 180))
            self.screen.blit(bg, (rect.left - 12, rect.top - 6))
            self.screen.blit(surf, rect)

        if not self.show_hud:
            if self.show_list:
                self._draw_action_list()
            return

        loop_tag  = "LOOP" if self.loop else "ONCE"
        pause_tag = " ⏸ PAUSED" if self.paused else ""

        # ── Top-left info block ───────────────────────────────────────────────
        # Line 1: action name + index
        line1 = "{} [{}/{}]  {}".format(
            action['name'], self.current_idx + 1, n_act, mc['name'])
        # Line 2: frame counter + effective fps + mode + speed + loop + render fps
        _fps_mode_label = {'source': 'SRC', 'meta': 'META', 'custom': 'CUST'}
        _mode_tag = _fps_mode_label.get(self.fps_mode, self.fps_mode.upper())
        line2 = "Frame {}/{}  |  {}fps [{}]  |  Speed {}x  |  {}  |  Render {:.0f}fps{}".format(
            local, total - 1, anim_fps, _mode_tag,
            "{:.1f}".format(self.speed),
            loop_tag, clk_fps, pause_tag)
        # Line 3: meta or rawbin info
        if meta_cfg is not None:
            line3 = "Meta  scale={:.2f}  offset=({:.0f},{:.0f})  flip={}".format(
                meta_cfg.scale, meta_cfg.offset_x, meta_cfg.offset_y,
                'Y' if meta_cfg.flip else 'N')
        else:
            fmt = ' [RawBin]' if self.renderer.rawbin else ''
            line3 = "MC idx={}  frames={}  action {}-{}{}".format(
                mc_idx, len(mc['frames']), action_start, action_end, fmt)

        colours = [(240, 240, 240), (180, 210, 255), (160, 255, 160)]
        surfs   = [self.font.render(l, True, c) for l, c in zip(
                   [line1, line2, line3], colours)]

        pad = 8
        bw  = max(s.get_width() for s in surfs) + pad * 2
        bh  = sum(s.get_height() for s in surfs) + pad + 6 * len(surfs)
        bg  = pygame.Surface((bw, bh), pygame.SRCALPHA)
        bg.fill((0, 0, 0, 155))
        self.screen.blit(bg, (6, 6))
        y = 6 + pad // 2
        for s in surfs:
            self.screen.blit(s, (6 + pad, y))
            y += s.get_height() + 4

        # ── Bottom key hints ──────────────────────────────────────────────────
        hints = ("</>action  SPACE pause  N/B step  F jump  UP/DN speed  L loop  "
                 "R fps-mode  1=src 2=meta 3=custom 4=set-fps  M reload-meta  "
                 "I list  G gif  A allgifs  S sprites  T atlas  X xfl  J dump-json  H hud")
        hsurf = self.font.render(hints, True, (150, 150, 150))
        hbg   = pygame.Surface((hsurf.get_width() + 16, hsurf.get_height() + 8),
                                pygame.SRCALPHA)
        hbg.fill((0, 0, 0, 140))
        hint_y = sh - hsurf.get_height() - 14
        self.screen.blit(hbg,  (6, hint_y))
        self.screen.blit(hsurf, (14, hint_y + 4))

        # ── Scrub bar (above hint bar) ────────────────────────────────────────
        bar_h  = 6
        bar_y  = hint_y - bar_h - 5
        bar_x  = 6
        bar_w  = sw - 12
        prog   = (local / max(1, total - 1))
        pygame.draw.rect(self.screen, (55,  55,  55),  (bar_x, bar_y, bar_w, bar_h))
        pygame.draw.rect(self.screen, (70, 150, 255),  (bar_x, bar_y, int(bar_w * prog), bar_h))
        tick_x = bar_x + int(bar_w * prog)
        pygame.draw.rect(self.screen, (220, 220, 255), (tick_x - 1, bar_y - 3, 3, bar_h + 6))
        # Frame numbers at left and right ends
        lbl_l = self.font.render(str(action_start), True, (120, 120, 120))
        lbl_r = self.font.render(str(action_end),   True, (120, 120, 120))
        self.screen.blit(lbl_l, (bar_x,            bar_y - lbl_l.get_height() - 2))
        self.screen.blit(lbl_r, (bar_x + bar_w - lbl_r.get_width(),
                                  bar_y - lbl_r.get_height() - 2))
        # Current frame number above tick
        lbl_cur = self.font.render(str(local), True, (200, 220, 255))
        cx = max(bar_x, min(bar_x + bar_w - lbl_cur.get_width(),
                             tick_x - lbl_cur.get_width() // 2))
        self.screen.blit(lbl_cur, (cx, bar_y - lbl_cur.get_height() - 2))

        # ── Frame-number input overlay ────────────────────────────────────────
        if self._frame_input_active:
            prompt = "Go to frame: {}_".format(self._frame_input_buf)
            psurf  = self.font_big.render(prompt, True, (255, 230, 80))
            px     = sw // 2 - psurf.get_width() // 2
            py     = sh // 2 - psurf.get_height() // 2
            bg2    = pygame.Surface((psurf.get_width() + 24,
                                     psurf.get_height() + 16), pygame.SRCALPHA)
            bg2.fill((0, 0, 0, 210))
            self.screen.blit(bg2,  (px - 12, py - 8))
            self.screen.blit(psurf, (px,      py))

        if self._fps_input_active:
            prompt = "Custom FPS: {}_  (common: 24 30 60 120)".format(self._fps_input_buf)
            psurf  = self.font_big.render(prompt, True, (80, 255, 200))
            px     = sw // 2 - psurf.get_width() // 2
            py     = sh // 2 + 40
            bg2    = pygame.Surface((psurf.get_width() + 24,
                                     psurf.get_height() + 16), pygame.SRCALPHA)
            bg2.fill((0, 0, 0, 210))
            self.screen.blit(bg2,  (px - 12, py - 8))
            self.screen.blit(psurf, (px,      py))

        if self.show_list:
            self._draw_action_list()

    def _draw_action_list(self) -> None:
        """Compact action picker anchored to the top-right corner."""
        sw, sh  = self.screen.get_size()
        n       = len(self.playlist)
        row_h   = self.cfg.hud_font_size + 5

        max_rows = max(6, (sh - 90) // row_h)
        visible  = min(n, max_rows)

        sample_labels = []
        for a in self.playlist:
            mc_name = (self.movie_clips[a['mc_idx']]['name']
                       if 0 <= a['mc_idx'] < len(self.movie_clips) else "?")
            sample_labels.append(f"  000  {a['name']}  [{mc_name}]")
        max_label_w = max(
            (self.font.size(lbl)[0] for lbl in sample_labels),
            default=260,
        )
        header_text = f"Actions ({n})  UP/DOWN  ENTER  ESC"
        header_w    = self.font_big.size(header_text)[0]

        panel_w = min(sw - 12, max(max_label_w, header_w) + 28)
        panel_h = visible * row_h + 44
        panel_x = sw - panel_w - 6
        panel_y = 6

        panel = pygame.Surface((panel_w, panel_h), pygame.SRCALPHA)
        panel.fill((12, 14, 22, 235))
        pygame.draw.rect(panel, (70, 80, 130, 220), (0, 0, panel_w, panel_h), 1)
        self.screen.blit(panel, (panel_x, panel_y))

        hsurf = self.font_big.render(header_text, True, (200, 200, 100))
        self.screen.blit(hsurf, (panel_x + 10, panel_y + 8))

        half   = visible // 2
        scroll = max(0, min(self.list_selected - half, n - visible))

        y = panel_y + 36
        for i in range(scroll, min(scroll + visible, n)):
            act        = self.playlist[i]
            is_sel     = (i == self.list_selected)
            is_playing = (i == self.current_idx)
            mc_name    = (self.movie_clips[act['mc_idx']]['name']
                          if 0 <= act['mc_idx'] < len(self.movie_clips) else "?")

            if is_sel:
                hl = pygame.Surface((panel_w - 4, row_h), pygame.SRCALPHA)
                hl.fill((45, 95, 200, 190))
                self.screen.blit(hl, (panel_x + 2, y))

            if is_playing and is_sel:
                marker = "> ";  col_action = (255, 255, 255); col_mc = (180, 230, 255)
            elif is_playing:
                marker = "> ";  col_action = (255, 255, 110); col_mc = (200, 200, 80)
            elif is_sel:
                marker = "  ";  col_action = (255, 255, 255); col_mc = (180, 210, 255)
            else:
                marker = "  ";  col_action = (185, 185, 185); col_mc = (120, 140, 160)

            idx_surf = self.font.render(f"{marker}{i:3d}  {act['name']}", True, col_action)
            self.screen.blit(idx_surf, (panel_x + 10, y + 2))

            mc_surf = self.font.render(f"[{mc_name}]", True, col_mc)
            self.screen.blit(mc_surf, (panel_x + panel_w - mc_surf.get_width() - 10, y + 2))

            y += row_h

    # ── Event handling ────────────────────────────────────────────────────────

    def _handle_events(self, anim_active: bool, frame_idx: int,
                       action_start: int, action_end: int):
        quit_requested = False
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                quit_requested = True
            elif event.type == pygame.VIDEORESIZE:
                self.screen = pygame.display.set_mode(event.size, pygame.RESIZABLE)
            elif event.type == pygame.KEYDOWN:
                if self._fps_input_active:
                    if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                        if self._fps_input_buf:
                            try:
                                val = int(self._fps_input_buf)
                                if val > 0:
                                    self.fps_custom  = val
                                    self.fps_mode    = 'custom'
                                    self._gif_msg    = f"FPS mode: CUSTOM ({self.fps_custom})"
                                    self._gif_msg_ttl = 120
                            except ValueError:
                                pass
                        self._fps_input_active = False
                        self._fps_input_buf    = ""
                    elif event.key == pygame.K_ESCAPE:
                        self._fps_input_active = False
                        self._fps_input_buf    = ""
                    elif event.key == pygame.K_BACKSPACE:
                        self._fps_input_buf = self._fps_input_buf[:-1]
                    elif event.unicode.isdigit():
                        self._fps_input_buf += event.unicode
                    continue
                if self._frame_input_active:
                    if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                        if self._frame_input_buf:
                            try:
                                target = int(self._frame_input_buf)
                                target = max(action_start,
                                             min(action_end, action_start + target))
                                self._step_frame_idx = target
                                self.paused = True
                            except ValueError:
                                pass
                        self._frame_input_active = False
                        self._frame_input_buf    = ""
                    elif event.key == pygame.K_ESCAPE:
                        self._frame_input_active = False
                        self._frame_input_buf    = ""
                    elif event.key == pygame.K_BACKSPACE:
                        self._frame_input_buf = self._frame_input_buf[:-1]
                    elif event.unicode.isdigit():
                        self._frame_input_buf += event.unicode
                    continue
                quit_requested, anim_active = self._handle_key(
                    event.key, quit_requested, anim_active,
                    frame_idx, action_start, action_end)
        return quit_requested, anim_active

    def _handle_key(self, key, quit_req, anim_active,
                    frame_idx, action_start, action_end):

        if self.show_list:
            if key in (pygame.K_ESCAPE, pygame.K_i):
                self.show_list = False
            elif key == pygame.K_UP:
                self.list_selected = (self.list_selected - 1) % len(self.playlist)
            elif key == pygame.K_DOWN:
                self.list_selected = (self.list_selected + 1) % len(self.playlist)
            elif key == pygame.K_RETURN:
                self.current_idx   = self.list_selected
                self.show_list     = False
                self.paused        = False
                anim_active        = False
            elif key == pygame.K_l:
                self.loop = not self.loop
            return quit_req, anim_active

        if key in (pygame.K_ESCAPE, pygame.K_q):
            return True, False

        if key == pygame.K_i:
            self.show_list     = True
            self.list_selected = self.current_idx
            return quit_req, anim_active

        if key == pygame.K_RIGHT:
            self.current_idx = (self.current_idx + 1) % len(self.playlist)
            self.paused = False; anim_active = False
        elif key == pygame.K_LEFT:
            self.current_idx = (self.current_idx - 1 + len(self.playlist)) % len(self.playlist)
            self.paused = False; anim_active = False
        elif key == pygame.K_UP:
            self.speed = min(round(self.speed + 0.1, 2), 8.0)
        elif key == pygame.K_DOWN:
            self.speed = max(round(self.speed - 0.1, 2), 0.1)
        elif key == pygame.K_SPACE:
            self.paused = not self.paused
        elif key == pygame.K_n:
            self.paused = True
            self._step_frame_idx = min(action_end,   frame_idx + 1)
        elif key == pygame.K_b:
            self.paused = True
            self._step_frame_idx = max(action_start, frame_idx - 1)
        elif key == pygame.K_f:
            self._frame_input_active = True
            self._frame_input_buf    = ""
            self.paused = True
        elif key == pygame.K_l:
            self.loop = not self.loop
        elif key == pygame.K_r:
            # Cycle fps mode: source → meta → custom → source …
            modes = ['source', 'meta', 'custom']
            self.fps_mode = modes[(modes.index(self.fps_mode) + 1) % len(modes)]
            self._gif_msg = f"FPS mode: {self.fps_mode.upper()}"
            self._gif_msg_ttl = 120
        elif key == pygame.K_1:
            self.fps_mode = 'source'
            self._gif_msg = "FPS mode: SOURCE";  self._gif_msg_ttl = 120
        elif key == pygame.K_2:
            self.fps_mode = 'meta'
            self._gif_msg = "FPS mode: META";    self._gif_msg_ttl = 120
        elif key == pygame.K_3:
            self.fps_mode = 'custom'
            self._gif_msg = f"FPS mode: CUSTOM ({self.fps_custom})";  self._gif_msg_ttl = 120
        elif key == pygame.K_4:
            # Enter custom fps via keyboard (reuse frame-input machinery)
            self._fps_input_active = True
            self._fps_input_buf    = str(self.fps_custom)
            self._gif_msg = f"Custom FPS — type value, Enter to confirm"
            self._gif_msg_ttl = 999
        elif key == pygame.K_g:
            self._export_gif_now()
        elif key == pygame.K_a:
            self._export_all_gifs()
        elif key == pygame.K_s:
            self._export_sprites_now()
        elif key == pygame.K_t:
            self._export_atlas_now()
        elif key == pygame.K_x:
            self._export_xfl_now()
        elif key == pygame.K_j:
            self._dump_frames_json()
        elif key == pygame.K_h:
            self.show_hud = not self.show_hud
        elif key == pygame.K_m:
            self._reload_meta()

        return quit_req, anim_active

    # ── GIF export ────────────────────────────────────────────────────────────

    def _export_gif_now(self) -> None:
        if PilImage is None:
            print("GIF export requires Pillow:  pip install Pillow")
            return

        action = self.playlist[self.current_idx]
        mc_idx = action['mc_idx']
        if not (0 <= mc_idx < len(self.movie_clips)):
            return

        mc         = self.movie_clips[mc_idx]
        last_frame = max(0, len(mc['frames']) - 1)
        a_start, a_end = self._clamp_action_range(action, last_frame)

        meta_cfg   = self._meta_for_action(action)
        frame_rate = self._resolve_fps(action, mc)
        dur_ms     = max(1, int(1000 / frame_rate))
        print(f"Exporting '{action['name']}' ({a_end - a_start + 1} frames)...")
        frames_to_save = self._render_gif_frames(mc_idx, a_start, a_end, meta_cfg)
        if not frames_to_save:
            return

        out_dir  = self.cfg.pvr_name
        os.makedirs(out_dir, exist_ok=True)
        out_name = os.path.join(out_dir, f"{action['name']}.gif")
        try:
            self._save_gif_fast(frames_to_save, out_name, dur_ms)
            msg = f"Saved {out_name}  ({len(frames_to_save)} frames)"
            print(msg); log.info(msg)
            self._gif_msg = f"Saved  {out_name}";  self._gif_msg_ttl = 180
        except Exception as exc:
            log.error("GIF export failed: %s", exc)

    def _export_all_gifs(self) -> None:
        if PilImage is None:
            print("GIF export requires Pillow:  pip install Pillow")
            return

        total = len(self.playlist);  saved = 0;  failed = 0
        out_dir = self.cfg.pvr_name
        os.makedirs(out_dir, exist_ok=True)
        print(f"\nExporting all {total} actions as GIFs → {out_dir}/…")
        self._gif_msg = f"Exporting all {total} actions…";  self._gif_msg_ttl = 999999

        # Show first frame while exporting
        action = self.playlist[self.current_idx]
        mc_idx = action['mc_idx']
        if 0 <= mc_idx < len(self.movie_clips):
            mc         = self.movie_clips[mc_idx]
            last_frame = max(0, len(mc['frames']) - 1)
            a_start, _ = self._clamp_action_range(action, last_frame)
            fb = BoundingBox()
            self._render(mc_idx, a_start, fb, a_start, last_frame,
                         self._meta_for_action(action))

        for idx, act in enumerate(self.playlist):
            mc_idx = act['mc_idx']
            if not (0 <= mc_idx < len(self.movie_clips)):
                continue

            mc         = self.movie_clips[mc_idx]
            last_frame = max(0, len(mc['frames']) - 1)
            a_start, a_end = self._clamp_action_range(act, last_frame)
            n_frames   = a_end - a_start + 1
            meta_cfg   = self._meta_for_action(act)

            frame_rate = self._resolve_fps(act, mc)
            dur_ms     = max(1, int(1000 / frame_rate))

            self._gif_msg = f"Exporting {idx + 1}/{total}:  {act['name']}  ({n_frames} frames)"
            self._gif_msg_ttl = 999999
            fb = BoundingBox()
            self._render(mc_idx, a_start, fb, a_start, a_end, meta_cfg)
            pygame.event.pump()

            frames_to_save = self._render_gif_frames(mc_idx, a_start, a_end, meta_cfg)
            if not frames_to_save:
                continue

            out_name = os.path.join(out_dir, f"{act['name']}.gif")
            try:
                self._save_gif_fast(frames_to_save, out_name, dur_ms)
                print(f"  [{idx + 1}/{total}] Saved {out_name}  ({len(frames_to_save)} frames)")
                saved += 1
            except Exception as exc:
                print(f"  [{idx + 1}/{total}] FAILED {out_name}: {exc}")
                failed += 1

        msg = f"Done — {saved} GIFs saved" + (f", {failed} failed" if failed else "")
        print(msg); log.info(msg)
        self._gif_msg = msg;  self._gif_msg_ttl = 300

    # ── Shared GIF frame renderer ─────────────────────────────────────────────

    def _render_gif_frames(self, mc_idx: int, a_start: int, a_end: int,
                           meta_cfg=None) -> list:
        # ── Pass 1: dry-run to find the union bounding box ────────────────────
        # Render every frame on a large probe surface (bounds-tracking only) so
        # we know the tight crop region before allocating the real canvas.
        # This avoids the old approach of a 4096×4096 surface (≈50 MB/frame).
        PROBE = 4096
        cx, cy = PROBE // 2, PROBE // 2

        if meta_cfg is not None:
            base = self._base_transform(PROBE, PROBE, meta_cfg)
        else:
            base = (1.0, 0.0, 0.0, -1.0, float(cx), float(cy))

        probe_surf = pygame.Surface((PROBE, PROBE))
        union_box  = BoundingBox()

        for f in range(a_start, a_end + 1):
            fb = BoundingBox()
            self.renderer.draw(probe_surf, mc_idx, f, base, fb)
            if fb.valid:
                union_box.minx = min(union_box.minx, fb.minx)
                union_box.miny = min(union_box.miny, fb.miny)
                union_box.maxx = max(union_box.maxx, fb.maxx)
                union_box.maxy = max(union_box.maxy, fb.maxy)

        del probe_surf

        # ── Compute tight crop rect ───────────────────────────────────────────
        pad = 4
        if union_box.valid:
            bx0 = max(0,     int(union_box.minx) - pad)
            by0 = max(0,     int(union_box.miny) - pad)
            bx1 = min(PROBE, int(union_box.maxx) + pad)
            by1 = min(PROBE, int(union_box.maxy) + pad)
        else:
            bx0, by0, bx1, by1 = cx - 64, cy - 64, cx + 64, cy + 64

        crop_w = max(1, bx1 - bx0)
        crop_h = max(1, by1 - by0)

        # ── Pass 2: render onto a canvas exactly the crop size ─────────────────
        # Shift the base transform so the crop region starts at (0, 0).
        pa, pb, pc, pd, ptx, pty = base
        small_base = (pa, pb, pc, pd, ptx - bx0, pty - by0)

        canvas = pygame.Surface((crop_w, crop_h))
        frames: list = []

        for f in range(a_start, a_end + 1):
            canvas.fill(self.cfg.background_rgb)
            self.renderer.draw(canvas, mc_idx, f, small_base)
            raw = pygame.image.tostring(canvas, "RGB")
            frames.append(PilImage.frombytes("RGB", (crop_w, crop_h), raw))

        del canvas
        return frames

    # ── Atlas / sprite export ─────────────────────────────────────────────────

    def _export_atlas_now(self) -> None:
        if PilImage is None:
            print("Atlas export requires Pillow:  pip install Pillow")
            return
        tw = self.renderer.texture.get_width()
        th = self.renderer.texture.get_height()
        raw = pygame.image.tostring(self.renderer.texture, "RGBA")
        atlas = PilImage.frombytes("RGBA", (tw, th), raw)
        out_path = f"{self.cfg.pvr_name}.png"
        atlas.save(out_path, "PNG")
        msg = f"Saved atlas {out_path}  ({tw}x{th})"
        print(msg); log.info(msg)
        self._gif_msg = f"Saved  {out_path}";  self._gif_msg_ttl = 180

    def _export_sprites_now(self) -> None:
        if PilImage is None:
            print("Sprite export requires Pillow:  pip install Pillow")
            return
        out_dir = f"{self.cfg.pvr_name}_sprites"
        os.makedirs(out_dir, exist_ok=True)
        tw = self.renderer.texture.get_width()
        th = self.renderer.texture.get_height()
        raw_atlas = pygame.image.tostring(self.renderer.texture, "RGBA")
        atlas_pil = PilImage.frombytes("RGBA", (tw, th), raw_atlas)
        saved = 0;  skipped = 0;  skipped_dup = 0
        seen_rects: set = set()

        for img_def in self.images:
            tx = int(img_def['tex_x']);  ty = int(img_def['tex_y'])
            w  = int(img_def['width']);  h  = int(img_def['height'])

            # Skip invalid rects
            if w <= 0 or h <= 0 or tx < 0 or ty < 0 or tx + w > tw or ty + h > th:
                skipped += 1;  continue

            # Skip sprites that sit at (0,0) but are clearly not real atlas regions:
            # these are procedural/generated sprites whose tex coords are placeholder
            # zeroes. A real (0,0) sprite would need to be the ONLY image at that
            # exact origin — if many images share (0,0) it's a placeholder.
            # We detect this by checking if (0,0) appears suspiciously often and
            # skipping all (tx=0,ty=0) entries EXCEPT the one with the smallest area
            # (which is most likely genuinely in the top-left corner of the atlas).
            if tx == 0 and ty == 0:
                # Count how many images share this origin
                origin_count = sum(
                    1 for d in self.images
                    if int(d['tex_x']) == 0 and int(d['tex_y']) == 0
                    and int(d['width']) > 0 and int(d['height']) > 0
                )
                if origin_count > 3:
                    # Too many images at (0,0) — they're all procedural placeholders
                    skipped += 1;  continue

            # Skip duplicate rects — same region already exported under another name
            rect_key = (tx, ty, w, h)
            if rect_key in seen_rects:
                skipped_dup += 1;  continue
            seen_rects.add(rect_key)

            # Skip fully transparent sprites (nothing to export)
            sprite = atlas_pil.crop((tx, ty, tx + w, ty + h))
            if sprite.getbbox() is None:
                skipped += 1;  continue

            raw_name  = img_def.get('name', f'sprite_{saved:04d}')
            safe_name = raw_name.replace('/', '_').replace('\\', '_').replace(':', '_')
            sprite.save(os.path.join(out_dir, f"{safe_name}.png"), "PNG")
            saved += 1

        msg = (f"Exported {saved} sprites → {out_dir}"
               + (f"  ({skipped} invalid/placeholder skipped" +
                  (f", {skipped_dup} duplicates skipped" if skipped_dup else "") + ")"
                  if skipped or skipped_dup else ""))
        print(msg); log.info(msg)
        self._gif_msg = f"Exported {saved} sprites  →  {out_dir}";  self._gif_msg_ttl = 180

    # ── Frame dump (JSON export for debugging / XFL pipeline) ────────────────

    def _dump_frames_json(self) -> None:
        """
        Press J to dump every frame of every action to a JSON file.

        Each entry records exactly what the renderer draws — the same world
        matrix (a,b,c,d,tx,ty) that gets passed to _draw_image — so an
        external tool can replicate positions perfectly.

        Output: <pvr_name>_frames.json
        Schema:
        {
          "images": [ {name, tex_x, tex_y, width, height, offset_x, offset_y}, ... ],
          "actions": [
            {
              "name": "idle",
              "frames": [
                [
                  {
                    "img_idx": 5,
                    "img_name": "025_118x86",
                    "world_matrix": [a, b, c, d, tx, ty],  // world transform passed to _draw_image
                    "local_matrix": [a, b, c, d, tx, ty],  // element's own local matrix
                    "alpha": 1.0
                  }, ...
                ], ...  // one list per frame
              ]
            }, ...
          ]
        }

        world_matrix is the fully-concatenated parent × element matrix that the
        renderer passes as *matrix* to _draw_image.  It already includes the
        base_transform (scale + offset from meta/default_settings) and every
        ancestor MC's transform.  This is the ground truth for position.

        local_matrix is the raw element matrix from the binary — used by the
        renderer only for decomposing rotation/scale/flip of the sprite itself.
        """
        import json, math

        out_path = f"{self.cfg.pvr_name}_frames.json"
        print(f"Dumping frame data → {out_path} ...")
        self._gif_msg     = f"Dumping frames → {out_path} ..."
        self._gif_msg_ttl = 999999
        pygame.event.pump()

        # ── image table ──────────────────────────────────────────────────────
        images_out = []
        for img in self.images:
            images_out.append({
                "name":     img.get("name", ""),
                "tex_x":    img.get("tex_x", 0),
                "tex_y":    img.get("tex_y", 0),
                "width":    img.get("width", 0),
                "height":   img.get("height", 0),
                "offset_x": img.get("offset_x", 0.0),
                "offset_y": img.get("offset_y", 0.0),
            })

        # ── walk the MC tree exactly like the renderer ────────────────────────
        rawbin = self.renderer.rawbin

        def _collect(mc_idx, frame_num, parent_mat, depth=0, visited=None):
            """Mirror renderer.draw() but collect draw calls instead of blitting."""
            if depth > 32: return []
            if visited is None: visited = frozenset()
            if mc_idx in visited: return []
            visited = visited | {mc_idx}

            mc  = self.movie_clips[mc_idx]
            if not mc['frames']: return []

            idx      = frame_num % len(mc['frames'])
            elements = list(mc['frames'][idx])
            pa, pb, pc, pd, ptx, pty = parent_mat

            # ── dedup (mirror renderer) ───────────────────────────────────
            if rawbin:
                seen = {}
                for i, elem in enumerate(elements):
                    seen[elem.get('frame_index', -1)] = i
                elements = [elements[i] for i in sorted(seen.values())]
            else:
                from collections import Counter
                counts = Counter(e['id'] for e in elements if not e['is_mc'])
                if any(c > 1 for c in counts.values()):
                    seen_img = {}
                    for i, elem in enumerate(elements):
                        if not elem['is_mc']:
                            seen_img[elem['id']] = i
                    kept = set(seen_img.values())
                    elements = [e for i, e in enumerate(elements)
                                if e['is_mc'] or i in kept]

            results = []
            for elem in elements:
                eid = elem['id']
                if eid < 0: continue

                la, lb, lc, ld, ltx, lty = elem['matrix']
                na  = pa*la + pc*lb
                nb  = pb*la + pd*lb
                nc  = pa*lc + pc*ld
                nd  = pb*lc + pd*ld
                ntx = pa*ltx + pc*lty + ptx
                nty = pb*ltx + pd*lty + pty
                world = (na, nb, nc, nd, ntx, nty)

                if elem['is_mc']:
                    child_frame = elem.get('frame_index', -1)
                    if eid >= len(self.movie_clips): continue
                    child_mc = self.movie_clips[eid]

                    if rawbin and len(child_mc['frames']) == 1 and child_frame >= 0:
                        if child_frame < len(self.images):
                            # leaf image via rawbin redirect
                            img = self.images[child_frame]
                            if not (int(img['tex_x'])==0 and int(img['tex_y'])==0
                                    and int(img['width'])<=4 and int(img['height'])<=4):
                                results.append({
                                    "img_idx":     child_frame,
                                    "img_name":    img.get("name",""),
                                    "world_matrix": list(world),
                                    "local_matrix": list(elem['matrix']),
                                    "alpha":       float(elem.get("alpha", 1.0)),
                                })
                        elif child_frame < len(self.movie_clips):
                            results.extend(_collect(child_frame, frame_num,
                                                    world, depth+1, visited))
                    else:
                        nf = child_frame if child_frame >= 0 else frame_num
                        results.extend(_collect(eid, nf, world, depth+1, visited))
                else:
                    if eid < len(self.images):
                        img = self.images[eid]
                        if not (int(img['tex_x'])==0 and int(img['tex_y'])==0
                                and int(img['width'])<=4 and int(img['height'])<=4):
                            results.append({
                                "img_idx":     eid,
                                "img_name":    img.get("name",""),
                                "world_matrix": list(world),
                                "local_matrix": list(elem['matrix']),
                                "alpha":       float(elem.get("alpha", 1.0)),
                            })
            return results

        # ── iterate actions → frames ──────────────────────────────────────────
        actions_out = []
        # Use canvas size = 1 (positions are world-space, canvas doesn't matter)
        # Use identity base transform so positions are in raw animation space.
        # The XFL exporter applies its own base transform.
        identity = (1.0, 0.0, 0.0, -1.0, 0.0, 0.0)

        for action in self.playlist:
            mc_idx   = action['mc_idx']
            if not (0 <= mc_idx < len(self.movie_clips)):
                continue
            mc         = self.movie_clips[mc_idx]
            last_frame = max(0, len(mc['frames']) - 1)
            a_start, a_end = self._clamp_action_range(action, last_frame)

            meta_cfg = self._meta_for_action(action)
            # Use the same base transform the player uses for this action
            # (includes scale + offset from meta/default_settings)
            base = self._base_transform(0, 0, meta_cfg)
            # Override tx/ty to 0,0 — let XFL handle positioning itself
            # Keep scale/flip from meta so the matrices match what gets blitted
            a, b, c, d, tx, ty = base
            base_for_dump = (a, b, c, d, 0.0, 0.0)

            frames_out = []
            for f in range(a_start, a_end + 1):
                draws = _collect(mc_idx, f, base_for_dump)
                frames_out.append(draws)

            actions_out.append({
                "name":        action['name'],
                "mc_idx":      mc_idx,
                "frame_start": a_start,
                "frame_end":   a_end,
                "fps":         self._resolve_fps(action, mc),
                "meta": {
                    "scale":    meta_cfg.scale    if meta_cfg else 1.0,
                    "offset_x": meta_cfg.offset_x if meta_cfg else 0.0,
                    "offset_y": meta_cfg.offset_y if meta_cfg else 0.0,
                    "flip":     meta_cfg.flip      if meta_cfg else False,
                } if meta_cfg else None,
                "frames": frames_out,
            })

        output = {"images": images_out, "actions": actions_out}
        with open(out_path, 'w', encoding='utf-8') as fh:
            json.dump(output, fh, indent=2)

        msg = f"Dumped {len(actions_out)} actions → {out_path}"
        print(msg)
        self._gif_msg     = msg
        self._gif_msg_ttl = 300

    def _export_xfl_now(self) -> None:
        """Export the whole character as an Adobe Animate XFL project + .fla zip."""
        if PilImage is None:
            print("XFL export requires Pillow:  pip install Pillow")
            self._gif_msg = "XFL needs Pillow: pip install Pillow"
            self._gif_msg_ttl = 180
            return

        try:
            from xfl_exporter import export_xfl
        except ImportError:
            print("XFL export requires xfl_exporter.py in the same folder as player.py")
            self._gif_msg = "Missing xfl_exporter.py"
            self._gif_msg_ttl = 180
            return

        # ── Write a temporary atlas PNG from the current texture surface ──────
        tw  = self.renderer.texture.get_width()
        th  = self.renderer.texture.get_height()
        raw = pygame.image.tostring(self.renderer.texture, "RGBA")
        atlas_pil = PilImage.frombytes("RGBA", (tw, th), raw)
        tmp_atlas = f"_tmp_atlas_{self.cfg.pvr_name}.png"
        atlas_pil.save(tmp_atlas, "PNG")

        stem    = self.cfg.pvr_name
        out_dir = "."

        # Derive FPS from the first action's MC
        fps = 24
        if self.playlist:
            first_act = self.playlist[0]
            midx = first_act.get("mc_idx", -1)
            if 0 <= midx < len(self.movie_clips):
                fps = self.movie_clips[midx].get("frame_rate", 24) or 24

        print(f"Exporting XFL: {stem}.xfl  (fps={fps}) …")
        self._gif_msg     = f"Exporting XFL: {stem}.xfl …"
        self._gif_msg_ttl = 999999
        pygame.event.pump()

        try:
            xfl_path = export_xfl(
                images      = self.images,
                movie_clips = self.movie_clips,
                actions     = self.playlist,
                texture_png = tmp_atlas,
                out_dir     = out_dir,
                stem        = stem,
                fps         = fps,
                rawbin      = self.renderer.rawbin,
                anim_meta   = self.anim_meta,
                define_key  = self.define_key,
            )
            fla_path = xfl_path.replace(".xfl", ".fla")
            msg = f"XFL saved → {xfl_path}  (.fla: {fla_path})"
            print(msg); log.info(msg)
            self._gif_msg     = f"XFL → {stem}.fla"
            self._gif_msg_ttl = 300
        except Exception as exc:
            log.error("XFL export failed: %s", exc)
            print(f"XFL export failed: {exc}")
            self._gif_msg     = f"XFL FAILED: {exc}"
            self._gif_msg_ttl = 300
        finally:
            try:
                os.remove(tmp_atlas)
            except OSError:
                pass

    # ── Fast GIF save ─────────────────────────────────────────────────────────

    @staticmethod
    def _save_gif_fast(frames: list, path: str, duration_ms: int) -> None:
        """
        Save a GIF using a single global palette shared across all frames.

        Per-frame quantisation (default PIL behaviour) is O(W×H×256) per frame
        and causes two problems: it is very slow for long animations, and the
        palette changes every frame producing colour flickering.

        This helper builds one palette from up to 16 evenly-spaced sample
        frames then applies it to every frame with fast nearest-colour mapping.
        Typical speedup: 10–50× vs the default PIL save path.
        """
        if not frames:
            return

        # Build a representative sample (at most 16 frames, evenly spaced).
        n       = len(frames)
        step    = max(1, n // 16)
        samples = frames[::step][:16]

        # Combine samples into one wide image for a single quantisation pass.
        w, h     = frames[0].size
        combined = PilImage.new("RGB", (w * len(samples), h))
        for i, s in enumerate(samples):
            combined.paste(s, (i * w, 0))

        # Quantise → extract palette (256 colours, no dither for speed).
        quantised = combined.quantize(colors=256, dither=0)
        palette   = quantised.getpalette()

        # Build a palette-mode template we can reuse for every frame.
        pal_img = PilImage.new("P", (1, 1))
        pal_img.putpalette(palette)

        # Convert every frame using the global palette (no per-frame quantise).
        pal_frames = [f.quantize(palette=pal_img, dither=0) for f in frames]

        pal_frames[0].save(
            path, save_all=True,
            append_images=pal_frames[1:],
            duration=duration_ms, loop=0, optimize=False,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _make_transparent(img: 'PilImage.Image') -> 'PilImage.Image':
        img = img.convert("RGBA")
        w, h = img.size;  pixels = img.load()
        bg = (40, 40, 40);  tolerance = 15

        def is_bg(r, g, b):
            return (abs(r-bg[0]) <= tolerance and
                    abs(g-bg[1]) <= tolerance and
                    abs(b-bg[2]) <= tolerance)

        visited = [[False]*h for _ in range(w)]
        stack   = []
        for cx, cy in [(0,0),(w-1,0),(0,h-1),(w-1,h-1)]:
            r, g, b, a = pixels[cx, cy]
            if is_bg(r, g, b):
                stack.append((cx, cy))
        while stack:
            x, y = stack.pop()
            if x < 0 or x >= w or y < 0 or y >= h or visited[x][y]:
                continue
            r, g, b, a = pixels[x, y]
            if not is_bg(r, g, b):
                continue
            visited[x][y] = True
            pixels[x, y]  = (r, g, b, 0)
            stack.extend([(x+1,y),(x-1,y),(x,y+1),(x,y-1)])
        return img

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
        log.info("No valid actions – building one entry per movie-clip.")
        return [
            {"name": mc['name'], "mc_idx": i,
             "start": 0, "end": max(0, len(mc['frames']) - 1), "p4": 0}
            for i, mc in enumerate(self.movie_clips) if mc['frames']
        ]
