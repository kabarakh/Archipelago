"""Per-slot logic engine. Reuses worlds/tracker/TrackerCore.py instead of reimplementing any
region/rule logic -- see design doc section 4.5 and the implementation plan's "key facts verified in
source" section for the real (non-pseudocode) TrackerCore API this drives.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Literal, Optional

from BaseClasses import CollectionState

from .Compatibility import CompatibilityTier, check_compatibility, get_world_type, supports_glitches
from .DataSource import SlotSnapshot

_logger = logging.getLogger("MultiSlotTracker")
_ut_player_folder_checked = False

# Mirrors the fallback computed in TrackerCore._set_host_settings() when host.yaml has no
# sorting_priorities configured, so pre-seeding this has no effect on games that do reach that
# codepath (they'll just overwrite it with the same values or the user's own host.yaml ones).
_DEFAULT_SORTING_PRIORITIES = {
    "default": 0, "hinted": 1, "excluded": 2, "excluded_glitched": 3, "hinted_glitched": 4,
    "glitched": 5, "unconnected": 6, "error": -1, "other": 7, "ut_status": 8,
}


def _apply_default_host_settings(core) -> None:
    """`run_generator()` -> `_set_host_settings()` is the only place that ever sets
    `output_format`/`hide_excluded`/`use_split`/`enforce_deferred_connections`/
    `enable_glitched_logic`/`sorting_priorities`/`sorting_method` on a `TrackerCore` instance.
    Several of `initalize_tracker_core`'s early-return paths (taken whenever a game needs a real
    YAML we don't have, e.g. `interpret_slot_data` without `ut_can_gen_without_yaml` -- verified
    against a real room: `Mega Man 2`) call `add_log_line`/`sort_log_lines`/`log_all_to_tab` before
    `run_generator` ever runs, which raises a bare `AttributeError`/`KeyError` on these instead of
    the clean `core.multiworld is None` we already handle. Pre-seed UT's own shipped defaults (see
    `TrackerSettings` in worlds/tracker/__init__.py) so those paths degrade gracefully instead."""
    from worlds.tracker import DeferredEntranceMode

    core.output_format = "Location"
    core.hide_excluded = False
    core.use_split = True
    core.enforce_deferred_connections = DeferredEntranceMode.default
    core.enable_glitched_logic = True
    core.sorting_priorities = dict(_DEFAULT_SORTING_PRIORITIES)
    core.sorting_method = "apworld"


def _ensure_ut_player_folder() -> None:
    """UT's own `_set_host_settings()` unconditionally resolves `TrackerSettings.player_files_path`
    at the top of `run_generator()`, even for the yaml-less path where the value is thrown away a
    few lines later in favor of our tempdir override -- if that folder doesn't exist yet (verified
    against a fresh checkout: it isn't shipped by default), `settings.py`'s `Group.__getattribute__`
    raises `FileNotFoundError` before we ever get there. Since we can't change UT's code, just make
    sure its configured folder exists. `object.__getattribute__` bypasses `Group`'s validating
    `__getattribute__` so reading the raw path doesn't itself trigger the same error."""
    global _ut_player_folder_checked
    if _ut_player_folder_checked:
        return
    _ut_player_folder_checked = True
    try:
        from worlds.tracker import TrackerWorld

        raw_path = object.__getattribute__(TrackerWorld.settings, "player_files_path")
        os.makedirs(raw_path.resolve(), exist_ok=True)
    except Exception:
        pass  # best effort; if this fails, the real error will surface from run_generator anyway


@dataclass
class SlotLogicResult:
    slot_id: int
    slot_name: str
    game: str
    compatibility: CompatibilityTier
    source: Literal["live", "poll"]
    total_locations: int
    checked: int
    in_logic_open: Optional[int] = None
    out_of_logic_open: Optional[int] = None  # None = game does not support glitches_item_name
    no_progression_needed: bool = False
    error: Optional[str] = None


def _reachable_with_empty_state(core, multiworld, player_id: int) -> set:
    """Same traversal updateTracker() does for received items, but starting from a state that
    only auto-collects precollected/start-inventory items -- see CollectionState.__init__ in
    BaseClasses.py. Used to answer "does this slot need no progression items at all?"."""
    from worlds.tracker import DeferredEntranceMode

    empty_state = CollectionState(multiworld, core.enforce_deferred_connections != DeferredEntranceMode.disabled)
    empty_state.sweep_for_advancements(
        locations=[location for location in multiworld.get_locations(player_id) if not location.address]
    )
    return set(multiworld.get_reachable_locations(empty_state, player_id))


