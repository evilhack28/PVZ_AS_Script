"""
player
------
Animation player built on top of Renderer.

The Player class is composed from four mixins, one per concern:
    _PlayerCore   – __init__, run loop, base transform, meta resolution
    HudMixin      – HUD overlay + action picker drawing
    InputMixin    – keyboard / window event handling
    ExportMixin   – GIF / sprite / atlas / JSON / XFL exports

Controls
========
ESC / Q     - quit
LEFT/RIGHT  - previous / next action
UP/DOWN     - speed +0.1x / -0.1x
SPACE       - pause / resume
N / B       - step one frame forward / back (auto-pauses)
F           - jump to frame
L           - toggle loop
I           - action picker
G / A / Z   - export current / all / all-no-bg GIFs
S / T       - export sprites / atlas
X           - export XFL / .fla
J           - dump frames as JSON
H           - toggle HUD
"""

from .core   import _PlayerCore, PlayerConfig
from .hud    import HudMixin
from .input  import InputMixin
from .export import ExportMixin


class Player(InputMixin, HudMixin, ExportMixin, _PlayerCore):
    """Animation player. See module docstring for controls."""
    pass


__all__ = ["Player", "PlayerConfig"]
