"""User-session hardware checks, report preview and opt-in media capture."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import threading
import sys

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from archer_health import GOALS, HealthHistory, guided_plan, report, sleep_comparison, sleep_sample, snapshot
from archer_inventory import collect_inventory
from archer_update_history import regression
from archer_session import AUDIO_PRESETS, mic_preset, VIDEO_FILTERS, audio_command, audio_nodes, camera_command, camera_devices
from archer.widgets.forms import Page, label


def button(group, title, callback):
    row = Adw.ButtonRow(title=title)
    row.connect("activated", lambda *_: callback())
    group.add(row)
    return row


class HealthPage(Page):
    def __init__(self, state, preferences, navigate):
        super().__init__("Hardware checks", "Inspect devices, review the evidence, and edit a support report before sharing it.", state)
        self.availability(True)
        self.preferences = preferences
        self.navigate = navigate
        self.group = Adw.PreferencesGroup()
        self.scan = button(self.group, "Scan hardware", self.refresh)
        self.content.append(self.group)
        self.rows = []
        self.text = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, monospace=True)
        self.text.update_property([Gtk.AccessibleProperty.LABEL], ["Editable support report"])
        scroll = Gtk.ScrolledWindow(min_content_height=200)
        scroll.set_child(self.text)
        self.preview = Adw.ExpanderRow(title="Preview and edit report")
        self.preview.add_row(scroll)
        self.group.add(self.preview)
        self.unsupported = Adw.ExpanderRow(title="Show unsupported features")
        self.unsupported.set_visible(False)
        self.group.add(self.unsupported)
        self.unsupported_rows = []
        self.copy = button(self.group, "Copy edited report", self.copy_report)
        self.copy.set_sensitive(False)
        self.history = label("Local history is empty. Scans are retained only on this computer.")
        self.content.append(self.history)
        self.plan_group = Adw.PreferencesGroup(
            title="Guided goal",
            description="Review a small, read-only plan. A plan never authorizes firmware updates, driver replacement, or package removal.")
        self.goal_keys = list(GOALS)
        self.goal = Adw.ComboRow(title="What would you like to improve?", model=Gtk.StringList.new(
            [key.replace("_", " ").title() for key in self.goal_keys]))
        self.plan_group.add(self.goal)
        self.plan = button(self.plan_group, "Review goal plan", self.show_plan)
        self.plan.set_sensitive(False)
        self.plan_rows = []
        self.content.append(self.plan_group)
        self.groups = {}
        saved = self.preferences.data["setup_goal"]
        if saved in self.goal_keys:
            self.goal.set_selected(self.goal_keys.index(saved))
        self.goal.connect("notify::selected", lambda *_: self.show_plan() if self.groups else None)

    def copy_report(self):
        buffer = self.text.get_buffer()
        self.get_clipboard().set(buffer.get_text(*buffer.get_bounds(), False))

    def refresh(self, save=True):
        if not self.scan.get_sensitive():
            return
        self.scan.set_sensitive(False)

        def work():
            try:
                inventory = collect_inventory()
                try:
                    capabilities = self.state.client._send_command("get_capabilities")
                    capabilities = capabilities.get("data") if capabilities.get("success") else None
                except (AttributeError, OSError, ValueError, TypeError):
                    capabilities = None
                firmware = (self.state.settings.get("firmware_info") or {}) if self.state.settings else {}
                sample = snapshot(inventory, Path("/"), capabilities, firmware)
                change = HealthHistory().record(sample) if save else None
                update = regression(Path("/"), sample)
                if update and update["lost"]:
                    group = sample["health"]["Update readiness"]
                    group["status"] = "attention"
                    group["findings"].append({"symptom": "Devices absent since the pre-update scan",
                                              "observed": ", ".join(update["lost"])})
                data = report(inventory, Path("/"))
                GLib.idle_add(self.render, data, sample["health"], change, save, capabilities)
            except (ValueError, OSError, TypeError) as error:
                GLib.idle_add(self.render, {"error": str(error)}, {}, None)
        threading.Thread(target=work, daemon=True).start()

    def render(self, data, groups, change=None, saved=True, capabilities=None):
        if self.state.closed:
            return False
        self.groups = groups
        for row in self.unsupported_rows:
            self.unsupported.remove(row)
        self.unsupported_rows.clear()
        records = capabilities.get("capabilities", []) if isinstance(capabilities, dict) else capabilities
        for item in records if isinstance(records, list) else ():
            if not isinstance(item, dict) or not all(isinstance(item.get(key), str) for key in ("id", "backend", "availability")):
                continue
            if item["availability"] == "available" and item.get("writable"):
                continue
            reason = item.get("reason_code") or ("Read only" if item["availability"] == "available" else item["availability"])
            title = item["id"].replace(".", " · ").replace("_", " ")
            row = Adw.ActionRow(title=title, subtitle=f'{item["backend"]} · {reason.replace("_", " ")}', use_markup=False)
            self.unsupported.add_row(row)
            self.unsupported_rows.append(row)
        self.unsupported.set_visible(bool(self.unsupported_rows))
        for row in self.rows:
            self.group.remove(row)
        self.rows.clear()
        for title, group in groups.items():
            counts = ", ".join(f"{key} × {val}" for key, val in group["counts"].items())
            subtitle = counts or group.get("status", "No interface detected").replace("_", " ")
            row = Adw.ExpanderRow(title=title, subtitle=subtitle, use_markup=False)
            for finding in group.get("findings", []):
                observed = finding.get("observed") or "No evidence returned"
                row.add_row(Adw.ActionRow(title=finding.get("symptom", "Finding"),
                                          subtitle=observed, use_markup=False))
            row.add_row(Adw.ActionRow(title="Functional check", subtitle=group["guidance"], use_markup=False))
            self.rows.append(row)
            self.group.add(row)
        self.text.get_buffer().set_text(json.dumps(data, indent=2, ensure_ascii=False))
        self.copy.set_sensitive(True)
        self.plan.set_sensitive(bool(groups))
        self.scan.set_sensitive(True)
        if change:
            changed = sum((len(change["added"]), len(change["lost"]),
                           len(change["binding_or_interface_changed"]),
                           len(change["package_changes"]), len(change["lost_capabilities"]),
                           len(change["capability_changes"]), len(change["config_changes"]),
                           int(change["boot_targets_changed"])))
            self.history.set_label(
                f"Compared with the previous local scan: {changed} change(s). "
                "Timing is evidence to investigate, not proof of cause.")
        elif groups:
            self.history.set_label("Saved the first local baseline." if saved else "Initial scan complete. Choose a goal below.")
        else:
            self.history.set_label("Scan failed. " + str(data.get("error", "No findings returned.")))
        if self.preferences.data["setup_goal"] and groups:
            self.show_plan()
        return False

    def show_plan(self):
        if not self.groups:
            return
        plan = guided_plan(self.goal_keys[self.goal.get_selected()], self.groups)
        for row in self.plan_rows:
            self.plan_group.remove(row)
        self.plan_rows.clear()
        goal = plan["goal"]
        index = self.preferences.data["setup_step"] if self.preferences.data["setup_goal"] == goal else 0
        if not 0 <= index < len(plan["steps"]):
            index = 0
        self.preferences.save(setup_goal=goal, setup_step=index)
        for number, item in enumerate(plan["steps"]):
            subtitle = "Reviewed" if number < index else "Current step" if number == index else "Upcoming"
            row = Adw.ActionRow(title=item["title"], subtitle=subtitle, use_markup=False)
            self.plan_group.add(row)
            self.plan_rows.append(row)
        item = plan["steps"][index]
        if item["page"] == "checks":
            action = button(self.plan_group, "Scan and compare", self.refresh)
        else:
            action = button(self.plan_group, "Open " + item["page"].title(), lambda: self.navigate(item["page"]))
        self.plan_rows.append(action)

        def advance():
            if index + 1 < len(plan["steps"]):
                self.preferences.save(setup_step=index + 1)
                self.show_plan()
            else:
                self.preferences.save(setup_goal="", setup_step=0)
                for row in self.plan_rows:
                    self.plan_group.remove(row)
                self.plan_rows.clear()
                self.refresh()

        title = "Mark reviewed and continue" if index + 1 < len(plan["steps"]) else "Finish and rescan hardware"
        self.plan_rows.append(button(self.plan_group, title, advance))


class MediaPage(Page):
    def __init__(self, state, camera):
        self.camera = camera
        super().__init__("Camera" if camera else "Microphone", "Select a physical source. Capture runs in your desktop session until you stop it.", state)
        self.availability(True)
        self.group = Adw.PreferencesGroup()
        self.content.append(self.group)
        self.source = Adw.ComboRow(title="Physical source", model=Gtk.StringList.new([]))
        self.group.add(self.source)
        self.devices = []
        self.outputs = []
        self.process = None
        self.stopping = False
        self.scan = button(self.group, "Find sources", self.refresh)
        self.tasks = []
        if camera:
            self.effect = Adw.ComboRow(title="Effect", model=Gtk.StringList.new(
                ["Color", "Monochrome", "Close-up", "Blur green backdrop"]))
            self.group.add(self.effect)
            self.tasks.append(button(self.group, "Preview camera", lambda: self.start("preview")))
            self.tasks.append(button(self.group, "Stream to Archer Camera", lambda: self.start("stream")))
        else:
            self.preset = Adw.ComboRow(title="Noise suppression preset", model=Gtk.StringList.new(
                [key.replace("-", " ").title() for key in AUDIO_PRESETS]))
            self.group.add(self.preset)
            self.speaker = Adw.ComboRow(title="Call output", model=Gtk.StringList.new([]))
            self.group.add(self.speaker)
            self.source.connect("notify::selected", lambda *_: self.load_preset())
            self.tasks.append(button(self.group, "Measure input level for three seconds", lambda: self.start("meter")))
            self.tasks.append(button(self.group, "Start noise suppression", lambda: self.start("noise")))
            self.tasks.append(button(self.group, "Start echo cancellation", lambda: self.start("echo")))
            self.save = button(self.group, "Save preset for this microphone", self.save_preset)
        self.stop = button(self.group, "Stop capture", self.stop_capture)
        self.feedback = label("Capture is off.")
        self.content.append(self.feedback)
        self.ready(False)

    def load_preset(self):
        index = self.source.get_selected()
        if index < len(self.devices):
            self.preset.set_selected(list(AUDIO_PRESETS).index(mic_preset(self.devices[index]["source"])))

    def save_preset(self):
        index = self.source.get_selected()
        if index < len(self.devices):
            try:
                mic_preset(self.devices[index]["source"], list(AUDIO_PRESETS)[self.preset.get_selected()])
                self.feedback.set_label("Microphone preset saved.")
            except (OSError, ValueError) as error:
                self.feedback.set_label(str(error))

    def ready(self, busy):
        self.source.set_sensitive(not busy)
        self.scan.set_sensitive(not busy)
        self.stop.set_sensitive(busy and self.process is not None)
        for i, task in enumerate(self.tasks):
            task.set_sensitive(bool(self.devices) and not busy and (not self.camera or i == 0 or len(self.outputs) == 1))

        if not self.camera:
            self.preset.set_sensitive(bool(self.devices) and not busy)
            self.save.set_sensitive(bool(self.devices) and not busy)
            self.speaker.set_sensitive(bool(self.outputs) and not busy)
            plugin = Path("/usr/lib/spa-0.2/aec/libspa-aec-webrtc.so")
            self.tasks[-1].set_sensitive(bool(self.devices and self.outputs) and not busy and plugin.is_file())

    def refresh(self):
        self.ready(True)

        def work():
            try:
                devices = camera_devices() if self.camera else audio_nodes() + [
                    {**d, "role": "output"} for d in audio_nodes(outputs=True) if d["physical"]]
                GLib.idle_add(self.listed, devices, "")
            except (ValueError, OSError) as error:
                GLib.idle_add(self.listed, [], str(error))
        threading.Thread(target=work, daemon=True).start()

    def listed(self, devices, message):
        if self.state.closed:
            return False
        self.devices = [d for d in devices if d.get("role") == "source" or d.get("physical") and d.get("role") != "output"]
        self.outputs = [d for d in devices if d.get("role") == "output"]
        self.source.set_model(Gtk.StringList.new([d["label"] + (" · " + d["node"] if self.camera else "") for d in self.devices]))
        if not self.camera:
            self.speaker.set_model(Gtk.StringList.new([d["label"] for d in self.outputs]))
            self.load_preset()
        self.feedback.set_label(message or ("Select a source and start capture." if self.devices else
                                            "No accessible physical source. Check the driver and session permissions."))
        self.ready(False)
        return False

    def start(self, action):
        index = self.source.get_selected()
        if self.process or index >= len(self.devices):
            return
        device = self.devices[index]
        effect = list(VIDEO_FILTERS)[self.effect.get_selected()] if self.camera else None
        if action == "echo" and self.speaker.get_selected() >= len(self.outputs):
            return
        output = self.outputs[self.speaker.get_selected()]["source"] if action == "echo" else None
        preset = list(AUDIO_PRESETS)[self.preset.get_selected()] if action == "noise" else None
        self.ready(True)
        self.feedback.set_label("Opening the selected source…")

        def work():
            try:
                with ExitStack() as cleanup:
                    directory = cleanup.enter_context(tempfile.TemporaryDirectory(prefix="archer-session-"))
                    if action in ("noise", "echo"):
                        session_tool = Path(__file__).resolve().parents[2] / "archer_session.py"
                        cmd = [sys.executable, str(session_tool), action, device["source"]]
                        cmd.extend([output] if output else ["--preset", preset])
                    elif self.camera:
                        cmd = camera_command(device, effect, self.outputs[0] if action == "stream" else None)
                    else:
                        cmd = audio_command(action, device["source"], Path(directory))
                    process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                               stdin=subprocess.DEVNULL, start_new_session=True)
                    GLib.idle_add(self.started, process, action)
                    tail = b""
                    while chunk := process.stderr.readline(4096):
                        tail = (tail + chunk)[-4096:]
                        if action in ("noise", "echo") and chunk.strip() in (b"ARCHER_SOURCE_CONNECTED", b"ARCHER_SOURCE_WAITING"):
                            GLib.idle_add(self.audio_verified, process, chunk.strip() == b"ARCHER_SOURCE_CONNECTED", action)
                    process.stderr.close()
                    status = process.wait()
                    detail = tail.decode(errors="replace")
                    if action == "meter" and status == 0:
                        match = re.search(r"max_volume: ([\w.\-]+ dB)", detail)
                        message = "Peak input level " + match[1] if match else "No input level returned"
                    elif status == 0:
                        message = "Capture finished."
                    else:
                        message = detail.strip()[-400:] or "Capture stopped or device unavailable."
            except (IndexError, ValueError, OSError) as error:
                message = str(error)
            GLib.idle_add(self.finished, message)
        threading.Thread(target=work, daemon=True).start()

    def started(self, process, action):
        self.process = process
        if self.state.closed:
            self.stop_capture()
        else:
            self.feedback.set_label({"echo": "Checking microphone and call output…", "meter": "Measuring microphone level…", "noise": "Waiting for a verified microphone connection…",
                                     "preview": "Camera preview is open.", "stream": "Camera producer is running. Select Archer Camera in your app."}[action])
            self.ready(True)
        return False

    def audio_verified(self, process, linked=True, action="noise"):
        if not self.state.closed and self.process is process and process.poll() is None:
            message = ("Choose Archer Echo Microphone and Archer Call Output in your calling app." if action == "echo" else
                       "Selected microphone connected. Choose Archer Noise Suppression in your app.")
            self.feedback.set_label(message if linked else "Waiting for the selected audio devices to reconnect…")
        return False

    def finished(self, message):
        self.process = None
        if not self.state.closed:
            self.feedback.set_label("Capture is off." if self.stopping else message)
            self.ready(False)
        self.stopping = False
        return False

    def stop_capture(self):
        if self.process and self.process.poll() is None:
            self.stopping = True
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def shutdown(self):
        self.stop_capture()
        super().shutdown()


class InputPage(Page):
    def __init__(self, state):
        super().__init__("Input", "Change the touchpad preferences supported by your desktop.", state)
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower().split(":")
        source = Gio.SettingsSchemaSource.get_default()
        schema = source.lookup("org.gnome.desktop.peripherals.touchpad", True) if source and "gnome" in desktop else None
        self.availability(True)
        if not schema:
            self.content.append(label("Use your desktop’s settings to configure the touchpad."))
        if schema:
            settings = Gio.Settings.new_full(schema, None, None)
            group = Adw.PreferencesGroup(title="Touchpad")
            for key, title in (("tap-to-click", "Tap to click"), ("natural-scroll", "Natural scrolling"),
                               ("disable-while-typing", "Disable while typing"), ("two-finger-scrolling-enabled", "Two-finger scrolling")):
                if schema.has_key(key):
                    row = Adw.SwitchRow(title=title)
                    settings.bind(key, row, "active", Gio.SettingsBindFlags.DEFAULT)
                    group.add(row)
            self.content.append(group)

        test = Adw.PreferencesGroup(title="Pointer and pen check",
                                    description="Move, click or draw inside the test area. Input stays in this window.")
        self.content.append(test)
        area = Gtk.DrawingArea(content_height=110, hexpand=True, focusable=True)
        area.add_css_class("card")
        area.update_property([Gtk.AccessibleProperty.LABEL], ["Pointer and pen test area"])
        test.add(area)
        output = label("Waiting for pointer input")
        self.content.append(output)
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_: output.set_label("Pointer entered the test area"))
        area.add_controller(motion)
        click = Gtk.GestureClick()
        click.connect("pressed", lambda *_: output.set_label("Button or tap received"))
        area.add_controller(click)
        stylus = Gtk.GestureStylus()

        def pen(controller, *_):
            present, pressure = controller.get_axis(Gdk.AxisUse.PRESSURE)
            output.set_label(f"Pen pressure {pressure:.2f}" if present else "Pen detected without a pressure reading")

        stylus.connect("down", pen)
        stylus.connect("motion", pen)
        area.add_controller(stylus)


class SleepPage(Page):
    def __init__(self, state):
        super().__init__("Sleep and wake", "Observe the next sleep cycle and compare battery readings after wake.", state)
        self.availability(True)
        self.group = Adw.PreferencesGroup()
        self.content.append(self.group)
        self.start = button(self.group, "Observe next sleep cycle", self.observe)
        self.stop = button(self.group, "Stop observing", self.cancel)
        self.stop.set_sensitive(False)
        self.output = label("Save your work and use your desktop’s sleep action after starting the observation.")
        self.content.append(self.output)
        self.bus = None
        self.subscription = None
        self.before = None

    def observe(self):
        if self.subscription is not None:
            return
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            self.bus.call_sync("org.freedesktop.login1", "/org/freedesktop/login1", "org.freedesktop.login1.Manager",
                               "CanSuspend", None, None, Gio.DBusCallFlags.NONE, 2000, None)
            self.subscription = self.bus.signal_subscribe(
                "org.freedesktop.login1", "org.freedesktop.login1.Manager", "PrepareForSleep", "/org/freedesktop/login1",
                None, Gio.DBusSignalFlags.NONE, self.sleep_event)
        except GLib.Error as error:
            self.output.set_label(str(error))
            return
        self.start.set_sensitive(False)
        self.stop.set_sensitive(True)
        self.output.set_label("Listening. Put the computer to sleep when ready.")

    def sleep_event(self, _bus, _sender, _path, _interface, _member, parameters):
        if parameters.unpack()[0]:
            self.before = sleep_sample(Path("/sys"))
        elif self.before is not None:
            result = sleep_comparison(self.before, sleep_sample(Path("/sys")))
            self.cancel()
            rows = [f"Elapsed time {result['elapsed_seconds']:.0f} seconds",
                    "Wake IRQ " + (result["wake_irq"] or "unavailable")]
            rows.extend(f"{key} change {entry['delta']:+} {entry['unit']}" for key, entry in result["changes"].items())
            self.output.set_label("\n".join(rows))

    def cancel(self):
        if self.subscription is not None:
            self.bus.signal_unsubscribe(self.subscription)
        self.subscription = None
        self.before = None
        self.start.set_sensitive(True)
        self.stop.set_sensitive(False)
        self.output.set_label("Observation stopped.")

    def shutdown(self):
        self.cancel()
        super().shutdown()
