"""player ------ Animation player built on top of Renderer (P = parts picker, D = game data)."""

from .core   import _PlayerCore, PlayerConfig
from .hud    import HudMixin
from .input  import InputMixin
from .export import ExportMixin
from .parts  import PartsMixin


class Player(PartsMixin, InputMixin, HudMixin, ExportMixin, _PlayerCore):
    """Animation player. See module docstring for controls."""
    pass


__all__ = ["Player", "PlayerConfig"]
