"""
Display Mode Manager page - GPU mode switching, MUX detection, Optimus info.
"""

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

import threading


_GPU_MODES = [
    ("Integrated", "video-display-symbolic", "integrated",
     "Uses only the integrated GPU. Best battery life."),
    ("Hybrid", "system-run-symbolic", "hybrid",
     "Automatically switches between integrated and discrete GPU."),
    ("NVIDIA", "applications-games-symbolic", "nvidia",
     "Uses the discrete NVIDIA GPU exclusively. Best performance."),
]


class DisplayPage(Gtk.Box):
    """GPU display mode switching and MUX detection page."""

    def __init__(self, client):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.client = client
        self._current_mode = None
        self._reboot_pending = False
        self._build_ui()

    def _build_ui(self):
        scrolled = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        clamp = Adw.Clamp(maximum_size=900, margin_top=24, margin_bottom=24,
                          margin_start=12, margin_end=12)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)

        # --- Warning Banner ---
        self._warning_banner = Adw.Banner(
            title="Linux Optimus support is limited. Mode switching requires "
                  "a reboot and may not work on all hardware configurations.",
        )
        self._warning_banner.set_revealed(True)
        content.append(self._warning_banner)

        # --- Reboot Required Banner ---
        self._reboot_banner = Adw.Banner(
            title="Reboot required for the display mode change to take effect.",
        )
        self._reboot_banner.add_css_class("error")
        self._reboot_banner.set_button_label("Dismiss")
        self._reboot_banner.connect("button-clicked",
                                    lambda b: b.set_revealed(False))
        self._reboot_banner.set_revealed(False)
        content.append(self._reboot_banner)

        # --- Current Mode ---
        self._status_group = Adw.PreferencesGroup(title="Current Display Mode")

        self._mode_row = Adw.ActionRow(title="Active Mode", subtitle="--")
        self._mode_row.add_suffix(Gtk.Image(icon_name="video-display-symbolic"))
        self._status_group.add(self._mode_row)

        content.append(self._status_group)

        # --- MUX Detection ---
        self._mux_group = Adw.PreferencesGroup(
            title="Hardware MUX",
            description="A hardware MUX switch allows direct GPU-to-display "
                        "connections, reducing latency in discrete GPU mode.",
        )

        self._mux_row = Adw.ActionRow(title="MUX Switch", subtitle="Detecting...")
        self._mux_group.add(self._mux_row)

        content.append(self._mux_group)

        # --- Mode Selection ---
        self._mode_group = Adw.PreferencesGroup(
            title="Switch Display Mode",
            description="Select a GPU mode. A reboot is required after switching.",
        )

        self._mode_rows = {}
        for label, icon_name, mode_key, description in _GPU_MODES:
            row = Adw.ActionRow(
                title=label,
                subtitle=description,
                activatable=True,
            )
            row.add_prefix(Gtk.Image(icon_name=icon_name))

            btn = Gtk.Button(label="Select")
            btn.add_css_class("suggested-action")
            btn.set_valign(Gtk.Align.CENTER)
            btn.connect("clicked", self._on_mode_selected, mode_key)
            row.add_suffix(btn)

            self._mode_rows[mode_key] = (row, btn)
            self._mode_group.add(row)

        content.append(self._mode_group)

        clamp.set_child(content)
        scrolled.set_child(clamp)
        self.append(scrolled)

    def load_settings(self, data):
        features = data.get("features", [])
        has_display_mode = "display_mode" in features

        self._status_group.set_visible(has_display_mode)
        self._mode_group.set_visible(has_display_mode)

        if has_display_mode:
            mode = data.get("display_mode", "hybrid")
            self._current_mode = mode
            self._update_mode_display(mode)

        # MUX detection
        mux_info = data.get("mux_switch", None)
        if mux_info is not None:
            detected = mux_info.get("detected", False)
            self._mux_row.set_subtitle(
                "Detected" if detected else "Not detected"
            )
            if detected:
                self._mux_row.add_suffix(
                    Gtk.Image(icon_name="emblem-ok-symbolic")
                )
        else:
            self._mux_row.set_subtitle("Not available")
            self._mux_group.set_visible(False)

        # Reboot pending
        pending = data.get("display_mode_reboot_pending", False)
        self._reboot_pending = pending
        self._reboot_banner.set_revealed(pending)

    def _update_mode_display(self, mode):
        """Update the current mode label and button states."""
        mode_labels = {
            "integrated": "Integrated",
            "hybrid": "Hybrid",
            "nvidia": "NVIDIA",
        }
        self._mode_row.set_subtitle(mode_labels.get(mode, mode.title()))

        for mode_key, (row, btn) in self._mode_rows.items():
            if mode_key == mode:
                btn.set_label("Active")
                btn.set_sensitive(False)
                btn.remove_css_class("suggested-action")
                btn.add_css_class("success")
            else:
                btn.set_label("Select")
                btn.set_sensitive(True)
                btn.add_css_class("suggested-action")
                btn.remove_css_class("success")

    def _on_mode_selected(self, button, mode_key):
        """Handle mode selection button click."""
        button.set_sensitive(False)
        button.set_label("Applying...")

        def _apply():
            resp = self.client._send_command("set_display_mode", {"mode": mode_key})
            success = resp.get("success", False)

            def _update():
                if success:
                    self._current_mode = mode_key
                    self._update_mode_display(mode_key)
                    self._reboot_banner.set_revealed(True)
                    self._reboot_pending = True
                else:
                    button.set_sensitive(True)
                    button.set_label("Select")

            GLib.idle_add(_update)

        threading.Thread(target=_apply, daemon=True).start()