def compute_slot_logic(snapshot: SlotSnapshot, fetch_slot_data=None) -> SlotLogicResult:
    """fetch_slot_data: optional callable(slot_id) -> dict | None, used to lazily pull slot_data
    for "slot_data" tier games when the snapshot itself didn't already carry it (poll mode fetches
    it from a separate, heavier endpoint -- see DataSource.PollSource.fetch_slot_data)."""

    compat = check_compatibility(snapshot.game)
    base_kwargs = dict(
        slot_id=snapshot.slot_id,
        slot_name=snapshot.slot_name,
        game=snapshot.game,
        compatibility=compat,
        source=snapshot.source,
        total_locations=snapshot.total_locations,
        checked=len(snapshot.checked_locations),
    )

    if compat == "unknown_game":
        return SlotLogicResult(**base_kwargs, error="Apworld not installed")

    if compat == "yaml_required":
        return SlotLogicResult(**base_kwargs, error="Logic unavailable")

    slot_data = snapshot.slot_data
    if slot_data is None and fetch_slot_data is not None:
        try:
            slot_data = fetch_slot_data(snapshot.slot_id)
        except Exception as e:  # network/etc, degrade to reporting the limitation, don't crash the dashboard
            return SlotLogicResult(**base_kwargs, error=f"failed to fetch slot_data: {e}")

    if not slot_data:
        return SlotLogicResult(**base_kwargs, error="no slot_data available for this slot")

    try:
        from worlds.tracker.TrackerCore import TrackerCore
    except ImportError as e:
        return SlotLogicResult(**base_kwargs, error=f"tracker apworld (Universal Tracker) not installed: {e}")

    _ensure_ut_player_folder()

    world_cls = get_world_type(snapshot.game)
    core = TrackerCore(_logger, print_list=False, print_count=False)
    core.set_slot_params(snapshot.game, snapshot.slot_id, snapshot.slot_name, 0)
    _apply_default_host_settings(core)

    # Reuse UT's own bootstrap exactly (worlds/tracker/TrackerCore.py:initalize_tracker_core), the
    # same entry point a live TrackerClient calls after connecting. It already contains the correct
    # branching for static vs. instance-style interpret_slot_data and for ut_can_gen_without_yaml;
    # hand-rolling a subset of it here would risk silently misusing that API (e.g. calling an
    # instance-style interpret_slot_data unbound on the class).
    try:
        core.initalize_tracker_core(world_cls, slot_data)
    except Exception as e:
        return SlotLogicResult(**base_kwargs, error=f"generation failed: {e}")

    if core.multiworld is None or core.tracker_disabled:
        detail = core.gen_error or (
            "world could not be regenerated from slot_data without a YAML "
            "(likely missing ut_can_gen_without_yaml support)"
        )
        return SlotLogicResult(**base_kwargs, error=detail)

    # Location addresses are arbitrary per-game datapackage IDs, not a 0..total_locations range
    # (verified against a real room: Timespinner location IDs are nowhere near that range) -- the
    # design doc's own §4.5 sketch of `set(range(total_locations)) - checked` was wrong on this
    # point. The regenerated world's own id<->name map is the authoritative set of addresses for
    # this exact slot/options combination.
    all_location_ids = set(core.multiworld.worlds[core.player_id].location_id_to_name)
    core.set_missing_locations(all_location_ids - snapshot.checked_locations)
    core.set_items_received(snapshot.received_items)

    try:
        state = core.updateTracker()
        empty_reachable = _reachable_with_empty_state(core, core.multiworld, core.player_id)
    except Exception as e:
        return SlotLogicResult(**base_kwargs, error=f"logic computation failed: {e}")

    no_progression = len(empty_reachable) >= len(all_location_ids) > 0

    return SlotLogicResult(
        **base_kwargs,
        in_logic_open=len(state.in_logic_locations),
        out_of_logic_open=len(state.glitched_locations) if supports_glitches(snapshot.game) else None,
        no_progression_needed=no_progression,
    )
