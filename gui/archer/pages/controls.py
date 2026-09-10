"""Task-oriented hardware pages, all using the same confirmed-state forms."""

import re
import subprocess
import threading
from datetime import datetime

from gi.repository import Adw, Gdk, GLib, Gtk, PangoCairo

from archer.widgets.forms import Page, label


PROFILES = [
    ("low-power", "Eco", "Longer battery life with lower performance."),
    ("quiet", "Quiet", "Reduce fan noise for lighter tasks."),
    ("balanced", "Balanced", "A balance of speed, noise, and battery life."),
    ("balanced-performance", "Performance", "More performance while connected to power."),
    ("performance", "Turbo", "Maximum performance with more heat and fan noise."),
]

# The effects the daemon can actually drive, in the order archer_ene.EFFECTS
# maps them: the value saved for "mode" is an index into that list. The
# previous list came from the WMI documentation, whose effect field this
# firmware ignores, so its entries neither matched nor did anything. Keep the
# two in step or the wrong effect runs.
KEYBOARD_EFFECTS = [
    "Static", "Breathing", "Neon", "Neon (fast)", "Wave",
    "Meteor", "Zoom", "Shifting", "Twinkling",
]


def supported(form, features, feature):
    form.set_visible(feature in features)


def link(group, title, subtitle, callback, icon="go-next-symbolic"):
    row = Adw.ActionRow(title=title, subtitle=subtitle, activatable=True)
    row.add_suffix(Gtk.Image(icon_name=icon))
    row.connect("activated", lambda *_: callback())
    group.add(row)
    return row


class PerformancePage(Page):
    def __init__(self, state):
        super().__init__("Performance", "Find the right balance of speed, cooling, and battery life.", state)
        client = state.client
        self.power = self.form("Power source").info("Power source")
        self.profiles = self.form("Performance profile", save=lambda v: client.set_thermal_profile(v["profile"]), immediate=True)
        self.profile_rows = {}
        first = None
        for key, title, description in PROFILES:
            row = Adw.ActionRow(title=title, subtitle=description, activatable=True)
            button = Gtk.CheckButton(valign=Gtk.Align.CENTER)
            button.update_property([Gtk.AccessibleProperty.LABEL], [title])
            if first is None:
                first = button
            else:
                button.set_group(first)
            row.add_prefix(button)
            row.set_activatable_widget(button)
            self.profiles.group.add(row)
            button.connect("toggled", lambda btn: self.profiles.changed() if btn.get_active() else None)
            self.profile_rows[key] = (row, button)
        self.profiles.fields["profile"] = (self._profile, self._set_profile)
        self.fans = self.form("Fan control", "Manual speeds are percentages of the available fan output. Zero returns a fan to automatic control.",
                              save=self._save_fans, action="Apply fan control")
        self.fan_mode = self.fans.combo("mode", "Fan mode", ["Automatic", "Maximum", "Manual"])
        self.cpu = self.fans.spin("cpu", "CPU fan (%)", 0, 100)
        self.gpu = self.fans.spin("gpu", "GPU fan (%)", 0, 100)
        self.fan_mode.connect("notify::selected", lambda *_: self._manual())
        self.curve = self.fans.info("Current control")
        self.game = self.form("Game Mode", "Temporarily requests the performance thermal profile, CPU energy preference, and NVIDIA persistence when available. Uses more power and may increase fan noise.",
                              save=lambda v: client.set_game_mode(v["enabled"]), immediate=True)
        self.game.switch("enabled", "Game Mode", "Use AC power for best results.")
        state.connect("settings-changed", self.load)
        state.connect("telemetry", self.monitor)

    def _profile(self):
        return next((key for key, (_, btn) in self.profile_rows.items() if btn.get_active()), "balanced")

    def _set_profile(self, key):
        for name, (_, btn) in self.profile_rows.items():
            btn.set_active(name == key)

    def _manual(self):
        manual = self.fan_mode.get_selected() == 2
        self.cpu.set_visible(manual)
        self.gpu.set_visible(manual)

    def _save_fans(self, values):
        mode = values["mode"]
        cpu, gpu = (0, 0) if mode == 0 else (100, 100) if mode == 1 else (values["cpu"], values["gpu"])
        return self.state.client.apply_fan_mode(cpu, gpu)

    def monitor(self, _state, data):
        ac = data.get("power_source_ac")
        self.power.set_subtitle("Connected to AC power" if ac else "Running on battery")

    def load(self, _state, data):
        features = data.get("features", [])
        self.availability(any(f in features for f in ("fan_control", "thermal_profiles", "game_mode")))
        self.monitor(_state, data)
        supported(self.profiles, features, "thermal_profiles")
        supported(self.fans, features, "fan_control")
        supported(self.game, features, "game_mode")
        choices = data.get("thermal_choices", [])
        for key, title, description in PROFILES:
            row, _ = self.profile_rows[key]
            available = key in choices
            # Native providers (e.g. AMD PMF) do not share Predator AC rules.
            # The provider validates transitions and returns any restriction.
            row.set_sensitive(available)
            row.set_subtitle(description if available else "Not supported on this device.")
        self.profiles.load({"profile": data.get("thermal_profile") or "balanced"})
        cpu, gpu = data.get("fan_speed_cpu") or 0, data.get("fan_speed_gpu") or 0
        mode = 0 if cpu == gpu == 0 else 1 if cpu == gpu == 100 else 2
        self.fans.load({"mode": mode, "cpu": cpu, "gpu": gpu})
        self._manual()
        curves = data.get("fan_curve") or {}
        active = any(isinstance(value, dict) and value.get("active", value.get("enabled", False)) for value in curves.values())
        self.curve.set_subtitle("Custom curve active — applying a mode will stop the curve." if active else ["Automatic", "Maximum", "Manual"][mode])
        self.game.load({"enabled": data.get("game_mode", False)})


