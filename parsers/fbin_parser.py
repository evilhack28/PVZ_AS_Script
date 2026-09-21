"""Public entry points for reading a game animation bin (FBIN "MinBin" or RawBin)."""

import logging

from input_buffer import DEFAULT_FRAME_RATE          # noqa: F401  (re-export)
import game_bin
from game_bin import GameBinError, LAST_INFO          # noqa: F401  (LAST_INFO shared)

log = logging.getLogger(__name__)

__all__ = ["parse_binary", "parse_fbin", "LAST_INFO", "GameBinError", "DEFAULT_FRAME_RATE"]


def parse_binary(bin_path) -> dict | None:
    """Parse a bin into a single dict, or None on failure."""
    images, movie_clips, actions, is_rawbin = parse_fbin(str(bin_path))
    if images is None or movie_clips is None:
        return None
    return {
        "format":      "RawBin" if is_rawbin else "FBIN",
        "info":        dict(LAST_INFO),
        "images":      images,
        "movie_clips": movie_clips,
        "actions":     actions,
        "is_rawbin":   is_rawbin,
    }


def parse_fbin(bin_path: str):
    """Load `bin_path` like the game does."""
    LAST_INFO.clear()
    try:
        with open(bin_path, 'rb') as fh:
            data = fh.read()
    except OSError as exc:
        log.error("Cannot open '%s': %s", bin_path, exc)
        return None, None, None, False
    try:
        return game_bin.parse_game_bin(data)
    except GameBinError as exc:
        log.warning("'%s' is not a loadable animation bin: %s", bin_path, exc)
        return None, None, None, False
