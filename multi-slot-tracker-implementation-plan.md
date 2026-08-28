# Implementation Plan — Multi Slot Tracker apworld

Companion to [archipelago-multi-slot-tracker-design.md](archipelago-multi-slot-tracker-design.md) and
`multi-slot-tracker-mockup.png`. This file tracks concrete implementation decisions and progress so
work can resume after a context reset without re-deriving anything below.

**Do not commit.** Work stays local until the user asks otherwise.

## Target location

`worlds/multi_slot_tracker/` (package name `multi_slot_tracker`, matches `game = "Multi Slot Tracker"`).

## Key facts verified in source (differ from the design doc's pseudocode in §4.5)

Correction after closer reading: `set_slot_params`, `set_missing_locations`, `set_items_received`,
`regen_slots` **do exist** on `TrackerCore` (an earlier grep pass mistakenly searched the wrong
file and said otherwise — ignore that). What the design doc's §4.5 snippet *doesn't* show is the
one extra call that makes yaml-less bootstrap actually safe: `core.initalize_tracker_core(world_cls,
slot_data)`. Calling `regen_slots(world_cls, ...)` directly (passing the class, not an instance) is
only correct when `interpret_slot_data` is a `@staticmethod`/`@classmethod` — for the older
instance-method style it would bind `slot_data` as `self`. `initalize_tracker_core` already contains
the correct branching (incl. the `write_empty_yaml` + tempdir handling) and is the same entry point
`TrackerClient` itself calls after a live connection, so `LogicEngine.compute_slot_logic` calls that
directly instead of re-deriving the branching. See `LogicEngine.py` for the actual call. Real API
(`worlds/tracker/TrackerCore.py`):

- `TrackerCore(logger, print_list, print_count)` — construct one instance.
- Set directly as attributes before running: `self.game`, `self.slot` (int), `self.slot_name`,
  `self.team`, `self.player_id` (set to `1`, since UT always builds a synthetic single-player world
  with `player_ids={1}`), `self.missing_locations: set[int]`, `self.ignored_locations: set[int]`,
  `self.tracker_items_received: list[NetworkItem]`, `self.re_gen_passthrough` (dict to hand to
  `multiworld.re_gen_passthrough`, keyed by game name — see `apworld-integration.md`).
- `core.run_generator(slot_data: dict | None, override_yaml_path: str | None, super_override_yaml_path: str | None)`
  → does the full `mystery_argparse()` + `GMain()` + `TMain()` pipeline and sets `core.multiworld`.
  This is the entry point to call, not `TMain` directly (`TMain` is a lower-level helper it calls
  internally). It **always goes through `mystery_argparse`/`Generate.main`**, i.e. it still wants a
  player-files directory even in `slot_data`-driven mode (a *dummy*/generated YAML dir is required —
  see open question below); only the actual logic then comes from `re_gen_passthrough`.
- `core.updateTracker() -> CurrentTrackerState` — namedtuple with `in_logic_locations: list[str]`
  (names, not ids!), `glitched_locations: list[str]` (names), `state`, `glitches_state`, etc. Location
  **names**, not addresses — need `location_id_to_name`/`location_name_to_id` from
  `core.multiworld.worlds[1]` to translate against the webhost's numeric IDs from
  `player_checks_done`/`total_locations`.
- Per-slot caching: `TrackerCore.cached_multiworlds` / `cached_slot_data` are **class-level** lists
  shared across all instances — fine for our "regenerate on slot_data change" use case, but be aware
  it's not per-instance.
- `glitches_item_name` support: read via
  `getattr(AutoWorldRegister.world_types[game], "glitches_item_name", "")`; `updateTracker()` already
  computes `glitched_locations` for us when that attribute exists — no extra pass needed (§4.5's
  `_reachable_with_empty_state` helper is still needed separately for "no progression needed", see
  below).
- "No progression needed": not something `updateTracker` gives us directly. Plan: run a **second**
  `CollectionState` sweep with only `multiworld.state` (precollected/start inventory, no received
  items) via the same `get_reachable_locations` call UT itself uses. Implement as a small standalone
  helper in `LogicEngine.py`, do not touch `TrackerCore`/`updateTracker`.

## Webhost API shapes verified (`WebHostLib/api/tracker.py`, `WebHostLib/tracker.py`)

- `NetworkItem` = `NamedTuple(item: int, location: int, player: int, flags: int = 0)` (from `NetUtils`).
- `GET /api/static_tracker/<uuid>` → `{groups, datapackage, player_locations_total: [{team,player,total_locations}], player_game: [{team,player,game}]}`.
- `GET /api/tracker/<uuid>` → `{aliases, player_items_received: [{team,player,items: NetworkItem[]}], player_checks_done: [{team,player,locations:[id]}], total_checks_done, hints, activity_timers, connection_timers, player_status}`.
- `GET /api/slot_data_tracker/<uuid>` → `[{player, slot_data}]` (no team key; per docstring this is rare/heavier, fetch lazily/opt-in per slot).
- All three are room-wide, no per-slot password, keyed by the room's public tracker UUID. Matches
  design doc §2 exactly.

## Decisions made to unblock implementation (design doc §9 "open decisions")

1. **PollSource first, LiveSource stubbed.** Implement `PollSource` fully; `LiveSource` gets a class
   skeleton that raises `NotImplementedError` so the `DataSource` interface/call sites are stable but
   nothing pretends to work. Rationale: doc's own recommendation, and poll mode alone already exercises
   the full compatibility/logic/aggregation stack.
2. **UI: superseded, see 2026-08-28 update below.** Originally built as a standalone local web
   dashboard (plain HTML/CSS + stdlib `http.server`), reasoning it'd be "easier to iterate on" per
   the doc's own §9 framing of the open question. The user corrected this: they want an actual Kivy
   window, matching how Universal Tracker itself presents (`worlds/tracker/TrackerClient.py` +
   `TrackerKivy.py`), not a browser tab. `Dashboard.py` (the http.server module) was deleted;
   `KivyUI.py` replaces it. See the 2026-08-28 status entry for the concrete shape of that pivot.
3. **Unknown/not-installed apworld** → compatibility tier `"unknown_game"`, slot rendered with an
   "apworld not installed" pill, counted in `restricted_count`, never dropped from the list (per doc's
   own suggestion).
