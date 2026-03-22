"""
Archer GUI Application entry point.
Supports system tray via D-Bus StatusNotifierItem (close-to-tray behavior).
"""

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio

from archer.window import ArcherWindow

# Try to load the D-Bus tray module
HAS_TRAY = False
try:
    from archer.tray import StatusNotifierItem
    HAS_TRAY = True
except Exception:
    pass


class ArcherApplication(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="io.github.archer.gui",
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.window = None
        self._tray = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        # Keep the app alive even when all windows are hidden
        self.hold()

        if HAS_TRAY:
            try:
                self._tray = StatusNotifierItem(
                    on_activate=self._tray_open,
                    on_quit=self._tray_exit,
                )
                self._tray.start()
            except Exception:
                self._tray = None

    def do_activate(self):
        if not self.window:
            self.window = ArcherWindow(application=self)
            self.window.connect("close-request", self._on_close_request)
        self.window.present()

    def _on_close_request(self, window):
        """Hide window instead of destroying it (minimize to tray)."""
        if self._tray is not None:
            window.set_visible(False)
            return True  # Prevent window destruction
        # No tray available — quit normally
        return False

    def _tray_open(self):
        """Re-present the main window from tray."""
        if self.window:
            self.window.set_visible(True)
            self.window.present()
        else:
            self.do_activate()

    def _tray_exit(self):
        """Fully quit the application."""
        if self._tray:
            self._tray.stop()
            self._tray = None
        self.release()
        self.quit()
