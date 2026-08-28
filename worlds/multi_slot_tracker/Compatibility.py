"""Per-game compatibility check, done at runtime against whatever apworlds happen to be installed --
see design doc section 4.4. Deliberately not hard-coded to a game list.
"""

from __future__ import annotations

from typing import Literal, Optional, Type

from worlds.AutoWorld import AutoWorldRegister, World

CompatibilityTier = Literal["slot_data", "yaml_required", "unknown_game"]


def get_world_type(game: str) -> Optional[Type[World]]:
    return AutoWorldRegister.world_types.get(game)


def check_compatibility(game: str) -> CompatibilityTier:
    world_cls = get_world_type(game)
    if world_cls is None:
        return "unknown_game"
    has_hook = callable(getattr(world_cls, "interpret_slot_data", None))
    return "slot_data" if has_hook else "yaml_required"


def supports_glitches(game: str) -> bool:
    world_cls = get_world_type(game)
    if world_cls is None:
        return False
    return bool(getattr(world_cls, "glitches_item_name", ""))
