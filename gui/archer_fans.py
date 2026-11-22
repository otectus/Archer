"""Qualified fan/sensor backends.

Only documented native Acer hwmon semantics and Archer's existing Linuwu ABI
are writable. Unknown hwmon layouts remain telemetry-only.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import time


def _read(path):
    try:
        return Path(path).read_text().strip()
    except (OSError, ValueError):
        return None


def _write(path, value):
    try:
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_TRUNC | os.O_CLOEXEC), "w") as stream:
            stream.write(str(value))
        return True
    except (OSError, ValueError, TypeError):
        return False


def stable_hwmon_key(path):
    """Use the physical device link rather than the unstable hwmon index."""
    path = Path(path)
    try:
        device = (path / "device").resolve(strict=True)
        return "hwmon:" + str(device).removeprefix("/sys/").replace("/", "_")
    except OSError:
        name = _read(path / "name") or "unknown"
        return f"hwmon:{name}:unresolved"


class NativeAcerHwmon:
    """Native acer-wmi backend as exposed by qualified current kernels.

    pwm*_enable: 0 turbo, 1 manual, 2 firmware automatic. PWM duty uses the
    standard 0..255 hwmon representation. Sensor mapping is native-specific:
    CPU=temp1, GPU=temp2, external=temp3.
    """

    backend = "native_acer_hwmon"
    modes = {0: "turbo", 1: "manual", 2: "auto"}

    def __init__(self, path):
        self.path = Path(path)
        self.device_key = stable_hwmon_key(path)
        try:
            self._physical = (self.path / "device").resolve(strict=True)
        except OSError:
            self._physical = None

    def _owned(self):
        try:
            physical = (self.path / "device").resolve(strict=True)
            module = (self.path / "device/driver/module").resolve(strict=True).name
            return self._physical is not None and physical == self._physical and module == "acer_wmi"
        except OSError:
            return False

    @classmethod
    def probe(cls, root=Path("/sys/class/hwmon")):
        found = []
        try:
            candidates = sorted(root.glob("hwmon*"))
        except OSError:
            return found
        for path in candidates:
            name = (_read(path / "name") or "").lower().replace("-", "_")
            # linuwu_sense has different sensor/mode semantics.
            if name not in ("acer", "acer_wmi"):
                continue
            try:
                module = (path / "device/driver/module").resolve(strict=True).name
            except OSError:
                continue
            if module != "acer_wmi":
                continue
            backend = cls(path)
            if backend.fans():
                found.append(backend)
        return found

    def fans(self):
        result = []
        indexes = set()
        for pwm in self.path.glob("pwm[0-9]*"):
            match = re.fullmatch(r"pwm([1-9][0-9]*)", pwm.name)
            if match:
                indexes.add(int(match[1]))
        for index in sorted(indexes):
            pwm = self.path / f"pwm{index}"
            enable = self.path / f"pwm{index}_enable"
            observed = _integer(_read(pwm))
            selected = _integer(_read(enable))
            if observed is None or not 0 <= observed <= 255 or selected not in self.modes:
                continue
            label = _read(self.path / f"fan{index}_label") or f"Fan {index}"
            result.append({
                "id": f"{self.device_key}:fan{index}", "index": index,
                "label": label, "backend": self.backend,
                "modes": ["auto", "manual", "turbo"], "mode": self.modes[selected],
                "percent": round(observed * 100 / 255) if selected == 1 else None,
                # Zero is legal in hwmon but Archer does not expose fan-off.
                "minimum_percent": 1, "maximum_percent": 100,
                "rpm": _integer(_read(self.path / f"fan{index}_input")),
            })
        return result

    def temperatures(self):
        labels = {1: "CPU", 2: "GPU", 3: "External"}
        result = []
        indexes = set()
        for source in self.path.glob("temp[0-9]*_input"):
            match = re.fullmatch(r"temp([1-9][0-9]*)_input", source.name)
            if match:
                indexes.add(int(match[1]))
        observed_at = time.time()
        for index in sorted(indexes):
            fallback = labels.get(index, f"Sensor {index}")
            value = _temperature(_read(self.path / f"temp{index}_input"))
            if value is None:
                continue
            result.append({"id": f"{self.device_key}:temp{index}",
                           "label": _read(self.path / f"temp{index}_label") or fallback,
                           "role": fallback.lower().replace(" ", "_"), "celsius": value,
                           "sampled_at": observed_at,
                           "source": self.backend})
        return result

    def temperature(self, sensor_id):
        if not self._owned() or not isinstance(sensor_id, str):
            return None
        for sensor in self.temperatures():
            if sensor["id"] == sensor_id:
                return sensor
        return None

    def set_auto(self, index=None):
        if not self._owned():
            return False
        indexes = [fan["index"] for fan in self.fans()]
        if index is not None:
            indexes = [index] if index in indexes else []
        restored = bool(indexes)
        # Never short-circuit: a failed fan must not leave later fans in manual.
        for i in indexes:
            restored = _write(self.path / f"pwm{i}_enable", 2) and restored
        return restored

    def set_mode(self, index, mode):
        if not self._owned():
            return False
        requested = {"turbo": 0, "auto": 2}.get(mode)
        indexes = {fan["index"] for fan in self.fans()}
        if index not in indexes or requested is None:
            return False
        target = self.path / f"pwm{index}_enable"
        if not _write(target, requested) or _read(target) != str(requested):
            self.set_auto(index)
            return False
        return True

    def set_manual(self, index, percent):
        if not self._owned():
            return False
        if type(index) is not int or type(percent) not in (int, float) or isinstance(percent, bool):
            return False
        fans = {fan["index"] for fan in self.fans()}
        if index not in fans or not 1 <= percent <= 100:
            return False
        raw = round(float(percent) * 255 / 100)
        enable = self.path / f"pwm{index}_enable"
        pwm = self.path / f"pwm{index}"
        if not _write(enable, 1) or not _write(pwm, raw):
            self.set_auto(index)
            return False
        verified = _read(enable) == "1" and _integer(_read(pwm)) == raw
        if not verified:
            self.set_auto(index)
        return verified


def _integer(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _temperature(value):
    raw = _integer(value)
    if raw is None:
        return None
    result = raw / 1000
    return result if 0 < result <= 125 else None
