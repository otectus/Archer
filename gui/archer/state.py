"""One asynchronous connection and authoritative settings store for every page."""

import json
import threading
import time

from gi.repository import GLib, GObject

from archer.client import normalize_telemetry


class AppState(GObject.Object):
    __gsignals__ = {
        "settings-changed": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "telemetry": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "connection-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "audio-changed": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
        "freshness-changed": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
    }

    def __init__(self, client):
        super().__init__()
        self.client = client
        self.settings = {}
        self.live = {}
        self.status = "Connecting"
        self.error = ""
        self.last_sample = 0
        self._monitoring_since = 0
        self.closed = False
        self._fetching = False
        self._refresh_again = False
        self._generation = 0
        self._retry = 0
        self._attempt = 0
        self._matches = []
        self._watchdog = 0

    @property
    def writable(self):
        return self.status == "Connected" and bool(self.settings)

    def start(self):
        if self.closed:
            return
        self._watchdog = GLib.timeout_add_seconds(1, self._check)
        self.refresh(reconnect=True)

    def _status(self, status):
        self.status = status
        self.emit("connection-changed", status)

    def refresh(self, reconnect=False):
        if self.closed:
            return
        if self._fetching:
            self._refresh_again = True
            return
        if self._retry:
            GLib.source_remove(self._retry)
            self._retry = 0
        self._fetching = True
        generation = self._generation

        def fetch():
            try:
                if reconnect:
                    self.client.reconnect()
                data = self.client.get_all_settings()
                live = self.client.get_monitoring_data() if data else None
                error = self.client.init_error or "The Archer service is unavailable."
            except Exception as exc:
                data, live, error = None, None, str(exc)
            GLib.idle_add(self._loaded, data, live, error, generation, reconnect)
        threading.Thread(target=fetch, daemon=True).start()

    def _loaded(self, data, live, error, generation, reconnect):
        if self.closed:
            return False
        self._fetching = False
        if generation != self._generation:
            self.refresh()
            return False
        if isinstance(data, dict) and data:
            self.settings = data
            self.error = ""
            self._attempt = 0
            self._status("Connected")
            self.emit("settings-changed", data)
            if not self._monitoring_since:
                self._monitoring_since = time.monotonic()
            if reconnect or not self._matches:
                self._subscribe()
            if live:
                self._sample(live)
        else:
            self.error = error
            self._status("Offline")
            self._schedule_retry()
        if self._refresh_again:
            self._refresh_again = False
            self.refresh(reconnect=self.status == "Offline")
        return False

    def invalidate(self):
        """Prevent a read started before an edit from replacing its result."""
        self._generation += 1

    def _subscribe(self):
        for match in self._matches:
            match.remove()
        self._matches.clear()
        iface = self.client.dbus_iface
        if iface is None:
            return
        for name, callback in (("TelemetryUpdated", self._signal),
                               ("AudioEnhancementChanged", self._audio),
                               ("ProfileChanged", lambda *_: self.refresh())):
            try:
                self._matches.append(iface.connect_to_signal(name, callback))
            except Exception:
                pass

    def _audio(self, enabled):
        if not self.closed:
            self.emit("audio-changed", bool(enabled))

    def _signal(self, payload):
        try:
            data = json.loads(str(payload))
            if isinstance(data, dict):
                self._sample(data)
        except (ValueError, TypeError):
            pass

    def _sample(self, data):
        if self.closed:
            return
        data = normalize_telemetry(data)
        old_ac = self.live.get("power_source_ac")
        self.live = data
        self.last_sample = time.monotonic()
        self.emit("telemetry", data)
        if old_ac is not None and old_ac != data.get("power_source_ac"):
            self.refresh()
        # After a disconnect, settings must be reconciled before enabling writes.
        if self.status not in ("Connected", "Restarting") and not self._fetching:
            self.refresh(reconnect=True)

    def expect_restart(self, delay):
        if self._retry:
            GLib.source_remove(self._retry)
        self._status("Restarting")
        self._retry = GLib.timeout_add_seconds(delay, self._reconnect)

    def _check(self):
        if self.closed:
            return False
        baseline = self.last_sample or self._monitoring_since
        if self.last_sample:
            self.emit("freshness-changed", int(time.monotonic() - self.last_sample))
        if baseline and time.monotonic() - baseline > 6:
            if self.status == "Connected":
                self.error = "Live readings have stopped. Last values are shown."
                self._status("Stale")
                self._schedule_retry()
        return True

    def _schedule_retry(self):
        if not self._retry and not self.closed:
            delay = (5, 10, 20, 60)[min(self._attempt, 3)]
            self._attempt += 1
            self._retry = GLib.timeout_add_seconds(delay, self._reconnect)

    def _reconnect(self):
        self._retry = 0
        self.refresh(reconnect=True)
        return False

    def close(self):
        if self.closed:
            return
        self.closed = True
        for source in (self._retry, self._watchdog):
            if source:
                GLib.source_remove(source)
        for match in self._matches:
            match.remove()
        self._matches.clear()
