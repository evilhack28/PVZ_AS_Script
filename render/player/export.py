"""
Export methods (GIF / sprite / atlas / JSON / XFL).  Mixin for Player.
"""

import logging
import os

import pygame

from renderer import BoundingBox

log = logging.getLogger(__name__)

# Optional GIF export
try:
    from PIL import Image as PilImage
except ImportError:
    PilImage = None


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
        last_frame = max(0, len(mc['frames']) - 1)
        a_start, a_end = self._clamp_action_range(action, last_frame)

        meta_cfg   = self._meta_for_action(action)
        frame_rate = self._resolve_fps(action, mc)
        dur_ms     = max(1, int(1000 / frame_rate))
        print(f"Exporting '{action['name']}' ({a_end - a_start + 1} frames)...")
        frames_to_save = self._render_gif_frames(mc_idx, a_start, a_end, meta_cfg)
        if not frames_to_save:
            return

        out_dir  = os.path.join(self.cfg.output_dir, self.cfg.pvr_name)
        os.makedirs(out_dir, exist_ok=True)
        out_name = os.path.join(out_dir, f"{self.cfg.pvr_name}_{action['name']}.gif")
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
        out_dir = os.path.join(self.cfg.output_dir, self.cfg.pvr_name)
        os.makedirs(out_dir, exist_ok=True)
        print(f"\nExporting all {total} actions as GIFs -> {out_dir}/...")
        self._gif_msg = f"Exporting all {total} actions...";  self._gif_msg_ttl = 999999

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

            out_name = os.path.join(out_dir, f"{self.cfg.pvr_name}_{act['name']}.gif")
            try:
                self._save_gif_fast(frames_to_save, out_name, dur_ms)
                print(f"  [{idx + 1}/{total}] Saved {out_name}  ({len(frames_to_save)} frames)")
                saved += 1
            except Exception as exc:
                print(f"  [{idx + 1}/{total}] FAILED {out_name}: {exc}")
                failed += 1

        msg = f"Done - {saved} GIFs saved" + (f", {failed} failed" if failed else "")
        print(msg); log.info(msg)
        self._gif_msg = msg;  self._gif_msg_ttl = 300

    def _export_all_gifs_nobg(self) -> None:
        if PilImage is None:
            print("GIF export requires Pillow:  pip install Pillow")
            return

        total = len(self.playlist);  saved = 0;  failed = 0
        out_dir = os.path.join(self.cfg.output_dir, self.cfg.pvr_name)
        os.makedirs(out_dir, exist_ok=True)
        print(f"\nExporting all {total} actions as transparent GIFs -> {out_dir}/...")
        self._gif_msg = f"Exporting all {total} actions (no bg)...";  self._gif_msg_ttl = 999999

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

            frames_to_save = self._render_gif_frames(mc_idx, a_start, a_end, meta_cfg,
                                                     transparent=True)
            if not frames_to_save:
                continue

            out_name = os.path.join(out_dir, f"{self.cfg.pvr_name}_{act['name']}_nobg.gif")
            try:
                self._save_gif_fast(frames_to_save, out_name, dur_ms)
                print(f"  [{idx + 1}/{total}] Saved {out_name}  ({len(frames_to_save)} frames)")
                saved += 1
            except Exception as exc:
                print(f"  [{idx + 1}/{total}] FAILED {out_name}: {exc}")
                failed += 1

        msg = f"Done - {saved} transparent GIFs saved" + (f", {failed} failed" if failed else "")
        print(msg); log.info(msg)
        self._gif_msg = msg;  self._gif_msg_ttl = 300

    # ── Shared GIF frame renderer ─────────────────────────────────────────────

    def _render_gif_frames(self, mc_idx: int, a_start: int, a_end: int,
                           meta_cfg=None, transparent: bool = False) -> list:
        # Pass 1: dry-run to find the union bounding box
        # so we know the tight crop region before allocating the real canvas.
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

        for f in range(a_start, a_end + 1):
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

        for img_def in self.images:
            tx = int(img_def['tex_x']);  ty = int(img_def['tex_y'])
            w  = int(img_def['width']);  h  = int(img_def['height'])

            if w <= 0 or h <= 0 or tx < 0 or ty < 0 or tx + w > tw or ty + h > th:
                skipped += 1;  continue

            # Skip placeholder sprites at (0,0) when many images share that origin.
            if tx == 0 and ty == 0:
                origin_count = sum(
                    1 for d in self.images
                    if int(d['tex_x']) == 0 and int(d['tex_y']) == 0
                    and int(d['width']) > 0 and int(d['height']) > 0
                )
                if origin_count > 3:
                    skipped += 1;  continue

            rect_key = (tx, ty, w, h)
            if rect_key in seen_rects:
                skipped_dup += 1;  continue
            seen_rects.add(rect_key)

            sprite = atlas_pil.crop((tx, ty, tx + w, ty + h))
            if sprite.getbbox() is None:
                skipped += 1;  continue

            raw_name  = img_def.get('name', f'sprite_{saved:04d}')
            safe_name = raw_name.replace('/', '_').replace('\\', '_').replace(':', '_')
            sprite.save(os.path.join(out_dir, f"{safe_name}.png"), "PNG")
            saved += 1

        msg = (f"Exported {saved} sprites -> {out_dir}"
               + (f"  ({skipped} invalid/placeholder skipped" +
                  (f", {skipped_dup} duplicates skipped" if skipped_dup else "") + ")"
                  if skipped or skipped_dup else ""))
        print(msg); log.info(msg)
        self._gif_msg = f"Exported {saved} sprites  ->  {out_dir}";  self._gif_msg_ttl = 180

    # ── Frame dump (JSON export for debugging / XFL pipeline) ─────────────────

    def _dump_frames_json(self) -> None:
        """
        Press J to dump every frame of every action to a JSON file.

        Each entry records exactly what the renderer draws - the same world
        matrix (a,b,c,d,tx,ty) that gets passed to _draw_image.

        Output: <pvr_name>_frames.json
        """
        import json

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

        # walk the MC tree exactly like the renderer
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

            # dedup (mirror renderer)
            if rawbin:
                seen = {}
                for i, elem in enumerate(elements):
                    tx_r = round(elem['matrix'][4], 1)
                    ty_r = round(elem['matrix'][5], 1)
                    key  = (elem.get('frame_index', -1), tx_r, ty_r)
                    seen[key] = i
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

                    if rawbin:
                        if eid == 1 and child_frame >= 0:
                            if child_frame < len(self.movie_clips):
                                results.extend(_collect(child_frame, frame_num,
                                                        world, depth+1, visited))
                            elif child_frame < len(self.images):
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
                        elif child_frame >= 0 and child_frame < len(self.images):
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
                        elif len(child_mc['frames']) == 1 and child_frame >= 0:
                            if child_frame < len(self.movie_clips):
                                results.extend(_collect(child_frame, frame_num,
                                                        world, depth+1, visited))
                        else:
                            nf = child_frame if child_frame >= 0 else frame_num
                            results.extend(_collect(eid, nf, world, depth+1, visited))
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

        # iterate actions → frames
        actions_out = []

        for action in self.playlist:
            mc_idx   = action['mc_idx']
            if not (0 <= mc_idx < len(self.movie_clips)):
                continue
            mc         = self.movie_clips[mc_idx]
            last_frame = max(0, len(mc['frames']) - 1)
            a_start, a_end = self._clamp_action_range(action, last_frame)

            meta_cfg = self._meta_for_action(action)
            base = self._base_transform(0, 0, meta_cfg)
            # Use 0,0 origin (XFL handles positioning itself) but keep scale/flip
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

        msg = f"Dumped {len(actions_out)} actions -> {out_path}"
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
            print("XFL export requires xfl_exporter to be importable")
            self._gif_msg = "Missing xfl_exporter"
            self._gif_msg_ttl = 180
            return

        # Write a temporary atlas PNG from the current texture surface
        tw  = self.renderer.texture.get_width()
        th  = self.renderer.texture.get_height()
        raw = pygame.image.tostring(self.renderer.texture, "RGBA")
        atlas_pil = PilImage.frombytes("RGBA", (tw, th), raw)
        os.makedirs(self.cfg.output_dir, exist_ok=True)
        tmp_atlas = os.path.join(self.cfg.output_dir, f"_tmp_atlas_{self.cfg.pvr_name}.png")
        atlas_pil.save(tmp_atlas, "PNG")

        stem    = self.cfg.pvr_name
        out_dir = self.cfg.output_dir

        # Derive FPS from the first action's MC
        fps = 24
        if self.playlist:
            first_act = self.playlist[0]
            midx = first_act.get("mc_idx", -1)
            if 0 <= midx < len(self.movie_clips):
                fps = self.movie_clips[midx].get("frame_rate", 24) or 24

        print(f"Exporting XFL: {stem}.xfl  (fps={fps}) ...")
        self._gif_msg     = f"Exporting XFL: {stem}.xfl ..."
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
            msg = f"XFL saved -> {xfl_path}  (.fla: {fla_path})"
            print(msg); log.info(msg)
            self._gif_msg     = f"XFL -> {stem}.fla"
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
        Save an animated GIF with a shared global palette (one quantise pass).

        RGBA frames produce a transparent GIF (index 255 = transparent).
        RGB  frames produce an opaque GIF.
        """
        if not frames:
            return

        has_alpha = (frames[0].mode == "RGBA")
        n         = len(frames)
        step      = max(1, n // 16)
        samples   = frames[::step][:16]
        w, h      = frames[0].size

        if has_alpha:
            # Build palette from sample frames composited onto white,
            # so transparent areas don't distort colour selection.
            combined = PilImage.new("RGB", (w * len(samples), h), (255, 255, 255))
            for i, s in enumerate(samples):
                bg = PilImage.new("RGB", (w, h), (255, 255, 255))
                bg.paste(s.convert("RGB"), mask=s.getchannel("A"))
                combined.paste(bg, (i * w, 0))

            # 255 colours - palette index 255 reserved for transparency.
            quantised = combined.quantize(colors=255, dither=0)
            palette   = list(quantised.getpalette())

            pal_img = PilImage.new("P", (1, 1))
            pal_img.putpalette(palette)

            TRANS = 255
            pal_frames = []
            for f in frames:
                alpha = f.getchannel("A")
                bg    = PilImage.new("RGB", (w, h), (255, 255, 255))
                bg.paste(f.convert("RGB"), mask=alpha)
                p = bg.quantize(palette=pal_img, dither=0)
                p_bytes = bytearray(p.tobytes())
                a_bytes = alpha.tobytes()
                for k in range(len(p_bytes)):
                    if a_bytes[k] < 128:
                        p_bytes[k] = TRANS
                result = PilImage.frombytes("P", (w, h), bytes(p_bytes))
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
            quantised  = combined.quantize(colors=256, dither=0)
            palette    = quantised.getpalette()
            pal_img    = PilImage.new("P", (1, 1))
            pal_img.putpalette(palette)
            pal_frames = [f.quantize(palette=pal_img, dither=0) for f in frames]
            pal_frames[0].save(
                path, save_all=True,
                append_images=pal_frames[1:],
                duration=duration_ms, loop=0, optimize=False,
            )
