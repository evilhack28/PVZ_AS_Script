"""Parts picker: hide images (e.g. a baked-in costume) by group or one by one."""

import pygame

from .hud import _PAL

_THUMB = 22
_ROW_H = 30


def image_usage_groups(movie_clips: list, playlist: list) -> list:
    """Group images by which actions draw them.

    Returns [{'images': [idx...], 'actions': {playlist idx...}}], most widely
    used group first. A baked-in costume shows up as a group used by some actions
    but not all of them.
    """
    memo: dict = {}

    def imgs(mc: int, frame: int, depth: int = 0, seen: frozenset = frozenset()):
        key = (mc, frame)
        if key in memo:
            return memo[key]
        frames = movie_clips[mc]['frames']
        if not frames or depth > 32 or mc in seen:
            return frozenset()
        out = set()
        for el in frames[frame % len(frames)]:
            if el['id'] < 0:
                continue
            if el['is_mc']:
                if el['id'] < len(movie_clips):
                    out |= imgs(el['id'], el.get('frame_index', 0), depth + 1, seen | {mc})
            else:
                out.add(el['id'])
        memo[key] = frozenset(out)
        return memo[key]

    used_by: dict = {}
    for ai, act in enumerate(playlist):
        mc = act.get('mc_idx', -1)
        if not (0 <= mc < len(movie_clips)):
            continue
        frames = act.get('frames') or range(len(movie_clips[mc]['frames']))
        for img in set().union(*(imgs(mc, f) for f in set(frames))):
            used_by.setdefault(img, set()).add(ai)

    by_sig: dict = {}
    for img, acts in used_by.items():
        by_sig.setdefault(frozenset(acts), []).append(img)
    groups = [{'images': sorted(ids), 'actions': set(sig)} for sig, ids in by_sig.items()]
    groups.sort(key=lambda g: (-len(g['actions']), -len(g['images']), g['images'][0]))
    return groups


