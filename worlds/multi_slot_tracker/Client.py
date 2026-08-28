"""Entry point wired by the Launcher component in __init__.py: Settings -> DataSource -> LogicEngine
(parallel per slot) -> Aggregator -> WebServer's SharedState -> the browser UI (webui/), with a
polling loop -- see design doc section 4.7 / implementation plan file-by-file list, and the
2026-08-28 "pivot to browser UI" entry for why this is no longer an all-Kivy dashboard: repeated
Kivy/KivyMD layout bugs (rows overlapping, labels bleeding past their container on resize) made a
native dashboard too fragile to keep maintaining, so the actual UI moved to a small Vue app served
over a local HTTP server; KivyUI.py is now just a launcher window (one button, one status label with
the URL to open) rather than the dashboard itself.
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .Aggregator import DashboardData
from .Compatibility import check_compatibility, supports_yamlless_regen
from .DataSource import PollSource, SlotFetchError, parse_room_reference
from .LogicEngine import build_yaml_launch_core, compute_slot_logic, compute_slot_logic_via_yaml
from .WebServer import SharedState, start_server

logger = logging.getLogger("MultiSlotTracker")

# Large rooms (a real one tested against had 193 slots) can take well over a minute to fully
# compute -- push partial results to shared state as they land instead of blocking on the whole
# batch, throttled so a big room doesn't make the frontend re-render on every single completion.
_PARTIAL_PUSH_INTERVAL_SECONDS = 1.0


class _SourceHolder:
    """Lets the web server's request-handling threads swap the active PollSource (user submitted a
    new room) while the poll thread is reading it concurrently."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._source: PollSource | None = None

    def set(self, source: PollSource) -> None:
        with self._lock:
            self._source = source

    def get(self) -> PollSource | None:
        with self._lock:
            return self._source


def _resolve_player_files_path(settings) -> str:
    """Same defensive dance as LogicEngine's _ensure_ut_player_folder(), but for our own
    player_files_path setting: settings.Group's validating __getattribute__ would otherwise try to
    open a folder-browse dialog (or raise FileNotFoundError headless) the first time this is read,
    if the configured folder doesn't exist yet."""
    raw = object.__getattribute__(settings, "player_files_path")
    path = raw.resolve()
    os.makedirs(path, exist_ok=True)
    return path


def _poll_loop(holder: _SourceHolder, state: SharedState, refresh_event: threading.Event,
                slot_selection_ready: threading.Event, interval_seconds: float, player_files_path: str) -> None:
    while True:
        source = holder.get()
        if source is None:
            refresh_event.wait(timeout=1)
            refresh_event.clear()
            continue

        # The slot list itself is already fetched (and the picker shown, frontend-side) by
        # on_room_submitted's own dedicated thread -- don't start the potentially very slow full
        # computation pass until the user has confirmed (or explicitly skipped) narrowing it down,
        # and don't hammer the API refetching the list every second while waiting either.
        if not slot_selection_ready.is_set():
            slot_selection_ready.wait(timeout=1)
            continue

        try:
            all_slots = source.get_available_slots()
            state.set_available_slots(all_slots)
            active_ids = state.get_active_slot_ids()
            slots = all_slots if active_ids is None else [s for s in all_slots if s[0] in active_ids]

            # One batched fetch each, not one-per-slot: get_snapshot()/fetch_slot_data() were each
            # separately refetching the *entire* room's tracker/slot_data JSON from scratch inside
            # the futures dict comprehension below, sequentially, before any task could even be
            # submitted -- for a real 193-slot room that meant no result (and no UI update at all)
            # until all 193 of those blocking calls finished, regardless of how fast individual
            # slots computed once actually submitted. See get_snapshots()/fetch_all_slot_data().
            snapshots = source.get_snapshots([s[0] for s in slots])
            slot_data_by_id = source.fetch_all_slot_data()

            # Route by the *actual* gate TrackerCore branches on (Compatibility.supports_yamlless_regen,
            # i.e. ut_can_gen_without_yaml) -- not by check_compatibility()'s "slot_data" tier. Verified
            # against a real room: plenty of "slot_data" tier worlds (they define interpret_slot_data)
            # still need a real YAML, exactly like plain "yaml_required" ones; conflating the two was
            # why every such slot used to fail with "world could not be regenerated from slot_data
            # without a YAML" even when the user had the YAML sitting right there in player_files_path.
            yamlless_slots = [s for s in slots if s[0] in snapshots and supports_yamlless_regen(s[2])]
            yaml_slots = [s for s in slots if s[0] in snapshots and not supports_yamlless_regen(s[2])]

            # Cheapest first within the yaml-less group (yaml_required/unknown_game don't apply
            # here; check_compatibility() still distinguishes "slot_data" from "unknown_game" for
            # display) so the frontend shows *something* almost immediately even in a big room.
            yamlless_slots.sort(key=lambda s: check_compatibility(s[2]) == "unknown_game")

            results = []
            last_push = 0.0

            def maybe_push():
                nonlocal last_push
                now = time.monotonic()
                if now - last_push >= _PARTIAL_PUSH_INTERVAL_SECONDS:
                    state.push_dashboard(DashboardData.build(list(results)))
                    last_push = now

            with ThreadPoolExecutor(max_workers=min(8, max(1, len(yamlless_slots) or 1))) as pool:
                futures = {
                    pool.submit(compute_slot_logic, snapshots[slot_id], fetch_slot_data=slot_data_by_id.get): slot_id
                    for slot_id, _name, _game in yamlless_slots
                }
                for future in as_completed(futures):
                    try:
                        results.append(future.result())
                    except Exception:
                        logger.exception(f"Multi Slot Tracker: failed to compute slot {futures[future]}")
                    maybe_push()

            if yaml_slots:
                # One shared TrackerCore for the whole group: generating from every YAML in
                # player_files_path is the expensive part and only needs to happen once per cycle,
                # not once per slot (see build_yaml_launch_core's docstring) -- so this runs
                # sequentially, reusing that one generated multiworld per slot.
                yaml_core = build_yaml_launch_core(player_files_path)
                try:
                    for slot_id, _name, _game in yaml_slots:
                        try:
                            results.append(compute_slot_logic_via_yaml(
                                snapshots[slot_id], yaml_core, fetch_slot_data=slot_data_by_id.get
                            ))
                        except Exception:
                            logger.exception(f"Multi Slot Tracker: failed to compute slot {slot_id}")
                        maybe_push()
                finally:
                    # only set when build_yaml_launch_core had to fall back to a filtered copy of
                    # player_files_path (one or more invalid YAMLs there -- see its docstring); that
                    # copy is ours to clean up, unlike the user's own player_files_path.
                    temp_dir = getattr(yaml_core, "mst_temp_dir", None)
                    if temp_dir:
                        shutil.rmtree(temp_dir, ignore_errors=True)

            state.push_dashboard(DashboardData.build(results))
        except SlotFetchError as e:
            logger.error(f"Multi Slot Tracker: could not reach tracker room: {e}")
            state.show_error(f"Lost connection to the tracker room: {e}")
        except Exception as e:
            logger.exception("Multi Slot Tracker: unexpected error while polling")
            state.show_error(f"Unexpected error while updating: {e}")

        refresh_event.wait(timeout=interval_seconds)
        refresh_event.clear()


