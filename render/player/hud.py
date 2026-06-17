"""
HUD overlay + action picker drawing.
Lives on the Player class as a mixin; consumes self.screen, self.font, self.playlist etc.

Layout
------
Top-left      : row of status pills (action, frame, fps mode, speed, loop/pause).
Bottom-left   : scrub bar with frame labels; clickable (hit-tested in input.py
                via the `_scrub_bar_rect` field stored on the player).
Bottom-right  : "Press ? for keys" hint.
Centre        : help overlay (toggled with `?`) and the frame/fps input prompts.
"""

import pygame


# Flat palette — tweak here to retheme the HUD.
_PAL = {
    "bg":          (14, 16, 24, 215),
    "border":      (60, 70, 110, 200),
    "pill_bg":     (28, 32, 46, 230),
    "pill_text":   (235, 238, 245),
    "pill_dim":    (140, 150, 170),
    "accent":      (110, 175, 255),
    "accent_dim":  (60, 110, 175),
    "good":        (110, 220, 140),
    "warn":        (255, 200, 90),
    "pause":       (255, 150, 90),
    "scrub_bg":    (45, 50, 65),
    "scrub_fill":  (110, 175, 255),
    "scrub_tick":  (235, 238, 245),
    "hint":        (130, 138, 160),
    "overlay":     (0, 0, 0, 195),
    "section":     (255, 220, 110),
    "section_dim": (180, 200, 230),
}

_FPS_LABEL = {"source": "SRC", "custom": "CUST"}

# Help overlay content — grouped, two columns.
_HELP_SECTIONS = [
    ("Navigation", [
        ("← / →",          "Previous / next action"),
        ("I",              "Open action picker"),
    ]),
    ("Playback", [
        ("SPACE",          "Pause / resume"),
        ("N / B",          "Step one frame fwd / back"),
        ("F",              "Jump to frame (type, Enter)"),
        ("L",              "Toggle loop"),
        ("↑ / ↓",          "Speed ±0.1×"),
    ]),
    ("FPS", [
        ("1 / 2",          "Set fps mode src / custom"),
        ("4",              "Enter custom fps"),
    ]),
    ("View", [
        ("Mouse wheel",    "Zoom in / out"),
        ("Right drag",     "Pan canvas"),
        ("Left click bar", "Seek to frame"),
        ("0",              "Reset zoom & pan"),
        ("F11",            "Toggle fullscreen"),
        ("PrtScr",         "Save screenshot"),
        ("H",              "Toggle HUD"),
        ("?",              "Toggle this help"),
    ]),
    ("Filters", [
        ("K",              "Toggle 'butter' (kungfu head sprite)"),
        ("C",              "Cycle costume (all / none / 1 / 2 …)"),
    ]),
    ("Export", [
        ("G / A / Z",      "GIF (current / all / all no-bg)"),
        ("S / T",          "Sprites / atlas PNG"),
        ("J",              "JSON dump"),
    ]),
]


def _draw_pill(surface, font, label, value, pos, value_color=None, value_bold=False):
    """Render a "Label  VALUE" pill at `pos`. Returns the rect (for stacking)."""
    pad_x, pad_y = 9, 5
    gap = 6
    lbl_surf = font.render(label, True, _PAL["pill_dim"])
    val_font = font  # keep single font; bold is purely a colour cue here
    val_col  = value_color or _PAL["pill_text"]
    val_surf = val_font.render(value, True, val_col)

    w = lbl_surf.get_width() + gap + val_surf.get_width() + pad_x * 2
    h = max(lbl_surf.get_height(), val_surf.get_height()) + pad_y * 2
    rect = pygame.Rect(pos[0], pos[1], w, h)

    bg = pygame.Surface((w, h), pygame.SRCALPHA)
    bg.fill(_PAL["pill_bg"])
    surface.blit(bg, rect.topleft)
    pygame.draw.rect(surface, _PAL["border"], rect, 1)

    y = pos[1] + (h - lbl_surf.get_height()) // 2
    surface.blit(lbl_surf, (pos[0] + pad_x, y))
    y = pos[1] + (h - val_surf.get_height()) // 2
    surface.blit(val_surf, (pos[0] + pad_x + lbl_surf.get_width() + gap, y))
    return rect


def _draw_icon_pill(surface, font, text, pos, color):
    """Single-text pill (no label/value split) — used for LOOP / PAUSE markers."""
    pad_x, pad_y = 10, 5
    surf = font.render(text, True, color)
    w = surf.get_width() + pad_x * 2
    h = surf.get_height() + pad_y * 2
    rect = pygame.Rect(pos[0], pos[1], w, h)
    bg = pygame.Surface((w, h), pygame.SRCALPHA)
    bg.fill(_PAL["pill_bg"])
    surface.blit(bg, rect.topleft)
    pygame.draw.rect(surface, _PAL["border"], rect, 1)
    surface.blit(surf, (pos[0] + pad_x, pos[1] + pad_y))
    return rect


