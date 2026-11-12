"""Requested, saved and observed state for Linuwu-Sense 0/1 controls.

Issue #10: on some firmware the driver's show() callback prints "-1" because it
cannot decode the WMI reply, while store() still accepts 0/1 commands. A
successful write proves only that the driver accepted the command. Each control
therefore keeps three facts apart and never infers one from another:

* configured: the validated preference saved for the next daemon start;
* requested: the last value the driver accepted in this daemon session;
* observed: a strictly parsed readback, or unknown.

One BinaryControl owns the command, its persistence outcome and every state
snapshot, so D-Bus, startup replay and settings reads cannot disagree.
"""

import copy
import logging
import os
import threading

logger = logging.getLogger("archer-daemon")

SOURCES = ("hardware", "last_write", "saved_preference", "unknown")
READBACK_STATUSES = ("known", "unrecognized", "invalid", "unreadable", "unavailable")
RAW_LIMIT = 32
READ_LIMIT = 4096


def parse_binary_readback(raw):
    """Strict parser for stripped 0/1 sysfs text; anything else, including -1, is unknown."""
    if raw == "0":
        return False
    if raw == "1":
        return True
    return None


def preference_value(value):
    """A saved preference as a boolean, or None when absent or invalid.

    JSON booleans and the integral 0/1 of older releases are accepted. Strings
    (including "0" and "false"), other numbers, floats, containers and null are not.
    """
    if type(value) is bool:
        return value
    if type(value) is int and value in (0, 1):
        return bool(value)
    return None


def request_value(value):
    """Requests must be native booleans; the D-Bus layer converts its wrapper first."""
    return value if type(value) is bool else None


def word(value):
    return "On" if value else "Off"


class SysfsIO:
    """Node access that keeps error detail and never creates a missing node."""

    def exists(self, path):
        return os.path.isfile(path)

    def identity(self, path):
        """Distinguish a node recreated by a driver reload from the one written earlier."""
        try:
            info = os.stat(path)
        except OSError:
            return None
        return info.st_dev, info.st_ino

    def read(self, path):
        """Return (text, None), (None, "missing") or (None, error description)."""
        try:
            fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
        except FileNotFoundError:
            return None, "missing"
        except OSError as error:
            return None, error.strerror or str(error)
        try:
            data = os.read(fd, READ_LIMIT)
        except OSError as error:
            return None, error.strerror or str(error)
        finally:
            os.close(fd)
        return data.decode("utf-8", "replace"), None

    def write(self, path, text):
        """Return None when the driver accepted the write, otherwise an error description."""
        try:
            # O_CREAT is intentionally absent: a disappearing sysfs node must fail.
            fd = os.open(path, os.O_WRONLY | os.O_TRUNC | os.O_CLOEXEC)
        except FileNotFoundError:
            return "the control interface is missing"
        except OSError as error:
            return error.strerror or str(error)
        try:
            data = text.encode()
            written = os.write(fd, data)
            if written != len(data):
                return f"short write ({written} of {len(data)} bytes)"
        except OSError as error:
            return error.strerror or str(error)
        finally:
            os.close(fd)
        return None


