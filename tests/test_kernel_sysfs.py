"""Kernel interface disappearance, native capabilities and provider selection."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'gui'))
import archer_daemon as daemon


class KernelSysfsTests(unittest.TestCase):
    def test_missing_interfaces_are_not_daemon_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'missing'
            self.assertIsNone(daemon.read_sysfs(path))
            self.assertFalse(daemon.write_sysfs(path, '0,0'))
            self.assertFalse(path.exists())
            with patch.object(Path, 'iterdir', side_effect=PermissionError):
                self.assertEqual(daemon.sysfs_children(tmp), [])

    def test_class_profile_fallback_refuses_ambiguous_providers(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(daemon, 'PLATFORM_PROFILE', tmp + '/missing'), \
                patch.object(daemon, 'PLATFORM_PROFILE_CLASS', tmp):
            first = Path(tmp) / 'one'
            first.mkdir()
            (first / 'profile').write_text('balanced')
            (first / 'choices').write_text('balanced performance')
            self.assertEqual(daemon.platform_profile_paths()[0], first / 'profile')
            second = Path(tmp) / 'two'
            second.mkdir()
            (second / 'profile').write_text('balanced')
            (second / 'choices').write_text('balanced')
            self.assertEqual(daemon.platform_profile_paths(), (None, None))

    def test_native_threshold_without_linuwu_and_disappearance(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(daemon, 'POWER_SUPPLY_DIR', tmp), \
                patch.object(daemon, 'BATTERY_HEALTH_PATH', tmp + '/absent'):
            battery = Path(tmp) / 'custom-battery'
            battery.mkdir()
            (battery / 'type').write_text('Battery')
            threshold = battery / 'charge_control_end_threshold'
            threshold.write_text('100')
            hw = daemon.HardwareManager.__new__(daemon.HardwareManager)
            hw.sense_base = None
            self.assertFalse(hw.get_battery_limiter())
            self.assertTrue(hw.set_battery_limiter(True))
            self.assertEqual(threshold.read_text(), '80')
            threshold.unlink()
            self.assertIsNone(hw.get_battery_limiter())
            self.assertFalse(hw.set_battery_limiter(True))