class HudMixin:

    def _draw_hud(self, mc_idx: int, frame_idx: int,
                  action_start: int, action_end: int) -> None:

        action  = self.playlist[self.current_idx]
        mc      = self.movie_clips[mc_idx]
        clk_fps = self.clock.get_fps()
        anim_fps = self._resolve_fps(action, mc)
        total   = action_end - action_start + 1
        local   = frame_idx - action_start
        n_act   = len(self.playlist)
        sw, sh = self.screen.get_size()

        # Temporary status message (screenshot saved, GIF exported, etc.)
        if self._gif_msg_ttl > 0:
            self._gif_msg_ttl -= 1
            surf = self.font_big.render(self._gif_msg, True, _PAL["good"])
            rect = surf.get_rect(center=(sw // 2, sh - 36))
            bg = pygame.Surface((rect.width + 24, rect.height + 12), pygame.SRCALPHA)
            bg.fill((0, 0, 0, 180))
            self.screen.blit(bg, (rect.left - 12, rect.top - 6))
            self.screen.blit(surf, rect)

        # Help overlay swallows the rest of the HUD when active.
        if self.show_help:
            self._draw_help_overlay()
            return

        if not self.show_hud:
            self._scrub_bar_rect = None
            if self.show_list:
                self._draw_action_list()
            return

        # ── Status pill row (top-left) ────────────────────────────────────────
        pill_x, pill_y = 8, 8
        gap = 6

        action_label = f"{action['name']}  [{self.current_idx + 1}/{n_act}]"
        r = _draw_pill(self.screen, self.font, "ACT", action_label,
                       (pill_x, pill_y), value_color=_PAL["accent"])
        pill_x = r.right + gap

        frame_label = f"{local}/{total - 1}"
        r = _draw_pill(self.screen, self.font, "FRAME", frame_label,
                       (pill_x, pill_y))
        pill_x = r.right + gap

        fps_label = f"{anim_fps} {_FPS_LABEL.get(self.fps_mode, self.fps_mode.upper())}"
        r = _draw_pill(self.screen, self.font, "FPS", fps_label,
                       (pill_x, pill_y), value_color=_PAL["good"])
        pill_x = r.right + gap

        r = _draw_pill(self.screen, self.font, "SPD", f"{self.speed:.1f}x",
                       (pill_x, pill_y))
        pill_x = r.right + gap

        loop_color = _PAL["good"] if self.loop else _PAL["pill_dim"]
        r = _draw_icon_pill(self.screen, self.font,
                            ("LOOP" if self.loop else "ONCE"),
                            (pill_x, pill_y), loop_color)
        pill_x = r.right + gap

        if self.paused:
            r = _draw_icon_pill(self.screen, self.font, "PAUSED",
                                (pill_x, pill_y), _PAL["pause"])
            pill_x = r.right + gap

        if getattr(self, "hide_butter", False):
            r = _draw_icon_pill(self.screen, self.font, "BUTTER OFF",
                                (pill_x, pill_y), _PAL["good"])
            pill_x = r.right + gap

        # Show a costume pill whenever the model HAS costume MCs, so the user
        # sees the feature exists. Default 'ALL' is dim; any other mode is
        # highlighted to make the active filter obvious.
        if getattr(self, "costume_all_mcs", set()):
            label    = f"CO {self._costume_mode_label()}"
            col      = _PAL["pill_dim"] if self.costume_mode == 'all' else _PAL["good"]
            r = _draw_icon_pill(self.screen, self.font, label,
                                (pill_x, pill_y), col)
            pill_x = r.right + gap

        # Second row: format badge + render fps (small, dim)
        meta_y = pill_y + r.height + 4
        fmt = "RawBin" if self.renderer.rawbin else "FBIN"
        meta_txt = (f"{fmt}  mc {mc_idx}  frames {len(mc['frames'])}  "
                    f"action {action_start}-{action_end}  "
                    f"render {clk_fps:.0f}fps")
        meta_surf = self.font.render(meta_txt, True, _PAL["pill_dim"])
        self.screen.blit(meta_surf, (10, meta_y))

        # ── Scrub bar (above hint) ────────────────────────────────────────────
        bar_h = 8
        bar_y = sh - 22
        bar_x = 8
        bar_w = sw - 16
        prog = (local / max(1, total - 1))
        pygame.draw.rect(self.screen, _PAL["scrub_bg"],
                         (bar_x, bar_y, bar_w, bar_h), border_radius=3)
        fill_w = max(1, int(bar_w * prog))
        pygame.draw.rect(self.screen, _PAL["scrub_fill"],
                         (bar_x, bar_y, fill_w, bar_h), border_radius=3)
        tick_x = bar_x + int(bar_w * prog)
        pygame.draw.rect(self.screen, _PAL["scrub_tick"],
                         (tick_x - 2, bar_y - 4, 4, bar_h + 8), border_radius=1)
        # Frame labels above the bar
        lbl_l = self.font.render(str(action_start), True, _PAL["hint"])
        lbl_r = self.font.render(str(action_end),   True, _PAL["hint"])
        self.screen.blit(lbl_l, (bar_x, bar_y - lbl_l.get_height() - 2))
        self.screen.blit(lbl_r, (bar_x + bar_w - lbl_r.get_width(),
                                  bar_y - lbl_r.get_height() - 2))
        lbl_cur = self.font.render(str(local), True, _PAL["accent"])
        cx = max(bar_x, min(bar_x + bar_w - lbl_cur.get_width(),
                            tick_x - lbl_cur.get_width() // 2))
        self.screen.blit(lbl_cur, (cx, bar_y - lbl_cur.get_height() - 2))

        # Expose the bar rect for the input handler's click-to-seek.
        # Bias the hit-test slightly above and below so the bar is easy to hit.
        self._scrub_bar_rect = pygame.Rect(bar_x, bar_y - 4, bar_w, bar_h + 8)

        # ── "Press ? for keys" hint (bottom-right) ────────────────────────────
        hint = self.font.render("Press ? for keys", True, _PAL["hint"])
        self.screen.blit(hint, (sw - hint.get_width() - 12,
                                bar_y - hint.get_height() - 6))

        # ── Frame-number input overlay ────────────────────────────────────────
        if self._frame_input_active:
            prompt = "Go to frame: {}_".format(self._frame_input_buf)
            psurf  = self.font_big.render(prompt, True, _PAL["warn"])
            px     = sw // 2 - psurf.get_width() // 2
            py     = sh // 2 - psurf.get_height() // 2
            bg2    = pygame.Surface((psurf.get_width() + 24,
                                     psurf.get_height() + 16), pygame.SRCALPHA)
            bg2.fill((0, 0, 0, 210))
            self.screen.blit(bg2,   (px - 12, py - 8))
            self.screen.blit(psurf, (px,      py))

        if self._fps_input_active:
            prompt = "Custom FPS: {}_  (common: 24 30 60 120)".format(self._fps_input_buf)
            psurf  = self.font_big.render(prompt, True, _PAL["good"])
            px     = sw // 2 - psurf.get_width() // 2
            py     = sh // 2 + 40
            bg2    = pygame.Surface((psurf.get_width() + 24,
                                     psurf.get_height() + 16), pygame.SRCALPHA)
            bg2.fill((0, 0, 0, 210))
            self.screen.blit(bg2,   (px - 12, py - 8))
            self.screen.blit(psurf, (px,      py))

        if self.show_list:
            self._draw_action_list()

    def _draw_help_overlay(self) -> None:
        """Full-screen darkened backdrop listing every key binding by section."""
        sw, sh = self.screen.get_size()
        overlay = pygame.Surface((sw, sh), pygame.SRCALPHA)
        overlay.fill(_PAL["overlay"])
        self.screen.blit(overlay, (0, 0))

        title = self.font_big.render("Keyboard & Mouse", True, _PAL["section"])
        self.screen.blit(title, (sw // 2 - title.get_width() // 2, 28))
        sub = self.font.render("Press ? or ESC to close", True, _PAL["section_dim"])
        self.screen.blit(sub, (sw // 2 - sub.get_width() // 2, 28 + title.get_height() + 4))

        # Two-column flow of sections
        col_gap = 40
        col_w   = (sw - col_gap * 3) // 2
        x0      = col_gap
        x1      = col_gap * 2 + col_w
        top_y   = 90
        y_left  = top_y
        y_right = top_y
        cur_x   = x0
        cur_y   = y_left
        sect_h  = self.font_big.get_height() + 6
        line_h  = self.font.get_height() + 4

        # Estimate section heights to balance the columns
        for idx, (section_name, items) in enumerate(_HELP_SECTIONS):
            block_h = sect_h + line_h * len(items) + 14
            # Place into shorter column
            if y_left <= y_right:
                cur_x, cur_y = x0, y_left
                y_left += block_h
            else:
                cur_x, cur_y = x1, y_right
                y_right += block_h

            sect_surf = self.font_big.render(section_name, True, _PAL["section"])
            self.screen.blit(sect_surf, (cur_x, cur_y))
            cur_y += sect_h
            for key, desc in items:
                key_surf  = self.font.render(key, True, _PAL["accent"])
                desc_surf = self.font.render(desc, True, _PAL["pill_text"])
                self.screen.blit(key_surf, (cur_x + 12, cur_y))
                self.screen.blit(desc_surf,
                                 (cur_x + 12 + 150, cur_y))
                cur_y += line_h

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
        panel.fill(_PAL["bg"])
        pygame.draw.rect(panel, _PAL["border"], (0, 0, panel_w, panel_h), 1)
        self.screen.blit(panel, (panel_x, panel_y))

        hsurf = self.font_big.render(header_text, True, _PAL["section"])
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
