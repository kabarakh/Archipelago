# Multi-Slot Logic Tracker for Archipelago — Design & Implementation Plan

Status: Draft for implementation
Target repo: fork of `FarisTheAncient/Archipelago`, branch `tracker` (contains `worlds/tracker`)
Target format: its own apworld (Python), registered analogously to `worlds/tracker`

## 1. Goal

A tool that, for a configurable list of Archipelago slots (across multiple players/games, not just
your own), shows:

- the number of open checks that are currently **in logic**
- the number of open checks that are **out of logic but technically reachable** (where supported by
  the game)
- whether a slot needs **no progression items** at all to be fully open

The tool is itself a `hidden` apworld (not a playable game), registers itself in the Archipelago
launcher the same way Universal Tracker (UT) does, and reuses UT's existing logic engine
(`worlds/tracker/TrackerCore.py`) instead of reimplementing it.

## 2. Background (verified in source code)

- UT is a genuine `World` subclass (`TrackerWorld(World)`, `game = "Universal Tracker"`,
  `hidden = True`), registered via `AutoWorld`. It does nothing playable — it only registers settings
  and a launcher `Component`. → `worlds/tracker/__init__.py`
- Core mechanism (`TrackerCore.TMain`): UT builds a **synthetic single-player MultiWorld** per slot
  (`multi=1`, `player_ids={1}`) and runs the real generation steps of the target world on it
  (`generate_early → create_regions → create_items → set_rules → connect_entrances → generate_basic`,
  without `fill`). This produces the real region/location graph with the real `access_rule`s of that
  apworld — UT does not reimplement any logic. → `worlds/tracker/TrackerCore.py:279-417`
- "In logic" = `multiworld.get_reachable_locations(state, player_id)` on a `CollectionState`
  populated with the items actually received. → `TrackerCore.py:419-565` (`updateTracker`)
- "Out of logic but reachable" is **not a generic mode** — it is only available if the given apworld
  defines a `glitches_item_name` attribute. UT then copies the state, adds that one item, and checks
  reachability again. Without this attribute there is no such difference set.
  → `TrackerCore.py:511-559`
- For options without a YAML there is an official but **per-game opt-in** mechanism: an apworld can
  use `fill_slot_data()` to pack generation-relevant randomized results/options into `slot_data`, and
  implement `interpret_slot_data()` to reconstruct the logic without a YAML (via
  `multiworld.re_gen_passthrough`). Optionally it can also set `ut_can_gen_without_yaml = True`.
  → `worlds/tracker/docs/apworld-integration.md`
- Worlds in this repo currently shipping `interpret_slot_data` (as of this branch checkout):
  `dark_souls_3, kdl3, marioland2, messenger, mm2, mm3, osrs, satisfactory, shapez, stardew_valley,
  timespinner, tunic, yugioh06`. Other games strictly require the real player YAML.
- The webhost already offers ready-made, room-wide JSON endpoints that need no slot password (as long
  as the room has a public tracker UUID): → `WebHostLib/api/tracker.py`
  - `GET /api/tracker/<uuid>` — per player: received items (`NetworkItem[]`), checked location IDs,
    team totals, hints, client status, activity/connection timestamps
  - `GET /api/static_tracker/<uuid>` — groups, `total_locations` per player, game played per slot,
    datapackage
  - `GET /api/slot_data_tracker/<uuid>` — `slot_data` per player
- Important networking clarification: the client tag `"Tracker"` (set by `TrackerGameContext`) only
  changes how the client is displayed in the room status (`_non_game_messages` in `MultiServer.py`);
  it grants **no** read access to other slots. A live connection only ever sees its own slot;
  room-wide visibility without credentials is only available through the webhost JSON API above.

## 3. Architecture overview

