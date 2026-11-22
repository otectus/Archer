"""Native fan-curve contracts using only temporary hwmon fixtures and mocks."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gui"))

import archer_daemon
import archer_dbus
import archer_fans
from archer.client import ArcherClient
from archer_settings import SettingsStore


class NativeFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.hwmon = self.root / "hwmon7"
        (self.hwmon / "device/driver").mkdir(parents=True)
        module = self.root / "modules/acer_wmi"
        module.mkdir(parents=True)
        (self.hwmon / "device/driver/module").symlink_to(module)
        (self.hwmon / "name").write_text("acer_wmi")
        for index in (1, 2, 9):
            (self.hwmon / f"pwm{index}").write_text("128")
            (self.hwmon / f"pwm{index}_enable").write_text("2")
            (self.hwmon / f"fan{index}_input").write_text(str(index * 100))
        for index, value in ((1, 60000), (2, 70000), (12, 45000)):
            (self.hwmon / f"temp{index}_input").write_text(str(value))
        self.backend = archer_fans.NativeAcerHwmon.probe(self.root)[0]
        self.backend.device_key = "inventory-hwmon-key"

    def manager(self):
        manager = archer_daemon.HardwareManager.__new__(archer_daemon.HardwareManager)
        manager.features = ["native_fan_control"]
        manager._native_fans = [self.backend]
        manager._fan_lock = threading.RLock()
        manager._inventory = {"generation": "fixture", "devices": []}
        manager.settings = SettingsStore(self.root / "settings.json")
        manager._native_curve_engine = archer_daemon.FanCurveEngine(
            manager._native_curve_get_temp, manager._native_curve_set_fan,
            manager._native_curve_restore_auto, paired=False, max_failures=1)
        manager._native_curve_engine._ensure_thread = Mock()
        return manager


class DiscoveryTests(NativeFixture):
    def test_arbitrary_indexes_have_stable_fan_and_sensor_ids(self):
        self.assertEqual([fan["index"] for fan in self.backend.fans()], [1, 2, 9])
        self.assertEqual([sensor["id"] for sensor in self.backend.temperatures()], [
            "inventory-hwmon-key:temp1", "inventory-hwmon-key:temp2",
            "inventory-hwmon-key:temp12"])

    def test_auto_attempts_every_fan_after_one_failure(self):
        attempted = []
        original = archer_fans._write

        def write(path, value):
            attempted.append(Path(path).name)
            if Path(path).name == "pwm1_enable":
                return False
            return original(path, value)

        with patch.object(archer_fans, "_write", side_effect=write):
            self.assertFalse(self.backend.set_auto())
        self.assertEqual(attempted, ["pwm1_enable", "pwm2_enable", "pwm9_enable"])


class EngineTests(unittest.TestCase):
    def test_stale_sensor_fails_safe_without_a_manual_write(self):
        clock = Mock(return_value=100.0)
        restore = Mock(return_value=True)
        write = Mock(return_value=True)
        engine = archer_daemon.FanCurveEngine(
            Mock(return_value={"celsius": 60, "sampled_at": 96}), write, restore,
            paired=False, max_failures=1, clock=clock)
        engine._ensure_thread = Mock()
        engine.start("stable-fan", [[30, 40], [80, 100]], sensor_id="stable-sensor",
                     expires_at=200, owner="test", resume_after_suspend=True)
        engine._tick()
        write.assert_not_called()
        restore.assert_called_once_with("stable-fan")
        state = engine.get_state()["stable-fan"]
        self.assertFalse(state["active"])
        self.assertIn("Stale", state["last_error"])

    def test_expiry_and_independent_stop_restore_the_selected_fan(self):
        clock = Mock(return_value=100.0)
        restore = Mock(return_value=True)
        engine = archer_daemon.FanCurveEngine(
            Mock(return_value={"celsius": 60, "sampled_at": 100}), Mock(return_value=True),
            restore, paired=False, max_failures=1, clock=clock)
        engine._ensure_thread = Mock()
        engine.start("fan-a", [[30, 40], [80, 100]], sensor_id="sensor", expires_at=100)
        engine.start("fan-b", [[30, 40], [80, 100]], sensor_id="sensor", expires_at=200)
        engine._tick()
        self.assertFalse(engine.get_state()["fan-a"]["active"])
        self.assertTrue(engine.get_state()["fan-b"]["active"])
        restore.assert_called_once_with("fan-a")
        self.assertTrue(engine.stop("fan-b"))
        restore.assert_called_with("fan-b")

    def test_idle_independent_engine_cleanup_performs_no_write(self):
        restore = Mock(return_value=True)
        engine = archer_daemon.FanCurveEngine(
            Mock(), Mock(), restore, paired=False, max_failures=1)
        self.assertTrue(engine.stop())
        restore.assert_not_called()


class ManagerTests(NativeFixture):
    def _initialized_manager(self, settings):
        inventory = {"generation": "fixture", "devices": [
            {"kind": "hwmon", "node": "hwmon7", "key": "inventory-hwmon-key"}]}
        with patch.object(archer_daemon.HardwareManager, "_collect_inventory", return_value=inventory), \
                patch.object(archer_fans.NativeAcerHwmon, "probe", return_value=[self.backend]), \
                patch.object(archer_daemon.HardwareManager, "_detect_driver", lambda manager: None), \
                patch.object(archer_daemon.HardwareManager, "_detect_laptop_type", lambda manager: None), \
                patch.object(archer_daemon.HardwareManager, "_detect_features",
                             lambda manager: manager.features.append("native_fan_control")), \
                patch.object(archer_daemon.HardwareManager, "_diagnose_driver", lambda manager: None):
            return archer_daemon.HardwareManager(settings, restore_settings=False)

    def test_clean_start_and_shutdown_do_not_write_unowned_native_fans(self):
        settings = SettingsStore(self.root / "clean-settings.json")
        with patch.object(self.backend, "set_auto", return_value=True) as auto:
            manager = self._initialized_manager(settings)
            auto.assert_not_called()
            manager.shutdown_fan_curves()
            auto.assert_not_called()

    def test_startup_restores_only_journaled_native_fan(self):
        settings = SettingsStore(self.root / "owned-settings.json")
        settings.set("native_fan_owners", {
            "inventory-hwmon-key:fan9": {
                "mode": "curve", "owner": "crashed", "claimed_at": 1}})
        with patch.object(self.backend, "set_auto", return_value=True) as auto:
            self._initialized_manager(settings)
        auto.assert_called_once_with(9)
        self.assertEqual(settings.get("native_fan_owners", {}), {})

    def test_start_requires_same_controller_sensor_and_safe_curve(self):
        manager = self.manager()
        fan_id = "inventory-hwmon-key:fan9"
        sensor_id = "inventory-hwmon-key:temp12"
        with patch.object(self.backend, "set_auto", return_value=True):
            result = manager.start_native_fan_curve(
                fan_id, sensor_id, [[30, 40], [70, 80], [90, 100]], 600,
                owner="dbus:test", resume_after_suspend=True)
        self.assertTrue(result["success"])
        state = manager._native_curve_engine.get_state()[fan_id]
        self.assertEqual((state["sensor_id"], state["owner"]), (sensor_id, "dbus:test"))
        self.assertTrue(state["resume_after_suspend"])
        self.assertEqual(manager.settings.get("native_fan_owners")[fan_id]["mode"], "curve")
        invalid = manager.start_native_fan_curve(
            fan_id, sensor_id, [[30, 80], [70, 40]], 600)
        self.assertFalse(invalid["success"])
        missing = manager.start_native_fan_curve(
            fan_id, "other-controller:temp1", [[30, 40], [80, 100]], 600)
        self.assertFalse(missing["success"])
        for points, duration in (([[30, "fast"], [80, 100]], 600), (None, 600),
                                 ([[30, 40], [80, 100]], None)):
            self.assertFalse(manager.start_native_fan_curve(
                fan_id, sensor_id, points, duration)["success"])

    def test_manual_ownership_is_persisted_before_fixture_write(self):
        manager = self.manager()
        fan_id = "inventory-hwmon-key:fan1"

        def verify_owned(_index, _percent):
            self.assertEqual(manager.settings.get("native_fan_owners")[fan_id]["mode"], "manual")
            return True

        with patch.object(self.backend, "set_auto", return_value=True), \
                patch.object(self.backend, "set_manual", side_effect=verify_owned):
            result = manager.set_fan_device(fan_id, 50, owner="dbus:test")
        self.assertTrue(result["success"])

    def test_resume_keeps_only_unexpired_opted_in_curve(self):
        manager = self.manager()
        manager._fan_curve_engine = Mock()
        manager._fan_curve_engine.get_state.return_value = {
            "cpu": {"active": False}, "gpu": {"active": False}}
        manager._game_mode_active = False
        future = 1000
        manager._native_curve_engine.get_state = Mock(return_value={
            "inventory-hwmon-key:fan1": {
                "active": True, "points": [[30, 40], [80, 100]],
                "sensor_id": "inventory-hwmon-key:temp1", "expires_at": future,
                "owner": "test", "resume_after_suspend": True}})
        manager.prepare_sleep()
        self.assertIn("inventory-hwmon-key:fan1", manager._resume_native_curves)

    def test_resume_restarts_only_curve_with_remaining_lease(self):
        manager = self.manager()
        manager.features = ["native_fan_control"]
        manager.sense_base = None
        manager.driver_base = None
        manager._collect_inventory = Mock(return_value={"generation": "next", "devices": []})
        manager._resume_curves = {}
        manager._resume_native_curves = {
            "expired": {"points": [[30, 40], [80, 100]], "sensor_id": "old",
                        "expires_at": 1, "owner": "old"},
            "inventory-hwmon-key:fan1": {
                "points": [[30, 40], [80, 100]], "sensor_id": "inventory-hwmon-key:temp1",
                "expires_at": archer_daemon.time.time() + 600, "owner": "test"},
        }
        with patch.object(archer_fans.NativeAcerHwmon, "probe", return_value=[self.backend]), \
                patch.object(manager, "start_native_fan_curve",
                             return_value={"success": True}) as start:
            manager.resume_controls()
        start.assert_called_once()
        self.assertEqual(start.call_args.args[:3], (
            "inventory-hwmon-key:fan1", "inventory-hwmon-key:temp1",
            [[30, 40], [80, 100]]))
        self.assertEqual(manager._resume_native_curves, {})


class DbusTests(unittest.TestCase):
    def test_additive_native_curve_request_is_typed_and_owned(self):
        service = archer_dbus.ArcherDBusService.__new__(archer_dbus.ArcherDBusService)
        service.hw = Mock()
        service._authorize = Mock(return_value=True)
        service.hw.start_native_fan_curve.return_value = {"success": True, "data": {}}
        request = {"fan_id": "stable-fan", "sensor_id": "stable-sensor",
                   "points": [[30, 40], [80, 100]], "duration_seconds": 600,
                   "enabled": True, "resume_after_suspend": True}
        result = json.loads(service._do_SetNativeFanCurve(json.dumps(request), sender=":1.42"))
        self.assertTrue(result["success"])
        service.hw.start_native_fan_curve.assert_called_once_with(
            "stable-fan", "stable-sensor", request["points"], 600,
            owner="dbus::1.42", resume_after_suspend=True)
        bad = json.loads(service._do_SetNativeFanCurve(
            json.dumps({**request, "enabled": 1}), sender=":1.42"))
        self.assertFalse(bad["success"])

    def test_client_preserves_typed_native_curve_fields(self):
        client = ArcherClient(connect=False)
        with patch.object(client, "_send_command", return_value={"success": True}) as send:
            client.set_native_fan_curve(
                "fan", "sensor", [[30, 40], [80, 100]], 600,
                enabled=True, resume_after_suspend=True)
        send.assert_called_once_with("set_native_fan_curve", {
            "fan_id": "fan", "sensor_id": "sensor", "points": [[30, 40], [80, 100]],
            "duration_seconds": 600, "enabled": True, "resume_after_suspend": True})


if __name__ == "__main__":
    unittest.main()