class BatteryPage(Page):
    def __init__(self, state):
        super().__init__("Battery", "Protect battery health and stay informed about your charge.", state)
        self.status = self.form("Battery status")
        self.charge = self.status.info("Charge")
        self.source = self.status.info("Status")
        self.remaining = self.status.info("Time remaining")
        self.limit = self.form("Charge protection", "Limit charging to 80% when you regularly use AC power.",
                               save=lambda v: state.client.set_battery_limiter(v["enabled"]), immediate=True)
        self.limit.switch("enabled", "80% charge limit")
        self.calibration = self.form("Battery calibration", "A full charge–discharge cycle recalibrates the battery gauge. Keep AC power connected throughout.",
                                     save=lambda _: state.client.set_battery_calibration(not self.calibrating),
                                     action="Start calibration",
                                     confirmation="Keep AC power connected throughout the calibration cycle. Start only when you can leave your laptop connected.")
        self.cal_status = self.calibration.info("Calibration status")
        self.calibrating = False
        self.usb = self.form("USB charging when off", "Choose the battery level at which off-state USB charging stops.",
                             save=lambda v: state.client.set_usb_charging([0, 10, 20, 30][v["level"]]), immediate=True)
        self.usb.combo("level", "Charging limit", ["Disabled", "Stop at 10%", "Stop at 20%", "Stop at 30%"])
        state.connect("settings-changed", self.load)
        state.connect("telemetry", self.monitor)

    def monitor(self, _state, data):
        battery = data.get("battery_info") or {}
        if battery:
            self.charge.set_subtitle(f"{battery['percentage']:.0f}%" if battery.get("percentage") is not None else "Unavailable")
            self.source.set_subtitle(str(battery.get("status") or "Unknown"))
            self.remaining.set_subtitle(battery.get("time_remaining") or "Estimating…")
        ac = data.get("power_source_ac", False)
        self.calibration.allowed = self.calibrating or ac
        self.calibration._sensitivity()
        self.cal_status.set_subtitle("Calibration running" if self.calibrating else "Ready" if ac else "Connect AC power to start")

    def load(self, _state, data):
        features = data.get("features", [])
        present = (data.get("battery_info") or {}).get("present", False)
        self.availability(present, "No battery was detected. Charging controls are unavailable.")
        for form, feature in ((self.status, "battery_info"), (self.limit, "battery_limiter"),
                              (self.calibration, "battery_calibration"), (self.usb, "usb_charging")):
            form.set_visible(present and (feature in features or form is self.status))
        self.limit.load({"enabled": bool(data.get("battery_limiter"))})
        self.calibrating = bool(data.get("battery_calibration"))
        self.calibration.apply_button.set_label("Stop calibration" if self.calibrating else "Start calibration")
        self.calibration.confirmation = None if self.calibrating else "Keep AC power connected throughout the calibration cycle. Start only when you can leave your laptop connected."
        level = str(data.get("usb_charging", 0))
        self.usb.load({"level": {"0": 0, "10": 1, "20": 2, "30": 3}.get(level, 0)})
        self.monitor(_state, data)


