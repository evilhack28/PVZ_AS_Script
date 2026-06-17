"""
_paths.py
---------
Side-effect import that puts every library subfolder on sys.path so the
project's flat imports keep working:

    from fbin_parser import parse_fbin     # parsers/fbin_parser.py
    from renderer    import Renderer       # render/renderer.py
    from player      import Player         # render/player/__init__.py

The entry point (AS_to_XFL.py) does:

    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _paths  # noqa: F401

Library files do not need to import this module — once the entry point has
registered the paths, the rest of Python's import resolution Just Works.
"""

import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))

# Library folders that should be on sys.path so flat imports resolve.
_SUBDIRS = (
    "parsers",
    "render",      # also makes render/player/ importable as `player` package
    "pvr",
)

for _name in _SUBDIRS:
    _path = os.path.join(_ROOT, _name)
    if os.path.isdir(_path) and _path not in sys.path:
        sys.path.insert(0, _path)
