"""User-session tasks delegated to the installed desktop and power-policy services."""
import json
from pathlib import Path
import re
import time
import xml.etree.ElementTree as ET

PPD = ("org.freedesktop.UPower.PowerProfiles", "net.hadess.PowerProfiles")
PRESETS = {"desk": "balanced", "conferencing": "balanced", "travel": "power-saver",
           "quiet": "power-saver", "gaming": "performance"}
APP_ID = "io.github.archer.gui"


def valid_profile(value):
    if not isinstance(value, dict) or set(value) != {"schema_version", "preset", "duration_minutes"}:
        raise ValueError("Expected a versioned workspace preference with a preset and duration")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["preset"] not in PRESETS:
        raise ValueError("Unsupported workspace preference")
    if type(value["duration_minutes"]) is not int or not 1 <= value["duration_minutes"] <= 240:
        raise ValueError("Choose a duration between one minute and four hours")
    return value


class ProfileProvider:
    def __init__(self):
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        for name in PPD:
            path = "/" + name.replace(".", "/")
            proxy = Gio.DBusProxy.new_sync(bus, Gio.DBusProxyFlags.DO_NOT_AUTO_START, None, name, path, name, None)
            if proxy.get_name_owner():
                self.proxy = proxy
                self.name = name
                self.properties = proxy.get_cached_property
                xml = bus.call_sync(name, path, "org.freedesktop.DBus.Introspectable", "Introspect", None,
                                    GLib.VariantType.new("(s)"), Gio.DBusCallFlags.NO_AUTO_START, 2500, None).unpack()[0]
                self.holds = ET.fromstring(xml).find(f"./interface[@name='{name}']/method[@name='HoldProfile']") is not None
                proxy.set_default_timeout(3000)
                return
        raise ValueError("No running power-profiles D-Bus provider")

    def plan(self, preset):
        selected = PRESETS[preset]
        profiles = self.properties("Profiles")
        offered = profiles and selected in [p.get("Profile") for p in profiles.unpack()]
        current = self.properties("ActiveProfile")
        active = current.unpack() if current else None
        # The PPD hold API accepts power-saver or performance. Do not overwrite
        # someone else's balanced setting or simulate a lease with repeated writes.
        available = bool(offered and (active == selected or self.holds and selected in ("performance", "power-saver")))
        return {"preset": preset, "owner": self.name, "requested": selected, "current": active,
                "available": available, "hold_required": active != selected,
                "reason": None if available else "The provider offers no temporary hold for this profile."}

    def hold(self, profile):
        return self.proxy.HoldProfile("(sss)", profile, "Archer workspace", APP_ID)

    def release(self, cookie):
        self.proxy.ReleaseProfile("(u)", cookie)


def run_preset(preference, *, apply=False, provider=None, sleep=time.sleep):
    preference = valid_profile(preference)
    provider = provider or ProfileProvider()
    plan = provider.plan(preference["preset"])
    if not apply:
        return plan
    if not plan["available"]:
        raise ValueError(plan["reason"])
    if not plan["hold_required"]:
        return {**plan, "operation": "already_active"}
    cookie = provider.hold(plan["requested"])
    try:
        print(json.dumps({**plan, "operation": "held", "minutes": preference["duration_minutes"]}), flush=True)
        sleep(preference["duration_minutes"] * 60)
    finally:
        provider.release(cookie)
    return {**plan, "operation": "released"}


def switcheroo():
    from gi.repository import Gio
    bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    name = "net.hadess.SwitcherooControl"
    proxy = Gio.DBusProxy.new_sync(bus, Gio.DBusProxyFlags.NONE, None, name, "/net/hadess/SwitcherooControl", name, None)
    value = proxy.get_cached_property("GPUs")
    if value is None:
        raise ValueError("SwitcherooControl did not report any GPUs")
    return value.unpack()


def launch(gpu, desktop):
    from gi.repository import Gio
    if not re.fullmatch(r"[\w.-]+\.desktop", desktop):
        raise ValueError("Select an installed desktop application")
    devices = switcheroo()
    if not 0 <= gpu < len(devices):
        raise ValueError("Selected GPU is unavailable")
    app = Gio.DesktopAppInfo.new(desktop)
    if not app or app.get_boolean("DBusActivatable"):
        raise ValueError("Application is missing or uses D-Bus activation with its own launch environment")
    env = devices[gpu]["Environment"]
    context = Gio.AppLaunchContext.new()
    for k, v in zip(env[::2], env[1::2]):
        context.setenv(k, v)
    if not app.launch([], context):
        raise ValueError("Application launch request failed")
    return {"requested_gpu": devices[gpu]["Name"], "application": desktop, "render_device_verified": False}


