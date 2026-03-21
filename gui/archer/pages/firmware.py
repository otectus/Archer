"""
Firmware Info page - BIOS version, fwupd support, firmware updates.
"""

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

import threading


class FirmwarePage(Gtk.Box):
    """Firmware information and update page."""

    def __init__(self, client):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.client = client
        self._build_ui()

    def _build_ui(self):
        scrolled = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        clamp = Adw.Clamp(maximum_size=900, margin_top=24, margin_bottom=24,
                          margin_start=12, margin_end=12)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)

        # --- Firmware Info ---
        self._info_group = Adw.PreferencesGroup(title="Firmware Information")

        self._bios_row = Adw.ActionRow(title="BIOS Version", subtitle="--")
        self._bios_row.add_prefix(Gtk.Image(icon_name="computer-symbolic"))
        self._info_group.add(self._bios_row)

        self._fwupd_row = Adw.ActionRow(title="fwupd Service", subtitle="Checking...")
        self._fwupd_row.add_prefix(Gtk.Image(icon_name="emblem-system-symbolic"))
        self._info_group.add(self._fwupd_row)

        self._vendor_row = Adw.ActionRow(title="Firmware Vendor", subtitle="--")
        self._vendor_row.add_prefix(Gtk.Image(icon_name="emblem-documents-symbolic"))
        self._info_group.add(self._vendor_row)

        content.append(self._info_group)

        # --- Check for Updates ---
        self._update_group = Adw.PreferencesGroup(
            title="Firmware Updates",
            description="Check for available firmware updates via fwupd.",
        )

        # Check button row
        check_row = Adw.PreferencesRow()
        check_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=6,
                              margin_bottom=12, margin_start=12, margin_end=12)

        self._check_button = Gtk.Button(label="Check for Updates")
        self._check_button.add_css_class("suggested-action")
        self._check_button.set_halign(Gtk.Align.CENTER)
        self._check_button.connect("clicked", self._on_check_updates)
        check_inner.append(self._check_button)

        self._check_spinner = Gtk.Spinner()
        self._check_spinner.set_halign(Gtk.Align.CENTER)
        self._check_spinner.set_visible(False)
        check_inner.append(self._check_spinner)

        check_row.set_child(check_inner)
        self._update_group.add(check_row)

        # Status row
        self._update_status_row = Adw.ActionRow(
            title="Update Status",
            subtitle="Not checked yet",
        )
        self._update_group.add(self._update_status_row)

        content.append(self._update_group)

        # --- Available Updates List ---
        self._updates_group = Adw.PreferencesGroup(title="Available Updates")
        self._updates_group.set_visible(False)
        self._no_updates_row = Adw.ActionRow(
            title="No updates available",
            subtitle="Your firmware is up to date.",
            icon_name="emblem-ok-symbolic",
        )
        self._updates_group.add(self._no_updates_row)

        content.append(self._updates_group)

        clamp.set_child(content)
        scrolled.set_child(clamp)
        self.append(scrolled)

    def load_settings(self, data):
        fw_info = data.get("firmware_info", {})

        bios = fw_info.get("bios_version", None)
        self._bios_row.set_subtitle(bios if bios else "--")

        fwupd = fw_info.get("fwupd_available", None)
        if fwupd is True:
            self._fwupd_row.set_subtitle("Available")
        elif fwupd is False:
            self._fwupd_row.set_subtitle("Not available")
            self._check_button.set_sensitive(False)
            self._update_status_row.set_subtitle(
                "fwupd is not installed. Install fwupd to check for firmware updates."
            )
        else:
            self._fwupd_row.set_subtitle("Unknown")

        vendor = fw_info.get("vendor", None)
        self._vendor_row.set_subtitle(vendor if vendor else "--")

        # Display any updates already known
        updates = fw_info.get("updates", [])
        self._display_updates(updates)

    def _on_check_updates(self, button):
        """Handle check for updates button click."""
        button.set_sensitive(False)
        self._check_spinner.set_visible(True)
        self._check_spinner.start()
        self._update_status_row.set_subtitle("Checking for updates...")

        def _fetch():
            resp = self.client._send_command("get_firmware_info")
            success = resp.get("success", False)
            fw_data = resp.get("data", {}) if success else {}

            def _update():
                self._check_spinner.stop()
                self._check_spinner.set_visible(False)
                button.set_sensitive(True)

                if success:
                    bios = fw_data.get("bios_version", None)
                    if bios:
                        self._bios_row.set_subtitle(bios)

                    vendor = fw_data.get("vendor", None)
                    if vendor:
                        self._vendor_row.set_subtitle(vendor)

                    fwupd = fw_data.get("fwupd_available", None)
                    if fwupd is True:
                        self._fwupd_row.set_subtitle("Available")
                    elif fwupd is False:
                        self._fwupd_row.set_subtitle("Not available")

                    updates = fw_data.get("updates", [])
                    self._display_updates(updates)

                    if updates:
                        self._update_status_row.set_subtitle(
                            f"{len(updates)} update(s) available"
                        )
                    else:
                        self._update_status_row.set_subtitle(
                            "No updates available. Firmware is up to date."
                        )
                else:
                    error = resp.get("error", "Unknown error")
                    self._update_status_row.set_subtitle(f"Check failed: {error}")

            GLib.idle_add(_update)

        threading.Thread(target=_fetch, daemon=True).start()

    def _display_updates(self, updates):
        """Show or hide the updates list."""
        if not updates:
            self._updates_group.set_visible(False)
            return

        self._updates_group.set_visible(True)
        self._no_updates_row.set_visible(False)

        # Remove old dynamic rows (keep the no_updates_row)
        # Re-add fresh update rows
        for update in updates:
            name = update.get("name", "Unknown Device")
            version = update.get("version", "")
            summary = update.get("summary", "")

            row = Adw.ActionRow(
                title=f"{name} - {version}" if version else name,
                subtitle=summary,
            )
            row.add_prefix(Gtk.Image(icon_name="software-update-available-symbolic"))
            self._updates_group.add(row)
