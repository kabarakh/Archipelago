"""Standalone Kivy/KivyMD window for the Multi Slot Tracker, launched like Universal Tracker's own
client (worlds/tracker/TrackerClient.py) -- but as its own app, not a GameManager subclass, since
GameManager assumes a single live CommonContext server connection (connect bar, hints tab, command
processor) which doesn't apply here: this tool watches many *other* players' slots via polling, not
one slot of its own. Reuses `kvui.ThemedApp` for the same theme Archipelago's other Kivy clients use
(`ThemedApp.set_colors()` applies the "Archipelago" theme_style/primary_palette/dynamic_scheme baked
into data/client.kv) -- all widget colors below are drawn from `theme_cls`'s M3 color roles rather
than hardcoded, so this stays visually consistent (light/dark, palette) with the rest of Archipelago.
"""

from __future__ import annotations

import kvui  # noqa: F401  -- must be imported before any other kivy/kivymd import (sets up env, see kvui.py)

from kivy.clock import Clock
from kivy.metrics import dp
from kivymd.app import MDApp
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDButton, MDButtonText, MDIconButton
from kivymd.uix.dialog import MDDialog, MDDialogButtonContainer, MDDialogContentContainer, MDDialogHeadlineText
from kivymd.uix.divider import MDDivider
from kivymd.uix.label import MDLabel
from kivymd.uix.list import MDListItem, MDListItemSupportingText, MDListItemTrailingCheckbox
from kivymd.uix.menu import MDDropdownMenu
from kivymd.uix.progressindicator import MDLinearProgressIndicator
from kivymd.uix.scrollview import MDScrollView
from kivymd.uix.textfield import MDTextField, MDTextFieldHintText

from kvui import ThemedApp, ToggleButton

from .Aggregator import DashboardData
from .LogicEngine import SlotLogicResult

_PILL_FONT = {"font_style": "Label", "role": "small"}


def _theme():
    return MDApp.get_running_app().theme_cls


# M3 color-role pairs (bg, fg) per semantic meaning -- see kivymd/dynamic_color.py for the full role
# list. Picked so this reads the same way in both light and dark variants of whatever palette
# data/client.kv configures, instead of fixed RGBA that would clash with a dark theme.
def _role_colors(role: str):
    t = _theme()
    return {
        "positive": (t.tertiaryContainerColor, t.onTertiaryContainerColor),   # "In logic"
        "caution": (t.secondaryContainerColor, t.onSecondaryContainerColor),  # "Out of logic"
        "accent": (t.primaryContainerColor, t.onPrimaryContainerColor),       # "Live" / "No progression needed"
        "neutral": (t.surfaceContainerHighColor, t.onSurfaceVariantColor),    # n/a / error / unknown
    }[role]


_BADGE_ROLE_BY_SOURCE = {"live": "accent"}
_BADGE_ROLE_BY_COMPAT = {"slot_data": "positive", "yaml_required": "caution", "unknown_game": "neutral"}
_BADGE_LABEL = {
    "live": "Live", "slot_data": "Slot data", "yaml_required": "Yaml required", "unknown_game": "Unknown game",
}
_COMPAT_FILTER_OPTIONS = ["All tiers", "Slot data", "Yaml required", "Unknown game"]
_COMPAT_FILTER_TO_TIER = {"Slot data": "slot_data", "Yaml required": "yaml_required", "Unknown game": "unknown_game"}


def _pill(text: str, role: str) -> MDBoxLayout:
    """A small rounded, adaptively-sized badge. Built as a tight box around a single adaptive
    label rather than a fixed/guessed size -- adaptive_size keeps it correctly sized as content
    changes instead of needing a one-off texture measurement."""
    bg, fg = _role_colors(role)
    box = MDBoxLayout(
        orientation="horizontal", adaptive_size=True, md_bg_color=bg, radius=[dp(6)] * 4,
        padding=(dp(8), dp(3)),
    )
    box.add_widget(MDLabel(text=text, adaptive_size=True, theme_text_color="Custom", text_color=fg, **_PILL_FONT))
    return box


