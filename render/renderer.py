"""Stateful renderer for the game's animation bins (FBIN and RawBin)."""

import math
import logging
from collections import OrderedDict
from functools import lru_cache
from dataclasses import dataclass
from typing import Optional

import pygame

log = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
MAX_CACHE_SIZE = 4096

# Lawn-alignment placeholder MCs.
_GROUND_PLANE_NAMES = frozenset({'ground_swatch', 'ground_swatch_plane', '_ground'})

# Sprites sheared beyond this many degrees use the slower PIL affine path.
_SHEAR_AFFINE_DEG = 5.0

# pygame 2 honours set_alpha() on per-pixel-alpha surfaces, so no copy is needed.
_PYGAME2 = pygame.version.vernum[0] >= 2

# Game draw paths: batch ignores the blend flag; non-batch honours it (and squares leaf colour).
HONOUR_ADDITIVE_BLEND   = True
GAME_LEAF_COLOR_SQUARED = False

_ONE4  = (1.0, 1.0, 1.0, 1.0)
_ZERO4 = (0.0, 0.0, 0.0, 0.0)
_WHITE_B = (255, 255, 255, 255)
_ZERO_B  = (0, 0, 0, 0)


@lru_cache(maxsize=512)
def _lut_mult(m: int) -> bytes:
    return bytes(int(i * m / 255) for i in range(256))


@lru_cache(maxsize=512)
def _lut_add(a: int) -> bytes:
    return bytes(min(255, i + a) for i in range(256))


