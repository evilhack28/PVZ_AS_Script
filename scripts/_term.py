"""
_term.py
--------
Tiny terminal-color helper. Wraps text in ANSI escape codes when stdout is a
TTY and colors aren't disabled; collapses to raw text otherwise.

No dependencies. Windows 10+ terminals support ANSI natively. Older cmd.exe
falls back to plain text via the TTY check.
"""

import logging
import os
import sys

_COLOR_ENABLED = True


def supports_color(no_color_flag: bool = False) -> bool:
    """True if ANSI colors should be emitted on stdout."""
    if no_color_flag:
        return False
    if os.environ.get("NO_COLOR"):
        return False
    if not hasattr(sys.stdout, "isatty"):
        return False
    return sys.stdout.isatty()


def set_enabled(enabled: bool) -> None:
    """Global on/off switch, called once after argparse."""
    global _COLOR_ENABLED
    _COLOR_ENABLED = enabled


def c(text: str, code: str) -> str:
    """Wrap `text` in an ANSI SGR sequence, or return it unchanged."""
    if not _COLOR_ENABLED:
        return text
    return f"\033[{code}m{text}\033[0m"


def bold(text):    return c(text, "1")
def dim(text):     return c(text, "2")
def red(text):     return c(text, "31")
def green(text):   return c(text, "32")
def yellow(text):  return c(text, "33")
def blue(text):    return c(text, "34")
def magenta(text): return c(text, "35")
def cyan(text):    return c(text, "36")
def bold_red(text): return c(text, "1;31")


class ColorFormatter(logging.Formatter):
    """Drop-in formatter that paints the levelname column."""

    _LEVEL_COLORS = {
        logging.DEBUG:    "2",       # dim
        logging.INFO:     "34",      # blue
        logging.WARNING:  "33",      # yellow
        logging.ERROR:    "31",      # red
        logging.CRITICAL: "1;31",    # bold red
    }

    def format(self, record: logging.LogRecord) -> str:
        levelname = f"{record.levelname:<8s}"
        if _COLOR_ENABLED:
            code = self._LEVEL_COLORS.get(record.levelno)
            if code:
                levelname = f"\033[{code}m{levelname}\033[0m"
        return f"{levelname} {record.name}: {record.getMessage()}"
