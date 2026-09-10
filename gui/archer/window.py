"""Adaptive control center shell and application-level dialogs."""

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from archer import __version__
from archer.client import ArcherClient
from archer.pages.controls import (
    AdvancedPage, AudioPage, BatteryPage, DisplayPage, FirmwarePage,
    KeyboardPage, PerformancePage, SystemPage, link,
)
from archer.pages.overview import OverviewPage
from archer.preferences import Preferences
from archer.state import AppState
from archer.widgets.forms import Page, confirm, label


DESTINATIONS = [
    ("overview", "Overview", "view-grid-symbolic"),
    ("performance", "Performance", "power-profile-performance-symbolic"),
    ("battery", "Battery", "battery-symbolic"),
    ("devices", "Display & Keyboard", "input-keyboard-symbolic"),
    ("audio", "Audio", "audio-input-microphone-symbolic"),
    ("system", "System", "preferences-system-symbolic"),
]


class ArcherWindow(Adw.ApplicationWindow):
    def __init__(self, client=None, preferences=None, autostart=True, **kwargs):
        self.preferences = preferences or Preferences()
        self.preferences.apply_appearance()
        super().__init__(title="Archer", default_width=self.preferences.data["width"],
                         default_height=self.preferences.data["height"], **kwargs)
        self.set_size_request(360, 600)
        self.add_css_class("archer")
        self.client = client or ArcherClient(connect=False)
        self.state = AppState(self.client)
        self._closing = False
        self._resources_closed = False
        self._normal_size = (self.preferences.data["width"], self.preferences.data["height"])
        self._load_css()
        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)
        self.split = Adw.OverlaySplitView(min_sidebar_width=220, max_sidebar_width=250)
        self.toast_overlay.set_child(self.split)
        self._build_sidebar()
        self._build_content()
        breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 900sp"))
        breakpoint.add_setter(self.split, "collapsed", True)
        breakpoint.add_setter(self.sidebar_button, "visible", True)
        self.add_breakpoint(breakpoint)
        compact = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 600sp"))
        compact.add_setter(self.split, "collapsed", True)
        compact.add_setter(self.sidebar_button, "visible", True)
        compact.add_setter(self.pages["overview"].grid, "max-children-per-line", 1)
        compact.add_setter(self.split, "min-sidebar-width", 0.0)
        compact.add_setter(self.header, "decoration-layout", ":close")
        compact.connect("apply", lambda *_: self._compact(True))
        compact.connect("unapply", lambda *_: self._compact(False))
        self.add_breakpoint(compact)
        self.state.connect("connection-changed", self._connection)
        self.state.connect("settings-changed", self._capabilities)
        self._install_actions()
        self.sidebar.select_row(self.sidebar.get_row_at_index(0))
        if self.preferences.data["maximized"]:
            self.maximize()
        self.connect("notify::default-width", self._size_changed)
        self.connect("notify::default-height", self._size_changed)
        self.connect("close-request", self._close_request)
        if autostart:
            GLib.idle_add(self._start)

    def _start(self):
        self.state.start()
        return False

    def _compact(self, enabled):
        if enabled:
            self.add_css_class("compact")
        else:
            self.remove_css_class("compact")
        for page in self.pages.values():
            for form in page.forms:
                form.actions.set_orientation(Gtk.Orientation.VERTICAL if enabled else Gtk.Orientation.HORIZONTAL)
                form.actions.set_halign(Gtk.Align.FILL if enabled else Gtk.Align.END)

    def _load_css(self):
        icons = Gtk.IconTheme.get_for_display(self.get_display())
        asset_path = str(Path(__file__).parent.parent / "assets")
        if asset_path not in icons.get_search_path():
            icons.add_search_path(asset_path)
        self.css_provider = Gtk.CssProvider()
        self.css_provider.load_from_path(str(Path(__file__).with_name("style.css")))
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), self.css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _build_sidebar(self):
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar(show_end_title_buttons=False)
        header.set_title_widget(label("Archer", "heading"))
        toolbar.add_top_bar(header)
        self.sidebar = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.sidebar.add_css_class("navigation-sidebar")
        self.sidebar.add_css_class("sidebar-list")
        for key, title, icon in DESTINATIONS:
            row = Gtk.ListBoxRow()
            row.destination = key
            box = Gtk.Box(spacing=12)
            box.append(Gtk.Image(icon_name=icon))
            text = label(title)
            box.append(text)
            row.set_child(box)
            self.sidebar.append(row)
        self.sidebar.connect("row-selected", self._selected)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_child(self.sidebar)
        toolbar.set_content(scroll)
        self.split.set_sidebar(toolbar)

    def _build_content(self):
        toolbar = Adw.ToolbarView()
        self.header = Adw.HeaderBar()
        self.header_title = Adw.WindowTitle(title="Overview", subtitle="Archer")
        self.header.set_title_widget(self.header_title)
        self.sidebar_button = Gtk.Button(icon_name="sidebar-show-symbolic", tooltip_text="Show navigation", visible=False)
        self.sidebar_button.connect("clicked", lambda *_: self.split.set_show_sidebar(not self.split.get_show_sidebar()))
        self.header.pack_start(self.sidebar_button)
        self.back_button = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text="Back (Alt+Left)", visible=False)
        self.back_button.connect("clicked", lambda *_: self.go_back())
        self.header.pack_start(self.back_button)
        menu = Gio.Menu()
        for title, action in (("Preferences", "win.preferences"), ("Keyboard Shortcuts", "win.shortcuts"),
                              ("About Archer", "win.about"), ("Quit", "win.quit")):
            menu.append(title, action)
        self.header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Main menu"))
        toolbar.add_top_bar(self.header)
        self.banner = Adw.Banner(title="Connecting to the Archer service…", revealed=True, button_label="Retry")
        self.banner.connect("button-clicked", lambda *_: self.state.refresh(reconnect=True))
        toolbar.add_top_bar(self.banner)
        self.stack = Gtk.Stack(vexpand=True, hhomogeneous=False, vhomogeneous=False,
                               transition_type=Gtk.StackTransitionType.CROSSFADE)
        toolbar.set_content(self.stack)
        self.split.set_content(toolbar)
        self.pages = {
            "overview": OverviewPage(self.state, self.navigate),
            "performance": PerformancePage(self.state), "battery": BatteryPage(self.state),
            "display": DisplayPage(self.state), "keyboard": KeyboardPage(self.state),
            "audio": AudioPage(self.state), "system": SystemPage(self.state, self.navigate),
            "firmware": FirmwarePage(self.state), "advanced": AdvancedPage(self.state),
        }
        devices = Page("Display & Keyboard", "Graphics, panel response, and keyboard lighting.", self.state)
        devices.availability(True)
        group = Adw.PreferencesGroup()
        link(group, "Display", "Graphics modes and panel response", lambda: self.navigate("display"))
        link(group, "Keyboard", "Zone colors, lighting effects, and backlight", lambda: self.navigate("keyboard"))
        devices.content.append(group)
        devices.links = group
        self.pages["devices"] = devices
        self.navigation = {}
        self.navigation_pages = {}
        for key, title, _ in DESTINATIONS:
            navigation = Adw.NavigationView()
            root = Adw.NavigationPage.new(self.pages[key], title)
            root.set_tag(key)
            navigation.add(root)
            navigation.connect("notify::visible-page", self._navigation_changed)
            self.navigation[key] = navigation
            self.stack.add_named(navigation, key)
        for key, parent, title in (("display", "devices", "Display"), ("keyboard", "devices", "Keyboard"),
                                   ("firmware", "system", "Firmware"), ("advanced", "system", "Advanced")):
            page = Adw.NavigationPage.new(self.pages[key], title)
            page.set_tag(key)
            self.navigation[parent].add(page)
            self.navigation_pages[key] = parent
        self.stack.connect("notify::visible-child-name", self._navigation_changed)

    def _selected(self, _list, row):
        if row is None or not hasattr(self, "stack"):
            return
        self.stack.set_visible_child_name(row.destination)
        if self.split.get_collapsed():
            self.split.set_show_sidebar(False)
        self._navigation_changed()

    def navigate(self, key):
        parent = self.navigation_pages.get(key, key)
        for row_index, (name, _, _) in enumerate(DESTINATIONS):
            if name == parent:
                self.sidebar.select_row(self.sidebar.get_row_at_index(row_index))
                break
        if key != parent:
            navigation = self.navigation[parent]
            navigation.pop_to_tag(parent)
            navigation.push_by_tag(key)
        self._navigation_changed()

    def _navigation_changed(self, *_):
        name = self.stack.get_visible_child_name()
        if not name:
            return
        page = self.navigation[name].get_visible_page()
        if page:
            self.header_title.set_title(page.get_title())
            self.back_button.set_visible(page.get_tag() != name)
            self.pages[page.get_tag()].heading.grab_focus()

    def go_back(self):
        self.navigation[self.stack.get_visible_child_name()].pop()

    def _connection(self, _state, status):
        self.banner.set_revealed(status != "Connected")
        self.banner.set_title({"Connecting": "Connecting to the Archer service…", "Offline": "Archer service unavailable. Controls will reconnect automatically.",
                               "Stale": "Live readings have stopped. Last known values are shown.",
                               "Restarting": "Waiting for the Archer service to restart…",
                               "Reconciling": "Checking whether your change was applied…"}.get(status, status))
        self.header_title.set_subtitle("Archer" if status == "Connected" else status)

    def _capabilities(self, _state, data):
        sections = {
            "performance": {"thermal_profiles": "thermal profiles", "fan_control": "fan control", "game_mode": "Game Mode"},
            "battery": {"battery_limiter": "charge protection", "battery_calibration": "calibration", "usb_charging": "USB charging"},
            "display": {"display_mode": "graphics modes", "lcd_override": "panel response"},
            "keyboard": {"keyboard_per_zone": "zone colors", "keyboard_effects": "lighting effects", "backlight_timeout": "backlight timeout"},
        }
        features = data.get("features", [])
        device_controls = ("display_mode", "lcd_override", "keyboard_per_zone", "keyboard_effects", "backlight_timeout")
        available = any(key in features for key in device_controls)
        self.pages["devices"].availability(available, "No supported display or keyboard controls were detected on this device.")
        self.pages["devices"].links.set_visible(available)
        for name, capabilities in sections.items():
            missing = [title for key, title in capabilities.items() if key not in features]
            page = self.pages[name]
            page.missing.set_visible(bool(missing) and not page.unavailable.get_visible())
            page.missing.set_label("Unavailable on this device: " + ", ".join(missing) + ".")

    def _install_actions(self):
        for name, callback in (("preferences", self.show_preferences), ("about", self.show_about),
                               ("shortcuts", self.show_shortcuts), ("quit", self.request_quit),
                               ("back", self.go_back), ("escape", self._escape)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _action, _parameter, cb=callback: cb())
            self.add_action(action)
        app = self.get_application()
        if app:
            for action, shortcuts in {"quit": ["<Control>q"], "preferences": ["<Control>comma"],
                                      "back": ["<Alt>Left"], "escape": ["Escape"], "shortcuts": ["<Control>question"]}.items():
                app.set_accels_for_action("win." + action, shortcuts)

    def _escape(self):
        dialog = self.get_visible_dialog()
        if dialog:
            dialog.close()
            return
        if self.split.get_collapsed():
            self.split.set_show_sidebar(False)

    def show_preferences(self):
        dialog = Adw.PreferencesDialog(title="Preferences")
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(title="Appearance", description="System follows the desktop’s light/dark preference. High contrast and supported accent colors always follow the system.")
        appearance = Adw.ComboRow(title="Color scheme", model=Gtk.StringList.new(["System", "Light", "Dark"]))
        appearance.set_selected(["system", "light", "dark"].index(self.preferences.data["appearance"]))

        def change(*_):
            self.preferences.save(appearance=["system", "light", "dark"][appearance.get_selected()])
            self.preferences.apply_appearance()
        appearance.connect("notify::selected", change)
        group.add(appearance)
        page.add(group)
        behavior = Adw.PreferencesGroup(title="Window behavior")
        tray = Adw.SwitchRow(title="Keep running in the system tray", subtitle="When a tray host is available, closing the window hides it. Quit always exits Archer.")
        tray.set_active(self.preferences.data["close_to_tray"])
        tray.connect("notify::active", lambda *_: self.preferences.save(close_to_tray=tray.get_active()))
        behavior.add(tray)
        page.add(behavior)
        dialog.add(page)
        dialog.present(self)
        return dialog

    def show_about(self):
        icons = Gtk.IconTheme.get_for_display(self.get_display())
        icon = "io.github.archer" if icons.has_icon("io.github.archer") else "archer"
        about = Adw.AboutDialog(application_name="Archer", application_icon=icon,
                                version=__version__, developer_name="Archer contributors",
                                website="https://github.com/otectus/Archer", issue_url="https://github.com/otectus/Archer/issues",
                                comments="Hardware controls for Acer laptops on Linux.")
        about.add_link("Archer releases", "https://github.com/otectus/Archer/releases")
        about.present(self)
        return about

    def show_shortcuts(self):
        dialog = Adw.AlertDialog(heading="Keyboard shortcuts", body="Ctrl+Q — Quit Archer\nCtrl+, — Preferences\nAlt+Left — Back\nEscape — Close navigation or a dialog\nTab / Shift+Tab — Move focus\nSpace / Enter — Activate a control")
        dialog.add_response("close", "Close")
        dialog.present(self)
        return dialog

    def _size_changed(self, *_):
        if not self.is_maximized():
            self._normal_size = self.get_default_size()

    def save_geometry(self):
        width, height = self._normal_size
        self.preferences.save(width=max(360, width), height=max(600, height), maximized=self.is_maximized())

    def _close_request(self, *_):
        if self._closing:
            return False
        self.save_geometry()
        app = self.get_application()
        if app and getattr(app, "tray_available", False) and self.preferences.data["close_to_tray"]:
            self.set_visible(False)
            return True
        self.request_quit()
        return True

    def request_quit(self):
        if any(page.dirty for page in self.pages.values()):
            confirm(self, "Discard unapplied changes?", "Your lighting or hardware edits have not been applied.",
                    "Discard and quit", self._quit, destructive=True)
        else:
            self._quit()

    def _quit(self):
        self.save_geometry()
        self.dispose_resources()
        self._closing = True
        app = self.get_application()
        if app:
            app.finish_quit()
        else:
            self.destroy()

    def dispose_resources(self):
        if self._resources_closed:
            return
        self._resources_closed = True
        self.state.close()
        for page in self.pages.values():
            page.shutdown()
        Gtk.StyleContext.remove_provider_for_display(self.get_display(), self.css_provider)

    def add_toast(self, toast):
        self.toast_overlay.add_toast(toast)