class DisplayPage(Page):
    MODES = ["integrated", "hybrid", "nvidia"]

    def __init__(self, state):
        super().__init__("Display", "Choose how your graphics hardware powers the display.", state)
        self.restart = Adw.Banner(title="Restart required to use the configured graphics mode.")
        self.content.append(self.restart)
        self.graphics = self.form("Graphics mode", "Integrated saves power. Hybrid uses integrated graphics with NVIDIA on demand. NVIDIA favors discrete graphics performance.",
                                  save=lambda v: state.client.set_display_mode(self.MODES[v["mode"]]), action="Apply graphics mode",
                                  confirmation="This changes the graphics configuration. Save your work and restart your computer afterward for the change to take effect.")
        self.configured = self.graphics.info("Configured mode")
        self.mode_rows = self.graphics.radio("mode", [
            ("Integrated", "Use integrated graphics for longer battery life."),
            ("Hybrid", "Use integrated graphics with NVIDIA available on demand."),
            ("NVIDIA", "Use discrete graphics for maximum GPU performance."),
        ])
        self.mux = self.graphics.info("Hardware MUX", "Detection unavailable")
        self.lcd = self.form("Panel response", save=lambda v: state.client.set_lcd_override(v["enabled"]), immediate=True)
        self.lcd.switch("enabled", "LCD override", "Reduce ghosting on supported panels. May increase power consumption.")
        state.connect("settings-changed", self.load)

    def load(self, _state, data):
        features = data.get("features", [])
        self.availability(any(f in features for f in ("display_mode", "lcd_override")))
        supported(self.graphics, features, "display_mode")
        supported(self.lcd, features, "lcd_override")
        display = data.get("display_mode") or {}
        mode = display.get("mode", "unknown")
        self.configured.set_subtitle(mode.title() if mode != "unknown" else "Unknown")
        self.graphics.load({"mode": self.MODES.index(mode) if mode in self.MODES else 1})
        for key, row in zip(self.MODES, self.mode_rows):
            row.set_sensitive(key in display.get("available_modes", self.MODES))
        self.restart.set_revealed(bool(display.get("reboot_required")))
        self.lcd.load({"enabled": bool(data.get("lcd_override"))})


