"""Per-slot logic engine. Reuses worlds/tracker/TrackerCore.py instead of reimplementing any
region/rule logic -- see design doc section 4.5 and the implementation plan's "key facts verified in
source" section for the real (non-pseudocode) TrackerCore API this drives.

Two execution paths, chosen by Compatibility.supports_yamlless_regen() (see that function's
docstring for why this is a different question than check_compatibility()'s tier):

- Yaml-less (`ut_can_gen_without_yaml = True`): one independent, single-player synthetic
  TrackerCore per slot, safely parallelizable (compute_slot_logic).
- Needs a real YAML (everything else -- including plenty of "slot_data" tier worlds that define
  interpret_slot_data but not ut_can_gen_without_yaml, verified against a real room): reuses one
  shared TrackerCore that has already generated the full multiworld from every YAML in
  player_files_path (build_yaml_launch_core), exactly like a live TrackerClient does when it has
  its own YAML sitting in that folder -- just reading a different slot's info out of the same
  generated multiworld per call instead of only ever looking at "itself". This must run
  sequentially per shared core (compute_slot_logic_via_yaml), since it mutates shared instance
  state (player_id, missing_locations, ...) each call; the expensive part (generating from every
  YAML) only happens once per poll cycle, not once per slot.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
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
    YAML we don't have yet) call `add_log_line`/`sort_log_lines`/`log_all_to_tab` before
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


def _ensure_ut_player_folder(path: str | None = None) -> None:
    """UT's own `_set_host_settings()` unconditionally resolves `TrackerSettings.player_files_path`
    at the top of `run_generator()`, even for the yaml-less path where the value is thrown away a
    few lines later in favor of our tempdir override -- if that folder doesn't exist yet (verified
    against a fresh checkout: it isn't shipped by default), `settings.py`'s `Group.__getattribute__`
    raises `FileNotFoundError` before we ever get there. Since we can't change UT's code, just make
    sure a folder exists. `object.__getattribute__` bypasses `Group`'s validating
    `__getattribute__` so reading the raw path doesn't itself trigger the same error.

    path: when given (our own configured player_files_path, for the real-YAML path), ensure that
    one instead of UT's own -- see build_yaml_launch_core()."""
    if path is not None:
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            pass
        return

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
    hinted_in_logic: int = 0  # of the open+in-logic checks, how many have a not-yet-found hint on them
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


def _finish_from_generated_core(core, snapshot: SlotSnapshot, base_kwargs: dict) -> SlotLogicResult:
    """Shared tail end of both execution paths, once `core.multiworld`/`core.player_id` point at
    the right, already-generated slot: compute missing locations, feed received items, run
    updateTracker(), and check the empty-state ("no progression needed") case."""
    if core.multiworld is None or core.tracker_disabled:
        detail = core.gen_error or "world could not be generated for this slot"
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

    # state.in_logic_locations is a list of location *names* (see updateTracker()'s
    # CurrentTrackerState), but hints from the webhost API are keyed by numeric location id (see
    # DataSource.SlotSnapshot.hinted_not_found_locations) -- translate via this same regenerated
    # world's own name<->id map (location_id_to_name's keys are already all_location_ids above, so
    # its reverse, location_name_to_id, is the correct translation for this exact slot/options).
    location_name_to_id = core.multiworld.worlds[core.player_id].location_name_to_id
    in_logic_ids = {location_name_to_id[name] for name in state.in_logic_locations
                     if name in location_name_to_id}
    hinted_in_logic = len(in_logic_ids & snapshot.hinted_not_found_locations)

    return SlotLogicResult(
        **base_kwargs,
        in_logic_open=len(state.in_logic_locations),
        out_of_logic_open=len(state.glitched_locations) if supports_glitches(snapshot.game) else None,
        hinted_in_logic=hinted_in_logic,
        no_progression_needed=no_progression,
    )


