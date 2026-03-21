"""
Main application window with tab navigation.
"""

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gdk

import os
import threading

from archer.client import ArcherClient
from archer.pages.dashboard import DashboardPage
from archer.pages.performance import PerformancePage
from archer.pages.battery import BatteryPage
from archer.pages.keyboard import KeyboardPage
from archer.pages.system import SystemPage
from archer.pages.internals import InternalsPage
from archer.pages.display import DisplayPage
from archer.pages.gamemode import GameModePage
from archer.pages.audio_enhance import AudioEnhancePage
from archer.pages.firmware import FirmwarePage


class ArcherWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(
            default_width=1000,
            default_height=700,
            title="Archer",
            **kwargs,
        )

        self.client = ArcherClient()
        self.settings_data = None
        self._monitoring_timer = None

        # Load CSS
        self._load_css()

        # Build UI
        self._build_ui()

        # Initial data load
        GLib.timeout_add(100, self._initial_load)

    def _load_css(self):
        css_path = os.path.join(os.path.dirname(__file__), "style.css")
        if os.path.exists(css_path):
            provider = Gtk.CssProvider()
            provider.load_from_path(css_path)
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(),
                provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )

    def _build_ui(self):
        # Toast overlay for notifications
        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)

        # Main layout
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toast_overlay.set_child(main_box)

        # Header bar with view switcher
        header = Adw.HeaderBar()
        self.view_switcher_title = Adw.ViewSwitcherTitle(title="Archer")
        header.set_title_widget(self.view_switcher_title)

        # Connection status indicator
        self.status_label = Gtk.Label(label="Connecting...")
        self.status_label.add_css_class("status-label")
        header.pack_end(self.status_label)

        main_box.append(header)

        # View stack for tab pages
        self.view_stack = Adw.ViewStack()
        self.view_stack.set_vexpand(True)
        self.view_switcher_title.set_stack(self.view_stack)

        # Bottom view switcher bar (for narrow windows)
        switcher_bar = Adw.ViewSwitcherBar(stack=self.view_stack)
        self.view_switcher_title.connect(
            "notify::title-visible",
            lambda obj, _: switcher_bar.set_reveal(obj.get_title_visible()),
        )

        # Create pages
        self.dashboard_page = DashboardPage(self.client)
        self.performance_page = PerformancePage(self.client)
        self.battery_page = BatteryPage(self.client)
        self.keyboard_page = KeyboardPage(self.client)
        self.system_page = SystemPage(self.client)
        self.internals_page = InternalsPage(self.client)
        self.display_page = DisplayPage(self.client)
        self.gamemode_page = GameModePage(self.client)
        self.audio_enhance_page = AudioEnhancePage(self.client)
        self.firmware_page = FirmwarePage(self.client)

        # Add pages to stack
        self.view_stack.add_titled_with_icon(
            self.dashboard_page, "dashboard", "Dashboard", "utilities-system-monitor-symbolic"
        )
        self.view_stack.add_titled_with_icon(
            self.performance_page, "performance", "Performance", "power-profile-performance-symbolic"
        )
        self.view_stack.add_titled_with_icon(
            self.battery_page, "battery", "Battery", "battery-symbolic"
        )
        self.view_stack.add_titled_with_icon(
            self.keyboard_page, "keyboard", "Keyboard", "input-keyboard-symbolic"
        )
        self.view_stack.add_titled_with_icon(
            self.system_page, "system", "System", "preferences-system-symbolic"
        )
        self.view_stack.add_titled_with_icon(
            self.internals_page, "internals", "Internals", "applications-engineering-symbolic"
        )
        self.view_stack.add_titled_with_icon(
            self.display_page, "display", "Display Mode", "video-display-symbolic"
        )
        self.view_stack.add_titled_with_icon(
            self.gamemode_page, "gamemode", "Game Mode", "applications-games-symbolic"
        )
        self.view_stack.add_titled_with_icon(
            self.audio_enhance_page, "audio_enhance", "Audio", "audio-input-microphone-symbolic"
        )
        self.view_stack.add_titled_with_icon(
            self.firmware_page, "firmware", "Firmware", "computer-symbolic"
        )

        main_box.append(self.view_stack)
        main_box.append(switcher_bar)

    def _initial_load(self):
        """Load initial settings from daemon."""
        thread = threading.Thread(target=self._fetch_settings, daemon=True)
        thread.start()
        return False  # Don't repeat

    def _fetch_settings(self):
        data = self.client.get_all_settings()
        GLib.idle_add(self._on_settings_loaded, data)

    def _on_settings_loaded(self, data):
        if data:
            self.settings_data = data
            self.status_label.set_label("Connected")
            self.status_label.remove_css_class("status-disconnected")
            self.status_label.add_css_class("status-connected")

            # Push settings to all pages
            features = data.get("features", [])
            self.dashboard_page.load_settings(data)
            self.performance_page.load_settings(data)
            self.battery_page.load_settings(data)
            self.keyboard_page.load_settings(data)
            self.system_page.load_settings(data)
            self.internals_page.load_settings(data)
            self.display_page.load_settings(data)
            self.gamemode_page.load_settings(data)
            self.audio_enhance_page.load_settings(data)
            self.firmware_page.load_settings(data)

            # Start monitoring timer
            self._start_monitoring()
        else:
            self.status_label.set_label("Daemon Offline")
            self.status_label.remove_css_class("status-connected")
            self.status_label.add_css_class("status-disconnected")

            # Retry connection in 5 seconds
            GLib.timeout_add_seconds(5, self._retry_connect)

        return False

    def _retry_connect(self):
        thread = threading.Thread(target=self._fetch_settings, daemon=True)
        thread.start()
        return False

    def _start_monitoring(self):
        """Start polling monitoring data every 2 seconds."""
        if self._monitoring_timer:
            return
        self._monitoring_timer = GLib.timeout_add_seconds(2, self._poll_monitoring)

    def _poll_monitoring(self):
        thread = threading.Thread(target=self._fetch_monitoring, daemon=True)
        thread.start()
        return True  # Keep repeating

    def _fetch_monitoring(self):
        data = self.client.get_monitoring_data()
        if data:
            GLib.idle_add(self.dashboard_page.update_monitoring, data)

    def add_toast(self, toast):
        """Show a toast notification."""
        self.toast_overlay.add_toast(toast)