class KeyboardPage(Page):
    def __init__(self, state):
        super().__init__("Keyboard", "Make your lighting your own. Preview changes, then apply them.", state)
        self.zones = self.form("Static zone colors", "Zones run from left to right. Colors use #RRGGBB notation.",
                               save=self._save_zones, action="Apply zone colors")
        self.preview = Gtk.DrawingArea(content_height=86, hexpand=True)
        self.preview.set_draw_func(self._draw_preview)
        self.preview.update_property([Gtk.AccessibleProperty.LABEL], ["Keyboard preview: four color zones, numbered left to right"])
        self.preview.add_css_class("keyboard-preview")
        self.content.insert_child_after(self.preview, self.missing)
        for index in range(4):
            entry = self.zones.color(f"zone{index + 1}", f"Zone {index + 1}")
            entry.connect("changed", lambda *_: self.preview.queue_draw())
        self.zones.spin("brightness", "Brightness (%)", 0, 100)
        self.effects = self.form("Lighting effects", "Effect settings apply separately from static zone colors.",
                                 save=self._save_effect, action="Apply effect")
        self.effects.combo("mode", "Effect", KEYBOARD_EFFECTS)
        self.effects.spin("speed", "Speed", 0, 9)
        self.effects.spin("brightness", "Brightness (%)", 0, 100)
        self.effects.color("color", "Effect color")
        self.effects.combo("direction", "Direction", ["Left to right", "Right to left"])
        self.backlight = self.form("Backlight", save=lambda v: state.client.set_backlight_timeout(v["enabled"]), immediate=True)
        self.backlight.switch("enabled", "Turn off after 30 seconds of inactivity")
        state.connect("settings-changed", self.load)
        self.watch_style(self.preview.queue_draw)

    @staticmethod
    def _color(value):
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("Enter colors as #RRGGBB, for example #3584e4.")
        return value[1:]

    def _save_zones(self, values):
        colors = [self._color(values[f"zone{i}"]) for i in range(1, 5)]
        return self.state.client.set_per_zone_mode(*colors, values["brightness"])

    def _save_effect(self, values):
        color = self._color(values["color"])
        rgb = [int(color[i:i + 2], 16) for i in (0, 2, 4)]
        return self.state.client.set_four_zone_mode(
            values["mode"], values["speed"], values["brightness"], 2 if values["direction"] == 0 else 1, *rgb)

    def _draw_preview(self, area, cr, width, height):
        values = self.zones.values()
        for index in range(4):
            rgba = Gdk.RGBA()
            rgba.parse(values.get(f"zone{index + 1}", "#808080") or "#808080")
            x = index * width / 4 + 4
            w = width / 4 - 8
            cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, 1)
            cr.rectangle(x, 8, w, height - 16)
            cr.fill()
            luminance = rgba.red * .2126 + rgba.green * .7152 + rgba.blue * .0722
            cr.set_source_rgb(*((0, 0, 0) if luminance > .5 else (1, 1, 1)))
            layout = area.create_pango_layout(str(index + 1))
            text_width, text_height = layout.get_pixel_size()
            cr.move_to(x + (w - text_width) / 2, (height - text_height) / 2)
            PangoCairo.show_layout(cr, layout)

    def load(self, _state, data):
        features = data.get("features", [])
        self.availability(any(f in features for f in ("keyboard_per_zone", "keyboard_effects", "backlight_timeout")))
        for form, feature in ((self.zones, "keyboard_per_zone"), (self.effects, "keyboard_effects"), (self.backlight, "backlight_timeout")):
            supported(form, features, feature)
        saved = data.get("saved_settings") or {}
        zone = saved.get("per_zone_mode") or {}
        self.zones.baseline_unknown = not bool(zone)
        description = "Zones run from left to right. Colors use #RRGGBB notation."
        if not zone:
            description += " No saved colors are available; the preview shows defaults."
        self.zones.group.set_description(description)
        values = {f"zone{i}": "#" + str(zone.get(f"zone{i}", "3584e4")).lstrip("#") for i in range(1, 5)}
        self.zones.load({**values, "brightness": zone.get("brightness", 100)})
        effect = saved.get("four_zone_mode") or {}
        self.effects.baseline_unknown = not bool(effect)
        color = "#{:02x}{:02x}{:02x}".format(*(int(effect.get(k, 0)) for k in ("red", "green", "blue")))
        # The effect list shrank when the WMI-derived entries gave way to the
        # verified ones, so a settings file written by an older build can hold
        # an index past the end of the list. Clamp it rather than let the row
        # silently fall back to the first entry.
        mode = min(int(effect.get("mode", 0)), len(KEYBOARD_EFFECTS) - 1)
        self.effects.load({"mode": mode, "speed": effect.get("speed", 5),
                           "brightness": effect.get("brightness", 100), "color": color,
                           "direction": 0 if effect.get("direction", 2) == 2 else 1})
        self.backlight.load({"enabled": bool(data.get("backlight_timeout"))})


class AudioPage(Page):
    def __init__(self, state):
        super().__init__("Audio", "Make your microphone clearer with background noise suppression.", state)
        self.noise = self.form("Noise suppression", "Changing this setting briefly restarts audio in your session. Ongoing calls and recordings may be interrupted.",
                               save=lambda v: state.client.set_audio_enhancement(v["enabled"]), immediate=True)
        self.noise.switch("enabled", "Noise suppression")
        self.operation = label("", "dim-label")
        self.content.append(self.operation)
        self.setup = self.form("Use in your apps")
        self.setup.info("1. Enable noise suppression", "Turn on the switch above.")
        self.setup.info("2. Open your app’s audio settings", "Find its microphone or input device selection.")
        self.setup.info("3. Select Archer Noise Suppression", "Choose the virtual input instead of your physical microphone.")
        state.connect("settings-changed", self.load)
        state.connect("audio-changed", self.restart_audio)

    def load(self, _state, data):
        available = "audio_enhancement" in data.get("features", [])
        self.availability(available, "Noise suppression is unavailable. Install Archer’s audio enhancement module to enable it.")
        self.noise.set_visible(available)
        self.setup.set_visible(available)
        self.noise.load({"enabled": bool((data.get("audio_enhancement") or {}).get("noise_suppression"))})

    def restart_audio(self, _state, enabled):
        self.operation.set_label("Configuration saved. Restarting audio…")
        self.noise.allowed = False
        self.noise._sensitivity()

        def work():
            try:
                result = subprocess.run(["systemctl", "--user", "restart", "pipewire.service"], capture_output=True, text=True, timeout=20)
                message = ("Noise suppression is ready." if enabled else "Noise suppression is off.") if result.returncode == 0 else "Configuration saved, but audio restart failed: " + result.stderr.strip()
            except (OSError, subprocess.TimeoutExpired) as error:
                message = f"Configuration saved, but audio restart failed: {error}"
            GLib.idle_add(done, message)

        def done(message):
            if not self.state.closed:
                self.operation.set_label(message)
                self.noise.allowed = True
                self.noise._sensitivity()
            return False
        threading.Thread(target=work, daemon=True).start()