def _base_kwargs(snapshot: SlotSnapshot, compat: CompatibilityTier) -> dict:
    return dict(
        slot_id=snapshot.slot_id,
        slot_name=snapshot.slot_name,
        game=snapshot.game,
        compatibility=compat,
        source=snapshot.source,
        total_locations=snapshot.total_locations,
        checked=len(snapshot.checked_locations),
    )


def compute_slot_logic(snapshot: SlotSnapshot, fetch_slot_data=None) -> SlotLogicResult:
    """Yaml-less path: only valid for games where Compatibility.supports_yamlless_regen() is True.
    Safe to call concurrently across slots -- each call gets its own independent TrackerCore.

    fetch_slot_data: optional callable(slot_id) -> dict | None, used to lazily pull slot_data if
    the snapshot itself didn't already carry it (poll mode fetches it from a separate, heavier
    endpoint -- see DataSource.PollSource.fetch_slot_data / fetch_all_slot_data)."""

    compat = check_compatibility(snapshot.game)
    base_kwargs = _base_kwargs(snapshot, compat)

    if compat == "unknown_game":
        return SlotLogicResult(**base_kwargs, error="Apworld not installed")

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
    # branching for static vs. instance-style interpret_slot_data; hand-rolling a subset of it here
    # would risk silently misusing that API (e.g. calling an instance-style interpret_slot_data
    # unbound on the class).
    try:
        core.initalize_tracker_core(world_cls, slot_data)
    except Exception as e:
        return SlotLogicResult(**base_kwargs, error=f"generation failed: {e}")

    return _finish_from_generated_core(core, snapshot, base_kwargs)


# Two distinct failure shapes seen live against a real 37-YAML room, both fatal for the *entire*
# shared generation (not just the offending slot), and neither offering a structured way to get the
# offending file -- so this is deliberately loose regex matching against `core.gen_error`'s text,
# not a full parse of either message format:
# 1. Generate.py's main() collects every invalid-YAML failure it finds across the whole folder and
#    raises ONE ValueError listing all of them: "N. File <path> document #<i> (with name: <name>)
#    is invalid. ...". Gives a filename directly.
# 2. A single world's generate_early()/create_regions()/etc. raising during the actual TMain
#    generation pass (worlds/AutoWorld.py's call_single wraps and re-raises with this context) --
#    e.g. a hard option-validation error like Spyro 2's "gemsanity set to full" check. Unlike (1),
#    TMain fails on the *first* such error rather than collecting every one up front, so this class
#    needs the iterative retry loop below (exclude one, retry, possibly hit a different one next).
#    Gives a player *name*, not a filename -- resolved against the actual folder contents below.
_INVALID_YAML_FILE_RE = re.compile(r"File (.+?) document #\d+")
_GENERATION_FAILED_FOR_PLAYER_RE = re.compile(r"for player \d+, named ([^.\n]+)\.")
_MAX_EXCLUSION_ROUNDS = 10  # backstop against pathological rooms; each round excludes >=1 more slot

# Process-wide, per-folder memory of previously-excluded bad YAMLs -- see build_yaml_launch_core's
# docstring for why this matters (avoids re-discovering the same persistently-bad files, and the
# full doomed-to-fail generation attempt that goes with each one, on every single poll cycle).
_known_bad_yaml_cache: dict[str, set[str]] = {}


def _new_yaml_core(player_folder: str):
    from worlds.tracker.TrackerCore import TrackerCore

    core = TrackerCore(_logger, print_list=False, print_count=False)
    _apply_default_host_settings(core)
    core.player_folder_override = player_folder
    core.mst_temp_dir = None  # set by build_yaml_launch_core if it had to fall back to a filtered copy
    try:
        core.run_generator(None, None)
    except Exception as e:
        core.gen_error = core.gen_error or str(e)
    return core


def _find_bad_identifiers(gen_error: str) -> set[str]:
    return set(_INVALID_YAML_FILE_RE.findall(gen_error)) | set(_GENERATION_FAILED_FOR_PLAYER_RE.findall(gen_error))


