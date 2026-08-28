from settings import Group, UserFolderPath


class MultiSlotTrackerSettings(Group):
    class TrackerPlayersPath(UserFolderPath):
        """Players folder to look for YAMLs in, for slots that require one (yaml_required tier)."""

    class DefaultSource(str):
        """Which data source to use by default: "live" (per-slot client connections) or "poll"
        (HTTP polling of the webhost's public tracker API). "poll" is the only fully implemented
        source right now; "live" is a placeholder for a future version."""

    class TrackerApiBaseUrl(str):
        """Base URL of the webhost to poll, e.g. https://archipelago.gg -- just which webhost to
        talk to, not which room. The room/tracker UUID itself is deliberately never persisted here
        (see docs/README.md "Privacy" note): it's entered fresh in the app's own Room field every
        time the app starts."""

    class PollIntervalSeconds(int):
        """How often (in seconds) to re-poll the webhost tracker API in poll mode."""

    class WebUiPort(int):
        """Local port the browser dashboard is served on (http://127.0.0.1:<port>). If this port is
        already in use, the next free one is picked automatically and shown in the launcher
        window -- this is only the preferred/starting port, not a guarantee."""

    player_files_path: TrackerPlayersPath = TrackerPlayersPath("Players")
    default_source: DefaultSource | str = "poll"
    tracker_api_base_url: TrackerApiBaseUrl | str = "https://archipelago.gg"
    poll_interval_seconds: PollIntervalSeconds | int = 30
    webui_port: WebUiPort | int = 8422
