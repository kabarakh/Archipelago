"""Data source abstraction: turns "somewhere data about a slot lives" into a uniform SlotSnapshot,
so LogicEngine/Aggregator/UI never need to know whether a slot came from HTTP polling or a live
client connection. See design doc section 4.3.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Literal, Protocol
from urllib.parse import urlsplit

from NetUtils import NetworkItem

_ROOM_OR_TRACKER_PATH = re.compile(r"/(room|tracker)/([^/?#]+)")


def parse_room_reference(text: str, default_base_url: str) -> tuple[str, str, str]:
    """Turns whatever a user pastes into the room field -- a bare UUID, or a full room/tracker URL
    on any webhost host, not just archipelago.gg (so self-hosted instances work too) -- into
    (base_url, room_uuid, tracker_uuid). Exactly one of room_uuid/tracker_uuid is non-empty unless
    neither pattern matched, in which case the whole trimmed string is treated as a room_uuid
    against default_base_url (mirrors how a bare tracker UUID would be typed)."""
    text = text.strip()
    match = _ROOM_OR_TRACKER_PATH.search(text)
    if match:
        kind, uuid = match.group(1), match.group(2)
        split = urlsplit(text if "://" in text else f"https://{text}")
        base_url = f"{split.scheme}://{split.netloc}" if split.netloc else default_base_url
        return (base_url, uuid, "") if kind == "room" else (base_url, "", uuid)
    return default_base_url, text, ""


@dataclass
class SlotSnapshot:
    slot_id: int
    slot_name: str
    game: str
    total_locations: int
    checked_locations: set[int]
    received_items: list[NetworkItem]
    slot_data: dict | None
    source: Literal["live", "poll"]
    # Location ids hinted (by anyone) *for this slot's own checks* and not yet checked -- see
    # PollSource._snapshot_from() for why this needs to filter on finding_player specifically, not
    # just "this slot's hint list" (that list is bidirectional: it also includes hints this slot
    # itself *received* about other players' locations, which aren't about this slot's own checks
    # at all and would be wrong to count here).
    hinted_not_found_locations: set[int] = field(default_factory=set)


class SlotFetchError(Exception):
    """Raised when a data source cannot produce a snapshot (network error, unknown room, ...)."""


class DataSource(Protocol):
    def get_snapshot(self, slot_id: int) -> SlotSnapshot: ...

    def get_available_slots(self) -> list[tuple[int, str, str]]:
        """Returns (slot_id, slot_name, game) for every slot visible through this source."""
        ...


@dataclass
class PollSource:
    """Polls the webhost's public tracker JSON API (no slot password needed). See design doc
    section 4.3 and `WebHostLib/api/tracker.py` for the endpoints this wraps.

    `room_uuid` is optional but strongly recommended: the three `/api/*_tracker` endpoints (keyed
    by `tracker_uuid`) never expose the actual slot/connection name, only an optional player-set
    `/alias` (verified live against a real room: `get_player_alias()` in `WebHostLib/tracker.py`
    returns None unless a player explicitly ran `/alias`, which in practice is almost never). The
    real slot name is only available in the room-wide `player_name`/`slot_info` data, which the
    `/api/room_status/<room_uuid>` endpoint exposes publicly (`get_players()` in
    `WebHostLib/api/__init__.py`) -- that endpoint also conveniently echoes the room's
    `tracker_uuid` back, so giving just `room_uuid` is enough to bootstrap both.
    """

    base_url: str
    tracker_uuid: str = ""
    room_uuid: str = ""
    timeout_seconds: float = 10.0

    # populated lazily by get_available_slots()/_refresh_static()/_refresh_room(), reused by get_snapshot()
    _player_game: dict[int, str] = field(default_factory=dict, init=False, repr=False)
    _player_locations_total: dict[int, int] = field(default_factory=dict, init=False, repr=False)
    _slot_names: dict[int, str] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.tracker_uuid and not self.room_uuid:
            raise ValueError("PollSource needs at least one of tracker_uuid/room_uuid")

    def _get_json(self, url: str):
        try:
            with urllib.request.urlopen(url, timeout=self.timeout_seconds) as response:
                return json.load(response)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            raise SlotFetchError(f"Failed to fetch {url}: {e}") from e

    def _tracker_json(self, path: str):
        if not self.tracker_uuid:
            self._refresh_room()
        return self._get_json(f"{self.base_url.rstrip('/')}/api/{path}/{self.tracker_uuid}")

    def _refresh_room(self) -> None:
        """Resolves real slot names (and tracker_uuid, if not given) from the room page's public
        JSON API. Room-status player order is 1:1 with player_id, see get_players() in
        WebHostLib/api/__init__.py."""
        if not self.room_uuid:
            return
        room_data = self._get_json(f"{self.base_url.rstrip('/')}/api/room_status/{self.room_uuid}")
        if not self.tracker_uuid:
            self.tracker_uuid = room_data["tracker"]
        self._slot_names = {
            player_id: name for player_id, (name, game) in enumerate(room_data["players"], start=1)
        }

    def _refresh_static(self) -> None:
        static_data = self._tracker_json("static_tracker")
        self._player_game = {entry["player"]: entry["game"] for entry in static_data["player_game"]}
        self._player_locations_total = {
            entry["player"]: entry["total_locations"] for entry in static_data["player_locations_total"]
        }

    def _slot_name(self, player: int, tracker_data: dict) -> str:
        if player in self._slot_names:
            return self._slot_names[player]
        alias = next((e["alias"] for e in tracker_data["aliases"] if e["player"] == player), None)
        return alias or f"Player {player}"

    def get_available_slots(self) -> list[tuple[int, str, str]]:
        self._refresh_room()
        self._refresh_static()
        tracker_data = self._tracker_json("tracker")
        return [
            (player, self._slot_name(player, tracker_data), self._player_game[player])
            for player in sorted(self._player_game)
        ]

    def _snapshot_from(self, slot_id: int, tracker_data: dict) -> SlotSnapshot:
        checked = next(
            (set(entry["locations"]) for entry in tracker_data["player_checks_done"] if entry["player"] == slot_id),
            set(),
        )
        received_raw = next(
            (entry["items"] for entry in tracker_data["player_items_received"] if entry["player"] == slot_id),
            [],
        )
        received_items = [NetworkItem(*item) for item in received_raw]

        # Hint tuples are NetUtils.Hint, serialized as plain JSON arrays in field-declaration order:
        # [receiving_player, finding_player, location, item, found, entrance, item_flags, status].
        # The server stores each hint under *both* the finding player's and the receiving player's
        # own entry (MultiServer.notify_hints), so tracker_data["hints"]'s entry for `slot_id` is a
        # mix of hints *about* this slot's locations and hints this slot merely *received* about
        # someone else's -- must filter on finding_player == slot_id (index 1) to get only the
        # former, and on found == False (index 4) since a found hint's location is already checked.
        hint_entries = next(
            (entry["hints"] for entry in tracker_data["hints"] if entry["player"] == slot_id),
            [],
        )
        hinted_not_found = {hint[2] for hint in hint_entries if hint[1] == slot_id and not hint[4]}

        return SlotSnapshot(
            slot_id=slot_id,
            slot_name=self._slot_name(slot_id, tracker_data),
            game=self._player_game[slot_id],
            total_locations=self._player_locations_total.get(slot_id, 0),
            checked_locations=checked,
            received_items=received_items,
            slot_data=None,  # fetched lazily via fetch_slot_data()/fetch_all_slot_data(); heavier endpoint
            source="poll",
            hinted_not_found_locations=hinted_not_found,
        )

    def get_snapshot(self, slot_id: int) -> SlotSnapshot:
        if slot_id not in self._player_game:
            self._refresh_room()
            self._refresh_static()
        if slot_id not in self._player_game:
            raise SlotFetchError(f"Slot {slot_id} not found in tracker room {self.tracker_uuid}")
        return self._snapshot_from(slot_id, self._tracker_json("tracker"))

    def get_snapshots(self, slot_ids: list[int]) -> dict[int, SlotSnapshot]:
        """Batched version of get_snapshot() -- fetches /api/tracker exactly once no matter how
        many slots are requested. Calling get_snapshot() in a loop for a whole room (a real one
        tested against had 193 slots) was refetching the entire room's tracker JSON once per slot,
        which is what made the poll cycle's *first* result take as long as fetching the whole room
        193 times over before any computation could even start; use this instead for anything more
        than a single ad-hoc slot lookup."""
        tracker_data = self._tracker_json("tracker")
        return {slot_id: self._snapshot_from(slot_id, tracker_data) for slot_id in slot_ids if slot_id in self._player_game}

    def fetch_slot_data(self, slot_id: int) -> dict | None:
        """Separate from get_snapshot() because /api/slot_data_tracker is heavier and rarely
        needed (only for compatibility == "slot_data" games) -- see WebHostLib/api/tracker.py.
        Prefer fetch_all_slot_data() when handling more than one slot, for the same reason
        get_snapshots() exists."""
        return self.fetch_all_slot_data().get(slot_id)

    def fetch_all_slot_data(self) -> dict[int, dict]:
        slot_data_list = self._tracker_json("slot_data_tracker")
        return {entry["player"]: entry["slot_data"] for entry in slot_data_list}


class LiveSource:
    """Placeholder for a future push-based source: one CommonContext connection per configured
    slot, analogous to TrackerGameContext in worlds/tracker/TrackerClient.py. Not implemented yet --
    PollSource covers the first version (see implementation plan, decision 1)."""

    def get_snapshot(self, slot_id: int) -> SlotSnapshot:
        raise NotImplementedError("LiveSource is not implemented yet; use PollSource.")

    def get_available_slots(self) -> list[tuple[int, str, str]]:
        raise NotImplementedError("LiveSource is not implemented yet; use PollSource.")
