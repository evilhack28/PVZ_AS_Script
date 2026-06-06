"""
anim_meta.py
------------
Loader for the optional animation metadata file (animaction.txt) referenced
by the --meta CLI flag.

Until this reorganisation, this module did not exist — `from anim_meta import ...`
calls inside player.py / xfl_label.py / main.py silently failed via ImportError
fallbacks, but anything that *did* manage to import (e.g. via default_settings
fallback in _meta_for_action) crashed.

This stub provides the API surface every caller expects:

    ActionConfig     – dataclass returned by AnimMeta.action_config()
    ParticleConfig   – dataclass returned by AnimMeta.particle_config()
    AnimMeta         – loader with .load(), .is_empty(), .action_config(),
                       .particle_config()

A minimal TSV/CSV reader is implemented for animaction.txt.  The format is:

    define <tab> action <tab> scale <tab> offset_x <tab> offset_y <tab> fps <tab>
    flip <tab> loop <tab> start_frame <tab> end_frame

Lines starting with `#` are comments.  Missing columns get their default values.
A second table for particle hide-parts uses the marker `[particles]` on a line by
itself, followed by rows of:

    define <tab> action <tab> hide_part1 <tab> hide_part2

If the file cannot be parsed (e.g. it is empty, or no rows are recognised), the
loader returns an empty AnimMeta whose `is_empty()` returns True so callers
gracefully fall back to default_settings.py or raw FBIN values.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional


log = logging.getLogger(__name__)


# ── Public dataclasses ────────────────────────────────────────────────────────

@dataclass
class ActionConfig:
    define:       str   = ""
    action_name:  str   = ""
    flip:         bool  = False
    loop:         bool  = True
    offset_x:     float = 0.0
    offset_y:     float = 0.0
    scale:        float = 1.0
    fps:          int   = 0
    start_frame:  int   = 0
    end_frame:    int   = 0


@dataclass
class ParticleConfig:
    define:     str = ""
    action:     str = ""
    hide_part1: str = "0"
    hide_part2: str = "0"


# ── Loader ────────────────────────────────────────────────────────────────────

@dataclass
class AnimMeta:
    # Keyed by (define.lower(), action.lower()); a "" action acts as the
    # wildcard fallback within a define.
    actions:   dict = field(default_factory=dict)
    particles: dict = field(default_factory=dict)

    @classmethod
    def load(cls, action_tsv: Optional[str] = None,
             particle_tsv: Optional[str] = None) -> "AnimMeta":
        """
        Load the action and particle tables from disk.

        Both args may point at the same file — the loader switches tables when
        it encounters a `[particles]` marker line.
        """
        meta = cls()

        seen = set()
        for path in (action_tsv, particle_tsv):
            if not path or path in seen or not os.path.isfile(path):
                continue
            seen.add(path)
            try:
                meta._ingest_file(path)
            except Exception as exc:
                log.warning("Failed to read meta file %s: %s", path, exc)

        return meta

    def is_empty(self) -> bool:
        return not self.actions and not self.particles

    def action_config(self, define: str, action_name: str) -> Optional[ActionConfig]:
        """Return the ActionConfig for (define, action_name) or None if absent."""
        if not define:
            return None
        key = (define.lower(), (action_name or "").lower())
        if key in self.actions:
            return self.actions[key]
        # Wildcard: define-level fallback (action == "")
        fb = (define.lower(), "")
        return self.actions.get(fb)

    def particle_config(self, define: str, action_name: str) -> Optional[ParticleConfig]:
        if not define:
            return None
        key = (define.lower(), (action_name or "").lower())
        return self.particles.get(key)

    # ── Parser internals ──────────────────────────────────────────────────────

    def _ingest_file(self, path: str) -> None:
        mode = "actions"   # 'actions' | 'particles'
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line_no, raw in enumerate(fh, start=1):
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue

                tag = line.lower().strip("[]")
                if line.startswith("[") and line.endswith("]"):
                    if tag in ("particles", "particle"):
                        mode = "particles"
                    elif tag in ("actions", "action"):
                        mode = "actions"
                    continue

                cols = [c.strip() for c in self._split_row(line)]
                if len(cols) < 2:
                    continue

                if mode == "actions":
                    cfg = self._parse_action_row(cols)
                    if cfg is not None:
                        self.actions[(cfg.define.lower(),
                                      cfg.action_name.lower())] = cfg
                else:
                    pcfg = self._parse_particle_row(cols)
                    if pcfg is not None:
                        self.particles[(pcfg.define.lower(),
                                        pcfg.action.lower())] = pcfg

    @staticmethod
    def _split_row(line: str) -> list:
        # Prefer tab if present; fall back to comma (CSV) or whitespace.
        if "\t" in line:
            return line.split("\t")
        if "," in line:
            return line.split(",")
        return line.split()

    @staticmethod
    def _parse_action_row(cols: list) -> Optional[ActionConfig]:
        def col(i, default=""):
            return cols[i] if i < len(cols) else default

        define = col(0)
        if not define or define.lower() in ("define", "code_name"):
            return None  # header row
        return ActionConfig(
            define      = define,
            action_name = col(1),
            scale       = _to_float(col(2), 1.0),
            offset_x    = _to_float(col(3), 0.0),
            offset_y    = _to_float(col(4), 0.0),
            fps         = _to_int(col(5),   0),
            flip        = _to_bool(col(6),  False),
            loop        = _to_bool(col(7),  True),
            start_frame = _to_int(col(8),   0),
            end_frame   = _to_int(col(9),   0),
        )

    @staticmethod
    def _parse_particle_row(cols: list) -> Optional[ParticleConfig]:
        def col(i, default=""):
            return cols[i] if i < len(cols) else default

        define = col(0)
        if not define or define.lower() in ("define", "code_name"):
            return None
        return ParticleConfig(
            define     = define,
            action     = col(1),
            hide_part1 = col(2, "0") or "0",
            hide_part2 = col(3, "0") or "0",
        )


# ── Coercion helpers ──────────────────────────────────────────────────────────

def _to_float(s: str, default: float) -> float:
    try:
        return float(s)
    except (TypeError, ValueError):
        return default


def _to_int(s: str, default: int) -> int:
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return default


def _to_bool(s: str, default: bool) -> bool:
    if s is None or s == "":
        return default
    return str(s).strip().lower() in ("1", "true", "yes", "y", "t")