class MetricCard(MDBoxLayout):
    def __init__(self, label_text: str, **kwargs):
        t = _theme()
        super().__init__(
            theme_bg_color="Custom", md_bg_color=t.surfaceContainerColor,
            radius=[dp(10)] * 4, orientation="vertical", padding=dp(16), spacing=dp(4),
            size_hint=(1, None), adaptive_height=True, **kwargs,
        )
        self.label = MDLabel(text=label_text, theme_text_color="Custom", text_color=t.onSurfaceVariantColor,
                              adaptive_height=True, font_style="Label", role="large")
        self.value = MDLabel(text="-", theme_text_color="Custom", text_color=t.onSurfaceColor,
                              adaptive_height=True, font_style="Headline", role="small")
        self.add_widget(self.label)
        self.add_widget(self.value)


class SlotRow(MDBoxLayout):
    def __init__(self, result: SlotLogicResult, **kwargs):
        t = _theme()
        super().__init__(orientation="vertical", adaptive_height=True, spacing=dp(6),
                          padding=(0, dp(10)), **kwargs)

        head = MDBoxLayout(orientation="horizontal", adaptive_height=True, spacing=dp(10))
        name_line = MDBoxLayout(orientation="horizontal", spacing=dp(8), adaptive_height=True)
        name_line.add_widget(MDLabel(text=result.slot_name, bold=True, adaptive_size=True,
                                      theme_text_color="Custom", text_color=t.onSurfaceColor,
                                      font_style="Body", role="large"))
        name_line.add_widget(MDLabel(text=result.game, theme_text_color="Custom",
                                      text_color=t.onSurfaceVariantColor, adaptive_height=True,
                                      font_style="Body", role="large"))
        head.add_widget(name_line)

        badge_role = _BADGE_ROLE_BY_SOURCE.get(result.source) or _BADGE_ROLE_BY_COMPAT.get(result.compatibility, "neutral")
        badge_key = result.source if result.source in _BADGE_ROLE_BY_SOURCE else result.compatibility
        badge_col = MDBoxLayout(size_hint_x=None, adaptive_height=True)
        badge_col.add_widget(_pill(_BADGE_LABEL.get(badge_key, badge_key), badge_role))
        badge_col.width = badge_col.minimum_width
        badge_col.bind(minimum_width=badge_col.setter("width"))
        head.add_widget(badge_col)
        self.add_widget(head)

        body = MDBoxLayout(orientation="horizontal", spacing=dp(24), adaptive_height=True)
        progress_col = MDBoxLayout(orientation="vertical", spacing=dp(4), adaptive_height=True)
        bar = MDLinearProgressIndicator(size_hint_y=None, height=dp(6))
        bar.value = (100 * result.checked / result.total_locations) if result.total_locations else 0
        progress_col.add_widget(bar)
        progress_col.add_widget(MDLabel(
            text=f"{result.checked} / {result.total_locations} checks done",
            theme_text_color="Custom", text_color=t.onSurfaceVariantColor,
            adaptive_height=True, font_style="Label", role="large",
        ))
        body.add_widget(progress_col)

        pills = MDBoxLayout(orientation="horizontal", spacing=dp(6), adaptive_size=True, size_hint_y=None)
        for widget in self._logic_pills(result):
            pills.add_widget(widget)
        pills_col = MDBoxLayout(size_hint_x=None, adaptive_height=True)
        pills_col.add_widget(pills)
        pills_col.width = pills_col.minimum_width
        pills_col.bind(minimum_width=pills_col.setter("width"))
        body.add_widget(pills_col)
        self.add_widget(body)

    @staticmethod
    def _logic_pills(result: SlotLogicResult) -> list:
        if result.error is not None:
            return [_pill(result.error, "neutral")]
        if result.no_progression_needed:
            return [_pill("No progression needed", "accent")]
        pills = [_pill(f"In logic {result.in_logic_open}", "positive")]
        if result.out_of_logic_open is None:
            pills.append(_pill("Out of logic n/a", "neutral"))
        else:
            pills.append(_pill(f"Out of logic {result.out_of_logic_open}", "caution"))
        return pills


