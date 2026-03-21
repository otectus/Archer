#!/usr/bin/env python3
"""
Archer Compatibility Suite - GUI Application
Requires: python-gobject, gtk4, libadwaita
"""

import sys
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw

from archer.application import ArcherApplication


def main():
    app = ArcherApplication()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