def _resolve_to_basenames(folder: str, identifiers: set[str]) -> set[str]:
    """Turns whatever _find_bad_identifiers() extracted -- a filename, or a bare player name with
    no extension -- into actual basenames present in `folder`, so _copy_player_folder_excluding can
    match on them directly. A bare name is matched case-insensitively against each file's stem."""
    try:
        entries = os.listdir(folder)
    except OSError:
        return set()
    resolved = set()
    lower_stem_to_name = {os.path.splitext(e)[0].lower(): e for e in entries}
    for ident in identifiers:
        ident = ident.strip()
        if ident in entries:
            resolved.add(ident)
            continue
        match = lower_stem_to_name.get(os.path.splitext(ident)[0].lower())
        if match:
            resolved.add(match)
    return resolved


def _copy_player_folder_excluding(source_dir: str, excluded_basenames: set[str]) -> str | None:
    """One bad YAML shouldn't be able to take down logic for every *other* slot sharing this
    player_files_path -- verified live against a real 37-YAML room: a single version-mismatched
    YAML made TrackerCore.run_generator(None, None) fail for the entire folder at once (it
    generates every YAML in the folder together as one batch), even though every other YAML there
    was perfectly valid on its own. Copies everything except the named files into a temp dir so a
    retry can exclude just the offenders; returns None if nothing ended up copyable."""
    try:
        tmp_dir = tempfile.mkdtemp(prefix="mst_players_")
        copied_any = False
        for name in os.listdir(source_dir):
            if name in excluded_basenames:
                continue
            src = os.path.join(source_dir, name)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(tmp_dir, name))
                copied_any = True
        if copied_any:
            return tmp_dir
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return None
    except OSError:
        return None


def build_yaml_launch_core(player_files_path: str) -> "TrackerCore":  # noqa: F821 -- imported below
    """Builds the ONE shared TrackerCore for the real-YAML path this poll cycle: generates the full
    multiworld from every YAML in player_files_path, exactly like a live TrackerClient does on
    connect (TrackerCore.run_generator(None, None)). Expensive (parses + generates every YAML in
    the folder) -- call this once per cycle, then feed the result into
    compute_slot_logic_via_yaml() for every slot that needs it, not once per slot.

    If one or more YAMLs in the folder are invalid or fail generation, iteratively retries against
    a temp copy of the folder with the offenders excluded (see module comment above
    _INVALID_YAML_FILE_RE for the two failure shapes this handles, and why it has to be iterative --
    verified live against a real 37-YAML room where this took two rounds: one YAML failed parsing,
    then a second, unrelated world failed a hard option-validation check during generation).
    Slots whose own YAML ended up excluded will simply not be found by
    compute_slot_logic_via_yaml() afterwards, same as any other missing YAML -- this never silently
    drops a slot's *result*, only its contribution to this shared multiworld. Caller is responsible
    for cleaning up `core.mst_temp_dir` (if not None) once done with the returned core -- it's a
    temp directory, not the caller's own player_files_path.

    The returned core's `launch_multiworld` is None (and `gen_error` set) if generation failed
    outright even after exhausting retries (e.g. the folder is empty, every YAML in it is invalid,
    or _MAX_EXCLUSION_ROUNDS was reached) -- compute_slot_logic_via_yaml() reports that clearly per
    slot rather than silently returning zero logic.

    Bad files found this way are remembered process-wide per folder (_known_bad_yaml_cache) and
    excluded from the very first attempt on every subsequent call -- without this, a folder with N
    persistently-bad YAMLs (a stale/incompatible one someone never got around to fixing) would pay
    for a full, doomed-to-fail generation of *every* YAML in the folder on *every single poll
    cycle* before rediscovering the same exclusions each time. Verified live against a real
    38-YAML room: each full generation attempt there took long enough that needing 2-3 of them
    per cycle (one per newly-discovered bad file) meaningfully delayed how soon any slot's data
    showed up at all.
    """
    _ensure_ut_player_folder(player_files_path)

    all_excluded: set[str] = set(_known_bad_yaml_cache.get(player_files_path, set()))
    current_dir = player_files_path
    if all_excluded:
        filtered = _copy_player_folder_excluding(player_files_path, all_excluded)
        if filtered is not None:
            current_dir = filtered

    core = _new_yaml_core(current_dir)

    for _round in range(_MAX_EXCLUSION_ROUNDS):
        if core.launch_multiworld is not None or not core.gen_error:
            break

        newly_bad = _resolve_to_basenames(current_dir, _find_bad_identifiers(core.gen_error)) - all_excluded
        if not newly_bad:
            break  # nothing new to exclude -- some other failure (empty folder, etc.), give up as-is

        all_excluded |= newly_bad
        _logger.warning(
            f"Multi Slot Tracker: excluding {len(all_excluded)} invalid/failing YAML(s) from "
            f"player_files_path so the rest of the room isn't affected: {sorted(all_excluded)}"
        )
        retry_dir = _copy_player_folder_excluding(player_files_path, all_excluded)
        if retry_dir is None:
            break  # couldn't even build a filtered copy -- surface the last error as-is

        if current_dir != player_files_path:
            shutil.rmtree(current_dir, ignore_errors=True)  # drop the previous round's temp copy
        current_dir = retry_dir
        core = _new_yaml_core(current_dir)

    if all_excluded:
        _known_bad_yaml_cache[player_files_path] = all_excluded
    core.mst_temp_dir = current_dir if current_dir != player_files_path else None
    return core


