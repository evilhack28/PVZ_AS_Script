"""Export methods (GIF / sprite / atlas / JSON)."""

import json
import logging
import os
import re

import pygame

from renderer import BoundingBox

log = logging.getLogger(__name__)

# Optional GIF export
try:
    from PIL import Image as PilImage
except ImportError:
    PilImage = None


_BAD_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_filename(name: str, fallback: str = "unnamed") -> str:
    """Make `name` safe as a single path component on every OS"""
    cleaned = _BAD_FILENAME_CHARS.sub("_", str(name)).strip(" .")
    return cleaned or fallback


def _unique_name(name: str, used: set) -> str:
    """`name`, or `name_2`, `name_3`..."""
    cand, n = name, 2
    while cand.lower() in used:
        cand = f"{name}_{n}"
        n += 1
    used.add(cand.lower())
    return cand


class ExportMixin:

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
        frame_seq  = self._action_frames(action)

        frame_rate = self._resolve_fps(action, mc)
        dur_ms     = max(1, int(1000 / frame_rate))
        print(f"Exporting '{action['name']}' ({len(frame_seq)} frames)...")
        frames_to_save = self._render_gif_frames(mc_idx, frame_seq)
        if not frames_to_save:
            return

        out_dir  = os.path.join(self.cfg.output_dir, self.cfg.pvr_name)
        os.makedirs(out_dir, exist_ok=True)
        out_name = os.path.join(
            out_dir, f"{self.cfg.pvr_name}_{_safe_filename(action['name'])}.gif")
        try:
            self._save_gif_fast(frames_to_save, out_name, dur_ms,
                                self.cfg.background_rgb)
            msg = f"Saved {out_name}  ({len(frames_to_save)} frames)"
            print(msg); log.info(msg)
            self._gif_msg = f"Saved  {out_name}";  self._gif_msg_ttl = 180
        except Exception as exc:
            log.error("GIF export failed: %s", exc)

    def _export_all_gifs(self) -> None:
        self._export_all_actions(transparent=False)

    def _export_all_gifs_nobg(self) -> None:
        self._export_all_actions(transparent=True)

    def _export_all_actions(self, transparent: bool) -> None:
        """Export every action as a GIF (opaque, or transparent `_nobg`)."""
        if PilImage is None:
            print("GIF export requires Pillow:  pip install Pillow")
            return

        kind    = "transparent GIFs" if transparent else "GIFs"
        suffix  = "_nobg" if transparent else ""
        total   = len(self.playlist);  saved = 0;  failed = 0
        out_dir = os.path.join(self.cfg.output_dir, self.cfg.pvr_name)
        os.makedirs(out_dir, exist_ok=True)
        print(f"\nExporting all {total} actions as {kind} -> {out_dir}/...")
        self._gif_msg = (f"Exporting all {total} actions"
                         + (" (no bg)..." if transparent else "..."))
        self._gif_msg_ttl = 999999

        # Show first frame while exporting
        action = self.playlist[self.current_idx]
        mc_idx = action['mc_idx']
        if 0 <= mc_idx < len(self.movie_clips):
            seq = self._action_frames(action)
            self._cur_frames = seq
            self._render(mc_idx, 0, BoundingBox(), 0, max(0, len(seq) - 1))

        used_names: set = set()
        for idx, act in enumerate(self.playlist):
            mc_idx = act['mc_idx']
            if not (0 <= mc_idx < len(self.movie_clips)):
                continue

            mc         = self.movie_clips[mc_idx]
            frame_seq  = self._action_frames(act)
            n_frames   = len(frame_seq)

            frame_rate = self._resolve_fps(act, mc)
            dur_ms     = max(1, int(1000 / frame_rate))

            self._gif_msg = f"Exporting {idx + 1}/{total}:  {act['name']}  ({n_frames} frames)"
            self._gif_msg_ttl = 999999
            self._cur_frames = frame_seq
            self._render(mc_idx, 0, BoundingBox(), 0, max(0, n_frames - 1))
            pygame.event.pump()

            frames_to_save = self._render_gif_frames(mc_idx, frame_seq,
                                                     transparent=transparent)
            if not frames_to_save:
                continue

            stem     = _unique_name(f"{self.cfg.pvr_name}_{_safe_filename(act['name'])}{suffix}",
                                    used_names)
            out_name = os.path.join(out_dir, f"{stem}.gif")
            try:
                self._save_gif_fast(frames_to_save, out_name, dur_ms,
                                    self.cfg.background_rgb)
                print(f"  [{idx + 1}/{total}] Saved {out_name}  ({len(frames_to_save)} frames)")
                saved += 1
            except Exception as exc:
                print(f"  [{idx + 1}/{total}] FAILED {out_name}: {exc}")
                failed += 1

        msg = (f"Done - {saved} {kind} saved"
               + (f", {failed} failed" if failed else ""))
        print(msg); log.info(msg)
        self._gif_msg = msg;  self._gif_msg_ttl = 300

    # ── WebP export (full alpha — fixes GIF's jagged anti-alias edges) ────────

    def _export_webp_now(self) -> None:
        """Export current action as animated WebP."""
        if PilImage is None:
            print("WebP export requires Pillow:  pip install Pillow")
            return

        action = self.playlist[self.current_idx]
        mc_idx = action['mc_idx']
        if not (0 <= mc_idx < len(self.movie_clips)):
            return

        mc         = self.movie_clips[mc_idx]
        frame_seq  = self._action_frames(action)

        frame_rate = self._resolve_fps(action, mc)
        dur_ms     = max(1, int(1000 / frame_rate))
        print(f"Exporting WebP '{action['name']}' "
              f"({len(frame_seq)} frames)...")
        frames = self._render_gif_frames(mc_idx, frame_seq, transparent=True)
        if not frames:
            return

        out_dir  = os.path.join(self.cfg.output_dir, self.cfg.pvr_name)
        os.makedirs(out_dir, exist_ok=True)
        out_name = os.path.join(
            out_dir, f"{self.cfg.pvr_name}_{_safe_filename(action['name'])}.webp")
        try:
            # lossless=True + quality=100 + method=6 → max-quality, max-effort.
            frames[0].save(
                out_name, save_all=True,
                append_images=frames[1:],
                duration=dur_ms, loop=0,
                lossless=True, quality=100, method=6,
            )
            msg = f"Saved {out_name}  ({len(frames)} frames)"
            print(msg); log.info(msg)
            self._gif_msg     = f"Saved  {out_name}"
            self._gif_msg_ttl = 180
        except Exception as exc:
            log.error("WebP export failed: %s", exc)
            self._gif_msg     = f"WebP failed: {exc}"
            self._gif_msg_ttl = 240

    # ── MP4 export (opaque, needs imageio + ffmpeg) ───────────────────────────

    def _export_mp4_now(self) -> None:
        """Export current action as H.264 MP4."""
        if PilImage is None:
            print("MP4 export requires Pillow:  pip install Pillow")
            return
        try:
            import imageio.v2 as iio
            import numpy as np
        except ImportError:
            msg = "MP4 needs:  pip install imageio imageio-ffmpeg numpy"
            print(msg)
            self._gif_msg     = msg
            self._gif_msg_ttl = 300
            return

        action = self.playlist[self.current_idx]
        mc_idx = action['mc_idx']
        if not (0 <= mc_idx < len(self.movie_clips)):
            return

        mc         = self.movie_clips[mc_idx]
        frame_seq  = self._action_frames(action)

        frame_rate = self._resolve_fps(action, mc)
        print(f"Exporting MP4 '{action['name']}' "
              f"({len(frame_seq)} frames)...")
        frames = self._render_gif_frames(mc_idx, frame_seq, transparent=False)
        if not frames:
            return

        # H.264 yuv420p needs even dimensions. Pad on the right/bottom with bg.
        w, h   = frames[0].size
        pad_w  = (w + 1) // 2 * 2
        pad_h  = (h + 1) // 2 * 2
        needs_pad = (pad_w, pad_h) != (w, h)

        out_dir  = os.path.join(self.cfg.output_dir, self.cfg.pvr_name)
        os.makedirs(out_dir, exist_ok=True)
        out_name = os.path.join(
            out_dir, f"{self.cfg.pvr_name}_{_safe_filename(action['name'])}.mp4")
        try:
            writer = iio.get_writer(
                out_name, fps=frame_rate, codec='libx264',
                pixelformat='yuv420p', quality=8, macro_block_size=2,
            )
            try:
                for f in frames:
                    if needs_pad:
                        bg = PilImage.new('RGB', (pad_w, pad_h),
                                          self.cfg.background_rgb)
                        bg.paste(f, (0, 0))
                        f = bg
                    writer.append_data(np.asarray(f))
            finally:
                writer.close()
            msg = f"Saved {out_name}  ({len(frames)} frames)"
            print(msg); log.info(msg)
            self._gif_msg     = f"Saved  {out_name}"
            self._gif_msg_ttl = 180
        except Exception as exc:
            log.error("MP4 export failed: %s", exc)
            self._gif_msg     = f"MP4 failed: {exc}"
            self._gif_msg_ttl = 240

    # ── Shared GIF frame renderer ─────────────────────────────────────────────

    def _render_gif_frames(self, mc_idx: int, frame_seq: list,
                           transparent: bool = False) -> list:
        """Render each MC frame in `frame_seq` (an action's frame list)."""
        # Pass 1: find the union bounding box to size the canvas.
        PROBE  = 2048
        TARGET = 1024         # target px for the longer axis of the output
        cx, cy = PROBE // 2, PROBE // 2

        # Pass 1: probe at z=1.0 to find the natural (unzoomed) bounding box.
        base_z1    = (1.0, 0.0, 0.0, -1.0, float(cx), float(cy))
        # The bbox is accumulated from each sprite's transformed rect, not from the pixels blitted
        probe_surf = pygame.Surface((1, 1))
        union_z1   = BoundingBox()

        for f in frame_seq:
            fb = BoundingBox()
            self.renderer.draw(probe_surf, mc_idx, f, base_z1, fb)
            if fb.valid:
                union_z1.minx = min(union_z1.minx, fb.minx)
                union_z1.miny = min(union_z1.miny, fb.miny)
                union_z1.maxx = max(union_z1.maxx, fb.maxx)
                union_z1.maxy = max(union_z1.maxy, fb.maxy)

        del probe_surf

        # Auto-zoom: scale so the longest axis of the union bbox fits TARGET px.
        if union_z1.valid:
            natural_w = max(1.0, union_z1.maxx - union_z1.minx)
            natural_h = max(1.0, union_z1.maxy - union_z1.miny)
            z = min(TARGET / max(natural_w, natural_h), 8.0)
        else:
            z = 1.0

        # Scale bbox linearly to the computed z (bbox scales around cx,cy).
        base = (z, 0.0, 0.0, -z, float(cx), float(cy))
        if union_z1.valid:
            union_box          = BoundingBox()
            union_box.minx     = cx + z * (union_z1.minx - cx)
            union_box.miny     = cy + z * (union_z1.miny - cy)
            union_box.maxx     = cx + z * (union_z1.maxx - cx)
            union_box.maxy     = cy + z * (union_z1.maxy - cy)
        else:
            union_box = union_z1

        # Compute tight crop rect
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

        # Pass 2: render onto a canvas exactly the crop size
        pa, pb, pc, pd, ptx, pty = base
        small_base = (pa, pb, pc, pd, ptx - bx0, pty - by0)

        if transparent:
            canvas = pygame.Surface((crop_w, crop_h), pygame.SRCALPHA)
        else:
            canvas = pygame.Surface((crop_w, crop_h))
        frames: list = []

        for f in frame_seq:
            if transparent:
                canvas.fill((0, 0, 0, 0))
                self.renderer.draw(canvas, mc_idx, f, small_base)
                raw = pygame.image.tostring(canvas, "RGBA")
                frames.append(PilImage.frombytes("RGBA", (crop_w, crop_h), raw))
            else:
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
        os.makedirs(self.cfg.output_dir, exist_ok=True)
        out_path = os.path.join(self.cfg.output_dir, f"{self.cfg.pvr_name}.png")
        atlas.save(out_path, "PNG")
        msg = f"Saved atlas {out_path}  ({tw}x{th})"
        print(msg); log.info(msg)
        self._gif_msg = f"Saved  {out_path}";  self._gif_msg_ttl = 180

    def _export_sprites_now(self) -> None:
        if PilImage is None:
            print("Sprite export requires Pillow:  pip install Pillow")
            return
        out_dir = os.path.join(self.cfg.output_dir, f"{self.cfg.pvr_name}_sprites")
        os.makedirs(out_dir, exist_ok=True)
        tw = self.renderer.texture.get_width()
        th = self.renderer.texture.get_height()
        raw_atlas = pygame.image.tostring(self.renderer.texture, "RGBA")
        atlas_pil = PilImage.frombytes("RGBA", (tw, th), raw_atlas)
        saved = 0;  skipped = 0;  skipped_dup = 0
        seen_rects: set = set()
        used_names: set = set()
        origin_count = sum(
            1 for d in self.images
            if int(d['tex_x']) == 0 and int(d['tex_y']) == 0
            and int(d['width']) > 0 and int(d['height']) > 0
        )

        for img_def in self.images:
            tx = int(img_def['tex_x']);  ty = int(img_def['tex_y'])
            w  = int(img_def['width']);  h  = int(img_def['height'])

            if w <= 0 or h <= 0 or tx < 0 or ty < 0 or tx + w > tw or ty + h > th:
                skipped += 1;  continue

            # Skip placeholder sprites at (0,0) when many images share that origin.
            if tx == 0 and ty == 0 and origin_count > 3:
                skipped += 1;  continue

            rect_key = (tx, ty, w, h)
            if rect_key in seen_rects:
                skipped_dup += 1;  continue
            seen_rects.add(rect_key)

            sprite = atlas_pil.crop((tx, ty, tx + w, ty + h))
            if sprite.getbbox() is None:
                skipped += 1;  continue

            raw_name  = img_def.get('name') or f'sprite_{saved:04d}'
            safe_name = _unique_name(_safe_filename(raw_name, f'sprite_{saved:04d}'),
                                     used_names)
            sprite.save(os.path.join(out_dir, f"{safe_name}.png"), "PNG")
            saved += 1

        msg = (f"Exported {saved} sprites -> {out_dir}"
               + (f"  ({skipped} invalid/placeholder skipped" +
                  (f", {skipped_dup} duplicates skipped" if skipped_dup else "") + ")"
                  if skipped or skipped_dup else ""))
        print(msg); log.info(msg)
        self._gif_msg = f"Exported {saved} sprites  ->  {out_dir}";  self._gif_msg_ttl = 180

    # ── Frame dump (JSON export for debugging / external pipelines) ───────────

    def _dump_frames_json(self) -> None:
        """Press J to dump every frame of every action to a JSON file."""
        os.makedirs(self.cfg.output_dir, exist_ok=True)
        out_path = os.path.join(self.cfg.output_dir, f"{self.cfg.pvr_name}_frames.json")
        print(f"Dumping frame data -> {out_path} ...")
        self._gif_msg     = f"Dumping frames -> {out_path} ..."
        self._gif_msg_ttl = 999999
        pygame.event.pump()

        # image table
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

        # iterate actions → frames
        actions_out = []

        for action in self.playlist:
            mc_idx   = action['mc_idx']
            if not (0 <= mc_idx < len(self.movie_clips)):
                continue
            mc         = self.movie_clips[mc_idx]
            frame_seq  = self._action_frames(action)

            base = self._base_transform(0, 0)
            # Use 0,0 origin (downstream tools handle positioning) but keep scale/flip
            a, b, c, d, _tx, _ty = base
            base_for_dump = (a, b, c, d, 0.0, 0.0)

            frames_out = []
            for f in frame_seq:
                draws = self.renderer.collect_draws(mc_idx, f, base_for_dump)
                frames_out.append(draws)

            actions_out.append({
                "name":        action['name'],
                "mc_idx":      mc_idx,
                "frame_start": 0,
                "frame_end":   len(frame_seq) - 1,
                "mc_frames":   list(frame_seq),
                "fps":         self._resolve_fps(action, mc),
                "frames":      frames_out,
            })

        output = {"images": images_out, "actions": actions_out}
        with open(out_path, 'w', encoding='utf-8') as fh:
            json.dump(output, fh, indent=2)

        msg = f"Dumped {len(actions_out)} actions -> {out_path}"
        print(msg)
        self._gif_msg     = msg
        self._gif_msg_ttl = 300

    # ── Fast GIF save ─────────────────────────────────────────────────────────

    @staticmethod
    def _save_gif_fast(frames: list, path: str, duration_ms: int,
                       background_rgb: tuple = (40, 40, 40)) -> None:
        """Save an animated GIF with a shared global palette (one quantise pass)."""
        if not frames:
            return

        has_alpha = (frames[0].mode == "RGBA")
        n         = len(frames)
        step      = max(1, n // 16)
        samples   = frames[::step][:16]
        w, h      = frames[0].size

        # MAXCOVERAGE spreads palette entries across the full colour space so rare-but-saturated colours
        try:
            _qmethod = PilImage.Quantize.MAXCOVERAGE
        except AttributeError:
            _qmethod = 1  # integer fallback for Pillow < 9.1

        if has_alpha:
            # Build palette from sample frames composited onto white
            combined = PilImage.new("RGB", (w * len(samples), h), (255, 255, 255))
            for i, s in enumerate(samples):
                bg = PilImage.new("RGB", (w, h), (255, 255, 255))
                bg.paste(s.convert("RGB"), mask=s.getchannel("A"))
                combined.paste(bg, (i * w, 0))

            # 255 colours - palette index 255 reserved for transparency.
            quantised = combined.quantize(colors=255, dither=0, method=_qmethod)
            palette   = list(quantised.getpalette())

            pal_img = PilImage.new("P", (1, 1))
            pal_img.putpalette(palette)

            TRANS = 255
            # Vectorise the alpha→transparent-index step.
            try:
                import numpy as np
                _have_np = True
            except ImportError:
                _have_np = False

            pal_frames = []
            for f in frames:
                alpha = f.getchannel("A")
                bg    = PilImage.new("RGB", (w, h), (255, 255, 255))
                bg.paste(f.convert("RGB"), mask=alpha)
                p = bg.quantize(palette=pal_img, dither=0)
                if _have_np:
                    p_arr = np.frombuffer(p.tobytes(), dtype=np.uint8).copy()
                    a_arr = np.frombuffer(alpha.tobytes(), dtype=np.uint8)
                    p_arr[a_arr < 128] = TRANS
                    result = PilImage.frombytes("P", (w, h), p_arr.tobytes())
                else:
                    # Map low-alpha pixels to the transparent index.
                    mask = bytes(255 if b < 128 else 0 for b in alpha.tobytes())
                    p_bytes = bytes(pb | mb for pb, mb in zip(p.tobytes(), mask))
                    result = PilImage.frombytes("P", (w, h), p_bytes)
                result.putpalette(palette)
                pal_frames.append(result)

            pal_frames[0].save(
                path, save_all=True,
                append_images=pal_frames[1:],
                duration=duration_ms, loop=0, optimize=False,
                transparency=TRANS, disposal=2,
            )
        else:
            combined = PilImage.new("RGB", (w * len(samples), h))
            for i, s in enumerate(samples):
                combined.paste(s, (i * w, 0))
            # 255 colours; index 0 holds the exact background colour.
            quantised = combined.quantize(colors=255, dither=0, method=_qmethod)
            raw_pal   = list(quantised.getpalette())[:255 * 3]
            palette   = list(background_rgb) + raw_pal     # index 0 = background
            palette  += [0] * max(0, 768 - len(palette))   # pad to 256 entries
            pal_img   = PilImage.new("P", (1, 1))
            pal_img.putpalette(palette)
            pal_frames = [f.quantize(palette=pal_img, dither=0) for f in frames]
            pal_frames[0].save(
                path, save_all=True,
                append_images=pal_frames[1:],
                duration=duration_ms, loop=0, optimize=False,
                disposal=2, background=0,
            )
