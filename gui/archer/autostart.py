"""Desktop login startup with an unprivileged, per-user XDG override."""

import configparser
import os
from pathlib import Path
import tempfile


DESKTOP_FILE = "io.github.archer.desktop"


class Autostart:
    def __init__(self):
        config_home = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
        self.path = Path(config_home) / "autostart" / DESKTOP_FILE
        self.system_paths = [Path(directory) / "autostart" / DESKTOP_FILE
                             for directory in (os.environ.get("XDG_CONFIG_DIRS") or "/etc/xdg").split(":")
                             if Path(directory).is_absolute()]

    def is_enabled(self):
        for path in [self.path, *self.system_paths]:
            if not path.exists():
                continue
            parser = configparser.ConfigParser(interpolation=None)
            try:
                parser.read_string(path.read_text())
                entry = parser["Desktop Entry"]
                return (not entry.getboolean("Hidden", fallback=False)
                        and entry.getboolean("X-GNOME-Autostart-enabled", fallback=True))
            except (OSError, KeyError, ValueError, configparser.Error):
                return False
        return False

    def set_enabled(self, enabled):
        # Use the same launcher as the application menu, including TryExec so
        # a retained user preference cannot launch Archer after uninstalling.
        template = Path(__file__).resolve().parents[1] / DESKTOP_FILE
        contents = template.read_text() + f"Hidden={'false' if enabled else 'true'}\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=self.path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(contents)
            temporary.replace(self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