class FirmwarePage(Page):
    def __init__(self, state):
        super().__init__("Firmware", "Check for device firmware updates. Archer never installs firmware automatically.", state)
        self.details = self.form("Device firmware")
        self.bios = self.details.info("BIOS version", copyable=True)
        self.vendor = self.details.info("Vendor")
        self.check = self.form("Updates", save=self._check, action="Check for updates")
        self.status = self.check.info("Last check", "Not checked")
        self.updates = Adw.PreferencesGroup(title="Available updates")
        self.content.append(self.updates)
        self.update_rows = []
        state.connect("settings-changed", self.load)

    def _check(self, _values):
        response = self.state.client.get_firmware_info()
        if response.get("data"):
            GLib.idle_add(self._render, response["data"])
        return response

    def _render(self, info):
        if self.state.closed:
            return False
        self.bios.set_subtitle(info.get("bios_version") or "Unknown")
        self.vendor.set_subtitle(info.get("vendor") or "Unknown")
        status = info.get("status", "not_checked")
        message = {"not_checked": "Not checked", "unavailable": "Install fwupd to check for updates.",
                   "current": "No updates available", "updates": "Updates available", "error": "Check failed"}.get(status, "Not checked")
        if info.get("checked_at"):
            message += " · " + datetime.fromtimestamp(info["checked_at"]).strftime("%b %d, %H:%M")
        self.status.set_subtitle(message)
        self.check.allowed = bool(info.get("fwupd_available"))
        self.check._sensitivity()
        for row in self.update_rows:
            self.updates.remove(row)
        self.update_rows.clear()
        for device in info.get("updates") or []:
            releases = device.get("Releases") or [{}]
            release = releases[0]
            name = device.get("Name", device.get("name", "Device"))
            version = release.get("Version", device.get("version", ""))
            summary = release.get("Summary", device.get("summary", ""))
            row = Adw.ActionRow(title=f"{name} {version}".strip(), subtitle=summary)
            self.updates.add(row)
            self.update_rows.append(row)
        self.updates.set_visible(bool(self.update_rows))
        return False

    def load(self, _state, data):
        self.availability(True)
        if not self.check.pending:
            self._render(data.get("firmware_info") or {})


