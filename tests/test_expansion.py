"""Contracts exercised without a laptop, desktop login or privileged hardware writes."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gui"))
from archer_firmware import firmware_status
from archer_health import compare, report, snapshot
from archer_inventory import collect_inventory, installer_values
from archer_runtime import CommandResult, run_command
from archer_workspace import run_preset, valid_profile


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InventoryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.sys = self.root / "sys"
        self.sys.mkdir()
        self.attribute("etc/os-release", 'ID=artix\nID_LIKE=arch')

    def attribute(self, path, value):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value)
        return target

    def test_installer_projection_matches_unusual_devices(self):
        for i, vendor in enumerate(("0x8086", "0x10de", "0x1002")):
            base = f"sys/bus/pci/devices/0000:0{i}:00.0/"
            self.attribute(base + "vendor", vendor)
            self.attribute(base + "class", "0x030200")
        for name in ("CMB0", "DOCK"):
            self.attribute(f"sys/class/power_supply/{name}/type", "Battery")
        data = collect_inventory(self.root)
        self.assertEqual(sum(d["kind"] == "power_supply" for d in data["devices"]), 2)
        values = installer_values(data)
        self.assertEqual((values[4], values[6], values[7], values[10]), (1, 1, 1, 1))
        self.assertEqual(values[-1], "unsupported-init")
        self.assertIsNone(data["dmi"]["product_name"])
        self.assertIsNone(data["environment"]["kernel"])

    def test_allocator_renumbering_and_rebind(self):
        node = self.attribute("sys/devices/platform/acer-wmi/hwmon/hwmon2/name", "acer").parent
        directory = self.sys / "class/hwmon"
        directory.mkdir(parents=True)
        link = directory / "hwmon2"
        link.symlink_to(node)
        first = collect_inventory(self.root)
        node = node.rename(node.with_name("hwmon9"))
        link.unlink()
        (directory / "hwmon9").symlink_to(node)
        second = collect_inventory(self.root)
        self.assertEqual(first["generation"], second["generation"])
        (node / "fan1_input").write_text("0")
        third = collect_inventory(self.root)
        self.assertNotEqual(third["generation"], first["generation"])
        change = compare(snapshot(first, self.root), snapshot(third, self.root))
        self.assertEqual(change["binding_or_interface_changed"], [third["devices"][0]["key"]])

    def test_export_excludes_unique_device_fields_and_paths(self):
        base = "sys/class/net/enx001122334455/"
        self.attribute(base + "address", "00:11:22:33:44:55")
        self.attribute("sys/class/dmi/id/product_uuid", "user-secret")
        self.attribute("sys/class/dmi/id/board_serial", "user-secret")
        self.attribute("sys/bus/usb/devices/1-1/idVendor", "1025")
        self.attribute("sys/bus/usb/devices/1-1/serial", "user-secret")
        self.attribute("sys/bus/usb/devices/1-1/modalias", "user-secret")
        data = report(collect_inventory(self.root), self.root)
        text = json.dumps(data)
        self.assertIn("1025", text)
        for secret in ("enx001122334455", "00:11:22:33:44:55", "user-secret", str(self.root), "topology", "modalias"):
            self.assertNotIn(secret, text)


class FirmwareTests(unittest.TestCase):
    def result(self, flags, updates=None, stamp=95, problems=None):
        payloads = {"get-devices": {"Devices": [{"Name": "test", "DeviceId": "local", "Flags": flags, "Problems": problems}]},
                    "get-remotes": {"Remotes": [{"Kind": "download", "Enabled": True, "RefreshInterval": 10, "Mtime": stamp}]},
                    "get-updates": {"Devices": updates or []}}
        return firmware_status(lambda args, timeout: CommandResult(0, json.dumps(payloads[args[1]])), now=100)

    def test_supported_no_update_requires_fresh_metadata(self):
        self.assertEqual(self.result(["supported"])["status"], "current")
        self.assertEqual(self.result(["supported"], stamp=80)["status"], "metadata_stale")
        self.assertEqual(self.result([])["status"], "unsupported")
        self.assertEqual(self.result(["supported"], problems=["require-ac-power"])["status"], "blocked")

    def test_release_fields_are_scoped_and_no_update_execution(self):
        rows = [{"DeviceId": "local", "Releases": [{
            "Version": "2", "Summary": "Fix", "Description": "Security update",
            "Urgency": "high", "InstallDuration": 90, "Flags": ["needs-reboot"],
            "Secret": "FOO", "Locations": ["https://secret.invalid"]}]}]
        commands = []
        payloads = {
            "get-devices": {"Devices": [{
                "Name": "test", "Vendor": "Acer", "Version": "1", "DeviceId": "local",
                "Flags": ["supported", "require-ac"]}]},
            "get-remotes": {"Remotes": [{
                "Kind": "download", "Enabled": True, "RefreshInterval": 10, "Mtime": 95}]},
            "get-updates": {"Devices": rows},
        }

        def read(args, timeout):
            commands.append((args, timeout))
            return CommandResult(0, json.dumps(payloads[args[1]]))

        data = firmware_status(read, now=100)
        self.assertEqual(data["updates"][0]["Releases"],
                         [{"Version": "2", "Summary": "Fix", "Urgency": "high"}])
        self.assertTrue(data["devices"][0]["ac_required"])
        self.assertEqual(data["devices"][0]["version"], "1")
        self.assertEqual(data["devices"][0]["releases"][0]["description"], "Security update")
        self.assertTrue(data["devices"][0]["releases"][0]["reboot"])
        self.assertIn("not guaranteed", data["devices"][0]["rollback"])
        self.assertNotIn("Secret", json.dumps(data))
        self.assertNotIn("Locations", json.dumps(data))
        self.assertEqual([call[0][1] for call in commands], ["get-devices", "get-updates", "get-remotes"])
        self.assertTrue(all(call[0][0] == "/usr/bin/fwupdmgr" and call[0][-2:] == ["--json", "--no-unreported-check"]
                            for call in commands))
        with self.assertRaises(ValueError):
            firmware_status(lambda *a, **k: CommandResult(0, "[]"))
        failure = firmware_status(lambda *a, **k: CommandResult(1, stderr="offline"))
        self.assertEqual((failure["status"], failure["devices"], failure["updates"]),
                         ("offline", [], []))


class SessionTests(unittest.TestCase):
    def test_presets_are_declarative_and_release_after_failure(self):
        pref = {"schema_version": 1, "preset": "travel", "duration_minutes": 15}
        for field, value in (("shell", "anything"), ("duration_minutes", True), ("duration_minutes", 241), ("preset", "../../x")):
            with self.assertRaises(ValueError):
                valid_profile({**pref, field: value})

        class Provider:
            def plan(self, _):
                return {"available": True, "hold_required": True, "requested": "power-saver"}

            def hold(self, value):
                self.held = value
                return 7

            def release(self, cookie):
                self.released = cookie

        provider = Provider()
        with self.assertRaises(InterruptedError):
            run_preset(pref, apply=True, provider=provider, sleep=lambda _: self.raise_interruption())
        self.assertEqual((provider.held, provider.released), ("power-saver", 7))

    def raise_interruption(self):
        raise InterruptedError()

    def test_runtime_caps_output(self):
        result = run_command([sys.executable, "-c", "print('a' * 5000000)"], 5)
        self.assertFalse(result.success)
        self.assertIn("output limit", result.stderr)


class MigrationTests(unittest.TestCase):
    def test_ownership_transfer_resumes_and_preserves_user_and_package_files(self):
        mod = load_script("archer-package-migration")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            desktop = root / "usr/share/applications/io.github.archer.desktop"
            desktop.parent.mkdir(parents=True)
            desktop.write_bytes((ROOT / "gui/io.github.archer.desktop").read_bytes())
            original = desktop.read_bytes()
            manifest = root / mod.MANIFEST
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({"modules_installed": ["audio", "gui"], "files_created": ["/" + str(desktop.relative_to(root))]}))
            settings = root / "etc/archer/settings.json"
            settings.parent.mkdir(parents=True)
            settings.write_text('{"schema_version":2,"lcd_override":false}')
            installed = []

            def simulate(*args, check=True):
                code = 0 if args[0] != "pacman" or args[-1] in installed else 1
                return subprocess.CompletedProcess(args, code, "active", "")
            with patch.object(mod, "run", side_effect=simulate):
                desktop.write_text("local edit")
                with self.assertRaises(ValueError):
                    mod.transfer("plan", root)
                desktop.write_bytes(original)
                mod.transfer("prepare", root)
                self.assertFalse(desktop.exists())
                mod.transfer("prepare", root)
                installed.extend(["archer-core", "archer-daemon", "archer-gui"])
                desktop.write_text("package launcher")
                mod.transfer("finish", root)
                mod.transfer("finish", root)
                self.assertEqual(desktop.read_text(), "package launcher")
                self.assertEqual(json.loads(manifest.read_text())["modules_installed"], ["audio"])
                self.assertFalse(json.loads(settings.read_text())["lcd_override"])


if __name__ == "__main__":
    unittest.main()
