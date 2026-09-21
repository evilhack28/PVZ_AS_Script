"""Side-effect import that puts every library subfolder on sys.path so the project's flat imports keep working:"""

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
