"""
Keyboard / window event handling.  Mixin for Player.
"""

import pygame


class InputMixin:

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
            # Cycle fps mode: source -> meta -> custom -> source ...
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
            self._fps_input_active = True
            self._fps_input_buf    = str(self.fps_custom)
            self._gif_msg = f"Custom FPS - type value, Enter to confirm"
            self._gif_msg_ttl = 999
        elif key == pygame.K_g:
            self._export_gif_now()
        elif key == pygame.K_a:
            self._export_all_gifs()
        elif key == pygame.K_z:
            self._export_all_gifs_nobg()
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
