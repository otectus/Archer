"""Unprivileged application preferences, independent of hardware settings."""

import json
import logging
import os
from pathlib import Path


class Preferences:
    DEFAULTS = {"appearance": "system", "width": 1100, "height": 760,
                "maximized": False, "close_to_tray": True}

    def __init__(self, path=None):
        self.path = Path(path) if path else Path(
            os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
        ) / "archer/gui.json"
        self.data = dict(self.DEFAULTS)
        try:
            loaded = json.loads(self.path.read_text())
            if isinstance(loaded, dict):
                for key, default in self.DEFAULTS.items():
                    if type(loaded.get(key)) is type(default):
                        self.data[key] = loaded[key]
        except (OSError, ValueError):
            pass
        if self.data["appearance"] not in ("system", "light", "dark"):
            self.data["appearance"] = "system"
        self.data["width"] = min(3840, max(360, self.data["width"]))
        self.data["height"] = min(2160, max(600, self.data["height"]))

    def save(self, **values):
        self.data.update(values)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.data, indent=2) + "\n")
            temporary.replace(self.path)
        except OSError as error:
            logging.getLogger("archer-gui").warning("Preferences not saved: %s", error)

    def apply_appearance(self):
        from gi.repository import Adw
        schemes = {"system": Adw.ColorScheme.DEFAULT,
                   "light": Adw.ColorScheme.FORCE_LIGHT,
                   "dark": Adw.ColorScheme.FORCE_DARK}
        Adw.StyleManager.get_default().set_color_scheme(schemes[self.data["appearance"]])
