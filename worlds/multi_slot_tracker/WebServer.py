"""Local HTTP server backing the browser dashboard (see webui-src/ sibling Vite+Vue project at the
repo root, and multi-slot-tracker-implementation-plan.md's 2026-08-28 "pivot to browser UI" entry).

Replaces the previous all-Kivy dashboard: KivyUI.py is now just a tiny launcher window (one button,
one status label showing the URL), and the actual room input / slot picker / filters / metrics /
slot list all live in the Vue app served from webui/dist/ (the built output of the sibling dev
project -- never the dev project's own source/node_modules, which stay out of the packaged
.apworld entirely).

Single shared in-memory `SharedState`, one lock, read by the HTTP handler thread(s) and written by
the poll loop thread in Client.py -- the same producer/consumer shape push_dashboard()/show_error()/
set_available_slots() had on the old Kivy app object, just polled over HTTP instead of pushed via
Clock.schedule_once.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Callable

from .Aggregator import DashboardData

logger = logging.getLogger("MultiSlotTracker")

# A packaged .apworld is loaded via zipimport straight out of its zip file -- it is *never*
# extracted to a real directory on disk first (see worlds/__init__.py's WorldSource/zipimporter
# handling) -- so plain pathlib.Path(__file__).parent / "webui" / "dist" (which only works for the
# loose-folder dev install this was tested against all session) silently found nothing once
# actually released as a real .apworld, reported live as "the webui is missing". importlib.resources
# is the stdlib-blessed way to read a package's bundled files regardless of whether the package
# sits on disk or inside a zip -- resources.files() returns a Traversable backed by zipfile.Path
# for the zip case, which does support real byte reads.
def _webui_dist_root() -> resources.abc.Traversable:
    return resources.files(__package__) / "webui" / "dist"


class _StrictPortServer(ThreadingHTTPServer):
    """http.server.HTTPServer sets allow_reuse_address = True by default. On Windows, SO_REUSEADDR
    does not mean "reuse a socket stuck in TIME_WAIT" like it does on Unix -- it means a *second*,
    completely separate process can bind the exact same port an already-running server is actively
    listening on, and the bind() call simply succeeds instead of raising OSError. That silently
    defeated start_server()'s whole "try the next port if this one's taken" loop below (verified
    live: this session's own dev instance and the user's separate real install both ended up bound
    to the same port at once, with requests routed unpredictably between the two). Disabling it
    makes bind() fail loudly on an actual conflict, as intended."""

    allow_reuse_address = False


class SharedState:
    """Everything the frontend needs to reconstruct its view, plus everything the poll loop needs
    to know what to compute. One lock for the whole thing -- reads/writes are cheap and infrequent
    enough (one poll cycle's worth of data at a time) that finer-grained locking isn't worth it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # Keyed by slot_id, holding each slot's *last successfully computed* result -- not simply
        # overwritten with whatever the current poll cycle's (possibly still-partial) result list
        # is. _poll_loop starts a fresh `results = []` at the top of every cycle and pushes it
        # (throttled) as slots complete one by one; pushing that list to the frontend as-is meant
        # the dashboard visibly shrank back down to just the first slot or two to finish, then grew
        # back out over the following seconds, every single poll interval -- reported live as "the
        # window keeps fully reloading, briefly down to one row, then the rest comes back". Each
        # slot's entry here is only ever replaced by a *newer* result for that same slot_id, so a
        # slot not yet recomputed this cycle keeps showing its last known values instead of
        # disappearing.
        self._results_by_id: dict[int, object] = {}
        self._generated_at = None
        self.available_slots: list[tuple[int, str, str]] = []
        self.selected_slot_ids: set[int] | None = None  # None == "all slots"
        self.selection_confirmed = False  # gates the poll loop, like slot_selection_ready before
        self.room_loaded = False
        self.error: str | None = None

    def set_available_slots(self, slots: list[tuple[int, str, str]]) -> None:
        with self._lock:
            self.available_slots = slots

    def set_room_submitted(self) -> None:
        with self._lock:
            self.room_loaded = True
            self.selection_confirmed = False
            self.selected_slot_ids = None
            self.available_slots = []
            self.error = None
            # a genuinely different room's data must not linger and get shown alongside/instead of
            # the newly-loaded one.
            self._results_by_id = {}
            self._generated_at = None

    def set_selection(self, slot_ids: list[int] | None) -> None:
        with self._lock:
            self.selected_slot_ids = None if slot_ids is None else set(slot_ids)
            self.selection_confirmed = True

    def get_active_slot_ids(self) -> set[int] | None:
        with self._lock:
            return self.selected_slot_ids

    def is_selection_confirmed(self) -> bool:
        with self._lock:
            return self.selection_confirmed

    def push_dashboard(self, data: DashboardData) -> None:
        with self._lock:
            for slot in data.slots:
                self._results_by_id[slot.slot_id] = slot
            self._generated_at = data.generated_at
            self.error = None

    def show_error(self, message: str) -> None:
        with self._lock:
            self.error = message

    def clear_error(self) -> None:
        with self._lock:
            self.error = None

    def snapshot(self) -> dict:
        with self._lock:
            active_ids = self.selected_slot_ids
            # Only ever include slots that are still part of the active selection -- an entry for
            # a slot the user has since deselected stays cached (harmless, and lets it reappear
            # instantly without a fresh compute if they re-select it) but must not show up here.
            slots = [
                s for sid, s in self._results_by_id.items() if active_ids is None or sid in active_ids
            ]
            generated_at = self._generated_at
            payload = {
                "room_loaded": self.room_loaded,
                "selection_confirmed": self.selection_confirmed,
                "available_slots": [
                    {"slot_id": sid, "slot_name": name, "game": game}
                    for sid, name, game in self.available_slots
                ],
                "selected_slot_ids": None if self.selected_slot_ids is None else sorted(self.selected_slot_ids),
                "error": self.error,
                "dashboard": None,
            }
        if generated_at is not None:
            data = DashboardData(generated_at=generated_at, slots=slots)
            payload["dashboard"] = {
                "generated_at": data.generated_at.isoformat(),
                "total_open": data.total_open,
                "total_in_logic": data.total_in_logic,
                "total_hinted_in_logic": data.total_hinted_in_logic,
                "restricted_count": data.restricted_count,
                "slots": [dataclasses.asdict(s) for s in data.slots],
            }
        return payload


def _make_handler(
    state: SharedState,
    on_room_submitted: Callable[[str], None],
    on_selection_changed: Callable[[], None],
    on_refresh_requested: Callable[[], None],
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "MultiSlotTracker/1.0"

        def log_message(self, format: str, *args) -> None:  # noqa: A002 -- matches base signature
            logger.debug("Multi Slot Tracker webui: " + format, *args)

        def _send_json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json_body(self) -> dict:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            return json.loads(raw or b"{}")

        def _serve_static(self) -> None:
            rel = self.path.split("?", 1)[0].lstrip("/") or "index.html"
            # Traversable (zipfile.Path, for a real packaged .apworld) has no .resolve()/.parents
            # to lean on for a "stays inside dist/" containment check the way a real pathlib.Path
            # would -- reject any path-traversal attempt directly on the URL's segments instead.
            segments = rel.split("/")
            if ".." in segments or "" in segments[1:] or "\\" in rel:
                self.send_error(404)
                return
            root = _webui_dist_root()
            candidate = root.joinpath(*segments)
            if not candidate.is_file():
                # SPA fallback: any unknown path (client-side routing) gets index.html
                candidate = root / "index.html"
            if not candidate.is_file():
                self._send_json({"error": "webui build not found -- run `npm run build` in "
                                           "multi_slot_tracker_webui/ first"}, status=500)
                return
            content_type, _ = mimetypes.guess_type(rel)
            data = candidate.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type or "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802 -- BaseHTTPRequestHandler's own naming convention
            if self.path.startswith("/api/state"):
                self._send_json(state.snapshot())
                return
            self._serve_static()

        def do_POST(self) -> None:  # noqa: N802
            try:
                if self.path == "/api/room":
                    body = self._read_json_body()
                    text = str(body.get("text", "")).strip()
                    if not text:
                        self._send_json({"ok": False, "error": "empty room reference"}, status=400)
                        return
                    on_room_submitted(text)
                    self._send_json({"ok": True})
                elif self.path == "/api/selection":
                    body = self._read_json_body()
                    slot_ids = body.get("slot_ids")
                    state.set_selection(slot_ids)
                    on_selection_changed()
                    self._send_json({"ok": True})
                elif self.path == "/api/refresh":
                    on_refresh_requested()
                    self._send_json({"ok": True})
                else:
                    self.send_error(404)
            except Exception:
                logger.exception("Multi Slot Tracker webui: request failed")
                self._send_json({"ok": False, "error": "internal error"}, status=500)

    return Handler


_MAX_PORT_ATTEMPTS = 50


def start_server(
    state: SharedState,
    on_room_submitted: Callable[[str], None],
    on_selection_changed: Callable[[], None],
    on_refresh_requested: Callable[[], None],
    preferred_port: int,
) -> tuple[ThreadingHTTPServer, int]:
    """Binds to preferred_port, or the next one up if that's taken, and the next one after that,
    and so on (the setting is a preference, not a guarantee -- see Settings.py) -- deterministic and
    sequential rather than an OS-assigned random port, so a second instance running alongside a real
    install lands somewhere predictable (preferred_port + 1, usually) instead of some arbitrary high
    port that has to be looked up. Returns the server (already serving in a background thread) and
    the port it actually bound to."""
    handler_cls = _make_handler(state, on_room_submitted, on_selection_changed, on_refresh_requested)
    server = None
    for offset in range(_MAX_PORT_ATTEMPTS):
        candidate_port = preferred_port + offset
        try:
            server = _StrictPortServer(("127.0.0.1", candidate_port), handler_cls)
            break
        except OSError:
            logger.warning(f"Multi Slot Tracker: port {candidate_port} is in use, trying the next one")
    if server is None:
        raise OSError(
            f"Multi Slot Tracker: could not find a free port in "
            f"{preferred_port}-{preferred_port + _MAX_PORT_ATTEMPTS - 1}"
        )
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port