class SlotPickerDialog(MDDialog):
    """Search + checkbox list to choose which slots the poll loop watches and the window shows at
    all -- separate from the game/tier/condition filters, which only affect how already-fetched
    data is displayed. See design doc section 6 ("Fairness/privacy: anyone reading other players'
    progress should have their consent") -- this is what lets a user deliberately narrow the room
    down instead of watching everyone in it by default."""

    def __init__(self, available_slots, currently_selected, on_apply, on_cancel=None, **kwargs):
        self._available_slots = available_slots  # list[(id, name, game)]
        self._checks: dict[int, MDListItemTrailingCheckbox] = {}
        self._rows: dict[int, MDListItem] = {}
        self._on_apply = on_apply
        self._on_cancel = on_cancel
        self._currently_selected = currently_selected

        self._list_box = MDBoxLayout(orientation="vertical", adaptive_height=True, spacing=dp(2))
        for slot_id, name, game in available_slots:
            self._list_box.add_widget(self._make_row(slot_id, name, game))

        scroll = MDScrollView(size_hint_y=None, height=dp(360))
        scroll.add_widget(self._list_box)

        search = MDTextField(MDTextFieldHintText(text="Filter by name or game"), size_hint_y=None, height=dp(48))
        search.bind(text=lambda _w, text: self._apply_search(text))

        quick_buttons = MDBoxLayout(orientation="horizontal", spacing=dp(8), adaptive_height=True)
        select_all = MDButton(MDButtonText(text="Select visible"), style="text")
        select_all.bind(on_release=lambda *_: self._set_visible(True))
        select_none = MDButton(MDButtonText(text="Deselect visible"), style="text")
        select_none.bind(on_release=lambda *_: self._set_visible(False))
        quick_buttons.add_widget(select_all)
        quick_buttons.add_widget(select_none)

        content = MDDialogContentContainer(
            search, quick_buttons, MDDivider(), scroll, orientation="vertical", spacing=dp(8),
        )

        cancel_btn = MDButton(MDButtonText(text="Cancel"), style="text")
        cancel_btn.bind(on_release=lambda *_: self._cancel())
        apply_btn = MDButton(MDButtonText(text="Apply"), style="filled")
        apply_btn.bind(on_release=lambda *_: self._apply())

        super().__init__(
            MDDialogHeadlineText(text="Select slots to watch"),
            content,
            MDDialogButtonContainer(cancel_btn, apply_btn, spacing=dp(8)),
            **kwargs,
        )

    def _make_row(self, slot_id: int, name: str, game: str) -> MDListItem:
        checkbox = MDListItemTrailingCheckbox(active=(self._currently_selected is None or slot_id in self._currently_selected))
        row = MDListItem(MDListItemSupportingText(text=f"{name}  –  {game}"), checkbox)
        row._search_key = f"{name} {game}".lower()
        self._checks[slot_id] = checkbox
        self._rows[slot_id] = row
        return row

    def _apply_search(self, text: str) -> None:
        needle = text.strip().lower()
        self._list_box.clear_widgets()
        for slot_id, _name, _game in self._available_slots:
            row = self._rows[slot_id]
            if not needle or needle in row._search_key:
                self._list_box.add_widget(row)

    def _set_visible(self, value: bool) -> None:
        for widget in self._list_box.children:
            slot_id = next(sid for sid, row in self._rows.items() if row is widget)
            self._checks[slot_id].active = value

    def _apply(self) -> None:
        selected = {slot_id for slot_id, checkbox in self._checks.items() if checkbox.active}
        self.dismiss()
        self._on_apply(selected)

    def _cancel(self) -> None:
        self.dismiss()
        if self._on_cancel:
            self._on_cancel()


