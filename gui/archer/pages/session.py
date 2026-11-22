"""User-approved, session-owned workspace changes and hardware probes."""
from pathlib import Path
import json
import signal
import sys
import threading

from gi.repository import Adw, Gio, GLib, Gtk

from archer.widgets.forms import Page, confirm, label
from archer_peripherals import probe
from archer_session import speaker_check
from archer_workspace import PRESETS, run_preset


class SessionPage(Page):
    checks = {"Network link": "network", "Bluetooth": "bluetooth", "Fingerprint readers": "fingerprint",
              "Mobile broadband": "wwan", "Orientation sensors": "orientation", "Storage and readers": "storage"}

    def __init__(self, state):
        super().__init__("Session tools", "Check a device service or request a temporary desktop power profile.", state)
        self.availability(True)
        self.worker = None
        self.group = Adw.PreferencesGroup(title="Workspace")
        self.content.append(self.group)
        self.preset = Adw.ComboRow(title="Preset", model=Gtk.StringList.new([p.title() for p in PRESETS]))
        self.group.add(self.preset)
        self.minutes = Adw.SpinRow.new_with_range(1, 240, 1)
        self.minutes.set_title("Duration (minutes)")
        self.minutes.set_value(30)
        self.group.add(self.minutes)
        self.review = Adw.ButtonRow(title="Review profile change")
        self.review.connect("activated", lambda *_: self.review_profile())
        self.group.add(self.review)
        self.stop = Adw.ButtonRow(title="Release profile")
        self.stop.connect("activated", lambda *_: self.release())
        self.stop.set_sensitive(False)
        self.group.add(self.stop)
        group = Adw.PreferencesGroup(title="Device services")
        self.content.append(group)
        self.device = Adw.ComboRow(title="Check", model=Gtk.StringList.new(list(self.checks)))
        group.add(self.device)
        scan = Adw.ButtonRow(title="Run check")

        def run_check(_row):
            selected = self.checks[self.device.get_selected_item().get_string()]
            self.background(lambda: probe(selected), self.show_result, scan)

        scan.connect("activated", run_check)
        group.add(scan)
        tone = Adw.ButtonRow(title="Play one-second speaker test")
        group.add(tone)

        def playback(_row):
            confirm(self.get_root(), "Test current audio output", "A short tone will play at your current output volume.",
                    "Play", lambda: self.background(speaker_check, lambda _: self.result.set_label(
                        "Playback finished. Confirm that you heard the tone on the intended output."), tone))

        tone.connect("activated", playback)
        self.result = label("")
        self.result.set_selectable(True)
        self.content.append(self.result)

    def background(self, operation, finish, button):
        button.set_sensitive(False)

        def display(data, error):
            if not self.state.closed:
                button.set_sensitive(True)
                if error:
                    self.result.set_label(error)
                else:
                    finish(data)
            return False

        def work():
            try:
                data = operation()
            except (ValueError, OSError, GLib.Error) as error:
                GLib.idle_add(display, None, str(error))
            else:
                GLib.idle_add(display, data, None)
        threading.Thread(target=work, daemon=True).start()

    def show_result(self, data):
        self.result.set_label(json.dumps(data, indent=2))

    def review_profile(self):
        preset = list(PRESETS)[self.preset.get_selected()]
        preference = {"schema_version": 1, "preset": preset, "duration_minutes": int(self.minutes.get_value())}

        def reviewed(plan):
            self.show_result(plan)
            if plan["available"] and plan["hold_required"]:
                confirm(self.get_root(), "Request temporary profile",
                        f'{plan["owner"]} will receive a request for {plan["requested"]} for {preference["duration_minutes"]} minutes.',
                        "Apply", lambda: self.apply_profile(preference))

        self.background(lambda: run_preset(preference), reviewed, self.review)

    def apply_profile(self, preference):
        if self.worker is not None or self.state.closed:
            return
        script = Path(__file__).resolve().parents[2] / "archer_session.py"
        try:
            self.worker = Gio.Subprocess.new([sys.executable, str(script), "workspace", preference["preset"], "--minutes",
                                              str(preference["duration_minutes"]), "--apply"],
                                             Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE)
        except GLib.Error as error:
            self.result.set_label(str(error))
            return
        self.review.set_sensitive(False)
        self.stop.set_sensitive(True)
        self.result.set_label("Temporary profile requested. Release it here or close Archer to end the request.")
        self.worker.communicate_utf8_async(None, None, self.completed)

    def completed(self, child, result):
        self.worker = None
        if self.state.closed:
            return
        self.review.set_sensitive(True)
        self.stop.set_sensitive(False)
        try:
            _, output, error = child.communicate_utf8_finish(result)
            self.result.set_label((error or "").strip() or (output or "").strip() or "Profile request ended.")
        except GLib.Error as error:
            self.result.set_label(str(error))

    def release(self):
        if self.worker:
            self.worker.send_signal(signal.SIGTERM)

    def shutdown(self):
        self.release()
        super().shutdown()