class PartsMixin:

    def _init_parts(self) -> None:
        self.part_groups: list = image_usage_groups(self.movie_clips, self.playlist)
        self.hidden_imgs: set = set()
        self.show_parts = False
        self.parts_sel = 0
        self.parts_open: set = set()
        self._thumbs: dict = {}

    def _parts_highlight(self) -> frozenset:
        """Images of the selected picker row, outlined on the canvas."""
        if not self.show_parts:
            return frozenset()
        rows = self._parts_rows()
        if not rows:
            return frozenset()
        row = rows[max(0, min(self.parts_sel, len(rows) - 1))]
        if row[0] == 'group':
            return frozenset(self.part_groups[row[1]]['images'])
        return frozenset((row[1],))

    # ── Rows ──────────────────────────────────────────────────────────────────

    def _parts_rows(self) -> list:
        rows = []
        for gi, g in enumerate(self.part_groups):
            rows.append(('group', gi))
            if gi in self.parts_open:
                rows.extend(('img', img, gi) for img in g['images'])
        return rows

    def _group_state(self, gi: int) -> int:
        """Number of hidden images in group `gi`."""
        return sum(1 for i in self.part_groups[gi]['images'] if i in self.hidden_imgs)

    def _group_label(self, gi: int) -> str:
        g = self.part_groups[gi]
        if len(g['actions']) == len(self.playlist):
            use = "all actions"
        else:
            use = ", ".join(self.playlist[a]['name'] for a in sorted(g['actions']))
        if len(use) > 24:
            use = use[:23] + "…"
        return f"{len(g['images'])} imgs · {use}"

    # ── Input ─────────────────────────────────────────────────────────────────

    def _parts_handle_key(self, key) -> None:
        rows = self._parts_rows()
        n = len(rows)
        if key in (pygame.K_ESCAPE, pygame.K_p):
            self.show_parts = False
            return
        if n == 0:
            return
        self.parts_sel = max(0, min(self.parts_sel, n - 1))
        row = rows[self.parts_sel]
        if key == pygame.K_UP:
            self.parts_sel = (self.parts_sel - 1) % n
        elif key == pygame.K_DOWN:
            self.parts_sel = (self.parts_sel + 1) % n
        elif key in (pygame.K_RETURN, pygame.K_SPACE):
            if row[0] == 'group':
                images = self.part_groups[row[1]]['images']
                if self._group_state(row[1]) == 0:
                    self.hidden_imgs.update(images)
                else:
                    self.hidden_imgs.difference_update(images)
            else:
                self.hidden_imgs ^= {row[1]}
            self._apply_filters()
        elif key == pygame.K_e:
            gi = row[1] if row[0] == 'group' else row[2]
            self.parts_open ^= {gi}
            self.parts_sel = self._parts_rows().index(('group', gi))
        elif key == pygame.K_a:
            self.hidden_imgs.clear()
            self._apply_filters()
        elif key == pygame.K_x:
            self.hidden_imgs = {i for g in self.part_groups for i in g['images']}
            self._apply_filters()

    # ── Drawing ───────────────────────────────────────────────────────────────

    def _part_thumb(self, idx: int):
        key = (idx, _THUMB)
        if key in self._thumbs:
            return self._thumbs[key]
        thumb = None
        im = self.images[idx]
        x, y, w, h = (int(im['tex_x']), int(im['tex_y']),
                      int(im['width']), int(im['height']))
        if (w > 0 and h > 0 and x >= 0 and y >= 0
                and x + w <= self.texture.get_width()
                and y + h <= self.texture.get_height()):
            sub = self.texture.subsurface((x, y, w, h))
            k = min(_THUMB / w, _THUMB / h, 2.0)
            thumb = pygame.transform.smoothscale(
                sub, (max(1, int(w * k)), max(1, int(h * k))))
        self._thumbs[key] = thumb
        return thumb

    def _draw_thumb(self, idx: int, x: int, y: int) -> int:
        """Draw a thumbnail in a _THUMB square at (x, y); returns the width used."""
        pygame.draw.rect(self.screen, (30, 34, 48), (x, y, _THUMB, _THUMB))
        t = self._part_thumb(idx)
        if t is not None:
            self.screen.blit(t, (x + (_THUMB - t.get_width()) // 2,
                                 y + (_THUMB - t.get_height()) // 2))
        return _THUMB + 3

    def _draw_parts_picker(self) -> None:
        rows = self._parts_rows()
        if not rows:
            return
        sw, sh = self.screen.get_size()
        top = 66
        max_rows = max(6, (sh - top - 60) // _ROW_H)
        visible = min(len(rows), max_rows)
        panel_w = min(sw - 12, 400)
        panel_h = visible * _ROW_H + 44
        panel_x = 6                       # left side, so the plant stays visible
        panel_y = top

        panel = pygame.Surface((panel_w, panel_h), pygame.SRCALPHA)
        panel.fill(_PAL["bg"])
        pygame.draw.rect(panel, _PAL["border"], (0, 0, panel_w, panel_h), 1)
        self.screen.blit(panel, (panel_x, panel_y))

        header = f"Parts ({len(self.part_groups)})  ENTER  E expand  A all  X none  P"
        self.screen.blit(self.font.render(header, True, _PAL["section"]),
                         (panel_x + 10, panel_y + 8))

        sel = max(0, min(self.parts_sel, len(rows) - 1))
        scroll = max(0, min(sel - visible // 2, len(rows) - visible))
        cur_action = self.current_idx
        y = panel_y + 36
        for ri in range(scroll, min(scroll + visible, len(rows))):
            row = rows[ri]
            if ri == sel:
                hl = pygame.Surface((panel_w - 4, _ROW_H), pygame.SRCALPHA)
                hl.fill((45, 95, 200, 190))
                self.screen.blit(hl, (panel_x + 2, y))
            x = panel_x + 10
            ty = y + (_ROW_H - _THUMB) // 2
            if row[0] == 'group':
                gi = row[1]
                g = self.part_groups[gi]
                hidden = self._group_state(gi)
                box = "[ ]" if hidden == len(g['images']) else ("[x]" if hidden == 0 else "[-]")
                in_use = cur_action in g['actions']
                col = (255, 255, 255) if ri == sel else (
                    _PAL["pill_text"] if in_use else _PAL["pill_dim"])
                bs = self.font.render(box, True, _PAL["good"] if hidden == 0 else _PAL["pill_dim"])
                self.screen.blit(bs, (x, y + (_ROW_H - bs.get_height()) // 2))
                x += bs.get_width() + 8
                for img in g['images'][:4]:
                    x += self._draw_thumb(img, x, ty)
                label = self._group_label(gi) + ("" if in_use else "  (other)")
                ls = self.font.render(label, True, col)
                self.screen.blit(ls, (x + 4, y + (_ROW_H - ls.get_height()) // 2))
            else:
                img = row[1]
                on = img not in self.hidden_imgs
                x += 22
                bs = self.font.render("[x]" if on else "[ ]", True,
                                      _PAL["good"] if on else _PAL["pill_dim"])
                self.screen.blit(bs, (x, y + (_ROW_H - bs.get_height()) // 2))
                x += bs.get_width() + 8
                x += self._draw_thumb(img, x, ty)
                im = self.images[img]
                col = (255, 255, 255) if ri == sel else (
                    _PAL["pill_text"] if on else _PAL["pill_dim"])
                ls = self.font.render(f"{img}  {im.get('name', '')}", True, col)
                self.screen.blit(ls, (x + 4, y + (_ROW_H - ls.get_height()) // 2))
            y += _ROW_H
