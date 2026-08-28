# Multi Slot Tracker

Watches a configurable list of Archipelago slots across multiple players/games and reports, per
slot: how many checks are open and in logic, how many are out of logic but technically reachable
(where the game supports it), and whether the slot needs no progression items at all.

This is a `hidden` apworld -- not a playable game. It registers itself in the Archipelago launcher
the same way [Universal Tracker](../../tracker/) does, and **requires UT to be installed**, since it
reuses UT's `TrackerCore` for all logic computation instead of reimplementing it. Launching it opens
a standalone Kivy/KivyMD window (`KivyUI.py`), styled consistently with the rest of Archipelago's
clients (`kvui.ThemedApp`) -- it's its own app rather than a `GameManager` tab, since `GameManager`
assumes a single live server connection (connect bar, hints tab); this tool instead polls many
*other* players' slots at once, which doesn't fit that model.

Design background: see `archipelago-multi-slot-tracker-design.md` and
`multi-slot-tracker-implementation-plan.md` at the repo root.

## Status

First version. Only the **poll** data source is implemented: it reads the webhost's public
per-room tracker API (no slot password needed, works as long as the room has a public tracker
UUID). A **live** (per-slot client connection) source is planned but not implemented yet.

## Privacy: nothing is remembered between runs

The app never persists which room was watched or which slots were selected -- no `host.yaml`
setting, no on-disk cache. Every time it starts:

1. The **Room** field in the window is empty; a room/tracker URL (or bare UUID) must be typed or
   pasted in and submitted (Enter or the Load button) before anything happens.
2. As soon as the slot list for that room is fetched, a **slot picker** (search + checkboxes)
   opens automatically, and the poll loop does not compute anything until it's been confirmed
   (Apply) or explicitly skipped (Cancel, which defaults to watching everything). This is
   deliberate, not just a UX nicety -- anyone reading other players' progress should have their
   consent, and requiring this choice fresh each session is part of that, not a one-time opt-in
   that's then forgotten about.

Both the room and the slot selection only live in memory for the running process; closing the
window and starting it again requires doing both again.

## Settings (`host.yaml`, section `multi_slot_tracker`)

Deliberately minimal -- see above for why room/slot state isn't among them.

- `default_source`: `"poll"` (only supported value right now).
- `tracker_api_base_url`: which webhost to talk to, e.g. `https://archipelago.gg`. This is just the
  server, not a specific room.
- `poll_interval_seconds`: how often to re-poll (default 30).
- `player_files_path`: reserved for a future YAML-based fallback; unused by the poll source.

## Using it

Paste a room URL (`.../room/<uuid>`), a tracker URL (`.../tracker/<uuid>`), or a bare UUID into the
Room field and hit Load/Enter. A room URL is preferred: it's the only way to get each slot's real
name instead of a "Player N" placeholder (the tracker-only API only ever exposes a player-set
`/alias`, which is almost never actually set -- verified against a real room: only 1 of 33 checked
slots had one, and it didn't even match the slot's actual name). Any webhost host works, not just
archipelago.gg, so self-hosted instances are fine too.

Once the slot picker appears, narrow the list down (search by name/game, or Select/Deselect
visible) and hit Apply, or Cancel to watch every slot in the room. The **Select slots...** button
in the header reopens the same picker later to change the selection without reloading the room.
The game filter, tier filter, and the three condition toggles (has in-logic checks / has
out-of-logic checks / go mode) only affect what's currently displayed, not what's computed.

## Compatibility tiers

Per slot, depending on whether the tracked game's apworld defines `interpret_slot_data`:

- **slot_data** -- full logic numbers, reconstructed from the room's `slot_data` without a YAML.
  Even here, if the world only implements the older instance-method style of `interpret_slot_data`
  (not `ut_can_gen_without_yaml`), a real YAML would still be required and the slot is reported with
  an error instead of guessed numbers.
- **yaml_required** -- no `interpret_slot_data` hook at all; only raw checked/total counts are shown.
- **unknown_game** -- the apworld isn't installed locally; slot stays listed, marked accordingly.

"Out of logic but reachable" is only ever shown for games that define `glitches_item_name`; there is
no generic "ignore all logic" mode (not a UT feature, see design doc section 6).
