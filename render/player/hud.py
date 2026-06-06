"""
HUD overlay + action picker drawing.
Lives on the Player class as a mixin; consumes self.screen, self.font, self.playlist etc.
"""

import pygame


class HudMixin:

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
        pause_tag = " PAUSED" if self.paused else ""

        # Top-left info block
        line1 = "{} [{}/{}]  {}".format(
            action['name'], self.current_idx + 1, n_act, mc['name'])
        _fps_mode_label = {'source': 'SRC', 'meta': 'META', 'custom': 'CUST'}
        _mode_tag = _fps_mode_label.get(self.fps_mode, self.fps_mode.upper())
        line2 = "Frame {}/{}  |  {}fps [{}]  |  Speed {}x  |  {}  |  Render {:.0f}fps{}".format(
            local, total - 1, anim_fps, _mode_tag,
            "{:.1f}".format(self.speed),
            loop_tag, clk_fps, pause_tag)
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

        # Bottom key hints
        hints = ("</>action  SPACE pause  N/B step  F jump  UP/DN speed  L loop  "
                 "R fps-mode  1=src 2=meta 3=custom 4=set-fps  M reload-meta  "
                 "I list  G gif  A allgifs  Z allgifs(nobg)  S sprites  T atlas  X xfl  J json  H hud")
        hsurf = self.font.render(hints, True, (150, 150, 150))
        hbg   = pygame.Surface((hsurf.get_width() + 16, hsurf.get_height() + 8),
                                pygame.SRCALPHA)
        hbg.fill((0, 0, 0, 140))
        hint_y = sh - hsurf.get_height() - 14
        self.screen.blit(hbg,  (6, hint_y))
        self.screen.blit(hsurf, (14, hint_y + 4))

        # Scrub bar (above hint bar)
        bar_h  = 6
        bar_y  = hint_y - bar_h - 5
        bar_x  = 6
        bar_w  = sw - 12
        prog   = (local / max(1, total - 1))
        pygame.draw.rect(self.screen, (55,  55,  55),  (bar_x, bar_y, bar_w, bar_h))
        pygame.draw.rect(self.screen, (70, 150, 255),  (bar_x, bar_y, int(bar_w * prog), bar_h))
        tick_x = bar_x + int(bar_w * prog)
        pygame.draw.rect(self.screen, (220, 220, 255), (tick_x - 1, bar_y - 3, 3, bar_h + 6))
        lbl_l = self.font.render(str(action_start), True, (120, 120, 120))
        lbl_r = self.font.render(str(action_end),   True, (120, 120, 120))
        self.screen.blit(lbl_l, (bar_x,            bar_y - lbl_l.get_height() - 2))
        self.screen.blit(lbl_r, (bar_x + bar_w - lbl_r.get_width(),
                                  bar_y - lbl_r.get_height() - 2))
        lbl_cur = self.font.render(str(local), True, (200, 220, 255))
        cx = max(bar_x, min(bar_x + bar_w - lbl_cur.get_width(),
                             tick_x - lbl_cur.get_width() // 2))
        self.screen.blit(lbl_cur, (cx, bar_y - lbl_cur.get_height() - 2))

        # Frame-number input overlay
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