def launch(*args) -> None:
    from .KivyUI import LauncherApp

    from . import MultiSlotTrackerWorld

    settings = MultiSlotTrackerWorld.settings
    if settings["default_source"] != "poll":
        logger.error(
            f"Multi Slot Tracker: source '{settings['default_source']}' is not implemented yet; "
            "only 'poll' is available in this version."
        )
        return

    base_url = settings["tracker_api_base_url"]
    holder = _SourceHolder()
    state = SharedState()
    refresh_event = threading.Event()
    slot_selection_ready = threading.Event()

    def on_room_submitted(text: str) -> None:
        resolved_base_url, room_uuid, tracker_uuid = parse_room_reference(text, base_url)
        source = PollSource(base_url=resolved_base_url, tracker_uuid=tracker_uuid, room_uuid=room_uuid)
        holder.set(source)
        slot_selection_ready.clear()
        state.set_room_submitted()

        # Fetching the slot list (2 lightweight API calls) is fast; a full poll cycle computing
        # logic for every slot is not (a real 193-slot room took minutes). Fetch it separately and
        # immediately so the slot picker (which the frontend auto-opens once available_slots comes
        # back non-empty for a freshly loaded room) has something to show right away, and so it
        # appears *before* any heavy computation starts, not as an afterthought once a full cycle
        # finally gets to it.
        def fetch_slot_list() -> None:
            try:
                state.set_available_slots(source.get_available_slots())
            except SlotFetchError as e:
                logger.error(f"Multi Slot Tracker: could not fetch slot list: {e}")
                state.show_error(f"Could not load that room: {e}")

        threading.Thread(target=fetch_slot_list, daemon=True).start()
        refresh_event.set()

    def on_selection_changed() -> None:
        slot_selection_ready.set()
        refresh_event.set()

    # Deliberately no persisted/auto-loaded room: connection data (which room, which slots) is
    # never remembered between runs -- the browser UI's Room field always starts empty and
    # on_room_submitted only ever fires from the user's own input each time the app is started.
    server, port = start_server(
        state,
        on_room_submitted=on_room_submitted,
        on_selection_changed=on_selection_changed,
        on_refresh_requested=refresh_event.set,
        preferred_port=settings["webui_port"],
    )
    url = f"http://127.0.0.1:{port}/"

    player_files_path = _resolve_player_files_path(settings)
    poll_thread = threading.Thread(
        target=_poll_loop,
        args=(holder, state, refresh_event, slot_selection_ready, settings["poll_interval_seconds"], player_files_path),
        daemon=True,
    )
    poll_thread.start()

    app = LauncherApp(url=url)
    try:
        app.run()  # blocks until the launcher window is closed
    finally:
        server.shutdown()


if __name__ == "__main__":
    launch()