class MultiSlotTrackerApp(ThemedApp):
    title = "Multi Slot Tracker"

    def __init__(self, on_room_submitted=None, on_refresh_requested=None,
                 on_startup_selection_confirmed=None):
        # Deliberately no persisted room/slot state: nothing here is saved between runs, and the
        # Room field below always starts empty -- entering a room (and confirming which slots to
        # watch) is required fresh every time the app starts.
        self.on_room_submitted = on_room_submitted
        self.on_refresh_requested = on_refresh_requested
        self.on_startup_selection_confirmed = on_startup_selection_confirmed
        self._latest: DashboardData | None = None
        self._game_filter: str | None = None
        self._compat_filter: str | None = None
        self._require_in_logic = False
        self._require_out_of_logic = False
        self._require_go_mode = False
        self._game_dropdown: MDDropdownMenu | None = None
        self._compat_dropdown: MDDropdownMenu | None = None
        self._available_slots: list[tuple[int, str, str]] = []
        self.selected_slot_ids: set[int] | None = None  # None == "all slots" (default, until narrowed)
        # Shows the slot picker once per loaded room, before the poll loop is allowed to start
        # computing anything -- see Client.py's slot_selection_ready gate.
        self._startup_picker_shown = False
        # Row widgets are expensive (each SlotRow/pill is several KivyMD widgets with theming,
        # ripple, and canvas behaviors) and a big room pushes updates roughly once a second while
        # its slots are still being computed. Rebuilding all ~193 of them from scratch on every
        # single push blocked the main thread long enough to make clicks/typing feel delayed
        # (Kivy is single-threaded -- it can't process input while a widget-heavy render is still
        # running). Cache built rows by slot_id and only reconstruct the ones whose data actually
        # changed since the last render; reordering/reparenting already-built rows is cheap.
        self._row_widgets: dict[int, "SlotRow"] = {}
        self._row_data_cache: dict[int, SlotLogicResult] = {}
        self._last_game_list: list[str] | None = None
        super().__init__()

    def build(self):
        self.set_colors()
        self.icon = r"data/icon.png"
        t = self.theme_cls

        root = MDBoxLayout(orientation="vertical", padding=dp(24), spacing=dp(12),
                            md_bg_color=t.backgroundColor)

        # -- room/connect row --
        room_row = MDBoxLayout(orientation="horizontal", spacing=dp(10), size_hint_y=None, height=dp(48))
        room_row.add_widget(MDLabel(text="Room", theme_text_color="Custom", text_color=t.onSurfaceVariantColor,
                                     adaptive_size=True, size_hint_x=None, halign="left"))
        self.room_field = MDTextField(
            MDTextFieldHintText(text="Room or tracker URL, or just its UUID"),
            mode="outlined", size_hint_y=None, height=dp(48),
        )
        self.room_field.bind(on_text_validate=lambda *_: self._submit_room())
        room_row.add_widget(self.room_field)
        load_button = MDButton(MDButtonText(text="Load"), style="filled")
        load_button.bind(on_release=lambda *_: self._submit_room())
        room_row.add_widget(load_button)
        root.add_widget(room_row)

        # -- title row --
        header = MDBoxLayout(orientation="horizontal", size_hint_y=None, height=dp(48))
        title_col = MDBoxLayout(orientation="vertical")
        title_col.add_widget(MDLabel(text="Multi-slot tracker", theme_text_color="Custom",
                                      text_color=t.onBackgroundColor, font_style="Headline", role="small",
                                      adaptive_height=True))
        self.updated_label = MDLabel(text="Enter a room above to get started.", theme_text_color="Custom",
                                      text_color=t.onSurfaceVariantColor, font_style="Body", role="medium",
                                      adaptive_height=True)
        title_col.add_widget(self.updated_label)
        header.add_widget(title_col)

        header_controls = MDBoxLayout(orientation="horizontal", spacing=dp(10), adaptive_size=True)
        slots_button = MDButton(MDButtonText(text="Select slots..."), style="outlined")
        slots_button.bind(on_release=lambda *_: self._open_slot_picker())
        header_controls.add_widget(slots_button)
        refresh_button = MDIconButton(icon="refresh", style="outlined")
        refresh_button.bind(on_release=lambda *_: self.on_refresh_requested and self.on_refresh_requested())
        header_controls.add_widget(refresh_button)
        header.add_widget(header_controls)
        root.add_widget(header)

        # -- filter row --
        filters = MDBoxLayout(orientation="horizontal", spacing=dp(8), size_hint_y=None, height=dp(40))
        self.game_filter_button = MDButton(MDButtonText(text="All games"), style="outlined")
        self.game_filter_button.bind(on_release=self._open_game_filter)
        filters.add_widget(self.game_filter_button)

        self.compat_filter_button = MDButton(MDButtonText(text="All tiers"), style="outlined")
        self.compat_filter_button.bind(on_release=self._open_compat_filter)
        filters.add_widget(self.compat_filter_button)

        self.in_logic_toggle = ToggleButton(MDButtonText(text="Has in-logic checks"))
        self.in_logic_toggle.bind(state=lambda w, v: self._set_condition("_require_in_logic", v == "down"))
        filters.add_widget(self.in_logic_toggle)

        self.out_of_logic_toggle = ToggleButton(MDButtonText(text="Has out-of-logic checks"))
        self.out_of_logic_toggle.bind(state=lambda w, v: self._set_condition("_require_out_of_logic", v == "down"))
        filters.add_widget(self.out_of_logic_toggle)

        self.go_mode_toggle = ToggleButton(MDButtonText(text="Go mode"))
        self.go_mode_toggle.bind(state=lambda w, v: self._set_condition("_require_go_mode", v == "down"))
        filters.add_widget(self.go_mode_toggle)
        root.add_widget(filters)

        # -- metrics row --
        metrics = MDBoxLayout(orientation="horizontal", spacing=dp(16), size_hint_y=None, height=dp(76))
        self.metric_slots = MetricCard("Slots watched")
        self.metric_open = MetricCard("Checks open")
        self.metric_logic = MetricCard("Of which in logic")
        self.metric_restricted = MetricCard("Restricted")
        for card in (self.metric_slots, self.metric_open, self.metric_logic, self.metric_restricted):
            metrics.add_widget(card)
        root.add_widget(metrics)

        scroll = MDScrollView()
        self.rows_box = MDBoxLayout(orientation="vertical", adaptive_height=True)
        scroll.add_widget(self.rows_box)
        root.add_widget(scroll)

        Clock.schedule_interval(self._tick_updated_label, 1)
        return root

    def _submit_room(self) -> None:
        text = self.room_field.text.strip()
        if text and self.on_room_submitted:
            self.selected_slot_ids = None
            self._available_slots = []
            self._startup_picker_shown = False
            self.on_room_submitted(text)

    # -- called once a room is loaded (and again if the room changes), so the slot picker has
    # names ready; also called every poll cycle so newly-appearing slots show up eventually. The
    # very first call for a freshly-loaded room auto-opens the picker -- see Client.py's
    # slot_selection_ready gate, which holds the poll loop off computing anything until the user
    # has confirmed (or explicitly skipped) narrowing the slot list down. --
    def set_available_slots(self, slots: list[tuple[int, str, str]]) -> None:
        def _apply(_dt):
            self._available_slots = slots
            if not self._startup_picker_shown:
                self._startup_picker_shown = True
                self._open_slot_picker(is_startup=True)
        Clock.schedule_once(_apply)

    def get_active_slot_ids(self) -> set[int] | None:
        return self.selected_slot_ids

    # -- called from the background poll thread; Clock.schedule_once marshals onto the Kivy thread --
    def push_dashboard(self, data: DashboardData) -> None:
        Clock.schedule_once(lambda _dt: self._apply_dashboard(data))

    def _apply_dashboard(self, data: DashboardData) -> None:
        self._latest = data
        self._rebuild_game_filter_menu(data)
        self._render()

    def _tick_updated_label(self, _dt) -> None:
        if self._latest is None:
            return
        from datetime import datetime, timezone
        secs = max(0, int((datetime.now(timezone.utc) - self._latest.generated_at).total_seconds()))
        self.updated_label.text = f"Updated {secs}s ago"

    def _open_slot_picker(self, is_startup: bool = False) -> None:
        if not self._available_slots:
            return

        def on_apply(selected: set[int]) -> None:
            self._apply_slot_selection(selected)
            if is_startup and self.on_startup_selection_confirmed:
                self.on_startup_selection_confirmed()

        def on_cancel() -> None:
            # don't leave the poll loop blocked forever just because the user dismissed the
            # startup picker without hitting Apply -- default to watching everything.
            if is_startup and self.on_startup_selection_confirmed:
                self.on_startup_selection_confirmed()

        dialog = SlotPickerDialog(self._available_slots, self.selected_slot_ids, on_apply,
                                   on_cancel=on_cancel if is_startup else None)
        dialog.open()

    def _apply_slot_selection(self, selected: set[int]) -> None:
        # treat "everything checked" as the None/"all" sentinel so newly-appearing slots aren't
        # silently excluded just because they didn't exist yet when the picker was last used.
        self.selected_slot_ids = None if selected == {s[0] for s in self._available_slots} else selected
        if self.on_refresh_requested:
            self.on_refresh_requested()

    def _rebuild_game_filter_menu(self, data: DashboardData) -> None:
        games = sorted({s.game for s in data.slots})
        if games == self._last_game_list:
            return  # avoid rebuilding ~N dropdown item widgets on every push once the game list settles
        self._last_game_list = games
        items = [{"text": "All games", "on_release": lambda: self._set_game_filter(None)}]
        items += [{"text": g, "on_release": lambda g=g: self._set_game_filter(g)} for g in games]
        if self._game_dropdown is not None:
            self._game_dropdown.dismiss()
        self._game_dropdown = MDDropdownMenu(caller=self.game_filter_button, items=items)

    def _open_game_filter(self, *_args) -> None:
        if self._game_dropdown is not None:
            self._game_dropdown.open()

    def _set_game_filter(self, game: str | None) -> None:
        self._game_filter = game
        self.game_filter_button.children[0].text = game or "All games"
        if self._game_dropdown is not None:
            self._game_dropdown.dismiss()
        self._render()

    def _open_compat_filter(self, *_args) -> None:
        items = [{"text": label, "on_release": lambda label=label: self._set_compat_filter(label)}
                 for label in _COMPAT_FILTER_OPTIONS]
        if self._compat_dropdown is not None:
            self._compat_dropdown.dismiss()
        self._compat_dropdown = MDDropdownMenu(caller=self.compat_filter_button, items=items)
        self._compat_dropdown.open()

    def _set_compat_filter(self, label: str) -> None:
        self._compat_filter = _COMPAT_FILTER_TO_TIER.get(label)
        self.compat_filter_button.children[0].text = label
        if self._compat_dropdown is not None:
            self._compat_dropdown.dismiss()
        self._render()

    def _set_condition(self, attr: str, value: bool) -> None:
        setattr(self, attr, value)
        self._render()

    def _passes_filters(self, s: SlotLogicResult) -> bool:
        if self._game_filter and s.game != self._game_filter:
            return False
        if self._compat_filter and s.compatibility != self._compat_filter:
            return False
        if self._require_in_logic and not (s.in_logic_open or 0) > 0:
            return False
        if self._require_out_of_logic and not (s.out_of_logic_open or 0) > 0:
            return False
        if self._require_go_mode and not s.no_progression_needed:
            return False
        return True

    def _render(self) -> None:
        data = self._latest
        if data is None:
            return
        self.metric_slots.value.text = str(len(data.slots))
        self.metric_open.value.text = str(data.total_open)
        self.metric_logic.value.text = str(data.total_in_logic)
        self.metric_restricted.value.text = str(data.restricted_count)

        slots = [s for s in data.slots if self._passes_filters(s)]

        # Only (re)build a SlotRow when it doesn't exist yet or its data actually changed since
        # last render (SlotLogicResult is a plain dataclass, so == compares by value) -- see the
        # comment on _row_widgets in __init__ for why this matters. Reparenting an unchanged,
        # already-built row into rows_box below is cheap; constructing one from scratch is not.
        seen_ids = set()
        for result in slots:
            seen_ids.add(result.slot_id)
            if self._row_data_cache.get(result.slot_id) != result:
                self._row_widgets[result.slot_id] = SlotRow(result)
                self._row_data_cache[result.slot_id] = result
        for stale_id in set(self._row_widgets) - seen_ids:
            del self._row_widgets[stale_id]
            self._row_data_cache.pop(stale_id, None)

        self.rows_box.clear_widgets()
        for i, result in enumerate(slots):
            if i > 0:
                self.rows_box.add_widget(MDDivider())
            self.rows_box.add_widget(self._row_widgets[result.slot_id])
