from typing import ClassVar

from worlds.AutoWorld import World
from worlds.LauncherComponents import Component, components, Type

from .Settings import MultiSlotTrackerSettings

MST_VERSION = "v0.2.0"


class MultiSlotTrackerException(Exception):
    """Raised for Multi Slot Tracker specific setup/configuration errors."""


def _check_tracker_dependency() -> None:
    """Multi Slot Tracker reuses Universal Tracker's logic engine instead of reimplementing it,
    so UT (the `tracker` apworld) must be installed alongside this one."""
    try:
        import worlds.tracker.TrackerCore  # noqa: F401
    except ImportError as e:
        raise MultiSlotTrackerException(
            "Multi Slot Tracker requires the 'tracker' apworld (Universal Tracker) to be installed "
            "alongside it, since it reuses UT's TrackerCore for logic computation."
        ) from e


class MultiSlotTrackerWorld(World):
    """Multi Slot Tracker is not a playable game. It watches a configurable list of Archipelago
    slots (across multiple players/games) and reports, per slot, how many checks are open and in
    logic, how many are out of logic but technically reachable (where the game supports it), and
    whether the slot needs no progression items at all."""

    settings: ClassVar[MultiSlotTrackerSettings]
    settings_key = "multi_slot_tracker"

    # required so AutoWorld will let us register settings/a launcher component; this world is never
    # actually played or generated into a multiworld.
    game = "Multi Slot Tracker"
    hidden = True
    item_name_to_id = {}
    location_name_to_id = {}


def launch_client(*args) -> None:
    _check_tracker_dependency()

    from worlds.LauncherComponents import launch
    from .Client import launch as ClientMain
    launch(ClientMain, name="Multi Slot Tracker", args=args)


components.append(Component("Multi Slot Tracker", None, func=launch_client, component_type=Type.CLIENT))
