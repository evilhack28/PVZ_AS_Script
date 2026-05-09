"""
renderer.py
-----------
Stateful renderer for Cocos2d-x FBIN animations.

Changes from v2
===============
* hidden_parts: frozenset of image-name substrings.  Any image whose name
  contains one of these substrings is skipped during draw.  Set by Player
  before each action to suppress body parts referenced by the particle table
  (e.g. suppress the intact head sprites during a "particle_head" action).
"""

import math
import logging
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from typing import Optional

import pygame

log = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
MAX_CACHE_SIZE = 2048

# ── Per-sprite name overrides ─────────────────────────────────────────────────
_NAME_OVERRIDES = {
    'jaw':    dict(flip_y_override=None),
    'flag':   dict(flip_y_override=True),
    '31-031': dict(flip_y_override=True, size_guard=(96, 72)),
}


# ── Bounding box ─────────────────────────────────────────────────────────────

@dataclass
class BoundingBox:
    minx: float = math.inf
    miny: float = math.inf
    maxx: float = -math.inf
    maxy: float = -math.inf

    @property
    def valid(self) -> bool:
        return self.minx < math.inf

    def expand(self, rect: pygame.Rect) -> None:
        self.minx = min(self.minx, rect.left)
        self.miny = min(self.miny, rect.top)
        self.maxx = max(self.maxx, rect.right)
        self.maxy = max(self.maxy, rect.bottom)

    def to_pygame_rect(self, screen_w: int, screen_h: int) -> pygame.Rect:
        x0 = max(0, int(self.minx))
        y0 = max(0, int(self.miny))
        x1 = min(screen_w, int(self.maxx))
        y1 = min(screen_h, int(self.maxy))
        return pygame.Rect(x0, y0, x1 - x0, y1 - y0)


# ── Renderer ─────────────────────────────────────────────────────────────────