4. **Player-files / dummy YAML requirement for `run_generator`**: for `slot_data` tier games, still need
   *some* YAML on disk for `mystery_argparse` to parse successfully (game name + `Multi Slot Tracker`-side
   generated options won't matter because `re_gen_passthrough` overrides them). Reuse UT's own approach:
   `TrackerCore.write_empty_yaml` already exists for exactly this (yamlless flow) — call it instead of
   inventing our own.

## File-by-file plan (package `worlds/multi_slot_tracker/`)

- [ ] `__init__.py` — `MultiSlotTrackerWorld(World)`, `game = "Multi Slot Tracker"`, `hidden = True`,
      empty `item_name_to_id`/`location_name_to_id`, settings wiring, dependency check
      (`import worlds.tracker.TrackerCore` in a try/except → clear error), `launch_client`, Launcher
      `Component` registration. Mirrors `worlds/tracker/__init__.py` structure.
- [ ] `Settings.py` — `MultiSlotTrackerSettings(Group)` per design §4.2: `default_source`,
      `tracker_api_base_url`, `tracker_uuid`, `player_files_path`, `poll_interval_seconds`,
      `dashboard_port`.
- [ ] `DataSource.py` — `SlotSnapshot` dataclass, `DataSource` Protocol, `PollSource` (uses stdlib
      `urllib.request` + `json`, no new dependency), `LiveSource` stub.
- [ ] `Compatibility.py` — `check_compatibility()`, `supports_glitches()` per design §4.4.
- [ ] `LogicEngine.py` — `SlotLogicResult` dataclass, `compute_slot_logic(snapshot, core_factory)`
      using the **real** TrackerCore API documented above, `_reachable_with_empty_state` helper.
- [ ] `Aggregator.py` — `DashboardData` per design §4.6.
- [ ] `Client.py` — `launch(*args)` entry point: load settings → poll loop → per-slot
      `compute_slot_logic` (thread pool, since each is CPU-bound and independent) → `DashboardData` →
      feed to the web dashboard server; opens browser tab.
- [ ] `Dashboard.py` (+ template) — minimal `http.server`-based server rendering the mockup layout
      (header, 4 metric cards, bordered slot rows, pills, progress bars) from the latest
      `DashboardData`; polls/refreshes client-side via a small JS fetch loop against a local JSON
      endpoint.
- [ ] `docs/README.md` in the package — short usage note (settings, requires `worlds/tracker`
      installed), mirrors doc conventions in the repo.

Build order follows design doc §5 (1→8); each step kept independently sane before moving to the next.
No tests framework wired in yet — flag to the user once core logic (`LogicEngine`) is in place in case
they want `test/` coverage added (repo has a `test/` convention under other `worlds/*`).

## Status (2026-08-28)

All files below have landed and are held **locally, uncommitted** per the user's instruction.

- [x] `__init__.py`
- [x] `Settings.py`
- [x] `DataSource.py` (PollSource implemented; LiveSource is a `NotImplementedError` stub as decided)
- [x] `Compatibility.py`
- [x] `LogicEngine.py`
- [x] `Aggregator.py`
- [x] `Client.py`
- [x] `Dashboard.py` (stdlib `http.server`, no Flask dependency — matches decision 2)
- [x] `docs/README.md`

Verified so far (see shell history in this session for exact commands):
- `ast.parse` on every new file.
- `import worlds.multi_slot_tracker` inside the real repo venv succeeds; `MultiSlotTrackerWorld` shows
  up in `AutoWorldRegister.world_types`.
- `Compatibility.check_compatibility()` cross-checked against the real installed registry: `Stardew
  Valley`/`Timespinner`/`TUNIC` → `slot_data`, `A Link to the Past`/`Ocarina of Time` → `yaml_required`,
  a made-up game name → `unknown_game`. Matches the design doc's §2 list exactly.
- `Dashboard`'s HTTP server (`/`, `/api/data`, `/api/refresh`) smoke-tested standalone with hand-built
  `SlotLogicResult`s reproducing the mockup's 5 rows; page text read back via the browser tool matches
  the mockup's layout and pill/badge wording 1:1 (after shortening the yaml_required/unknown_game
  error strings to "Logic unavailable"/"Apworld not installed" to match the mockup's pill text —
  the design doc's own §4.5 pseudocode string was more verbose, mockup wins since it's the actual UI
  spec).

**Update 2026-08-28: tested end-to-end against a real room.** User-provided room:
`https://archipelago.gg/room/RZ1KT7KtSQiGq2K2KXL6Qw` (connect string `archipelago.gg:50753`, tracker
UUID `dSSPUwQnT9GVHxqCLgPs-g`), filtered to the 33 slots whose name starts with "kaba". Ran the full
`PollSource` → `compute_slot_logic` → `Aggregator` → `Dashboard` pipeline against real data (not
synthetic). Result distribution across the 33 real slots: 1 `slot_data` (Timespinner, full logic
numbers computed), 7 `yaml_required`, 25 `unknown_game` (third-party apworlds not installed in this
checkout — expected, not a bug). Full per-slot output and the dashboard page text are in this
session's transcript. This surfaced and fixed four real bugs, all in files we wrote (not in
`worlds/tracker`, which was only ever driven through its public API):