def compute_slot_logic_via_yaml(snapshot: SlotSnapshot, shared_core, fetch_slot_data=None) -> SlotLogicResult:
    """Real-YAML path: looks the slot up by name in shared_core.launch_multiworld (built once by
    build_yaml_launch_core() for this whole poll cycle) instead of generating anything new. Must be
    called sequentially per shared_core -- it mutates shared instance state
    (player_id/missing_locations/tracker_items_received/...) on every call."""

    compat = check_compatibility(snapshot.game)
    base_kwargs = _base_kwargs(snapshot, compat)

    if compat == "unknown_game":
        return SlotLogicResult(**base_kwargs, error="Apworld not installed")

    if shared_core.launch_multiworld is None:
        detail = shared_core.gen_error or (
            "no player YAMLs could be generated -- check that player_files_path points at a "
            "folder containing them"
        )
        return SlotLogicResult(**base_kwargs, error=detail)

    world_cls = get_world_type(snapshot.game)
    if world_cls is None:
        return SlotLogicResult(**base_kwargs, error="Apworld not installed")

    slot_data = snapshot.slot_data
    if slot_data is None and fetch_slot_data is not None:
        try:
            slot_data = fetch_slot_data(snapshot.slot_id)
        except Exception:
            slot_data = None  # not fatal here -- only used for the optional interpret_slot_data refinement below

    shared_core.set_slot_params(snapshot.game, snapshot.slot_id, snapshot.slot_name, 0)
    # shared_core is reused sequentially across every slot needing this path this cycle (see
    # build_yaml_launch_core's docstring) -- initalize_tracker_core() only ever *sets* these on
    # success, so a previous slot's leftover value would otherwise silently leak into this one's
    # result on any failure path (e.g. one slot being disable_ut-flagged would wrongly disable
    # every slot checked afterwards in the same cycle).
    shared_core.multiworld = None
    shared_core.gen_error = ""
    shared_core.tracker_disabled = False
    try:
        shared_core.initalize_tracker_core(world_cls, slot_data)
    except Exception as e:
        return SlotLogicResult(**base_kwargs, error=f"generation failed: {e}")

    if shared_core.multiworld is None:
        detail = shared_core.gen_error or (
            f'no YAML for slot name "{snapshot.slot_name}" found in player_files_path '
            "(or its game doesn't match)"
        )
        return SlotLogicResult(**base_kwargs, error=detail)

    return _finish_from_generated_core(shared_core, snapshot, base_kwargs)