```
+-------------------------------------------------------------+
| own apworld: "MultiSlotTracker" (hidden=True)                |
|                                                               |
|  Settings           Launcher component -> launch_client()    |
|                                                               |
|  +---------------------------------------------------------+ |
|  | DataSource (swappable)                                   | |
|  |  - LiveSource:  one CommonContext connection per slot     | |
|  |  - PollSource:  HTTP polling of the webhost tracker API   | |
|  +---------------------------------------------------------+ |
|                          |                                    |
|                          v                                    |
|  +---------------------------------------------------------+ |
|  | SlotEngine (one instance per selected slot)              | |
|  |  - compatibility check (full / slot_data / yaml_required)| |
|  |  - wraps/reuses TrackerCore (TMain, update)              | |
|  |  - produces a SlotLogicResult                            | |
|  +---------------------------------------------------------+ |
|                          |                                    |
|                          v                                    |
|  +---------------------------------------------------------+ |
|  | Aggregator: collects all SlotLogicResult -> DashboardData| |
|  +---------------------------------------------------------+ |
|                          |                                    |
|                          v                                    |
|  UI (Kivy tab like TrackerClient, or a standalone web         |
|  dashboard)                                                   |
+-------------------------------------------------------------+
```

## 4. Components in detail

### 4.1 World registration / launcher integration

Template: `worlds/tracker/__init__.py`. New file `worlds/multi_slot_tracker/__init__.py`:

```python
from worlds.AutoWorld import World
from worlds.LauncherComponents import Component, components, Type, icon_paths

class MultiSlotTrackerWorld(World):
    game = "Multi Slot Tracker"   # must differ from "Universal Tracker"
    hidden = True
    item_name_to_id = {}
    location_name_to_id = {}
    settings: "MultiSlotTrackerSettings"

def launch_client(*args):
    from worlds.LauncherComponents import launch
    from .Client import launch as ClientMain
    launch(ClientMain, name="Multi Slot Tracker", args=args)

components.append(Component("Multi Slot Tracker", None, func=launch_client, component_type=Type.CLIENT))
```

Dependency: this world requires `worlds.tracker` (UT) to be installed, because it imports its
`TrackerCore`. Check this on load and fail clearly if it's missing:

```python
try:
    from worlds.tracker.TrackerCore import TrackerCore
except ImportError:
    raise TrackerException("Multi Slot Tracker requires the 'tracker' apworld (Universal Tracker) to be installed.")
```

### 4.2 Settings

Analogous to `TrackerSettings` (`settings.Group`), fields:

- `default_source: str` — `"live"` | `"poll"`
- `tracker_api_base_url: str` — base URL of the webhost, e.g. `https://archipelago.gg`
- `tracker_uuid: str` — the tracker UUID of the room to watch (poll mode)
- `player_files_path` — as in UT, for the live/YAML fallback mode
- `poll_interval_seconds: int` — default e.g. 30

### 4.3 Data source abstraction

Shared interface so the logic engine and UI stay independent of the data source:

```python
@dataclass
class SlotSnapshot:
    slot_id: int
    slot_name: str
    game: str
    total_locations: int
    checked_locations: set[int]
    received_items: list[NetworkItem]   # from NetUtils
    slot_data: dict | None
    source: Literal["live", "poll"]

class DataSource(Protocol):
    def get_snapshot(self, slot_id: int) -> SlotSnapshot: ...
    def get_available_slots(self) -> list[tuple[int, str, str]]: ...  # (id, name, game)
```

**PollSource** (implement first — no password needed, easiest starting point):

1. `GET {base}/api/static_tracker/{uuid}` → `player_game`, `player_locations_total`, `datapackage`
2. `GET {base}/api/tracker/{uuid}` → `player_items_received`, `player_checks_done`
3. `GET {base}/api/slot_data_tracker/{uuid}` → `slot_data` per player
4. Merge results per `player` ID into one `SlotSnapshot`.

**LiveSource** (later, optional): a dedicated `CommonContext` per configured slot (analogous to
`TrackerGameContext`), connecting with the slot name (+ password if set), collecting
`ReceivedItems`/`RoomUpdate` packets push-based instead of polling.

### 4.4 Per-game compatibility check

Don't hard-code this — check the installed apworld at runtime:

```python
from worlds.AutoWorld import AutoWorldRegister

def check_compatibility(game: str) -> Literal["slot_data", "yaml_required", "unknown_game"]:
    world_cls = AutoWorldRegister.world_types.get(game)
    if world_cls is None:
        return "unknown_game"
    has_hook = callable(getattr(world_cls, "interpret_slot_data", None))
    return "slot_data" if has_hook else "yaml_required"
```

The result feeds directly into `SlotLogicResult.compatibility` and controls which numbers the UI is
allowed to show (see 4.7 — with `yaml_required` and no YAML available, logic numbers are hidden, not
guessed).

### 4.5 Per-slot logic engine (reusing TrackerCore)

Keep one **independent** `TrackerCore` instance per slot (no shared state between slots):

```python
@dataclass
class SlotLogicResult:
    slot_id: int
    compatibility: Literal["slot_data", "yaml_required", "unknown_game"]
    total_locations: int
    checked: int
    in_logic_open: int
    out_of_logic_open: int | None       # None = game does not support glitches_item_name
    no_progression_needed: bool
    error: str | None = None

def compute_slot_logic(snapshot: SlotSnapshot, core: "TrackerCore") -> SlotLogicResult:
    compat = check_compatibility(snapshot.game)
    if compat == "yaml_required" and snapshot.slot_data is None:
        return SlotLogicResult(snapshot.slot_id, compat, snapshot.total_locations,
                                len(snapshot.checked_locations), 0, None, False,
                                error="YAML required, logic computation not possible")

    core.set_slot_params(snapshot.game, snapshot.slot_id, snapshot.slot_name, team=0)
    core.regen_slots(AutoWorldRegister.world_types[snapshot.game], snapshot.slot_data)
    core.set_missing_locations(set(range(snapshot.total_locations)) - snapshot.checked_locations)
    core.set_items_received(snapshot.received_items)

    state: CurrentTrackerState = core.updateTracker()

    # "no progression needed": check reachability with an empty state
    empty_reachable = _reachable_with_empty_state(core)
    no_progression = len(empty_reachable) >= snapshot.total_locations

    return SlotLogicResult(
        slot_id=snapshot.slot_id,
        compatibility=compat,
        total_locations=snapshot.total_locations,
        checked=len(snapshot.checked_locations),
        in_logic_open=len(state.in_logic_locations),
        out_of_logic_open=len(state.glitched_locations) if _supports_glitches(snapshot.game) else None,
        no_progression_needed=no_progression,
    )
```

Implementation notes:

- `_supports_glitches(game)` checks `getattr(AutoWorldRegister.world_types[game], "glitches_item_name", "")`.
- `_reachable_with_empty_state` builds a second `CollectionState` with no received items (only
  starting/precollected items) and calls `get_reachable_locations` — the exact same method
  `TrackerCore.updateTracker` uses, just with an empty item set. Implement this as a small helper
  next to `TrackerCore` rather than modifying `updateTracker` itself.
- Every `TMain` run is a mini generation pass — parallelize across slots (thread or process pool)
  since it's CPU-bound.

### 4.6 Aggregating across the slot list

```python
@dataclass
class DashboardData:
    generated_at: datetime
    slots: list[SlotLogicResult]

    @property
    def total_open(self) -> int: ...
    @property
    def total_in_logic(self) -> int: ...
    @property
    def restricted_count(self) -> int:  # compatibility == "yaml_required" with no data
        ...
```

Pure aggregation logic, no network/logic dependency — easy to test in isolation.

### 4.7 UI / output

Reference: the dashboard mockup shown in chat. Core elements:

- Header: title, "last updated", game filter, refresh button
- 4 metric cards: slots watched, total open checks, of which in logic, restricted slots
- List (bordered rows, no cards) — per slot:
  - Name + game, data-source badge on the right (`Live` / `Slot data` / `Yaml required`)
  - Progress bar `checked / total`
  - Green pill "In logic N", amber pill "Out of logic N" (or a gray "n/a" pill if
    `out_of_logic_open is None`)
  - if `no_progression_needed`: replace the logic pills with a single "No progression needed" pill
  - if `error` is set: a gray pill with the error text instead of numbers