1. **`PollSource` slot names were almost always "Player N"**: `/api/tracker`'s `alias` field is only
   set if a player explicitly ran `/alias` (verified against `WebHostLib/tracker.py:
   get_player_alias`, near-never true in practice; only 1 of 33 real Kaba slots had one, and it
   didn't match the connection name). Fixed by adding `room_uuid` support to `PollSource`, using the
   public, no-password `/api/room_status/<room_uuid>` endpoint (`get_players()` in
   `WebHostLib/api/__init__.py`) for real slot names, which also conveniently echoes the room's
   `tracker_uuid` back so `room_uuid` alone is enough to bootstrap everything. Added
   `tracker_room_uuid` to `Settings.py`/`Client.py` accordingly; `docs/README.md` updated.
2. **`compute_slot_logic` built `missing_locations` as `set(range(total_locations))`**: wrong -- a
   direct copy of the design doc's own §4.5 sketch, which was itself wrong. Location addresses are
   arbitrary per-game datapackage IDs (verified: real Timespinner IDs are ~1337000+, nowhere near
   `0..720`), not a small contiguous range. Fixed to use the actually-regenerated world's own
   `location_id_to_name` keys as the address universe, checked against real data (100% overlap
   between the webhost's `checked_locations` IDs and this set, confirming correctness).
3. **Missing `Players` folder crashed generation entirely**: `TrackerCore.run_generator()` ->
   `_set_host_settings()` unconditionally resolves `TrackerSettings.player_files_path` even on the
   yaml-less path where the value is immediately overridden -- and a fresh checkout doesn't ship that
   folder. Fixed with `_ensure_ut_player_folder()`, which reads the raw configured path via
   `object.__getattribute__` (bypassing `settings.Group`'s validating `__getattribute__`, so reading
   it doesn't itself trigger the same error) and creates it if missing.
4. **`AttributeError`/`KeyError` instead of a clean error for "needs a real YAML" slots**: confirmed
   live with `Mega Man 2` (has `interpret_slot_data`, but not `ut_can_gen_without_yaml`, exactly the
   edge case flagged in decision 4/docs/README.md). `initalize_tracker_core`'s early-return path for
   this case calls `add_log_line`/`log_all_to_tab` before `run_generator` ever sets
   `sorting_priorities`/`output_format`/etc., raising instead of returning `multiworld = None`. Fixed
   with `_apply_default_host_settings()`, pre-seeding UT's own shipped defaults so that path degrades
   to the clean, already-handled `core.multiworld is None` case.

No automated test suite was added yet (repo convention is per-world `test/`); flag to the user before
adding one. `LiveSource` remains unimplemented/untested (decision 1, unchanged).

Known deviations from the design doc worth telling the user about:
- `SlotLogicResult` carries `slot_name`/`game`/`source` in addition to the doc's §4.5 fields — needed
  by the UI to render the mockup's per-row name/game/badge, the doc's own dataclass sketch omitted them.

## Update 2026-08-28 (later): UI pivot to Kivy, per direct user correction

The user corrected decision 2 above: they want an actual standalone Kivy/KivyMD window (like
`worlds/tracker/TrackerClient.py`'s), not a browser tab. `Dashboard.py` was deleted. New file
`KivyUI.py` holds the window (`MultiSlotTrackerApp(kvui.ThemedApp)`); `Client.py` rewritten to drive
it instead of an HTTP server. Not a `GameManager` subclass -- that class assumes one live
`CommonContext` server connection (connect bar, hints tab, command processor), which doesn't fit a
tool that polls *other* players' slots. All widget colors come from `theme_cls`'s M3 color roles
(`tertiaryContainerColor` etc, see `kivymd/dynamic_color.py`) rather than hardcoded RGBA, per the
user's explicit ask to reuse Archipelago's existing Kivy theme config (`ThemedApp.set_colors()` /
`data/client.kv`) instead of inventing a separate palette.

Iterated live against the real 193-slot test room, catching real bugs a screenshot-free build would
have missed (screenshotting the actual window via a PowerShell `PrintWindow` capture + the `Read`
tool, since there's no way to view a native desktop window otherwise):

1. **All pills/badges rendered stacked/overlapping across rows.** Root cause: `head`/`body`
   sub-layouts and the `SlotRow` itself used hand-guessed fixed `dp(...)` heights that didn't match
   real content size (pill height, wrapped text, etc.), so `MDBoxLayout` positioned later rows using
   stale/wrong cumulative heights. Fixed by switching essentially everything to KivyMD's
   `adaptive_height`/`adaptive_size` (`kivymd/uix/__init__.py:MDAdaptiveWidget`) instead of guessed
   fixed sizes -- it binds continuously to `texture_size` (for `Label` subclasses) or
   `minimum_height`/`minimum_size` (for layouts), so widgets are always exactly as large as their
   real content and `MDBoxLayout` lays out subsequent siblings correctly.
   - Caveat found along the way: `adaptive_size`/`adaptive_height` are **not** valid on `MDButton`/
     `MDIconButton`/`ToggleButton` (raises `TypeError: Properties ['adaptive_size'] ... may not be
     existing property names` -- they're not `MDAdaptiveWidget` subclasses, and already auto-size to
     their content anyway). Only used on `MDBoxLayout`/`MDLabel` instances.
2. **User: "braucht Trennlinien zwischen den Slots" + "Badges sind ziemlich groß".** Added
   `MDDivider()` between rows in `_render()`; pills rebuilt as a tight adaptive `MDBoxLayout` wrapping
   one adaptive `MDLabel` (`font_style="Label", role="small"`, small padding) instead of the earlier
   oversized `MDCard`-based version.
3. **User: "Select slots... macht nichts, oder erst nach sehr langer Zeit".** Root cause:
   `app.set_available_slots()` was only ever called as a side effect of the *start* of a full poll
   cycle, and a full cycle over 193 slots (one of them -- Crash Team Racing -- dumps hundreds of
   `DEBUG` lines per rule during generation, visibly slowing everything sharing that process's
   logging) can take minutes. Fixed by fetching the slot list (2 lightweight API calls, ~3.4s
   measured for this room) in its own dedicated one-shot background thread right when a room is
   submitted, decoupled entirely from the slow per-slot logic computation. Dialog construction for
   all 193 rows measured at ~0.77s -- not itself the bottleneck.

Additional features added along the way (beyond the original design doc, all requested live):
- **Room field** in the window itself (`KivyUI.py`'s room row + `Client.py`'s `_SourceHolder`/
  `on_room_submitted`), so a room can be loaded/changed without editing `host.yaml` and restarting.
  Accepts a bare UUID or a full room/tracker URL on any host, not just archipelago.gg
  (`DataSource.parse_room_reference`) -- self-hosted webhost instances work too.
- **Slot picker dialog** (`SlotPickerDialog` in `KivyUI.py`): search field + checkbox list + Select/
  Deselect-visible + Apply, gates which slots the poll loop even computes
  (`app.get_active_slot_ids()`, read each cycle in `Client._poll_loop`) -- addresses design doc §6's
  "anyone reading other players' progress should have their consent" by making narrowing down from
  "the whole room" a first-class, easy action instead of an all-or-nothing default.
- **Compatibility-tier filter** (All tiers / Slot data / Yaml required / Unknown game) alongside the
  game filter -- display-only, same mechanism.
- **Combinable condition toggles**: "Has in-logic checks", "Has out-of-logic checks", "Go mode"
  (mapped to `no_progression_needed` -- the closest existing field to what "go mode" means for this
  tool; flag to the user if they meant something narrower, e.g. "goal reachable" rather than "fully
  open with zero progression items"). All AND together with the game/tier filters in `_passes_filters`.
- **Incremental dashboard pushes**: `Client._poll_loop` switched from `pool.map` (blocks until the
  *entire* batch finishes before the window shows anything) to `as_completed`, pushing partial
  `DashboardData` to the window at most once per second as results land -- matters a lot for a
  193-slot room where the full batch can take minutes.

Still open / worth flagging to the user:
- The "Go mode" mapping above is a best guess, not confirmed with the user.
- Crash Team Racing's per-rule `DEBUG` log spam during generation is a third-party apworld issue, not
  ours, but it visibly slows down concurrent polling in the same process; nothing done about it here.
- No automated tests added for `KivyUI.py`/the new `Client.py` flow -- this was all verified by
  actually running the app (smoke tests + real-room runs + window screenshots), not a test suite.

## Update 2026-08-28 (real-YAML path + redraw, round 2): copied real Kaba YAMLs, tested thoroughly

User request: copy the real `Kaba*.yaml` files from their live installation's `Players` folder into
this dev checkout, then test the redraw/flicker behavior against all 33 Kaba slots in the real room
with those YAMLs present. User then stepped away and asked for thorough autonomous testing until the
behavior was correct. Found and fixed several more real issues, all verified live:

1. **`build_yaml_launch_core()` regenerates from every YAML in the folder, not just selected
   slots.** Confirmed by watching real log output: `run_generator(None, None)` builds one joint
   multiworld from literally every YAML file present in `player_files_path`, regardless of which
   slots the user selected in the picker. With 37 real YAMLs copied in, this is a genuinely
   expensive operation (tens of seconds), and needing 2-3 exclusion rounds (see below) multiplies
   that. This explains why early observations during this round looked like "only one slot ever
   updates" -- it wasn't a bug, later screenshots (after waiting long enough) showed all 33 slots
   populated correctly. Documented here so a future reader doesn't re-diagnose the same non-bug.
2. **Cross-cycle caching for known-bad YAMLs.** The exclusion-retry loop from the previous round
   worked correctly *within* one `build_yaml_launch_core()` call, but every fresh call (i.e. every
   poll cycle) re-discovered the same persistently-bad files from scratch, paying for a full doomed
   attempt each time before excluding them again. Verified live: `KabaTimespinner.yaml` (world
   version mismatch) and `KabaSpyro2.yaml`/`KabaSpyro3.yaml` (Spyro 2/3's own "gemsanity set to
   full" hard option-validation error, unrelated to the version issue -- a second, structurally
   different failure class that needed generalizing `_find_bad_identifiers` to also match
   `AutoWorld.py`'s "Exception in ... for player N, named X." wrapper text, not just
   `Generate.py`'s "File ... document #N" YAML-parse-time message) kept getting rediscovered every
   cycle. Fixed with `_known_bad_yaml_cache` (module-level, keyed by `player_files_path`): once a
   file is known bad, every subsequent call pre-filters it out before the first attempt. Verified:
   after the fix, the "excluding" log line count stayed flat across multiple full poll cycles
   instead of growing by 2-3 every cycle.
3. **Row order instability caused spurious full-list rebuilds.** Even with the per-row in-place
   update fix from the previous round, `data.slots`'s raw order varies cycle to cycle (Client.py
   builds it from a mix of a parallel pool for yaml-less slots and a sequential loop for
   real-YAML slots, so completion order isn't stable), which made `order != self._last_rendered_order`
   true on nearly every refresh even when every individual slot's data was byte-identical --
   forcing `rows_box.clear_widgets()` + full re-add regardless of the per-row caching. Fixed by
   sorting `slots` by `slot_name.lower()` in `_render()` before comparing/rendering, giving a
   stable, human-friendly (alphabetical) order that only changes when the actual slot *set*
   changes. Verified live: two consecutive successful poll cycles with identical underlying data
   produced pixel-identical row order in back-to-back screenshots.

## Update 2026-08-28 (feature): user-selectable sort

User request: an optional sort, with "Name descending" and "Open checks ascending" specifically
named. Added a "Sort: ..." dropdown to the filter row (`_SORT_OPTIONS` in `KivyUI.py`) with four
options: Name (A-Z) [default, matches the previous hardcoded behavior], Name (Z-A), Open checks
(low-high), Open checks (high-low) -- "open checks" = `total_locations - checked`, not `checked`
itself. `_render()` now always sorts by whichever of these is selected (never falls back to
`data.slots`'s raw, unstable order -- see the update above on why that mattered for the redraw
fix). Verified with a small standalone script exercising all four modes against three synthetic
slots with distinct names and open-check counts -- each produced the expected order.

End-to-end result verified against the real room with real YAMLs: 33/33 selected Kaba slots
computed, 3 correctly reported as restricted (`KabaHK` -- known datapackage/version mismatch
limitation, unrelated to this session's code; `KabaSpyro2`/`KabaSpyro3` -- real "gemsanity" option
errors in their own YAMLs), stable metrics (`Checks open 12511`, `Of which in logic 454`,
`Restricted 3`) reproduced identically across multiple poll cycles, alphabetical row order stable
across cycles, and (per the earlier round's instrumented test, whose underlying mechanism is
unchanged here) zero `rows_box` mutation for polls where nothing actually changed.

## Update 2026-08-28 (later still): startup gating + a real perf bug

Two more rounds of live user feedback while watching the actual running window, both fixed:

1. **"Select slots... macht nichts, oder erst nach sehr langer Zeit"** (does nothing, or only after
   a very long time) -- confirmed: `app.set_available_slots()` was only ever populated as a side
   effect of the *start* of a full poll cycle, and (see #2 below) a full cycle over 193 slots could
   take minutes before that side effect even ran. Fixed two ways:
   - `on_room_submitted` now fetches the slot list in its own dedicated one-shot thread immediately
     when a room is loaded, fully decoupled from the (slow) per-slot computation loop.
   - Per a follow-up request ("die Auswahl der Slots sollte zuerst kommen, bevor sonstige Daten
     abgerufen werden" -- selection should come before other data is fetched): `set_available_slots`
     now auto-opens the picker the first time it's called for a freshly-loaded room, and
     `Client._poll_loop` gates behind a new `slot_selection_ready` event that only gets set once the
     user hits Apply *or* Cancel on that startup picker (Cancel defaults to "watch everything" rather
     than leaving the loop stuck) -- so no computation happens at all until the user has seen and
     confirmed the slot list, addressing the design doc §6 consent point more directly than a
     post-hoc filter would.
2. **The real perf bug, found while verifying #1's timing**: `PollSource.get_snapshot()` and
   `.fetch_slot_data()` each refetch their *entire* room-wide JSON endpoint from scratch, every
   single call. `Client._poll_loop`'s futures-dict comprehension called `source.get_snapshot(slot_id)`
   once per slot **inline, synchronously, in the main poll thread**, before any task could even be
   submitted to the pool -- so for a 193-slot room that meant 193 sequential full-room `/api/tracker`
   fetches (plus, for every slot_data-tier slot, its own full `/api/slot_data_tracker` fetch) had to
   finish before `as_completed()` could start yielding *anything*, regardless of how fast individual
   `compute_slot_logic` calls were once actually running. Measured against the real room:
   `get_snapshot()`-in-a-loop-style fetching would have taken over a minute just for the fetching
   phase; the fix -- `PollSource.get_snapshots(slot_ids)` / `.fetch_all_slot_data()`, each hitting
   their endpoint exactly once and building all requested entries from that single response --
   dropped it to **0.35s and 0.36s respectively for all 193 slots**. `Client._poll_loop` now also
   submits cheap slots (`yaml_required`/`unknown_game`, resolved without touching TrackerCore) before
   `slot_data`-tier ones, so the window shows real data almost immediately even in a large room
   instead of waiting on whichever slots happened to be submitted first. Verified end-to-end: after
   this fix, hitting Apply on the startup picker for all 193 real slots produced live numbers in the
   window within ~11 seconds (screenshot in this session's transcript), versus multiple minutes
   before. This was a real, previously-undetected bug in code from earlier in this session, not
   something the user's feedback merely surfaced by chance -- worth remembering that "loop calling a
   per-item fetch method" is exactly the shape that hides this kind of thing.

## Update 2026-08-28 (final): no persisted connection/slot state, by explicit request

The user: "die App soll sich keine Slotliste oder Verbindungsdaten merken, die Auswahl bzw Eingabe
wird bei jedem Start vorausgesetzt" (the app should not remember any slot list or connection data;
the selection/input is required fresh every start). This directly reversed the earlier
`tracker_uuid`/`tracker_room_uuid` host.yaml settings + auto-load-on-launch behavior added a few
updates back in this same file -- worth noting since it shows that convenience default was never
actually asked for, just something added along the way while wiring up the room field.

Removed entirely:
- `tracker_uuid`/`tracker_room_uuid` from `Settings.py` (kept `tracker_api_base_url` -- that's
  "which webhost", not "which room", so it stays a legitimate default).
- The `initial_room_text` param on `MultiSlotTrackerApp` and the `if initial_room_text:
  on_room_submitted(...)` auto-load call in `Client.launch()`. The Room field's `MDTextField` now
  has no `text=` at all, i.e. starts genuinely empty.
- The now-orphaned `tracker_uuid`/`tracker_room_uuid` lines from the local `host.yaml` (gitignored,
  not that it matters for commits, but kept it consistent with the settings that generate it).

Slot selection was already in-memory-only (`MultiSlotTrackerApp.selected_slot_ids`, never written to
disk), so nothing needed to change there beyond what already existed -- the startup picker gate from
the previous update already enforces "confirm or explicitly skip every time" for slot selection; this
update makes the room identity itself follow the same rule. Verified by relaunching the real app:
Room field renders with only the hint-text placeholder, no data shown, until a room is typed in and
loaded by hand (screenshot in this session's transcript).

## Update 2026-08-28 (even later): row-rebuild perf fix ("App fühlt sich langsam an")

The user: the app feels slow, interaction seems to wait on redraws. Root cause: `_render()`
unconditionally did `rows_box.clear_widgets()` then reconstructed a brand new `SlotRow` (several
KivyMD widgets each, with theming/ripple/state-layer behaviors) for *every* currently-visible slot,
every single time it ran -- and `Client._poll_loop` calls `push_dashboard()` roughly once a second
while a room's slots are still being computed. Kivy is single-threaded: it can't process clicks/
typing while a Clock callback is still running, so ~60-120 consecutive renders at up to ~0.5-0.6s
each (measured, see below) for a 193-slot room adds up to long stretches where input just doesn't
register until the current render finishes.

Fixed with a cache: `MultiSlotTrackerApp._row_widgets`/`_row_data_cache` keep the built `SlotRow` per
slot_id, and `_render()` only reconstructs a row when `SlotLogicResult` (a plain dataclass, so `==`
is a real value comparison) actually changed since the last render -- unchanged rows are just
reparented into `rows_box` in the new order, which is far cheaper than rebuilding their widget tree.
Also stopped rebuilding the game-filter `MDDropdownMenu` (one item per distinct game) on every push
when the game list hasn't actually changed (`_last_game_list` cache).

Measured with a synthetic growing-result-set benchmark mimicking the real incremental-push pattern
(193 slots, mostly-unchanged between calls): per-render cost for calls that only add new rows stayed
in the 0.11-0.6s range (proportional to how many *new* rows actually needed building, which is
unavoidable and correct); once the result set stopped changing, cost dropped to a stable **~0.03s**
per render, down from what full unconditional rebuilds would have cost every single time. Verified
against the real room afterward (screenshot in transcript): loads and updates smoothly with no
observed input lag during active computation.

## Update 2026-08-28: CI packaging workflow

Added [.github/workflows/build-multi-slot-tracker.yml](.github/workflows/build-multi-slot-tracker.yml):
on `release: published` (or manual `workflow_dispatch`), packages `worlds/multi_slot_tracker` into
`multi_slot_tracker.apworld` via Archipelago's own documented, official path -- the "Build APWorlds"
launcher component (`python Launcher.py "Build APWorlds" -- "Multi Slot Tracker" --skip_open_folder`,
see `docs/apworld specification.md`) -- then uploads it both as a workflow artifact and, on a real
release event, as a release asset (`gh release upload ... --clobber`).

Added `worlds/multi_slot_tracker/archipelago.json` (the recommended way to seed a manifest per the
same doc): `game`, `world_version: "0.1.0"`, `minimum_ap_version: "0.6.7"` (latest stable per the
user, not this checkout's own `Utils.__version__` of "0.6.8" which is a newer dev version -- correctly
matches the doc's own guidance to use latest *stable*). No `authors` field set; add one if wanted.

**Found and fixed a real, unrelated upstream bug while testing this**, since it completely blocked
the "Build APWorlds" component regardless of which world was being built:
`worlds/LauncherComponents.py`'s `_build_apworlds()` had `from Launcher import open_folder`, but
`open_folder` is defined in `worlds/LauncherComponents.py` itself, not in `Launcher.py` -- every
invocation crashed with `ImportError: cannot import name 'open_folder' from 'Launcher'`, even with
`--skip_open_folder` (the bad import runs unconditionally, before the flag is even checked).
`git blame` traces it to commit `50f6cf04f6` (not one of this fork's own local commits) -- a genuine
upstream regression, not something introduced by this session's work. Fixed by removing the
redundant/wrong import (the function is already in scope in the same module). Verified: rebuilt the
apworld locally afterward, inspected the resulting zip -- correct folder name
(`multi_slot_tracker/`, already lowercase), `__pycache__` excluded, manifest merged correctly
(`compatible_version`/`version` auto-added on top of our own `archipelago.json` fields). This fix is
an uncommitted local change like everything else in this session (`worlds/LauncherComponents.py`
shows as modified in `git status`) -- flag to the user in case they want to upstream it separately
from the apworld itself.

## Update 2026-08-28 (last): filter/status layout overlap was a stale-process artifact; added missing error banner

**Filter-row overlap ("die filter und statusanzeige sehen nicht gut aus, überlagern sich"):**
turned out to have TWO separate causes stacked on top of each other, only one of which was a real
code bug:

1. **Not a code bug at all**: every one of the 3 previous fix attempts (splitting the filter row in
   two, `style="outlined"`/`elevation=0` on the toggles, explicit `height=dp(40)` on the toggles) was
   screenshotted against a `python.exe` process that had never actually been killed -- `Stop-Process`
   appeared to succeed but a background python instance (PID confirmed via `Get-Process python |
   Select StartTime`, timestamp matched the *original* buggy launch, not any later edit) was still
   serving every "fresh" screenshot. All 4 screenshots (baseline + 3 fix attempts) were pixel-identical
   because none of them were ever looking at the edited code. Confirmed by explicitly checking
   `Get-Process python` returned zero results before relaunching, then diffing process start-time
   against file edit time.
2. **A real bug**, found only after actually looking at freshly-launched code: the error banner added
   in this same update (see below) initially rendered as a huge empty-looking red bar even with no
   error text -- see next section, this is what "filters overlapping the status display" was actually
   showing once the stale-process issue was eliminated. With that fixed, the original 3-fix-attempt
   layout (two filter rows + metrics row) turned out to already be correct -- verified via
   [KivyUI.py](worlds/multi_slot_tracker/KivyUI.py) idle-state screenshot: room row, error banner
   (collapsed), title/header row, both filter rows, and the 4 metrics cards all render with clean
   separation, no overlap.

**Missing error display ("eine fehler-anzeige bei invalider raum-id und/oder verbindungsproblemen
fehlt")**: `Client.py`'s `on_room_submitted`/`fetch_slot_list` and `_poll_loop` caught `SlotFetchError`
(bad room ID, network failure, room gone) and only ever `logger.error(...)`'d it -- nothing surfaced
in the GUI, so a failed load just looked like the app doing nothing. Added an error banner to
[KivyUI.py](worlds/multi_slot_tracker/KivyUI.py): `MultiSlotTrackerApp.show_error(message)` /
`.clear_error()` (both `Clock.schedule_once`-marshaled, safe to call from the background poll
thread like `push_dashboard`), backed by a `MDBoxLayout` (`error_banner`, `errorContainerColor`
role, M3's actual error color pair -- added `"error"` to `_role_colors`) wrapping an `MDLabel`
(`error_label`). Wired into [Client.py](worlds/multi_slot_tracker/Client.py): `fetch_slot_list`'s
`except SlotFetchError` now also calls `app.show_error(...)`, as does `_poll_loop`'s `except
SlotFetchError`/`except Exception` (mid-session connection drops, not just the initial load).
`_submit_room` clears any previous error immediately on a new attempt; `_apply_dashboard` clears it
on any successful push (so a transient error goes away once data starts flowing again).

**Found and fixed a real bug while building the banner itself**: KivyMD's `adaptive_height` on a
`Label` subclass only binds `height` to `texture_size` on *future changes* to it
(`kivymd/uix/__init__.py::on_adaptive_height`) -- since an empty string's `texture_size` is already
`(0, 0)` as the property's own default, that binding never actually fires on an initially-empty
label, leaving `height` stuck at Kivy's generic `Widget` default of `100`. That 100px phantom height
then inflated `error_banner`'s `minimum_height`, rendering as a big empty-looking red bar even with
zero error text -- this is what looked like "the filter/status area overlapping" once the stale
python-process issue above was ruled out. Fixed by explicitly setting `self.error_label.height = 0`
right after construction, before it's added as `error_banner`'s child; all *subsequent* `show_error`/
`clear_error` transitions genuinely change the text (and thus `texture_size` away from and back to
`(0, 0)`), so the normal adaptive binding fires correctly from then on -- no further manual overrides
needed.

**Verified end-to-end** against the same real room (`RZ1KT7KtSQiGq2K2KXL6Qw`, all 33 `Kaba*` slots):
idle state (no overlap, banner fully collapsed) -> invalid room ID submitted (banner shows the
actual fetch error, correctly wrapped/sized, nothing else displaced) -> valid room loaded (banner
clears, slot picker opens) -> Kaba slots selected and applied -> live dashboard with real numbers
(33 slots watched, metrics/filters/rows all cleanly separated, no overlap anywhere).

**Unrelated new observation, not yet investigated**: while testing the above, a recurring
`Player's Yaml not in tracker's list` error appeared in the log for a handful of `yaml_required`
slots each poll cycle (others succeeded normally). This looks like a real YAML-vs-live-room
player-name mismatch in the shared-core matching logic (separate from the invalid/failing-YAML
exclusion-retry logic, which only handles YAMLs that fail to *generate* at all, not ones that
generate fine but don't match up with the room's own player list) -- flagging for a future session,
not fixed here since it wasn't part of what was reported this round and didn't affect the layout.

## Update 2026-08-28 (last): the overlap was real after all -- MetricCard label wrap on window resize

The previous update's conclusion ("the layout was fine, it was just a stale process") turned out to
be incomplete: the user sent a live screenshot crop still showing the toggle row visibly overlapping
the metrics cards, from the exact fresh instance handed to them right after that update. Multiple
verification passes (Python-side `minimum_height`/`pos` introspection, pixel-level probing of two
independent screenshots with `PIL`) all showed a clean ~12dp gap in the idle-state default window
size -- the bug only reproduces once the window is narrower than its default size.

**Root cause**: [KivyUI.py](worlds/multi_slot_tracker/KivyUI.py)'s `MetricCard` labels are plain
`MDLabel`s, and KivyMD's own `<MDLabel>` kv rule binds `text_size: (self.width, None)` by default --
i.e. word-wrap is on unless something overrides it. `MetricCard` itself has `adaptive_height=True`
(so *it* correctly grows to fit wrapped text), but the `metrics` row containing all four cards has a
**fixed** `size_hint_y=None, height=dp(76)`. Kivy does not clip a child to its parent's box, so once
the window narrows enough that a longer title like "Of which in logic" wraps to two lines, the now-
taller card simply renders past the top edge of its fixed-height row, straight into the filter-toggle
row sitting directly above it. Reproduced on demand via `MoveWindow` (resized the live window to
500x639) -- confirmed both the failure at narrow width and that it's absent at the default 816x639.

**Fix**: added `shorten=True, shorten_from="right"` to both of `MetricCard`'s labels (title and
value) -- they now stay single-line and truncate with an ellipsis ("Slots w...") instead of wrapping,
so the card's height no longer depends on window width at all. Verified: no overlap at 500px width
(ellipsis kicks in correctly) or at the default 816px width (full text, unaffected).

## Update 2026-08-28 (last): pivoted the dashboard from Kivy to a browser UI (Vue), after repeated layout bugs

After several rounds of genuine Kivy/KivyMD layout bugs in the same area (rows overlapping neighbors,
a label wrap silently growing a fixed-height row past its container -- see the two updates above),
the user asked to stop fighting Kivy's layout system for this: reduce the Kivy window to a minimal
launcher (one button, one status field with the URL to open) and move the actual dashboard to a
browser UI built with Vue, developed with a real Vite dev setup rather than a vendored single-file
build, with only the *built* JS/CSS ending up inside the packaged .apworld.

**New project layout**:
- **`multi_slot_tracker_webui/`** (repo root, sibling to `worlds/`, NOT part of the apworld package)
  -- a normal Vite + Vue 3 (JS, not TS) project: `npm install`, `npm run dev` for local development
  (proxies `/api/*` to the real Python backend on port 8422 so hot-reload works against live data),
  `npm run build` for production. `vite.config.js` points `build.outDir` directly at
  `../worlds/multi_slot_tracker/webui/dist` -- running the build writes straight into the packaged
  location, no manual copy step. This directory (its `node_modules/`, `src/`, `package.json`, ...)
  is local-only tooling and must never be committed into `worlds/multi_slot_tracker/` or bundled into
  the .apworld -- only `worlds/multi_slot_tracker/webui/dist/`'s built output is.
- **`worlds/multi_slot_tracker/WebServer.py`** (new): `SharedState` (thread-safe, holds available
  slots, selection, per-slot last-known results, error) + a stdlib `http.server.ThreadingHTTPServer`
  serving both the built static files (`webui/dist/`, with an SPA fallback to `index.html` for
  unknown paths) and a small JSON API: `GET /api/state` (the entire frontend-needed snapshot in one
  call), `POST /api/room`, `POST /api/selection`, `POST /api/refresh`. Binds to
  `Settings.webui_port` (new setting, default 8422), falling back to an OS-assigned free port if
  that one's taken.
- **`worlds/multi_slot_tracker/Client.py`** (rewritten): identical poll-loop logic to before, just
  writing into `WebServer.SharedState` instead of calling Kivy app methods (`push_dashboard`/
  `show_error`/`set_available_slots` all became `SharedState` methods with the same names/shapes).
  `launch()` now starts the web server, then a minimal `LauncherApp`.
- **`worlds/multi_slot_tracker/KivyUI.py`** (rewritten, ~70 lines instead of ~600): `LauncherApp`
  now just shows the URL in a read-only field, an "Open in browser" button
  (`webbrowser.open(self.url)`), and auto-opens the browser once on startup. Still built on
  `kvui.ThemedApp` for visual consistency with other Archipelago Kivy clients, per the original
  requirement -- that part didn't change, only the *scope* of what's rendered in Kivy did.
- **Vue app** (`multi_slot_tracker_webui/src/`): `App.vue` (room input, error banner, header, filter
  dropdowns + condition toggle pills, metrics cards, slot list, 1s polling of `/api/state`),
  `components/SlotPicker.vue` (search/select-visible/deselect-visible/apply/cancel), `MetricCard.vue`,
  `SlotRow.vue`, `api.js` (thin fetch wrapper), `style.css` (dark theme, plain CSS custom properties
  rather than a Material/KivyMD-style theme system).

**Verified end-to-end** against the same real room (`RZ1KT7KtSQiGq2K2KXL6Qw`): `npm run build` ->
launched the real `launch_client()` entry point -> Kivy launcher window opened -> browser auto-opened
-> submitted the room -> slot picker auto-opened with real slot names -> applied a filtered
selection -> live dashboard with real numbers, confirmed via both a real browser session and an
automated one hitting the same backend.

**Bugs found and fixed while building this** (all in the new code, not the ported poll-loop logic):

1. **Picker selection silently reset itself ~1s after editing.** `SlotPicker.vue` had a `watch` on
   `[props.available, props.initialSelection]` that re-seeded the local selection on every prop
   change -- but `App.vue` polls `/api/state` every second and each poll produces a brand-new
   `available_slots` array (even with identical content), so the watch fired on every tick and
   quietly reset an in-progress "deselect visible" back to "everything selected" a moment later.
   Reported live as "deselect visible looks like it undoes itself". Fixed by seeding the selection
   once at component creation instead of reactively -- `v-if`-mounting a fresh `SlotPicker` instance
   each time the dialog opens already gives the right "reseed once per opening" semantics without
   needing a watch at all.
2. **Sort label/key mismatch.** The "Open checks" sort was actually meant to sort by *checks in
   logic* (`in_logic_open`), not total open checks (`total_locations - checked`) -- a
   miscommunication from when this sort option was first requested, corrected on user feedback.
   Fixed both the sort function and the dropdown labels ("Checks in logic (low-high/high-low)").
3. **Dashboard flicker every poll cycle -- same root symptom as the old Kivy flicker, resurfaced in
   the new backend.** `_poll_loop` starts a fresh `results = []` at the top of every poll cycle and
   pushes it (throttled) as slots complete one by one; `SharedState.push_dashboard()` was replacing
   its stored dashboard outright with whatever partial list that was, so the frontend -- which polls
   every second -- saw the visible slot list genuinely shrink back down to the first slot or two to
   finish, then grow back out, *every single poll interval*. Reported live as "the window keeps
   fully reloading, briefly down to one row, then the rest comes back". Fixed by keying
   `SharedState`'s storage by `slot_id` (`_results_by_id`) and *merging* each push into it rather
   than replacing wholesale -- a slot not yet recomputed this cycle keeps showing its last known
   values instead of disappearing; `snapshot()` filters that dict down to the currently active
   selection at read time. `set_room_submitted()` (a genuinely new room) clears it, as it should.
   Verified with a dedicated regression test simulating exactly this partial-push pattern across two
   "poll cycles".
4. **Initial slot picker UX**: on user request, changed the very-first (never-confirmed) picker to
   start with nothing checked (rather than everything) and disabled Apply until at least one slot is
   selected -- large rooms otherwise required deselecting ~200 slots by hand. Re-opening the picker
   after a selection was already confirmed still correctly restores that selection (including
   restoring "everything checked" if the user had previously confirmed "all slots" -- resolving the
   backend's `selected_slot_ids: null` == "all" convention into an explicit array happens in
   `App.vue`, not inside `SlotPicker.vue`, so the picker's own contract stays simple).
5. **No feedback when the backend process itself is gone** (as opposed to a backend-*reported*
   error like a bad room id, which already had a banner): added a full-page "Lost connection to
   Multi Slot Tracker" overlay in `App.vue`, shown after 3 consecutive failed `/api/state` polls (a
   couple of misses are tolerated first so one slow request doesn't flash it) and cleared
   automatically once polling succeeds again. Verified live: killed the Python process while a
   browser tab was open against it -- overlay appeared within ~3s; relaunched the process --
   overlay cleared on its own within the next poll, no page reload needed.

**Operationally**: since the backend just serves whatever's on disk in `webui/dist/` on every
request (no caching), a frontend-only change only needs `npm run build` + a browser refresh -- no
Python restart. A backend (`WebServer.py`/`Client.py`) change needs the actual app process
restarted, same as before.

## Update 2026-08-28 (last): webui missing from the real released .apworld -- zip-loaded packages can't use plain pathlib

The user cut an actual GitHub release (`0.1.0`) and ran the real built `.apworld` for the first
time -- and the webui was missing entirely. This is a real bug this whole session's testing never
caught, because every single test so far ran against the **loose dev folder**
(`worlds/multi_slot_tracker/` directly on disk), never the packaged `.apworld` a real user installs.

**Root cause**: a `.apworld` is loaded via `zipimport` straight out of its zip file -- it is never
extracted to a real directory first (`worlds/__init__.py`'s `WorldSource`/`zipimporter` handling).
`WebServer.py`'s static file serving used `_DIST_DIR = Path(__file__).parent / "webui" / "dist"`
with plain `pathlib.Path` reads (`.is_file()`, `.read_bytes()`) -- these only work against a real
filesystem path. For a zip-loaded module, `__file__` points *into* the zip
(`.../multi_slot_tracker.apworld/multi_slot_tracker/WebServer.py`), which plain `pathlib.Path`
cannot read at all; it silently found nothing, so the server's own "webui build not found" fallback
kicked in for every request.

**Fix**: switched to `importlib.resources` (`resources.files(__package__) / "webui" / "dist"`) --
the stdlib-blessed way to read a package's bundled files that works correctly whether the package
sits on disk or inside a zip (backed by `zipfile.Path` for the zip case). The path-traversal guard
in `_serve_static` also had to change: `zipfile.Path` has no `.resolve()`/`.parents` to lean on for
a real-pathlib-style containment check, so it now validates the URL's path segments directly
(rejects `..`, empty segments, backslashes) before ever joining them onto the resources root.

**Verified against the real failure mode**, not just the dev folder: built the actual `.apworld`
locally (`python Launcher.py "Build APWorlds" -- "Multi Slot Tracker"`), then loaded
`WebServer._webui_dist_root()` from *inside* that zip via a real `zipimport` (temporarily moved the
loose `worlds/multi_slot_tracker/` folder fully out of `worlds/` first, so only the zip-imported
copy could register) -- confirmed `index.html` and the hashed JS/CSS assets all read correctly
by byte count, and a made-up missing filename correctly reports `is_file() == False`. Also
downloaded the actual broken `0.1.0` release asset and inspected it directly: the webui files
*were* present in the zip (the CI "Build the browser dashboard" step worked fine) -- confirming this
was purely the runtime read-path bug above, not a packaging/CI problem.

Also removed a stray `worlds/multi_slot_tracker/webui/vendor/vue.global.prod.js` (~167KB) left over
from the earlier abandoned vendored-Vue-without-a-build-step approach, before the user asked for a
real Vite dev setup -- unused dead weight in every build since.

**Not yet done**: the `0.1.0` release's attached asset is still the broken one; a new build needs to
be attached (re-running the workflow against the fixed code would overwrite it via `--clobber` onto
the same release, or a new release/tag works too) -- left to the user, per the standing rule that
versioning is their call, not something to decide unilaterally.

## Update 2026-08-29 (last): "hinted checks in logic" -- new per-slot and aggregate metric

Added a `hinted_in_logic` count per slot: of the open+in-logic checks, how many have a not-yet-found
hint on them (UT's own tracker highlights hinted locations in a distinct color, so this mirrors that
concept here). Data flow, end to end:

- **`DataSource.py`**: `SlotSnapshot` gains `hinted_not_found_locations: set[int]`. The webhost
  tracker API's `hints` field is `[{team, player, hints: [Hint, ...]}, ...]`, one entry per player --
  verified live against the same real room used all session: each `Hint` serializes as a plain JSON
  array in `NetUtils.Hint`'s field order, `[receiving_player, finding_player, location, item, found,
  entrance, item_flags, status]` (no custom encoder involved, tuples/IntEnum just fall out of
  `json.dumps` naturally). Critically, a player's own `hints` entry is **bidirectional**
  (`MultiServer.notify_hints` stores every hint under both the finding player's and the receiving
  player's own key) -- it mixes hints *about that player's own locations* with hints that player
  merely *received* about someone else's. `_snapshot_from` filters on `finding_player == slot_id`
  (index 1) and `not found` (index 4) to get only the former; verified with a dedicated unit test
  covering exactly this found/not-found and finder/receiver distinction.
- **`LogicEngine.py`**: `SlotLogicResult` gains `hinted_in_logic: int`. `updateTracker()`'s
  `in_logic_locations` is a list of location *names*; translated to ids via this slot's own
  regenerated world's `location_name_to_id` (the reverse of `location_id_to_name`, already used
  elsewhere in this function) and intersected with `snapshot.hinted_not_found_locations`.
- **`Aggregator.py`**: `DashboardData.total_hinted_in_logic` (sums across non-errored slots, same
  pattern as `total_in_logic`).
- **`WebServer.py`**: threaded through the JSON payload (`dataclasses.asdict` already carries the
  new per-slot field; `total_hinted_in_logic` added alongside the dashboard's other totals).
- **Frontend**: a new "Hinted in logic N" pill per slot row (only shown when > 0, a new `--hinted-*`
  violet badge color distinct from the existing positive/caution/accent/neutral roles), a "Hinted in
  logic" metric card, and a matching "Has hinted checks" condition filter toggle -- mirrors the
  existing in-logic/out-of-logic pattern exactly.

Verified the parsing logic with a synthetic-data unit test (found/not-found and finder/receiver
filtering); a live end-to-end check against real hinted data on the test room was interrupted by a
port collision with the user's own separate real install (`D:\Archipelago\ArchipelagoLauncher.exe`)
also running on the default 8422 -- rebuilt the .apworld locally instead so the user can verify
directly in that real install.

## Update 2026-08-29 (last): deterministic sequential port fallback, and it uncovered why this session's port collisions kept happening

Per user request: `start_server()` now tries `preferred_port`, then `preferred_port + 1`,
`+ 2`, ... sequentially (up to `_MAX_PORT_ATTEMPTS = 50`) instead of falling back to an OS-assigned
random port -- landing on a predictable port (usually just one above the default) instead of some
arbitrary high one that has to be looked up, which is also what let today's earlier collision with
the user's own real install (`D:\Archipelago`'s `ArchipelagoLauncher.exe`, also on port 8422 by
default) resolve itself immediately once actually implemented.

**Also fixed the reason a naive version of this wouldn't have worked at all**: `http.server.
HTTPServer` sets `allow_reuse_address = True` by default. On Windows, `SO_REUSEADDR` does not mean
"reuse a socket stuck in TIME_WAIT" like it does on Unix -- it means a second, completely unrelated
process can bind the *exact same port* an already-listening server is using, and the `bind()` call
just silently succeeds instead of raising `OSError`. That's what actually caused this session's
confusing "why is `total_hinted_in_logic` `None`" and "two tabs, unpredictable which one responds"
moments earlier today: this dev instance and the user's separate real install had both silently
bound port 8422 at once, with requests routed unpredictably between the two -- not a bug in the
hint feature itself. Added `_StrictPortServer(ThreadingHTTPServer)` with `allow_reuse_address =
False` and used that instead, so a real conflict now correctly raises and the fallback loop actually
triggers.

Verified with two dedicated tests (a plain blocking socket, and -- the scenario that actually
matters -- a *reuse-enabled* `HTTPServer` blocker, matching what any other unrelated process
including this tool's old code would do by default) plus a live run against the user's actual real
install: confirmed via `Get-NetTCPConnection` that the dev instance correctly logged "port 8422 is
in use, trying the next one" and landed cleanly on 8423, both instances independently reachable at
the same time.

Also, per user request: reordered the metrics row so "Hinted in logic" appears right after "Slots
watched" (more prominent, near the top of the summary), and added "Hinted in logic (low-high/
high-low)" to the sort dropdown, mirroring the existing "Checks in logic" sort pattern.

Rebuilt `build/apworlds/multi_slot_tracker.apworld` locally with all of the above for the user to
drop into their real install.
