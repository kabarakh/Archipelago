# Multi Slot Tracker

Watches a configurable list of Archipelago slots across multiple players/games and reports, per
slot: how many checks are open and in logic, how many are out of logic but technically reachable
(where the game supports it), and whether the slot needs no progression items at all.

This is a `hidden` apworld -- not a playable game. It registers itself in the Archipelago launcher
the same way [Universal Tracker](../../tracker/) does, and **requires UT to be installed**, since it
reuses UT's `TrackerCore` for all logic computation instead of reimplementing it.

Launching it opens a small Kivy/KivyMD launcher window (`KivyUI.py`, styled consistently with the
rest of Archipelago's clients via `kvui.ThemedApp`) whose only job is to start a local HTTP server
and open the actual dashboard in your browser -- the dashboard itself (room input, slot picker,
filters, metrics, slot list) is a small Vue app served from that server, not a Kivy layout. This
replaced an earlier all-Kivy dashboard after repeated KivyMD layout bugs there (rows overlapping
neighbors, a label wrap silently growing a row past its container) made it too fragile to keep
maintaining -- see `multi-slot-tracker-implementation-plan.md`'s "pivoted the dashboard from Kivy to
a browser UI" entry for the full history. If the launcher window is closed, or the browser tab loses
its connection to it, the dashboard shows a clear "Lost connection" overlay rather than silently
freezing.

Design background: see `archipelago-multi-slot-tracker-design.md` and
`multi-slot-tracker-implementation-plan.md` at the repo root.

## Working on the browser dashboard (`multi_slot_tracker_webui/`)

The dashboard's source lives in `multi_slot_tracker_webui/` at the repo root -- a normal Vite + Vue
3 project, *not* part of this apworld package (its `node_modules/`, `src/`, `package.json`, ... must
never end up inside a built `.apworld`, only its **built output** does):

```bash
cd multi_slot_tracker_webui
npm install
npm run dev     # local dev server with hot reload, proxies /api/* to a real backend on :8422
npm run build   # writes straight into worlds/multi_slot_tracker/webui/dist/ -- rebuild after any change
```

`worlds/multi_slot_tracker/webui/dist/` is gitignored (the repo's blanket `dist/` rule) and must be
rebuilt by CI before packaging -- see `.github/workflows/build-multi-slot-tracker.yml`'s "Build the
browser dashboard" step. The Python backend (`WebServer.py`) just serves whatever's on disk there on
every request, so a frontend-only change only needs a rebuild + a browser refresh, no app restart;
changing `WebServer.py`/`Client.py` itself needs the app relaunched, same as any other Python change.

## Status

First version. Only the **poll** data source is implemented: it reads the webhost's public
per-room tracker API (no slot password needed, works as long as the room has a public tracker
UUID). A **live** (per-slot client connection) source was scoped out (see
`multi-slot-tracker-implementation-plan.md`'s "live per-slot connections" entry for the full
feasibility writeup -- what it would take, why one connection can't cover every slot, and the
password/connection-count tradeoffs involved) but a deliberate decision was made not to build it;
see "Known limitation" below for what that means in practice.

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
- `player_files_path`: folder to look for YAMLs in, for slots that need one (`yaml_required` tier
  below) -- same "Players" folder any Archipelago install already has. A slot with no matching YAML
  here just shows raw checked/total counts instead of full logic numbers.
- `webui_port`: local port the browser dashboard is served on (default 8422). If it's already taken,
  the next free port is picked automatically and shown in the launcher window -- this is only the
  preferred/starting port, not a guarantee.

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

- **slot_data** -- full logic numbers, reconstructed from the room's `slot_data` without needing a
  YAML on disk. Even here, if the world only implements the older instance-method style of
  `interpret_slot_data` (not `ut_can_gen_without_yaml`), a real YAML would still be required and the
  slot falls through to the case below instead.
- **yaml_required** -- needs a matching YAML in `player_files_path` to compute full logic numbers at
  all (regenerated the same way a real Universal Tracker client would, from that YAML); without a
  match, only raw checked/total counts are shown, no in-logic/out-of-logic breakdown.
- **unknown_game** -- the apworld isn't installed locally; slot stays listed, marked accordingly.

"Out of logic but reachable" is only ever shown for games that define `glitches_item_name`; there is
no generic "ignore all logic" mode (not a UT feature, see design doc section 6).

## Known limitation: check counts can be slightly off for some games

Both tiers above that compute real logic numbers do it by **regenerating a `World` object** from
either `slot_data` or a YAML -- neither ever downloads the room's actual, already-generated
multiworld. For the vast majority of games this is fine: the region/rule graph a world builds is a
pure function of its options, so regenerating it (even with a fresh random seed, since this tool
never has the room's real one) reliably reproduces the same logic structure the real room has.

**A minority of games make randomized decisions *during generation itself* that affect which
locations exist at all** -- not just where items land, which locations exist in the first place.
Kingdom Hearts 2 is a concrete example found while investigating a real discrepancy (see
`multi-slot-tracker-implementation-plan.md`'s "why UT live-connect shows a different number" entry
for the full trace): it randomly picks a set of "bounty" locations during its own `generate_early()`
and removes each one from the normal location pool. That random pick depends on the room's true
original seed and, in a shared multiworld, on the exact order every other player's world generates
in -- neither of which this tool has access to. Regenerating with a fresh seed can therefore end up
with a *different* total location count (and different reachability) than the real room, for these
specific games only. This isn't a bug in this tool's counting logic; it's a structural limit of
regenerating from options/YAML alone rather than reading the real generated output.

A live, per-slot connection to the actual running server *would* sidestep this (it can merge the
real, server-reported location set onto the regenerated world instead of trusting the regenerated
world's own guess) -- this was scoped out in detail but deliberately not built; see the
implementation plan for why. If a slot's numbers look off and the game in question is known to
randomize location existence at generation time (not just item placement), this is almost certainly
why -- there isn't a workaround short of that live-connection feature.
