"""
default_settings.py
-------------------
Hardcoded per-character display settings used when no --meta file is supplied.

How it works
============
When the player starts it derives a "define key" from the --bin filename stem
(e.g. "pirate_cannon_imp.bin" → define key "pirate_cannon_imp").
It then looks up that key in CHARACTER_DEFAULTS below to get the base scale,
offset, fps, and any per-action overrides.

If the key is not found, DEFAULT_FALLBACK is used (scale=1.0, no offset).

IMPORTANT — RawBin vs FBIN
==========================
FBIN files:  element matrices store LOCAL offsets relative to the parent MC.
             scale + offset in this file are needed to position correctly.

RawBin files: element matrices already store COMPLETE world positions.
              Do NOT add entries here for RawBin characters — they render
              correctly with the default scale=1.0, offset=(0,0) fallback.
              Adding scale/offset on top of RawBin world coords breaks them.

To check if a file is RawBin: run debug_anim.py and look for [rawbin=True].

Adding a new character (FBIN only)
===================================
Copy one of the existing blocks and change the values.  The minimum you need
is an entry in CHARACTER_DEFAULTS with at least 'offset_x', 'offset_y', and
'scale'.  Per-action overrides in 'actions' are optional — if an action name
is not listed it inherits the character defaults.

Data format
===========
Each character entry is a dict with:
    offset_x  : float   horizontal offset in Cocos Y-up pixels
    offset_y  : float   vertical offset
    scale     : float   uniform scale (e.g. 0.6 = 60% of original size)
    fps       : int     default fps for all actions (0 = use FBIN value)
    flip      : bool    mirror the whole animation horizontally
    actions   : dict    optional per-action overrides, keyed by action name.
                        Each value is a dict with any subset of the above keys.
"""

# ── Per-character defaults ────────────────────────────────────────────────────

CHARACTER_DEFAULTS: dict = {

    # ── Zombie Pirate Imp ─────────────────────────────────────────────────────
    "zombie_pirate_imp1": {
        "offset_x": 68.0,
        "offset_y": 134.0,
        "scale":    0.6,
        "fps":      60,
        "flip":     False,
        "actions": {
            "walk":      {"fps": 120},
            "particles": {},
            "land":      {},
            "fly":       {},
            "attack":    {},
            "beaten":    {},
            "die":       {},
            "eat":       {},
            "idle":      {},
        },
    },

    # ── Pirate Cannon Imp ─────────────────────────────────────────────────────
    "pirate_cannon_imp2": {
        "offset_x": 67.0,
        "offset_y": 130.0,
        "scale":    0.6,
        "fps":      60,
        "flip":     False,
        "actions": {
            "walk":    {"fps": 120},
            "tuck":    {},
            "stand":   {},
            "eat":     {},
            "die":     {},
            "attack":  {},
            "beaten":  {},
        },
    },

    # ── Xiaojingteng (vine/tendril plant) ────────────────────────────────────
    "xiaojingteng1": {
        "offset_x": 54.0,
        "offset_y": 130.0,
        "scale":    0.8,
        "fps":      60,
        "flip":     False,
        "actions": {
            "in":        {},
            "idle":      {},
            "beaten":    {},
            "attack":    {},
            "plantfood": {},
        },
    },

    # ── Zombie Greetwall Helmet ───────────────────────────────────────────────
    "zombie_greetwall_helmet1": {
        "offset_x": 66.0,
        "offset_y": 130.0,
        "scale":    0.6,
        "fps":      60,
        "flip":     False,
        "actions": {
            "idle":      {},
            "walk":      {},
            "attack":    {},
            "beaten":    {},
            "die":       {},
            "particles": {},
        },
    },

    # ── Add more characters below ─────────────────────────────────────────────
    # "zombie_bobsled_team": {
    #     "offset_x": 70.0,
    #     "offset_y": 140.0,
    #     "scale":    0.6,
    #     "fps":      60,
    #     "flip":     False,
    #     "actions":  {},
    # },

}

# Used when the define key is not found in CHARACTER_DEFAULTS
DEFAULT_FALLBACK: dict = {
    "offset_x": 0.0,
    "offset_y": 0.0,
    "scale":    1.0,
    "fps":      0,
    "flip":     False,
    "actions":  {},
}


# ── Public helpers ────────────────────────────────────────────────────────────

def get_character_defaults(define_key: str) -> dict:
    """
    Return the character-level defaults for *define_key*.
    Falls back to DEFAULT_FALLBACK if the key is unknown.
    The returned dict is a shallow copy — safe to modify.
    """
    base = CHARACTER_DEFAULTS.get(define_key.lower())
    if base is None:
        import logging
        logging.getLogger(__name__).info(
            "default_settings: no entry for '%s', using fallback (scale=1, no offset).",
            define_key,
        )
        return dict(DEFAULT_FALLBACK)
    return dict(base)


def get_action_config(define_key: str, action_name: str) -> dict:
    """
    Return a fully-merged config dict for a specific (define, action) pair.

    Merge order (later wins):
        DEFAULT_FALLBACK  →  character defaults  →  per-action overrides

    The returned dict always contains:
        offset_x, offset_y, scale, fps, flip
    """
    char = CHARACTER_DEFAULTS.get(define_key.lower(), DEFAULT_FALLBACK)
    # Start from character-level values (excluding the nested 'actions' key)
    merged = {k: v for k, v in char.items() if k != "actions"}
    # Apply per-action overrides if present
    action_overrides = char.get("actions", {}).get(action_name.lower(), {})
    merged.update(action_overrides)
    return merged


def list_known_characters() -> list:
    """Return a sorted list of all define keys that have hardcoded settings."""
    return sorted(CHARACTER_DEFAULTS.keys())
