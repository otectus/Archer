"""Application lifetime, desktop integration and system tray recovery."""

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio

from archer.preferences import Preferences
from archer.pages.controls import PROFILES
from archer.window import ArcherWindow

logger = logging.getLogger("archer-gui")


class ArcherApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.archer.gui", flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.window = None
        self._tray = None
        self.tray_available = False
        self._watcher = 0
        self._tray_property_watch = 0
        self._tray_bus = None
        self._tray_profile_pending = False
        self.preferences = Preferences()

    def do_startup(self):
        Adw.Application.do_startup(self)
        self.preferences.apply_appearance()
        self.hold()
        self._watcher = Gio.bus_watch_name(Gio.BusType.SESSION, "org.kde.StatusNotifierWatcher",
                                           Gio.BusNameWatcherFlags.NONE, self._tray_appeared, self._tray_vanished)

    def _tray_appeared(self, connection=None, *_):
        if connection is not None and not self._tray_property_watch:
            self._tray_bus = connection
            self._tray_property_watch = connection.signal_subscribe(
                "org.kde.StatusNotifierWatcher", "org.freedesktop.DBus.Properties",
                "PropertiesChanged", "/StatusNotifierWatcher", "org.kde.StatusNotifierWatcher",
                Gio.DBusSignalFlags.NONE, self._tray_properties)
        if self._tray is not None:
            return
        try:
            from archer.tray import StatusNotifierItem
            self._tray = StatusNotifierItem(on_activate=self.activate, on_quit=self._request_quit,
                                            on_profile=self._select_profile)
            self._sync_tray_profiles()
            self._tray.start()
            self.tray_available = True
        except Exception as error:
            logger.info("System tray unavailable: %s", error)
            if self._tray:
                self._tray.stop()
            self._tray = None
            self.tray_available = False

    def _tray_properties(self, connection, _sender, _path, _interface, _signal, parameters):
        _interface_name, changed, _invalidated = parameters.unpack()
        if "IsStatusNotifierHostRegistered" in changed:
            if changed["IsStatusNotifierHostRegistered"]:
                self._tray_appeared(connection)
            else:
                self._tray_vanished()

    def _tray_vanished(self, *_):
        self.tray_available = False
        if self._tray:
            self._tray.stop()
            self._tray = None
        if self.window and not self.window.get_visible():
            self.window.present()

    def do_activate(self):
        if not self.window:
            self.window = ArcherWindow(application=self, preferences=self.preferences)
            self._bind_tray_profiles()
        self.window.present()

    def _bind_tray_profiles(self):
        state = self.window.state
        state.connect("settings-changed", self._sync_tray_profiles)
        state.connect("connection-changed", self._sync_tray_profiles)
        form = self.window.pages["performance"].profiles
        form.group.connect("notify::sensitive", self._sync_tray_profiles)
        form.connect("completed", self._profile_completed)
        self._sync_tray_profiles()

    def _sync_tray_profiles(self, *_):
        if not self._tray or not self.window:
            return
        state = self.window.state
        form = self.window.pages["performance"].profiles
        choices = state.settings.get("thermal_choices", [])
        profiles = [(key, title) for key, title, _ in PROFILES if key in choices]
        enabled = (not state.closed and state.writable and not form.pending and form.allowed
                   and "thermal_profiles" in state.settings.get("features", []))
        self._tray.update_profiles(profiles, state.settings.get("thermal_profile"), enabled)

    def _select_profile(self, profile):
        if not self.window:
            return
        state = self.window.state
        page = self.window.pages["performance"]
        form = page.profiles
        if (state.closed or not state.writable or form.pending or not form.allowed
                or "thermal_profiles" not in state.settings.get("features", [])
                or profile not in state.settings.get("thermal_choices", [])
                or profile not in page.profile_rows or profile == state.settings.get("thermal_profile")):
            return
        self._tray_profile_pending = True
        # Select through the same immediate form as the full GUI.
        form.loading = True
        try:
            page._set_profile(profile)
        finally:
            form.loading = False
        form.submit()

    def _profile_completed(self, form, success):
        self._sync_tray_profiles()
        if self._tray_profile_pending:
            self._tray_profile_pending = False
            if not success:
                self.window.navigate("performance")
                self.window.present()

    def _request_quit(self):
        if self.window:
            self.window.present()
            self.window.request_quit()
        else:
            self.finish_quit()

    def finish_quit(self):
        if self.window:
            self.window.dispose_resources()
        if self._watcher:
            Gio.bus_unwatch_name(self._watcher)
            self._watcher = 0
        if self._tray_property_watch and self._tray_bus:
            self._tray_bus.signal_unsubscribe(self._tray_property_watch)
            self._tray_property_watch = 0
        if self._tray:
            self._tray.stop()
            self._tray = None
        self.release()
        self.quit()