def npu_self_test():
    # This tiny MIT-licensed model is built locally. A CPU fallback fails the test.
    import numpy as np
    from openvino import Core, Model, opset13 as ops

    parameter = ops.parameter([1, 4], np.float32)
    model = Model([ops.relu(parameter)], [parameter], "archer-relu")
    compiled = Core().compile_model(model, "NPU")
    targets = compiled.get_property("EXECUTION_DEVICES")
    if not targets or not all(str(d).split(".")[0] == "NPU" for d in targets):
        raise ValueError("Runtime did not confirm exclusive execution on the NPU")
    result = compiled([np.array([[-1, 0, 1, 2]], dtype=np.float32)])[0]
    if not np.array_equal(result, [[0, 0, 1, 2]]):
        raise ValueError("NPU output differs from the expected result")
    return {"test": "relu-1", "devices": list(targets), "result": "passed"}


def preference_file(path):
    with Path(path).open() as stream:
        source = stream.read(4097)
    if len(source) > 4096:
        raise ValueError("Workspace preference exceeds size limit")
    return valid_profile(json.loads(source))


def brightness_devices(sysfs=Path("/sys")):
    devices = []
    for kind in ("backlight", "leds"):
        for path in sorted((sysfs / "class" / kind).glob("*")):
            if kind == "leds" and not path.name.endswith("::kbd_backlight") and path.name != "kbd_backlight":
                continue
            try:
                maximum = int((path / "max_brightness").read_text())
                value = int((path / "brightness").read_text())
            except (OSError, ValueError):
                continue
            if maximum > 0 and 0 <= value <= maximum:
                devices.append({"kind": kind, "device": path.name, "maximum": maximum, "observed": value})
    return devices


def set_brightness(kind, name, value):
    from gi.repository import Gio, GLib
    import os
    selected = next((d for d in brightness_devices() if d["kind"] == kind and d["device"] == name), None)
    if selected is None or type(value) is not int or not 0 <= value <= selected["maximum"]:
        raise ValueError("Brightness device or requested level is unavailable")
    bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    owner = "org.freedesktop.login1"
    session = bus.call_sync(owner, "/org/freedesktop/login1", owner + ".Manager", "GetSessionByPID",
                            GLib.Variant("(u)", (os.getpid(),)), GLib.VariantType.new("(o)"),
                            Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]
    bus.call_sync(owner, session, owner + ".Session", "SetBrightness", GLib.Variant("(ssu)", (kind, name, value)),
                  None, Gio.DBusCallFlags.NONE, 3000, None)
    observed = next((d["observed"] for d in brightness_devices() if d["kind"] == kind and d["device"] == name), None)
    return {"requested": value, "observed": observed, "verification": "matched" if value == observed else "unverified"}


def power_context(sysfs=Path("/sys")):
    from archer_inventory import read
    supplies = [read(p / "online") for p in (sysfs / "class/power_supply").glob("*")
                if read(p / "type") in ("Mains", "USB", "USB_C", "USB_PD", "Wireless")]
    if "1" in supplies:
        return "ac"
    if supplies and all(v == "0" for v in supplies):
        return "battery"
    return "unknown"


def dock_state(sysfs=Path("/sys")):
    from archer_inventory import read
    # A Type-C partner can be a charger. Require a bound Thunderbolt peripheral
    # or an active external display to select a desk preset.
    displays = [p for p in (sysfs / "class/drm").glob("card*-*")
                if not re.search(r"-(?:eDP|LVDS|DSI)-", p.name)]
    external = any(read(p / "enabled") == "enabled" for p in displays)
    authorized = any(read(p / "authorized") == "1" for p in (sysfs / "bus/thunderbolt/devices").glob("*-*"))
    return external or authorized


def follow_power(ac, battery, minutes, apply=False, dock=None):
    provider = ProfileProvider()
    plans = {"ac": provider.plan(ac), "battery": provider.plan(battery)}
    if dock:
        plans["dock"] = provider.plan(dock)
    if not apply:
        return {"plans": plans, "current_power": power_context(), "docked": dock_state(), "minutes": minutes}
    if not all(plan["available"] for plan in plans.values()):
        raise ValueError("The provider cannot apply both requested profiles")
    until = time.clock_gettime(time.CLOCK_BOOTTIME) + minutes * 60
    context, candidate, repeats, cookie = None, None, 0, None
    try:
        while time.clock_gettime(time.CLOCK_BOOTTIME) < until:
            current = power_context()
            observation = "dock" if dock and dock_state() else current
            repeats = repeats + 1 if observation == candidate else 1
            candidate = observation
            if repeats >= 2 and observation != context:
                if cookie is not None:
                    provider.release(cookie)
                    cookie = None
                context = observation
                if observation in plans:
                    plan = provider.plan({"dock": dock, "battery": battery, "ac": ac}[observation])
                    if plan["available"] and plan["hold_required"]:
                        cookie = provider.hold(plan["requested"])
                    print(json.dumps({"power": current, "context": observation, "plan": plan, "held": cookie is not None}), flush=True)
            time.sleep(2)
    finally:
        if cookie is not None:
            provider.release(cookie)
    return {"operation": "expired"}
