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

## Update 2026-08-28 (last): CI packaging workflow

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
