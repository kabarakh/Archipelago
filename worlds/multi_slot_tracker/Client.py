"""Entry point wired by the Launcher component in __init__.py: Settings -> DataSource -> LogicEngine
(parallel per slot) -> Aggregator -> the Kivy window (KivyUI.py), with a polling loop -- see design
doc section 4.7 / implementation plan file-by-file list. A standalone Kivy window rather than a
GameManager tab: this tool watches many other players' slots via polling, not one live connection of
its own, so the connect-bar/hints/command-processor machinery GameManager assumes doesn't apply
(though the room field in KivyUI.py deliberately echoes that same connect-bar idea, just pointed at
a room/tracker UUID instead of a server+password).
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .Aggregator import DashboardData
from .Compatibility import check_compatibility
from .DataSource import PollSource, SlotFetchError, parse_room_reference
from .LogicEngine import compute_slot_logic

logger = logging.getLogger("MultiSlotTracker")

# Large rooms (a real one tested against had 193 slots) can take well over a minute to fully
# compute -- push partial results to the window as they land instead of blocking on the whole
# batch, throttled so a big room doesn't trigger a full widget-list rebuild on every single
# completion.
_PARTIAL_PUSH_INTERVAL_SECONDS = 1.0


class _SourceHolder:
    """Lets the Kivy thread swap the active PollSource (user typed a new room) while the poll
    thread is reading it concurrently."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._source: PollSource | None = None

    def set(self, source: PollSource) -> None:
        with self._lock:
            self._source = source

    def get(self) -> PollSource | None:
        with self._lock:
            return self._source


def _poll_loop(holder: _SourceHolder, app, refresh_event: threading.Event,
                slot_selection_ready: threading.Event, interval_seconds: float) -> None:
    while True:
        source = holder.get()
        if source is None:
            refresh_event.wait(timeout=1)
            refresh_event.clear()
            continue

        # The slot list itself is already fetched (and the picker shown) by on_room_submitted's
        # own dedicated thread -- don't start the potentially very slow full computation pass
        # until the user has confirmed (or explicitly skipped) narrowing it down, and don't
        # hammer the API refetching the list every second while waiting either.
        if not slot_selection_ready.is_set():
            slot_selection_ready.wait(timeout=1)
            continue

        try:
            all_slots = source.get_available_slots()
            app.set_available_slots(all_slots)
            active_ids = app.get_active_slot_ids()
            slots = all_slots if active_ids is None else [s for s in all_slots if s[0] in active_ids]

            # One batched fetch each, not one-per-slot: get_snapshot()/fetch_slot_data() were each
            # separately refetching the *entire* room's tracker/slot_data JSON from scratch inside
            # the futures dict comprehension below, sequentially, before any task could even be
            # submitted -- for a real 193-slot room that meant no result (and no UI update at all)
            # until all 193 of those blocking calls finished, regardless of how fast individual
            # slots computed once actually submitted. See get_snapshots()/fetch_all_slot_data().
            snapshots = source.get_snapshots([s[0] for s in slots])
            slot_data_by_id = source.fetch_all_slot_data()

            # Cheapest slots first (yaml_required/unknown_game resolve without touching
            # TrackerCore, see LogicEngine.compute_slot_logic) so the window shows *something*
            # almost immediately even in a big room, while the expensive slot_data-tier slots
            # trickle in behind them instead of being interleaved by submission order alone.
            slots = sorted(slots, key=lambda s: check_compatibility(s[2]) == "slot_data")

            results = []
            last_push = 0.0
            with ThreadPoolExecutor(max_workers=min(8, max(1, len(slots)))) as pool:
                futures = {
                    pool.submit(compute_slot_logic, snapshots[slot_id], fetch_slot_data=slot_data_by_id.get): slot_id
                    for slot_id, _name, _game in slots if slot_id in snapshots
                }
                for future in as_completed(futures):
                    try:
                        results.append(future.result())
                    except Exception:
                        logger.exception(f"Multi Slot Tracker: failed to compute slot {futures[future]}")
                    now = time.monotonic()
                    if now - last_push >= _PARTIAL_PUSH_INTERVAL_SECONDS:
                        app.push_dashboard(DashboardData.build(list(results)))
                        last_push = now
            app.push_dashboard(DashboardData.build(results))
        except SlotFetchError as e:
            logger.error(f"Multi Slot Tracker: could not reach tracker room: {e}")
        except Exception:
            logger.exception("Multi Slot Tracker: unexpected error while polling")

        refresh_event.wait(timeout=interval_seconds)
        refresh_event.clear()


def launch(*args) -> None:
    from . import MultiSlotTrackerWorld
    from .KivyUI import MultiSlotTrackerApp

    settings = MultiSlotTrackerWorld.settings
    if settings["default_source"] != "poll":
        logger.error(
            f"Multi Slot Tracker: source '{settings['default_source']}' is not implemented yet; "
            "only 'poll' is available in this version."
        )
        return

    base_url = settings["tracker_api_base_url"]
    holder = _SourceHolder()
    refresh_event = threading.Event()
    slot_selection_ready = threading.Event()

    def on_room_submitted(text: str) -> None:
        resolved_base_url, room_uuid, tracker_uuid = parse_room_reference(text, base_url)
        source = PollSource(base_url=resolved_base_url, tracker_uuid=tracker_uuid, room_uuid=room_uuid)
        holder.set(source)
        slot_selection_ready.clear()

        # Fetching the slot list (2 lightweight API calls) is fast; a full poll cycle computing
        # logic for every slot is not (a real 193-slot room took minutes). Fetch it separately and
        # immediately so the slot picker (which set_available_slots auto-opens for a freshly
        # loaded room) has something to show right away, and so the picker appears *before* any
        # heavy computation starts, not as an afterthought once a full cycle finally gets to it.
        def fetch_slot_list() -> None:
            try:
                app.set_available_slots(source.get_available_slots())
            except SlotFetchError as e:
                logger.error(f"Multi Slot Tracker: could not fetch slot list: {e}")

        threading.Thread(target=fetch_slot_list, daemon=True).start()
        refresh_event.set()

    # Deliberately no persisted/auto-loaded room: connection data (which room, which slots) is
    # never remembered between runs -- the Room field always starts empty and on_room_submitted
    # only ever fires from the user's own input each time the app is started.
    app = MultiSlotTrackerApp(
        on_room_submitted=on_room_submitted,
        on_refresh_requested=refresh_event.set,
        on_startup_selection_confirmed=slot_selection_ready.set,
    )

    poll_thread = threading.Thread(
        target=_poll_loop, args=(holder, app, refresh_event, slot_selection_ready, settings["poll_interval_seconds"]),
        daemon=True,
    )
    poll_thread.start()

    app.run()  # blocks until the window is closed


if __name__ == "__main__":
    launch()
