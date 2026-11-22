"""Hardware-free Archer 2.1 control/backend contract tests."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gui"))

import archer_battery
from archer_capabilities import capability
import archer_daemon
import archer_fans
import archer_graphics
import archer_policy
from archer_settings import SettingsStore


class NativeFanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.hwmon = self.root / "hwmon0"
        (self.hwmon / "device/driver").mkdir(parents=True)
        module = self.root / "modules/acer_wmi"
        module.mkdir(parents=True)
        (self.hwmon / "device/driver/module").symlink_to(module)
        (self.hwmon / "name").write_text("acer")
        for name, value in (("pwm1", "0"), ("pwm1_enable", "2"),
                            ("pwm2", "0"), ("pwm2_enable", "2"),
                            ("fan1_input", "0"), ("fan2_input", "3100"),
                            ("temp1_input", "60000"), ("temp2_input", "71000"),
                            ("temp3_input", "42000")):
            (self.hwmon / name).write_text(value)

    def test_native_mapping_modes_and_real_zero_rpm(self):
        backends = archer_fans.NativeAcerHwmon.probe(self.root)
        self.assertEqual(len(backends), 1)
        backend = backends[0]
        self.assertEqual([item["role"] for item in backend.temperatures()], ["cpu", "gpu", "external"])
        self.assertEqual(backend.fans()[0]["rpm"], 0)
        self.assertTrue(backend.set_manual(1, 50))
        self.assertEqual((self.hwmon / "pwm1_enable").read_text(), "1")
        self.assertEqual((self.hwmon / "pwm1").read_text(), "128")
        self.assertTrue(backend.set_mode(1, "turbo"))
        self.assertEqual((self.hwmon / "pwm1_enable").read_text(), "0")
        self.assertTrue(backend.set_auto())
        self.assertEqual((self.hwmon / "pwm2_enable").read_text(), "2")

    def test_wrong_module_is_never_treated_as_native(self):
        (self.hwmon / "device/driver/module").unlink()
        other = self.root / "modules/linuwu_sense"
        other.mkdir()
        (self.hwmon / "device/driver/module").symlink_to(other)
        self.assertEqual(archer_fans.NativeAcerHwmon.probe(self.root), [])

    def test_failed_write_restores_every_fan_to_auto(self):
        backend = archer_fans.NativeAcerHwmon.probe(self.root)[0]
        original = archer_fans._write

        def fail_pwm(path, value):
            if Path(path).name == "pwm1" and str(value) == "128":
                return False
            return original(path, value)

        with patch.object(archer_fans, "_write", side_effect=fail_pwm):
            self.assertFalse(backend.set_manual(1, 50))
        self.assertEqual((self.hwmon / "pwm1_enable").read_text(), "2")
        self.assertEqual((self.hwmon / "pwm2_enable").read_text(), "2")

    def test_suspend_releases_curves_and_resume_reprobes(self):
        manager = archer_daemon.HardwareManager.__new__(
            archer_daemon.HardwareManager)
        manager._fan_curve_engine = Mock()
        manager._fan_curve_engine.get_state.return_value = {
            "cpu": {"active": True, "points": [[40, 40], [80, 90]]},
            "gpu": {"active": False, "points": []}}
        manager._game_mode_active = False
        manager.prepare_sleep()
        manager._fan_curve_engine.stop.assert_called_once_with()
        self.assertIn("cpu", manager._resume_curves)

        manager._inventory = {"devices": []}
        manager._collect_inventory = lambda: manager._inventory
        manager._native_fans = []
        manager.features = []
        manager.sense_base = None
        with patch.object(archer_fans.NativeAcerHwmon, "probe", return_value=[]):
            manager.resume_controls()
        self.assertEqual(manager._resume_curves, {})


class BatteryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.power = self.root / "class/power_supply"
        self.power.mkdir(parents=True)

    def battery(self, name, values):
        path = self.power / name
        path.mkdir()
        (path / "type").write_text("Battery")
        for key, value in values.items():
            (path / key).write_text(str(value))
        return path

    def test_multiple_units_missing_values_and_stable_keys(self):
        self.battery("BAT0", {"status": "Discharging", "energy_now": 30000,
                              "energy_full": 60000, "power_now": 10000})
        self.battery("CMB1", {"status": "Charging", "charge_now": 2000,
                              "charge_full": 5000, "current_now": 1000})
        rows = archer_battery.discover(self.root, {"BAT0": "inventory-a", "CMB1": "inventory-b"})
        self.assertEqual([row["device_key"] for row in rows], ["inventory-a", "inventory-b"])
        self.assertEqual(rows[0]["time_remaining_seconds"], 10800)
        self.assertEqual(rows[1]["time_remaining_seconds"], 10800)
        self.assertNotIn("capacity", rows[0]["measurements"])

    def test_unknown_constraints_are_read_only(self):
        battery = self.battery("BAT0", {"charge_control_end_threshold": 80})
        control = archer_battery.charge_control(battery)
        self.assertFalse(control["writable"])
        self.assertEqual(control["reason_code"], "constraints_unknown")
        self.assertFalse(archer_battery.set_threshold(self.root, "power_supply:BAT0", None, 90))

    def test_qualified_threshold_and_partial_failure_rollback(self):
        battery = self.battery("BAT0", {
            "charge_control_start_threshold": 60,
            "charge_control_end_threshold": 80,
            "charge_control_threshold_min": 50,
            "charge_control_threshold_max": 100,
            "charge_control_threshold_step": 5})
        self.assertTrue(archer_battery.set_threshold(self.root, "power_supply:BAT0", 65, 90))
        self.assertEqual((battery / "charge_control_end_threshold").read_text(), "90")
        original = archer_battery._write_existing

        def fail_end(path, value):
            if Path(path).name == "charge_control_end_threshold" and value == 95:
                raise OSError("write failed")
            return original(path, value)

        with patch.object(archer_battery, "_write_existing", side_effect=fail_end):
            self.assertFalse(archer_battery.set_threshold(self.root, "power_supply:BAT0", 70, 95))
        self.assertEqual((battery / "charge_control_start_threshold").read_text(), "65")
        self.assertEqual((battery / "charge_control_end_threshold").read_text(), "90")

    def test_discovery_is_read_only(self):
        self.battery("BAT0", {"status": "Full", "capacity": 100})
        with patch.object(archer_battery, "_write_existing") as write:
            archer_battery.discover(self.root)
        write.assert_not_called()

    def test_new_policy_does_not_apply_to_unrelated_new_battery(self):
        battery = self.battery("DOCK", {"charge_control_end_threshold": 100})
        policy = self.root / "battery.json"
        policy.write_text(json.dumps({"schema_version": 2, "enabled": True, "batteries": {}}))
        self.assertFalse(archer_battery.apply(self.root, policy, respect_owners=False))
        self.assertEqual((battery / "charge_control_end_threshold").read_text(), "100")

    def test_full_once_persists_ownership_before_hardware_write(self):
        manager = archer_daemon.HardwareManager.__new__(
            archer_daemon.HardwareManager)
        manager.settings = SettingsStore(self.root / "settings.json")
        manager._inventory = {"devices": [{
            "kind": "power_supply", "key": "stable-battery", "node": "BAT0"}]}
        manager._collect_inventory = lambda: manager._inventory
        battery = {"device_key": "stable-battery", "node": "BAT0",
                   "charge_control": {"kind": "variable_threshold",
                                      "writable": True, "start": 60, "end": 80,
                                      "constraints": {"maximum": 100}}}

        def applied_after_persist(root, key, start, end):
            data = archer_battery.read_policy_data(self.root / "battery.json")
            override = data["batteries"]["stable-battery"]["full_override"]
            self.assertEqual(override["last_written"], 100)
            return True

        with patch.object(manager, "_charge_owner_conflicts", return_value=[]), \
                patch.object(manager, "get_battery_devices", return_value=[battery]), \
                patch.object(archer_battery, "set_threshold",
                             side_effect=applied_after_persist):
            result = manager.charge_full_once("stable-battery", 600)
        self.assertTrue(result["success"])

    def test_expired_override_restores_only_owned_value(self):
        manager = archer_daemon.HardwareManager.__new__(
            archer_daemon.HardwareManager)
        manager.settings = SettingsStore(self.root / "settings.json")
        manager._inventory = {"devices": [{
            "kind": "power_supply", "key": "stable-battery", "node": "BAT0"}]}
        policy = self.root / "battery.json"
        archer_battery.write_policy_data({
            "schema_version": 2, "enabled": True,
            "batteries": {"stable-battery": {
                "node": "BAT0", "end": 80, "owner": "archer",
                "full_override": {"expires_at": 1, "original_end": 80,
                                  "owner": "archer", "last_written": 100}}}}, policy)
        current = {"device_key": "stable-battery", "node": "BAT0",
                   "charge_control": {"start": 60, "end": 100}}
        with patch.object(manager, "_charge_owner_conflicts", return_value=[]), \
                patch.object(manager, "get_battery_devices", return_value=[current]), \
                patch.object(archer_battery, "set_threshold", return_value=True) as write:
            manager._expire_battery_overrides()
        write.assert_called_once_with(
            Path("/sys"), "power_supply:BAT0", 60, 80)
        saved = archer_battery.read_policy_data(policy)
        self.assertNotIn(
            "full_override", saved["batteries"]["stable-battery"])


class GraphicsAndPolicyTests(unittest.TestCase):
    def inventory(self):
        return {"schema_version": 1, "generation": "abc", "devices": [
            {"key": "nvidia", "kind": "pci", "driver": "nvidia", "attributes": {
                "class": "0x030200", "vendor": "0x10de", "device": "0x2684"}},
            {"key": "intel", "kind": "pci", "driver": "i915", "attributes": {
                "class": "0x030000", "vendor": "0x8086", "device": "0x46a6", "gpu_role": "integrated"}},
            {"key": "audio", "kind": "pci", "driver": "snd_hda_intel", "attributes": {
                "class": "0x040300", "vendor": "0x10de", "device": "0x22ba"}},
        ]}

    def test_numeric_support_data_filters_non_graphics_pci(self):
        with patch.object(archer_graphics, "kernel_module_status", return_value=[]), \
                patch.object(archer_graphics, "active_outputs", return_value=[]), \
                patch.object(archer_graphics, "secure_boot_state", return_value="disabled"):
            plan = archer_graphics.build_plan(
                self.inventory(), support_db={"2684": {"devid": "0x2684", "open": True}},
                which=lambda name: "/usr/bin/envycontrol" if name == "envycontrol" else None,
                os_release={"ID": "arch"}, package_available=lambda package: True)
        self.assertTrue(plan["available"])
        self.assertEqual([gpu["device_key"] for gpu in plan["gpus"]], ["nvidia", "intel"])
        self.assertEqual(plan["packages"], ["nvidia-open-dkms", "nvidia-utils"])

    def test_distribution_owner_is_blocked_without_executor(self):
        plan = archer_graphics.build_plan(
            self.inventory(), support_db={"2684": {"devid": "0x2684", "open": True}},
            which=lambda name: "/usr/bin/" + name if name in ("mhwd", "envycontrol") else None,
            os_release={"ID": "manjaro"})
        self.assertFalse(plan["available"])
        self.assertEqual(plan["backend"], "mhwd")
        self.assertEqual(plan["reason_code"], "distribution_manager_required")

    def test_supported_gpu_database_accepts_list_root(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "supported-gpus.json"
            path.write_text(json.dumps([{"devid": "0x2684", "open": True}]))
            self.assertIn("2684", archer_graphics.supported_gpu_database([path]))

    def test_conditional_restoration(self):
        record = archer_policy.lease("archer", "cpu_policy", "balance", "performance", token="x")
        self.assertTrue(archer_policy.may_restore("performance", record))
        self.assertFalse(archer_policy.may_restore("power", record))

    def test_capability_accepts_shared_inventory_generation(self):
        item = capability("fan.a.duty", "hwmon-key", "native", generation="deadbeef")
        self.assertEqual(item["inventory_generation"], "deadbeef")

    def test_unqualified_forced_driver_override_is_rejected_but_removable(self):
        manager = archer_daemon.HardwareManager.__new__(
            archer_daemon.HardwareManager)
        with patch.object(archer_daemon, "read_sysfs", return_value="Unknown"), \
                patch.object(manager, "_system_operation",
                             return_value={"success": True}) as operation:
            result = manager.set_modprobe_parameter("enable_all")
            self.assertFalse(result["success"])
            operation.assert_not_called()
            self.assertTrue(manager.remove_modprobe_parameter()["success"])
            operation.assert_called_once_with("remove-override")


if __name__ == "__main__":
    unittest.main()
