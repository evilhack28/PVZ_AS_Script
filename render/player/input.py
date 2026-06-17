"""
Keyboard / mouse / window event handling.  Mixin for Player.
"""

import pygame


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


class InputMixin:

    def _seek_from_mouse(self, mouse_x: int,
                        action_start: int, action_end: int) -> None:
        """Translate a mouse-x inside the scrub bar to a frame index."""
        rect = self._scrub_bar_rect
        if rect is None or rect.width <= 0:
            return
        prog = _clamp((mouse_x - rect.x) / rect.width, 0.0, 1.0)
        total = action_end - action_start
        if total <= 0:
            return
        target = action_start + int(round(prog * total))
        target = _clamp(target, action_start, action_end)
        self._step_frame_idx = target
        self.paused = True

    def _handle_events(self, anim_active: bool, frame_idx: int,
                       action_start: int, action_end: int):
        quit_requested = False
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                quit_requested = True
            elif event.type == pygame.VIDEORESIZE:
                # Honor fullscreen flag if we're in fullscreen mode
                flags = pygame.RESIZABLE | (pygame.FULLSCREEN if self.fullscreen else 0)
                self.screen = pygame.display.set_mode(event.size, flags)
            elif event.type == pygame.MOUSEWHEEL:
                # Wheel up → zoom in, wheel down → zoom out
                factor = 1.1 ** event.y
                self.zoom = _clamp(self.zoom * factor, 0.25, 8.0)
            elif event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 1:
                    # Left click: try scrub-bar hit-test
                    if (self._scrub_bar_rect is not None
                            and self._scrub_bar_rect.collidepoint(event.pos)):
                        self._scrub_dragging = True
                        self._seek_from_mouse(event.pos[0], action_start, action_end)
                elif event.button == 3:
                    # Right click: start drag-pan
                    self._pan_origin = (event.pos[0], event.pos[1],
                                        self.pan_x, self.pan_y)
            elif event.type == pygame.MOUSEBUTTONUP:
                if event.button == 1:
                    self._scrub_dragging = False
                elif event.button == 3:
                    self._pan_origin = None
            elif event.type == pygame.MOUSEMOTION:
                if self._pan_origin is not None:
                    ox, oy, px0, py0 = self._pan_origin
                    self.pan_x = px0 + (event.pos[0] - ox)
                    self.pan_y = py0 + (event.pos[1] - oy)
                elif self._scrub_dragging:
                    self._seek_from_mouse(event.pos[0], action_start, action_end)
            elif event.type == pygame.KEYDOWN:
                # `?` toggles the help overlay regardless of keyboard layout.
                # K_QUESTION is rarely emitted (most layouts produce K_SLASH +
                # KMOD_SHIFT), so route via event.unicode which always reflects
                # the produced character. Works even when an input prompt is
                # active so the user can always discover the close action.
                if event.unicode == '?':
                    if not (self._fps_input_active or self._frame_input_active):
                        self.show_help = not self.show_help
                        continue
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

        # Help overlay is modal: ESC or Q closes it (so does `?`, handled in
        # _handle_events). Other keys are swallowed.
        if self.show_help:
            if key in (pygame.K_ESCAPE, pygame.K_q):
                self.show_help = False
            return quit_req, anim_active

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
        elif key == pygame.K_k:
            # Toggle hiding the 'butter' sprite (covers the kungfu zombies' face)
            self.hide_butter = not self.hide_butter
            self.renderer.hidden_parts = (frozenset({'butter'})
                                          if self.hide_butter else frozenset())
            self._gif_msg = f"Butter: {'HIDDEN' if self.hide_butter else 'SHOWN'}"
            self._gif_msg_ttl = 120
        elif key == pygame.K_c:
            # Cycle costume modes: ALL -> NONE -> 1 -> 2 -> ... -> ALL.
            # Cycle only has 'all' when the model has no costume swap slots.
            if len(self.costume_cycle) <= 1:
                self._gif_msg = "No costumes on this model"
                self._gif_msg_ttl = 120
            else:
                self.costume_mode_idx = (self.costume_mode_idx + 1) % len(self.costume_cycle)
                self.costume_mode     = self.costume_cycle[self.costume_mode_idx]
                self._apply_costume()
                self._gif_msg = f"Costume: {self._costume_mode_label()}"
                self._gif_msg_ttl = 120
        elif key == pygame.K_1:
            self.fps_mode = 'source'
            self._gif_msg = "FPS mode: SOURCE";  self._gif_msg_ttl = 120
        elif key == pygame.K_2:
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
        elif key == pygame.K_j:
            self._dump_frames_json()
        elif key == pygame.K_h:
            self.show_hud = not self.show_hud
        elif key == pygame.K_0:
            self._reset_view()
            self._gif_msg     = "View reset"
            self._gif_msg_ttl = 90
        elif key == pygame.K_F11:
            self._toggle_fullscreen()
        elif key == pygame.K_PRINT:
            action_name = self.playlist[self.current_idx].get('name', 'action')
            self._screenshot(action_name, frame_idx)

        return quit_req, anim_active
