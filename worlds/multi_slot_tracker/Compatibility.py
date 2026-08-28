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


def supports_yamlless_regen(game: str) -> bool:
    """The actual gate TrackerCore.initalize_tracker_core() branches on to decide whether it can
    regenerate from slot_data alone or needs a real YAML in player_files_path -- verified against
    worlds/tracker/TrackerCore.py and, live, against a real room: several worlds define
    interpret_slot_data (so check_compatibility() reports "slot_data") without also setting
    `ut_can_gen_without_yaml = True`, and for those UT always requires the real YAML regardless of
    slot_data being available. Do not conflate this with check_compatibility()'s "slot_data" tier --
    that only reflects the interpret_slot_data hook, not whether a YAML is still required."""
    world_cls = get_world_type(game)
    if world_cls is None:
        return False
    return bool(getattr(world_cls, "ut_can_gen_without_yaml", False))
