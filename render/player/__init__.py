"""player ------ Animation player built on top of Renderer."""

from .core   import _PlayerCore, PlayerConfig
from .hud    import HudMixin
from .input  import InputMixin
from .export import ExportMixin


class Player(InputMixin, HudMixin, ExportMixin, _PlayerCore):
    """Animation player. See module docstring for controls."""
    pass


__all__ = ["Player", "PlayerConfig"]