class AdvancedPage(Page):
    def __init__(self, state):
        super().__init__("Advanced", "Driver configuration and recovery tools. Changes here can require a restart.", state)
        self.availability(True)
        self.override = self.form("Persistent model override", "Changes the driver configuration used on subsequent driver loads and reboots.",
                                  save=self._override, action="Apply override", destructive=True,
                                  confirmation="This changes the persistent driver configuration. An incorrect model override can disable hardware controls. Reload the driver or restart afterward.")
        self.current = self.override.info("Saved configuration")
        self.override.combo("mode", "Model override", ["Disabled", "Nitro (nitro_v4)", "Predator (predator_v4)", "Enable all features (enable_all)"])
        self.restart = self.form("Restart Archer service", "Hardware controls will reconnect automatically.",
                                 save=lambda _: state.client.restart_daemon(), action="Restart service")
        self.reload = self.form("Reload drivers and service", "Temporarily interrupts hardware control. You may need to restart your computer if the driver cannot reload.",
                                save=lambda _: state.client.restart_drivers_and_daemon(), action="Reload drivers", destructive=True,
                                confirmation="Hardware controls will be interrupted while the driver and Archer service reload. Continue?")
        self.restart.restart_delay = 3
        self.reload.restart_delay = 5
        self.connection = self.form("Connection")
        self.connection.info("Service", "io.otectus.Archer1 on the system bus")
        self.status = self.connection.info("Status")
        retry = Gtk.Button(label="Retry connection", halign=Gtk.Align.START)
        retry.connect("clicked", lambda *_: state.refresh(reconnect=True))
        self.content.append(retry)
        self.logs_button = Gtk.Button(label="View recent service logs", halign=Gtk.Align.START)
        self.logs_button.get_child().set_wrap(True)
        self.logs_button.connect("clicked", self._logs)
        self.content.append(self.logs_button)
        state.connect("settings-changed", self.load)
        state.connect("connection-changed", lambda _, status: self.status.set_subtitle(
            status + (" — " + state.error if state.error else "")))

    def _override(self, values):
        mode = values["mode"]
        return self.state.client.remove_modprobe_parameter() if mode == 0 else self.state.client.set_modprobe_parameter(["", "nitro_v4", "predator_v4", "enable_all"][mode])

    def load(self, _state, data):
        value = data.get("driver_override", "Unknown (older service)")
        self.current.set_subtitle(value)
        mode = next((index for index, key in enumerate(("", "nitro_v4", "predator_v4", "enable_all")) if key and key in value), 0)
        self.override.load({"mode": mode})

    def _logs(self, _button):
        self.logs_button.set_sensitive(False)

        def read():
            try:
                result = subprocess.run(["journalctl", "-u", "archer-daemon", "-n", "100", "--no-pager"], capture_output=True, text=True, timeout=10)
                text = result.stdout or "No accessible service logs. Your session may not have permission to read the system journal."
                if result.stderr:
                    text += "\n" + result.stderr
            except (OSError, subprocess.TimeoutExpired) as error:
                text = f"Unable to read service logs: {error}"
            GLib.idle_add(show, text)

        def show(text):
            if self.state.closed:
                return False
            self.logs_button.set_sensitive(True)
            dialog = Adw.Dialog(title="Recent service logs", content_width=720, content_height=480)
            toolbar = Adw.ToolbarView()
            header = Adw.HeaderBar()
            copy = Gtk.Button(label="Copy")
            copy.connect("clicked", lambda *_: self.get_clipboard().set(text))
            header.pack_end(copy)
            toolbar.add_top_bar(header)
            view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True,
                                wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=16, right_margin=16)
            view.get_buffer().set_text(text)
            scroll = Gtk.ScrolledWindow(vexpand=True)
            scroll.set_child(view)
            toolbar.set_content(scroll)
            dialog.set_child(toolbar)
            dialog.present(self.get_root())
            return False
        threading.Thread(target=read, daemon=True).start()


class SystemPage(Page):
    def __init__(self, state, navigate):
        super().__init__("System", "Device details, firmware, and tools to keep Archer running smoothly.", state)
        self.availability(True)
        details = self.form("Device information")
        self.rows = {key: details.info(title, copyable=True) for key, title in (
            ("product_name", "Model"), ("vendor", "Vendor"), ("cpu_model", "CPU"),
            ("gpu_model", "GPU"), ("kernel", "Kernel"), ("daemon_version", "Archer service"), ("driver_version", "Driver"))}
        self.capabilities = details.info("Available controls")
        self.boot = self.form("Startup", save=lambda v: state.client.set_boot_animation_sound(v["enabled"]), immediate=True)
        self.boot.switch("enabled", "Boot animation and sound", "Play Acer’s startup animation and sound.")
        group = Adw.PreferencesGroup(title="Maintenance")
        link(group, "Firmware", "BIOS information and available device updates", lambda: navigate("firmware"))
        link(group, "Advanced", "Driver overrides, service recovery, and logs", lambda: navigate("advanced"))
        self.content.append(group)
        state.connect("settings-changed", self.load)

    def load(self, _state, data):
        info = data.get("system_info") or {}
        for key, row in self.rows.items():
            row.set_subtitle(str(info.get(key) or "Unknown"))
        names = {"thermal_profiles": "Thermal profiles", "fan_control": "Fans", "battery_limiter": "Charge protection",
                 "battery_calibration": "Calibration", "battery_info": "Battery monitoring", "usb_charging": "USB charging",
                 "keyboard_per_zone": "Zone lighting", "keyboard_effects": "Lighting effects", "backlight_timeout": "Backlight timeout",
                 "lcd_override": "Panel response", "boot_animation_sound": "Startup sound", "display_mode": "Graphics modes",
                 "game_mode": "Game Mode", "audio_enhancement": "Noise suppression"}
        features = data.get("features", [])
        self.capabilities.set_subtitle(", ".join(names.get(key, key.replace("_", " ").capitalize()) for key in features) or "None detected")
        supported(self.boot, features, "boot_animation_sound")
        self.boot.load({"enabled": bool(data.get("boot_animation_sound"))})