def _affine_shear_deg(na: float, nb: float, nc: float, nd: float) -> float:
    """Degrees the matrix's two axes deviate from orthogonal (0 = no shear)."""
    m0 = math.hypot(na, nb)
    m1 = math.hypot(nc, nd)
    if m0 < 1e-9 or m1 < 1e-9:
        return 0.0
    cos_ang = max(-1.0, min(1.0, (na * nc + nb * nd) / (m0 * m1)))
    return abs(math.degrees(math.acos(cos_ang)) - 90.0)


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
        # Lowercased substrings; any image whose name contains one of these is skipped during draw.
        self.hidden_parts: frozenset = frozenset()
        # MC id remap applied at element walk: `{src_mc_id: dst_mc_id_or_None}`.
        self.mc_remap: dict = {}

        # Base-transform state, refreshed by every top-level draw().
        self._base_inv_lin = (1.0, 0.0, 0.0, 1.0)
        self._base_scale   = (1.0, 1.0)
        # When a list, _draw_image appends draw records instead of blitting
        self._collector: Optional[list] = None
        # Skip lawn-alignment placeholder MCs (see _GROUND_PLANE_NAMES).
        self.skip_ground_swatch: bool = True
        # Lower-cased names / ground-plane flags, precomputed for the hot loop.
        self._mc_lname  = [str(m.get('name', '')).lower() for m in movie_clips]
        self._img_lname = [str(i.get('name', '')).lower() for i in images]
        self._ground_mcs = frozenset(i for i, n in enumerate(self._mc_lname)
                                     if n in _GROUND_PLANE_NAMES)

    # ── Public draw call ──────────────────────────────────────────────────────

    def collect_draws(self, mc_idx: int, frame_num: int,
                      transform_matrix: tuple) -> list:
        """Walk the tree exactly like draw() but return the image draws as records {img_idx, img_name, world_matrix"""
        self._collector = []
        try:
            self.draw(None, mc_idx, frame_num, transform_matrix)
            return self._collector
        finally:
            self._collector = None

    def draw(self, surface: pygame.Surface,
             mc_idx: int, frame_num: int,
             transform_matrix: tuple,
             bounds: Optional[BoundingBox] = None) -> None:
        """Draw MC `mc_idx` at MC frame `frame_num` under `transform_matrix` (a, b, c, d, tx, ty)."""
        if not (0 <= mc_idx < len(self.movie_clips)):
            return

        # Cache the inverse of the base transform's linear part
        ba, bb, bc, bd, _, _ = transform_matrix
        bdet = ba * bd - bc * bb
        if abs(bdet) > 1e-12:
            self._base_inv_lin = (bd / bdet, -bb / bdet,
                                  -bc / bdet,  ba / bdet)
        else:
            self._base_inv_lin = (1.0, 0.0, 0.0, 1.0)
        self._base_scale = (math.sqrt(ba * ba + bb * bb),
                            math.sqrt(bc * bc + bd * bd))

        self._visit(surface, mc_idx, frame_num, transform_matrix,
                    _ONE4, _ZERO4, False, 0, frozenset(), bounds)

    def _visit(self, surface, mc_idx: int, frame_num: int, matrix: tuple,
               mult: tuple, add: tuple, blend: bool, depth: int,
               visited: frozenset, bounds: Optional[BoundingBox]) -> None:
        """One MC frame."""
        if depth > 32 or mc_idx in visited:
            return
        frames = self.movie_clips[mc_idx]['frames']
        if not frames:
            return
        visited = visited | {mc_idx}
        elements = frames[frame_num % len(frames)]
        pa, pb, pc, pd, ptx, pty = matrix
        n_mc = len(self.movie_clips)
        n_img = len(self.images)

        for el in elements:
            eid = el['id']
            if eid < 0:
                continue

            la, lb, lc, ld, ltx, lty = el['matrix']
            m = (pa * la + pc * lb,
                 pb * la + pd * lb,
                 pa * lc + pc * ld,
                 pb * lc + pd * ld,
                 pa * ltx + pc * lty + ptx,
                 pb * ltx + pd * lty + pty)

            if el['is_mc']:
                # Costume remap: swap a base body-part MC for its variant (or hide it when mapped to None)
                if self.mc_remap and eid in self.mc_remap:
                    eid = self.mc_remap[eid]
                    if eid is None:
                        continue
                if eid >= n_mc:
                    continue
                if self.hidden_parts:
                    name = self._mc_lname[eid]
                    if any(part in name for part in self.hidden_parts):
                        continue
                if self.skip_ground_swatch and eid in self._ground_mcs:
                    continue
                cm = el.get('color_mult') or _WHITE_B
                ca = el.get('color_add') or _ZERO_B
                nmult = (mult[0] * cm[0] / 255.0, mult[1] * cm[1] / 255.0,
                         mult[2] * cm[2] / 255.0, mult[3] * cm[3] / 255.0)
                nadd  = (add[0] + ca[0] / 255.0, add[1] + ca[1] / 255.0,
                         add[2] + ca[2] / 255.0, add[3] + ca[3] / 255.0)
                self._visit(surface, eid, el.get('frame_index', 0), m, nmult, nadd,
                            el.get('blend', False), depth + 1, visited, bounds)
            elif eid < n_img:
                additive = (el.get('blend', False) if depth == 0 else blend)
                self._draw_image(surface, eid, el, m, mult, add,
                                 additive and HONOUR_ADDITIVE_BLEND, bounds)

    # ── Image drawing ─────────────────────────────────────────────────────────

    def _draw_image(self, surface: pygame.Surface,
                    img_idx: int, elem: dict,
                    matrix: tuple, mult: tuple, add: tuple,
                    additive: bool,
                    bounds: Optional[BoundingBox]) -> None:
        img_def = self.images[img_idx]

        # ── Hidden-parts filter (e.g. butter on the kungfu zombies' heads) ──
        if self.hidden_parts:
            img_name_lower = self._img_lname[img_idx]
            if any(p in img_name_lower for p in self.hidden_parts):
                return

        tx_i = int(img_def['tex_x'])
        ty_i = int(img_def['tex_y'])
        w_i  = int(img_def['width'])
        h_i  = int(img_def['height'])
        if w_i <= 0 or h_i <= 0:
            return
        # Skip Flash pivot/registration markers: tiny images at tex origin
        if tx_i == 0 and ty_i == 0 and w_i <= 4 and h_i <= 4:
            return

        # ── Colour: accumulated multiply/add (see GAME_LEAF_COLOR_SQUARED)
        cm = elem.get('color_mult') or _WHITE_B
        ca = elem.get('color_add') or _ZERO_B
        if GAME_LEAF_COLOR_SQUARED:
            mr, mg, mb, ma = (min(255, int(cm[i] * (mult[i] * cm[i] / 255.0)))
                              for i in range(4))
        else:
            mr, mg, mb, ma = (min(255, int(cm[i] * mult[i])) for i in range(4))
        add_rgb = tuple(min(255, int((add[i] + ca[i] / 255.0) * 255.0))
                        for i in range(3))
        alpha_val = ma / 255.0
        if self._collector is not None:
            self._collector.append({
                "img_idx":      img_idx,
                "img_name":     img_def.get("name", ""),
                "world_matrix": list(matrix),
                "local_matrix": list(elem['matrix']),
                "alpha":        alpha_val,
                "color_mult":   [mr, mg, mb],
                "color_add":    list(add_rgb),
                "additive":     bool(additive),
            })
            return
        if ma <= 0:
            return
        mult_rgb = (mr, mg, mb)

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

        na, nb, nc, nd, ntx, nty = matrix

        # ── Guarded affine path for sheared sprites ──────────────────────────
        if _affine_shear_deg(na, nb, nc, nd) > _SHEAR_AFFINE_DEG:
            if self._draw_image_affine(surface, sprite, img_def, matrix,
                                       mult_rgb, add_rgb, alpha_val,
                                       additive, bounds):
                return

        # Use the cumulative matrix minus the base transform to get the tree's scale/rotation.
        iba, ibb, ibc, ibd = self._base_inv_lin
        la = iba * na + ibc * nb
        lb = ibb * na + ibd * nb
        lc = iba * nc + ibc * nd
        ld = ibb * nc + ibd * nd

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

        # Re-apply the base scale (zoom x meta scale) stripped with the Y-flip.
        bsx, bsy = self._base_scale
        scale_x = scale_x_l * bsx
        scale_y = scale_y_l * bsy

        cache_key = (img_idx, round(scale_x, 4), round(scale_y, 4),
                     round(rotation_deg, 2), flip_y, mult_rgb, add_rgb)
        xformed   = self._get_cached(sprite, cache_key, scale_x, scale_y,
                                     rotation_deg, flip_y, mult_rgb, add_rgb)
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

        r_rect        = xformed.get_rect()
        r_rect.center = (int(wcx), int(wcy))
        self._blit(surface, xformed, r_rect, alpha_val, additive)

        if bounds is not None:
            bounds.expand(r_rect)

    def _blit(self, surface: pygame.Surface, surf: pygame.Surface, dest,
              alpha: float, additive: bool) -> None:
        """Blit `surf` with surface alpha `alpha` (0..1)."""
        if additive:
            # Alpha-weighted colour = the sprite composited over black.
            tmp = pygame.Surface(surf.get_size())
            tmp.fill((0, 0, 0))
            if alpha >= 1.0:
                tmp.blit(surf, (0, 0))
            elif _PYGAME2:
                surf.set_alpha(int(alpha * 255))
                tmp.blit(surf, (0, 0))
                surf.set_alpha(255)
            else:
                tmp.blit(self._faded_copy(surf, alpha), (0, 0))
            surface.blit(tmp, dest, special_flags=pygame.BLEND_RGB_ADD)
        elif alpha >= 1.0:
            surface.blit(surf, dest)
        elif _PYGAME2:
            # Blit the cached surface with a temporary surface alpha (no copy).
            surf.set_alpha(int(alpha * 255))
            surface.blit(surf, dest)
            surf.set_alpha(255)
        else:
            surface.blit(self._faded_copy(surf, alpha), dest)

    @staticmethod
    def _faded_copy(surf: pygame.Surface, alpha_val: float) -> pygame.Surface:
        """pygame 1.x fallback: per-pixel-alpha surfaces ignore set_alpha()."""
        out = surf.copy()
        if out.get_flags() & pygame.SRCALPHA:
            mod = pygame.Surface(out.get_size(), pygame.SRCALPHA)
            mod.fill((255, 255, 255, int(alpha_val * 255)))
            out.blit(mod, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
        else:
            out.set_alpha(int(alpha_val * 255))
        return out

    # ── Affine (shear) path ───────────────────────────────────────────────────

    def _draw_image_affine(self, surface: pygame.Surface,
                           sprite: pygame.Surface, img_def: dict,
                           matrix: tuple, mult_rgb: tuple, add_rgb: tuple,
                           alpha_val: float, additive: bool,
                           bounds: Optional[BoundingBox]) -> bool:
        """Warp `sprite` by the full cumulative matrix (incl."""
        try:
            from PIL import Image
        except Exception:
            return False

        na, nb, nc, nd, ntx, nty = matrix
        w = sprite.get_width()
        h = sprite.get_height()
        if w <= 0 or h <= 0:
            return False

        A0, A1, A2, A3 = na, -nc, nb, -nd          # pixel→screen linear map
        det = A0 * A3 - A1 * A2
        if abs(det) < 1e-9:
            return False

        w_half = img_def['width'] * 0.5
        h_half = img_def['height'] * 0.5
        lcx =  img_def['offset_x'] + w_half
        lcy = -img_def['offset_y'] - h_half
        wcx = na * lcx + nc * lcy + ntx
        wcy = nb * lcx + nd * lcy + nty
        if not (math.isfinite(wcx) and math.isfinite(wcy)):
            return False

        # Transformed corner offsets (relative to centre) → output bbox.
        xs, ys = [], []
        for qx, qy in ((-w / 2, -h / 2), (w / 2, -h / 2),
                       (w / 2, h / 2), (-w / 2, h / 2)):
            xs.append(A0 * qx + A1 * qy)
            ys.append(A2 * qx + A3 * qy)
        minx, miny = min(xs), min(ys)
        ow = int(math.ceil(max(xs) - minx))
        oh = int(math.ceil(max(ys) - miny))
        if ow <= 0 or oh <= 0 or ow > 4096 or oh > 4096:
            return False

        # Inverse map (output pixel → input texel) for PIL's AFFINE coeffs.
        iA0, iA1 = A3 / det, -A1 / det
        iA2, iA3 = -A2 / det, A0 / det
        a = iA0; b = iA1; c = iA0 * minx + iA1 * miny + w / 2
        d = iA2; e = iA3; f = iA2 * minx + iA3 * miny + h / 2

        try:
            pil = Image.frombytes('RGBA', (w, h),
                                  pygame.image.tostring(sprite, 'RGBA'))
            out = pil.transform((ow, oh), Image.AFFINE, (a, b, c, d, e, f),
                                resample=Image.BILINEAR)
            # Apply color transform on the warped result
            cm, ca = mult_rgb, add_rgb
            has_mult = (cm[0] != 255 or cm[1] != 255 or cm[2] != 255)
            has_add  = (ca[0] != 0 or ca[1] != 0 or ca[2] != 0)
            if has_mult or has_add:
                r, g, b_ch, a_ch = out.split()
                if has_mult:
                    r = r.point(_lut_mult(cm[0]))
                    g = g.point(_lut_mult(cm[1]))
                    b_ch = b_ch.point(_lut_mult(cm[2]))
                if has_add:
                    r = r.point(_lut_add(ca[0]))
                    g = g.point(_lut_add(ca[1]))
                    b_ch = b_ch.point(_lut_add(ca[2]))
                out = Image.merge('RGBA', (r, g, b_ch, a_ch))
            if alpha_val <= 0.0:
                return True
            if alpha_val < 1.0:
                bands = out.split()
                scaled = bands[3].point(
                    lambda v: int(v * max(0.0, min(1.0, alpha_val))))
                out = Image.merge('RGBA', (bands[0], bands[1], bands[2], scaled))
            warped = pygame.image.fromstring(out.tobytes(), (ow, oh), 'RGBA')
        except Exception as exc:
            log.debug("Affine warp failed for img %s: %s",
                      img_def.get('name'), exc)
            return False

        bx = int(round(wcx + minx))
        by = int(round(wcy + miny))
        self._blit(surface, warped, (bx, by), 1.0, additive)
        if bounds is not None:
            bounds.expand(pygame.Rect(bx, by, ow, oh))
        return True

    # ── Transform cache ───────────────────────────────────────────────────────

    def _get_cached(self, sprite: pygame.Surface, key: tuple,
                    scale_x: float, scale_y: float,
                    rotation_deg: float, flip_y: bool,
                    color_mult=None, color_add=None) -> Optional[pygame.Surface]:
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

        has_mult = (color_mult is not None and
                    (color_mult[0] != 255 or color_mult[1] != 255 or color_mult[2] != 255))
        has_add  = (color_add is not None and
                    (color_add[0] != 0 or color_add[1] != 0 or color_add[2] != 0))
        if has_mult or has_add:
            rotated = self._apply_color_transform(rotated, color_mult, color_add,
                                                  has_mult, has_add)

        if len(self._cache) >= MAX_CACHE_SIZE:
            self._cache.popitem(last=False)
        self._cache[key] = rotated
        return rotated

    def _apply_color_transform(self, surf: pygame.Surface,
                               color_mult, color_add,
                               has_mult: bool, has_add: bool) -> pygame.Surface:
        """Apply Flash ColorTransform RGB channels."""
        w, h = surf.get_size()
        raw = pygame.image.tostring(surf, 'RGBA')  # platform-independent RGBA

        # ── numpy fast path ──────────────────────────────────────────────────
        try:
            import numpy as np
            arr = np.frombuffer(raw, dtype=np.uint8).reshape(h, w, 4).copy()

            if has_mult:
                mr = color_mult[0]; mg = color_mult[1]; mb = color_mult[2]
                arr[:, :, 0] = np.clip(
                    arr[:, :, 0].astype(np.float32) * (mr / 255.0), 0, 255)
                arr[:, :, 1] = np.clip(
                    arr[:, :, 1].astype(np.float32) * (mg / 255.0), 0, 255)
                arr[:, :, 2] = np.clip(
                    arr[:, :, 2].astype(np.float32) * (mb / 255.0), 0, 255)

            if has_add:
                ar = color_add[0]; ag = color_add[1]; ab = color_add[2]
                arr[:, :, 0] = np.clip(arr[:, :, 0].astype(np.int16) + ar, 0, 255)
                arr[:, :, 1] = np.clip(arr[:, :, 1].astype(np.int16) + ag, 0, 255)
                arr[:, :, 2] = np.clip(arr[:, :, 2].astype(np.int16) + ab, 0, 255)

            out = pygame.image.frombuffer(arr, (w, h), 'RGBA')
            try:
                return out.convert_alpha()
            except pygame.error:          # no display (headless export)
                return out.copy()         # detach from arr's buffer

        except Exception:
            pass

        # ── PIL fallback ─────────────────────────────────────────────────────
        try:
            from PIL import Image as PILImage
            pil  = PILImage.frombytes('RGBA', (w, h), raw)
            r, g, b, a = pil.split()

            if has_mult:
                r = r.point(_lut_mult(color_mult[0]))
                g = g.point(_lut_mult(color_mult[1]))
                b = b.point(_lut_mult(color_mult[2]))

            if has_add:
                r = r.point(_lut_add(color_add[0]))
                g = g.point(_lut_add(color_add[1]))
                b = b.point(_lut_add(color_add[2]))

            out = PILImage.merge('RGBA', (r, g, b, a))
            out = pygame.image.fromstring(out.tobytes(), (w, h), 'RGBA')
            return out.convert_alpha()

        except Exception as exc:
            log.debug("Color transform failed: %s", exc)
            return surf
