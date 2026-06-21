"""
player
------
Animation player built on top of Renderer.

The Player class is composed from four mixins, one per concern:
    _PlayerCore   – __init__, run loop, base transform, fps resolution
    HudMixin      – HUD overlay + action picker drawing
    InputMixin    – keyboard / window event handling
    ExportMixin   – GIF / sprite / atlas / JSON exports

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
M           - helmet picker (cone / bucket damage states)
K           - toggle 'butter' sprite (kungfu zombies' head accessory)
1 / 2 / 4   - fps mode src / custom / enter custom value
G / A / Z   - export current / all / all-no-bg GIFs
W           - export current as animated WebP (full alpha)
V           - export current as MP4 (needs imageio + ffmpeg)
S / T       - export sprites / atlas
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