class Renderer:
    def __init__(self, images: list, movie_clips: list,
                 texture_surf: pygame.Surface,
                 rawbin: bool = False) -> None:
        self.images      = images
        self.movie_clips = movie_clips
        self.texture     = texture_surf
        self.rawbin      = rawbin
        self._cache: OrderedDict = OrderedDict()
        # Set by Player before each action to suppress named body parts
        self.hidden_parts: frozenset = frozenset()
        # Populated at the start of each draw() call (top-level only)
        self._plane_imgs: set = set()

    # ── Public draw call ──────────────────────────────────────────────────────

    def _scan_plane_images(self, mc_idx: int, frame_num: int,
                           visited: frozenset = None) -> set:
        """
        Recursively find all image indices that will be drawn via mc_id=0
        (ground_swatch_plane) elements in this MC tree frame.
        Used to suppress redundant mc_id=1 draws of the same image.
        """
        if visited is None: visited = frozenset()
        if mc_idx in visited: return set()
        visited = visited | {mc_idx}

        mc = self.movie_clips[mc_idx]
        if not mc['frames']: return set()
        idx      = frame_num % len(mc['frames'])
        elements = mc['frames'][idx]

        result = set()
        for elem in elements:
            eid = elem['id']
            cf  = elem.get('frame_index', -1)
            if eid < 0: continue
            if eid >= len(self.movie_clips): continue
            child_mc = self.movie_clips[eid]

            if eid == 0:  # ground_swatch_plane
                # Direct leaf draw via plane
                if len(child_mc['frames']) == 1 and 0 <= cf < len(self.images):
                    result.add(cf)
                elif len(child_mc['frames']) == 1 and len(self.images) <= cf < len(self.movie_clips):
                    result |= self._scan_plane_images(cf, frame_num, visited)
            else:
                # Recurse into composite MCs to find nested plane draws
                if len(child_mc['frames']) > 1 or cf < 0:
                    nf = cf if cf >= 0 else frame_num
                    result |= self._scan_plane_images(eid, nf, visited)
                elif len(child_mc['frames']) == 1 and len(self.images) <= cf < len(self.movie_clips):
                    result |= self._scan_plane_images(cf, frame_num, visited)
        return result

    def draw(self, surface: pygame.Surface,
             mc_idx: int, frame_num: int,
             transform_matrix: tuple,
             bounds: Optional[BoundingBox] = None,
             _depth: int = 0,
             _visited: Optional[frozenset] = None) -> None:
        if _depth > 32:
            return
        if mc_idx < 0 or mc_idx >= len(self.movie_clips):
            return

        if _visited is None:
            _visited = frozenset()
        if mc_idx in _visited:
            return
        _visited = _visited | {mc_idx}

        # At top level: find all img_idxs drawn via ground_swatch_plane (mc_id=0).
        # We suppress mc_id=1 draws for those same images to avoid duplicates
        # (e.g. zombie_skull draws the head via mc_id=0 AND the idle frame
        # has a stale direct mc_id=1 reference to the same head image).
        if _depth == 0 and self.rawbin:
            self._plane_imgs: set = self._scan_plane_images(mc_idx, frame_num)
        elif _depth == 0:
            self._plane_imgs = set()

        mc = self.movie_clips[mc_idx]
        if not mc['frames']:
            return

        idx      = frame_num % len(mc['frames'])
        elements = mc['frames'][idx]
        pa, pb, pc, pd, ptx, pty = transform_matrix

        # ── RawBin display-list deduplication ────────────────────────────────
        # In RawBin, frame_index is the actual sprite/pose selector.
        # The same frame_index CAN appear multiple times legitimately when
        # the same sprite is placed at DIFFERENT positions (e.g. left and
        # right pupils both reference the same MC at different coordinates).
        # We only suppress a placement when (frame_index, tx, ty) is truly
        # identical — a genuine duplicate at the exact same spot.
        if self.rawbin:
            seen: dict = {}
            for i, elem in enumerate(elements):
                tx_r = round(elem['matrix'][4], 1)
                ty_r = round(elem['matrix'][5], 1)
                key  = (elem.get('frame_index', -1), tx_r, ty_r)
                seen[key] = i              # last wins per (fi, tx, ty)
            elements = [elements[i] for i in sorted(seen.values())]

        # ── FBIN image display-list deduplication ─────────────────────────────
        # In FBIN, Flash sometimes exports stale keyframe placements: the same
        # image element (is_mc=False) appears exactly TWICE per frame — the
        # earlier one is the old keyframe state, the later one is correct.
        # Only dedup when count == 2 (last wins). Three or more copies of the
        # same image id are intentional multi-instance placement (e.g. repeated
        # vine thorns/nodes) and must all be rendered.
        else:
            img_id_counts: dict = {}
            for elem in elements:
                if not elem['is_mc']:
                    eid = elem['id']
                    img_id_counts[eid] = img_id_counts.get(eid, 0) + 1
            dedup_ids = {eid for eid, cnt in img_id_counts.items() if cnt == 2}
            if dedup_ids:
                last_pos: dict = {}
                for i, elem in enumerate(elements):
                    if not elem['is_mc'] and elem['id'] in dedup_ids:
                        last_pos[elem['id']] = i
                elements = [
                    elem for i, elem in enumerate(elements)
                    if elem['is_mc']
                    or elem['id'] not in dedup_ids
                    or i == last_pos[elem['id']]
                ]

        for elem in elements:
            eid = elem['id']
            if eid < 0:
                continue

            la, lb, lc, ld, ltx, lty = elem['matrix']
            na  = pa * la + pc * lb
            nb  = pb * la + pd * lb
            nc  = pa * lc + pc * ld
            nd  = pb * lc + pd * ld
            ntx = pa * ltx + pc * lty + ptx
            nty = pb * ltx + pd * lty + pty

            if elem['is_mc']:
                child_frame = elem.get('frame_index', -1)
                if eid >= len(self.movie_clips):
                    continue
                child_mc = self.movie_clips[eid]

                if self.rawbin:
                    if eid == 1 and child_frame >= 0:
                        # mc_id=1 is universally the body-part redirect MC.
                        # frame_index is the target MC index, not an image index.
                        if child_frame < len(self.movie_clips):
                            self.draw(surface, child_frame, frame_num,
                                      (na, nb, nc, nd, ntx, nty),
                                      bounds, _depth + 1, _visited)
                        elif child_frame < len(self.images):
                            self._draw_image(surface, child_frame, elem,
                                             (na, nb, nc, nd, ntx, nty), bounds)
                    elif child_frame >= 0 and child_frame < len(self.images):
                        # mc_id≠1 (eid=0 ground, eid=2 image-pointer, etc.):
                        # frame_index is a direct image index.
                        self._draw_image(surface, child_frame, elem,
                                         (na, nb, nc, nd, ntx, nty), bounds)
                    elif len(child_mc['frames']) == 1 and child_frame >= 0:
                        if child_frame < len(self.movie_clips):
                            self.draw(surface, child_frame, frame_num,
                                      (na, nb, nc, nd, ntx, nty),
                                      bounds, _depth + 1, _visited)
                    else:
                        next_frame = child_frame if child_frame >= 0 else frame_num
                        self.draw(surface, eid, next_frame,
                                  (na, nb, nc, nd, ntx, nty),
                                  bounds, _depth + 1, _visited)
                else:
                    next_frame = child_frame if child_frame >= 0 else frame_num
                    self.draw(surface, eid, next_frame,
                              (na, nb, nc, nd, ntx, nty),
                              bounds, _depth + 1, _visited)
            else:
                if eid < len(self.images):
                    self._draw_image(surface, eid, elem,
                                     (na, nb, nc, nd, ntx, nty), bounds)

    # ── Image drawing ─────────────────────────────────────────────────────────

    def _draw_image(self, surface: pygame.Surface,
                    img_idx: int, elem: dict,
                    matrix: tuple,
                    bounds: Optional[BoundingBox]) -> None:
        img_def   = self.images[img_idx]

        # ── RawBin plane-image suppression ───────────────────────────────────
        # If this image is drawn via ground_swatch_plane (mc_id=0) elsewhere
        # in the same frame, skip this mc_id=1 instance — it's a stale direct
        # reference that would produce a duplicate (e.g. two heads).
        if (self.rawbin
                and elem.get('id') == 1
                and hasattr(self, '_plane_imgs')
                and img_idx in self._plane_imgs):
            return

        # ── Hidden-parts filter ───────────────────────────────────────────────
        # If the game's particle table says to suppress this sprite name
        # (e.g. the intact head should be hidden during a particle_head action),
        # skip it entirely.
        if self.hidden_parts:
            img_name_lower = str(img_def.get('name', '')).lower()
            if any(part in img_name_lower for part in self.hidden_parts):
                return

        tx_i = int(img_def['tex_x'])
        ty_i = int(img_def['tex_y'])
        w_i  = int(img_def['width'])
        h_i  = int(img_def['height'])
        if w_i <= 0 or h_i <= 0:
            return
        # Skip Flash pivot/registration markers: tiny images at tex origin (0,0)
        # contain PVRTC block-corner garbage and are never meant to be visible.
        if tx_i == 0 and ty_i == 0 and w_i <= 4 and h_i <= 4:
            return
        src_rect = pygame.Rect(tx_i, ty_i, w_i, h_i)
        tw = self.texture.get_width()
        th = self.texture.get_height()
        if (src_rect.width <= 0 or src_rect.height <= 0
                or src_rect.x < 0 or src_rect.y < 0
                or src_rect.right > tw or src_rect.bottom > th):
            return

        try:
            sprite = self.texture.subsurface(src_rect)
        except ValueError:
            return

        la, lb, lc, ld, _ltx, _lty = elem['matrix']

        scale_x_l = math.sqrt(la * la + lb * lb)
        if scale_x_l == 0.0:
            return

        rotation_rad_local = math.atan2(lb, la)
        rotation_deg = -math.degrees(rotation_rad_local)

        det_local = la * ld - lb * lc
        scale_y_l = det_local / scale_x_l
        flip_y    = scale_y_l < 0
        if flip_y:
            scale_y_l = -scale_y_l

        scale_x = scale_x_l
        scale_y = scale_y_l

        na, nb, nc, nd, ntx, nty = matrix

        name_lower = str(img_def.get('name', '')).lower()
        for key, overrides in _NAME_OVERRIDES.items():
            if key not in name_lower:
                continue
            sg = overrides.get('size_guard')
            if sg and (int(img_def.get('width', 0)), int(img_def.get('height', 0))) != sg:
                continue
            if overrides.get('flip_y_override') is not None:
                flip_y = overrides['flip_y_override']
            break

        cache_key = (img_idx, round(scale_x, 4), round(scale_y, 4),
                     round(rotation_deg, 2), flip_y)
        xformed   = self._get_cached(sprite, cache_key, scale_x, scale_y,
                                     rotation_deg, flip_y,
                                     img_def['width'], img_def['height'])
        if xformed is None:
            return

        w_half = img_def['width']  * 0.5
        h_half = img_def['height'] * 0.5
        lcx    =  img_def['offset_x'] + w_half
        lcy    = -img_def['offset_y'] - h_half
        wcx = na * lcx + nc * lcy + ntx
        wcy = nb * lcx + nd * lcy + nty

        if not (math.isfinite(wcx) and math.isfinite(wcy)):
            return

        alpha_val = elem.get('alpha', 1.0)
        if alpha_val < 1.0:
            xformed = xformed.copy()
            xformed.set_alpha(int(alpha_val * 255))

        r_rect        = xformed.get_rect()
        r_rect.center = (int(wcx), int(wcy))
        surface.blit(xformed, r_rect)

        if bounds is not None:
            bounds.expand(r_rect)

    # ── Transform cache ───────────────────────────────────────────────────────

    def _get_cached(self, sprite: pygame.Surface, key: tuple,
                    scale_x: float, scale_y: float,
                    rotation_deg: float, flip_y: bool,
                    orig_w: float, orig_h: float) -> Optional[pygame.Surface]:
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]

        try:
            w = int(sprite.get_width()  * scale_x)
            h = int(sprite.get_height() * scale_y)
            if w <= 0 or h <= 0:
                return None
            scaled = pygame.transform.scale(sprite, (w, h))
            if flip_y:
                scaled = pygame.transform.flip(scaled, False, True)
            rotated = pygame.transform.rotate(scaled, -rotation_deg)
        except Exception as exc:
            log.debug("Transform failed for key %s: %s", key, exc)
            return None

        if len(self._cache) >= MAX_CACHE_SIZE:
            self._cache.popitem(last=False)
        self._cache[key] = rotated
        return rotated

    def clear_cache(self) -> None:
        self._cache.clear()
