#!/usr/bin/env python3
"""
Archer Compatibility Suite - GUI Application
Requires: python-gobject, gtk4, libadwaita
"""

import sys
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk

from archer.application import ArcherApplication


def main():
    if (Gtk.get_major_version(), Gtk.get_minor_version()) < (4, 12) or (Adw.get_major_version(), Adw.get_minor_version()) < (1, 6):
        print("Archer requires GTK 4.12 and libadwaita 1.6 or newer. Update your system packages.", file=sys.stderr)
        return 1
    app = ArcherApplication()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