class BinaryControl:
    """The single owner of one 0/1 control's commands, persistence outcome and state."""

    def __init__(self, key, resolve_path, settings, label, io=None):
        self.key = key
        self.label = label
        self._resolve = resolve_path  # () -> existing node path or None
        self._settings = settings  # () -> settings store with save_checked()
        self._io = io or SysfsIO()
        self._lock = threading.RLock()
        self._request = None
        self._operation = None
        self._restore = None
        self._observation = None
        self._evidence_reset = None
        self._logged_read = None

    # --- Observation -------------------------------------------------------

    def _read(self):
        path = self._resolve()
        if not path:
            return {"available": False, "observed": None, "readback_status": "unavailable",
                    "raw_readback": None, "error": None, "identity": None}
        text, error = self._io.read(path)
        if text is None:
            missing = error == "missing"
            return {"available": not missing, "observed": None,
                    "readback_status": "unavailable" if missing else "unreadable",
                    "raw_readback": None, "error": None if missing else error,
                    "identity": None if missing else self._io.identity(path)}
        raw = text.strip()
        observed = parse_binary_readback(raw)
        status = "known" if observed is not None else "unrecognized" if raw == "-1" else "invalid"
        return {"available": True, "observed": observed, "readback_status": status,
                "raw_readback": raw[:RAW_LIMIT], "error": None, "identity": self._io.identity(path)}

    def _observe(self):
        observation = self._read()
        if self._request is not None and (
                not observation["available"] or observation["identity"] != self._request["identity"]):
            # A missing or recreated node means the driver was unloaded; earlier
            # acceptance says nothing about the new instance.
            self._invalidate("interface_changed")
        self._observation = observation
        signature = (observation["readback_status"], observation["raw_readback"], observation["error"])
        if signature != self._logged_read:
            self._logged_read = signature
            if observation["readback_status"] in ("unrecognized", "invalid", "unreadable"):
                logger.info("%s readback is %s (%r%s); hardware state is unverified", self.label,
                            observation["readback_status"], observation["raw_readback"],
                            f", {observation['error']}" if observation["error"] else "")

    def _invalidate(self, reason):
        if self._request is not None:
            logger.info("%s: discarding evidence of the last accepted request (%s)", self.label, reason)
            self._request = None
            self._evidence_reset = reason

    def note_resume(self):
        """A pre-suspend request or observation does not prove the post-resume state."""
        with self._lock:
            self._invalidate("resume")
            self._observation = None

    # --- State -------------------------------------------------------------

    def _configured(self):
        store = self._settings()
        if getattr(store, "unreadable", False):
            return None, "unreadable"
        raw = store.get(self.key)
        value = preference_value(raw)
        if value is not None:
            return value, "valid"
        is_rejected = getattr(store, "is_rejected", None)
        if raw is not None or (is_rejected and is_rejected(self.key)):
            return None, "invalid"
        return None, "absent"

    def _build(self):
        observation = self._observation
        configured, configured_status = self._configured()
        requested = self._request["value"] if self._request else None
        if not observation["available"]:
            effective, source = None, "unknown"
        elif observation["observed"] is not None:
            effective, source = observation["observed"], "hardware"
        elif requested is not None:
            effective, source = requested, "last_write"
        elif configured is not None:
            effective, source = configured, "saved_preference"
        else:
            effective, source = None, "unknown"
        return {
            "effective": effective,
            "source": source,
            "configured": configured,
            "configured_status": configured_status,
            "requested": requested,
            "requested_origin": self._request["origin"] if self._request else None,
            "observed": observation["observed"],
            "readback_status": observation["readback_status"],
            "raw_readback": observation["raw_readback"],
            "readback_error": observation["error"],
            "available": observation["available"],
            "evidence_reset": None if self._request else self._evidence_reset,
            "last_operation": copy.deepcopy(self._operation),
            "restore": copy.deepcopy(self._restore),
        }

    def snapshot(self, refresh=True):
        """A fresh, read-only state dictionary. Reading never writes hardware or settings."""
        with self._lock:
            if refresh or self._observation is None:
                self._observe()
            return self._build()

    def _response(self, success, error, operation):
        state = self._build()
        return {"success": success, "error": error,
                "data": {self.key: state["effective"], f"{self.key}_state": state,
                         "operation": copy.deepcopy(operation)}}

    # --- Commands ----------------------------------------------------------

    def apply(self, value, persist=True, origin="user"):
        """Send an explicit 0/1 command, then persist and verify it once.

        The same value is always written again: an explicit Off is the recovery
        path when hardware state is unknown or was changed elsewhere.
        """
        target = request_value(value)
        with self._lock:
            if target is None:
                if self._observation is None:
                    self._observe()
                return self._response(False, "Invalid request: expected true or false", None)
            path = self._resolve()
            if not path or not self._io.exists(path):
                self._invalidate("interface_changed")
                self._operation = self._record(origin, target, "not_attempted", "not_attempted", "not_checked",
                                               f"{self.label} control is unavailable")
                self._observe()
                logger.warning("%s: %s request not sent; the control is unavailable", self.label, word(target))
                return self._response(False, self._operation["error"], self._operation)
            failure = self._io.write(path, "1" if target else "0")
            if failure:
                self._operation = self._record(origin, target, "failed", "not_attempted", "not_checked",
                                               f"The driver rejected the request: {failure}")
                # A failed transport does not prove that nothing changed.
                self._observe()
                logger.warning("%s: %s request failed (%s); saved preference unchanged", self.label, word(target), failure)
                return self._response(False, self._operation["error"], self._operation)
            self._request = {"value": target, "origin": origin, "identity": self._io.identity(path)}
            self._evidence_reset = None
            save_status, save_error = "not_attempted", None
            if persist:
                save_status, save_error = self._settings().save_checked({self.key: target}, force=True)
            self._observe()
            observed = self._observation["observed"]
            verification = "unverified" if observed is None else "matches" if observed == target else "mismatch"
            error = save_error if save_status in ("failed", "unsynced") else None
            if error is None and verification == "mismatch":
                error = f"The driver reports {word(observed)} after an {word(target)} request."
            self._operation = self._record(origin, target, "accepted", save_status, verification, error)
            log = logger.error if save_status in ("failed", "unsynced") else logger.warning if verification == "mismatch" else logger.info
            log("%s: %s request accepted by the driver (%s); save %s%s; readback %s (%r), %s", self.label, word(target),
                origin, save_status, f" ({save_error})" if save_error else "", self._observation["readback_status"],
                self._observation["raw_readback"], verification)
            success = save_status in ("saved", "not_attempted") and verification != "mismatch"
            return self._response(success, error, self._operation)

    @staticmethod
    def _record(origin, target, write_status, save_status, verification, error):
        return {"origin": origin, "target": target, "write_status": write_status,
                "save_status": save_status, "verification": verification, "error": error}

    def restore(self, present):
        """Replay a valid saved preference once at startup without saving it again."""
        with self._lock:
            configured, status = self._configured()
            outcome = {"status": "not_attempted", "reason": None, "target": configured,
                       "verification": "not_checked", "error": None}
            if status == "unreadable":
                outcome["reason"] = "settings_unreadable"
            elif status == "invalid":
                outcome["reason"] = "invalid_preference"
                logger.warning("%s: the saved value is invalid and was not applied; choose On or Off to replace it",
                               self.label)
            elif configured is None:
                outcome["reason"] = "no_preference"
            elif not present:
                outcome["reason"] = "unavailable"
                logger.warning("%s: saved %s preference not applied; the control is unavailable", self.label,
                               word(configured))
            else:
                self.apply(configured, persist=False, origin="startup")
                operation = self._operation
                outcome.update(status="accepted" if operation["write_status"] == "accepted" else "failed",
                               verification=operation["verification"], error=operation["error"])
            self._restore = outcome
            return outcome["status"] == "accepted"
