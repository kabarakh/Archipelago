"""Minimal Kivy/KivyMD launcher window -- see the 2026-08-28 "pivot to browser UI" entry in
multi-slot-tracker-implementation-plan.md. The actual dashboard (room input, slot picker, filters,
metrics, slot list) used to live entirely in this module as a full KivyMD layout; repeated
Kivy/KivyMD layout bugs (rows overlapping neighbors, labels bleeding past their container on
resize -- see that log for the root causes) made it too fragile to keep maintaining, so the
dashboard moved to a small Vue app served over a local HTTP server (WebServer.py) instead. This
window's only job now is to start that server (done by Client.py before constructing this app),
show the URL to open, and offer a button to open it -- reusing `kvui.ThemedApp` purely so this
still matches Archipelago's other Kivy clients visually, same as before.
"""

from __future__ import annotations

import webbrowser

import kvui  # noqa: F401  -- must be imported before any other kivy/kivymd import (sets up env, see kvui.py)

from kivy.metrics import dp
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDButton, MDButtonText
from kivymd.uix.label import MDLabel
from kivymd.uix.textfield import MDTextField

from kvui import ThemedApp


class LauncherApp(ThemedApp):
    title = "Multi Slot Tracker"

    def __init__(self, url: str, **kwargs):
        self.url = url
        super().__init__(**kwargs)

    def build(self):
        self.set_colors()
        self.icon = r"data/icon.png"
        t = self.theme_cls

        root = MDBoxLayout(orientation="vertical", padding=dp(32), spacing=dp(16),
                            md_bg_color=t.backgroundColor)

        root.add_widget(MDLabel(
            text="Multi Slot Tracker", theme_text_color="Custom", text_color=t.onBackgroundColor,
            font_style="Headline", role="small", adaptive_height=True,
        ))
        root.add_widget(MDLabel(
            text="The dashboard runs in your browser. It opened automatically -- if it didn't, "
                 "or you closed the tab, use the button below.",
            theme_text_color="Custom", text_color=t.onSurfaceVariantColor,
            font_style="Body", role="medium", adaptive_height=True,
        ))

        url_row = MDBoxLayout(orientation="horizontal", spacing=dp(10), size_hint_y=None, height=dp(48))
        url_field = MDTextField(mode="outlined", size_hint_y=None, height=dp(48))
        url_field.text = self.url
        url_field.readonly = True
        url_row.add_widget(url_field)
        open_button = MDButton(MDButtonText(text="Open in browser"), style="filled",
                                size_hint_x=None, width=dp(180))
        open_button.bind(on_release=lambda *_: webbrowser.open(self.url))
        url_row.add_widget(open_button)
        root.add_widget(url_row)

        # spacer -- keeps the window from looking awkwardly cramped at its default size without
        # needing a scrollview or any of the layout machinery that kept causing trouble before.
        root.add_widget(MDBoxLayout())

        webbrowser.open(self.url)
        return root


if __name__ == "__main__":
    LauncherApp(url="http://127.0.0.1:8422/").run()