The UI technology is decoupled from the backend/logic — a CLI table, a Kivy tab, or a web dashboard
can all be built from the same `DashboardData` object.

## 5. Implementation steps (order for an AI agent)

1. Create a new directory `worlds/multi_slot_tracker/`, `__init__.py` with `MultiSlotTrackerWorld`
   + component registration (section 4.1). Add the dependency check on `worlds.tracker`.
2. `Settings.py`: settings class per 4.2.
3. `DataSource.py`: `SlotSnapshot` dataclass + `PollSource`, which fetches the three webhost endpoints
   and merges them into snapshots. Unit-test with mocked HTTP responses (reproduce example payloads
   from section 2).
4. `Compatibility.py`: `check_compatibility()` + `_supports_glitches()` (section 4.4).
5. `LogicEngine.py`: `SlotLogicResult`, `compute_slot_logic()`, `_reachable_with_empty_state()`
   (section 4.5). Verify against 2-3 real test slots covering different compatibility tiers (see
   section 7, "Test setup").
6. `Aggregator.py`: `DashboardData` (section 4.6).
7. `Client.py`: `launch_client` entry point, wires Settings → DataSource → LogicEngine (parallel per
   slot) → Aggregator → UI, with a polling loop per `poll_interval_seconds`.
8. UI last: start with a plain table output (stdout/CLI), only implement the dashboard look from
   section 4.7 afterward.

Each step should be independently testable before the next one begins — especially steps 3-5, since
that's where the actual domain complexity lives.

## 6. Known limitations & edge cases

- Without a YAML **and** without `interpret_slot_data` support from the world: no logic computation
  is possible, only raw item/check counts. This must be clearly visible as a limitation in the UI, not
  silently displayed as "0 in logic".
- `out_of_logic_open` is only available for games with `glitches_item_name` support — there is no
  generic "ignore all logic" mode in UT. If that's desired, a separate second traversal would need to
  be written that skips `access_rule` and only checks entrance connectivity (not part of this
  implementation plan; to be decided separately).
- `slot_data` can be incomplete if the world implementation didn't include all generation-relevant
  values in `fill_slot_data()` — even for "supported" games, deviation from the real logic is
  theoretically possible.
- Each `TrackerCore`/`TMain` instance holds its own, non-trivially-sized state — watch memory/CPU
  usage when watching many slots at once.
- Fairness/privacy: anyone reading other players' progress should have their consent, even though
  technically only the public tracker API is used.

## 7. Test setup (recommendation)

Set up a local test room with at least three slots covering one compatibility tier each:

1. A game from the `interpret_slot_data` list (e.g. Stardew Valley or Timespinner) → poll mode should
   return complete logic numbers.
2. A game without that support → poll mode should cleanly report `yaml_required` instead of faking
   logic numbers.
3. Optional: a game with `glitches_item_name` (if present in the repo) → `out_of_logic_open` should
   return a value > 0 once a corresponding "glitch" item is added for testing.

## 8. References (files in the cloned `tracker` branch)

- `worlds/tracker/__init__.py` — registration pattern for a custom apworld
- `worlds/tracker/TrackerCore.py` — `TMain`, `updateTracker`, `CurrentTrackerState`, `regen_slots`
- `worlds/tracker/docs/apworld-integration.md` — official docs on `interpret_slot_data`,
  `fill_slot_data`, `ut_can_gen_without_yaml`, `re_gen_passthrough`
- `WebHostLib/api/tracker.py` — webhost JSON endpoints for poll mode
- `MultiServer.py` (`_non_game_messages`) — evidence that the `"Tracker"` tag grants no cross-slot
  visibility

## 9. Open decisions (for the developer)

- Should `LiveSource` be built from the start, or is `PollSource` enough for a first version?
- Should the UI be its own Kivy tab in the Archipelago launcher, or a standalone web dashboard
  (easier to iterate on, but no native launcher entry)?
- How should games that `AutoWorldRegister` doesn't know about be handled (e.g. apworld not installed
  locally)? Suggestion: mark the slot in the UI as "apworld not installed" instead of dropping it.
