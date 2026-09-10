"""Pure contract tests for UI data and the daemon's supporting read APIs."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'gui'))

from archer.client import ArcherClient, normalize_settings
from archer.preferences import Preferences
from archer_daemon import HardwareManager, SettingsStore


class ContractTests(unittest.TestCase):
    def test_normalizes_legacy_and_current_payloads(self):
        for display in ('hybrid', {'mode': 'hybrid'}):
            data = normalize_settings({'display_mode': display, 'game_mode': {'active': False},
                                       'daemon_version': 'test', 'saved_settings': {'audio_enhancement': {'noise_suppression': True}}})
            self.assertEqual(data['display_mode']['mode'], 'hybrid')
            self.assertFalse(data['game_mode'])
            self.assertTrue(data['audio_enhancement']['noise_suppression'])
            self.assertEqual(data['system_info']['daemon_version'], 'test')
        self.assertEqual(normalize_settings(None), {})

    def test_preferences_validate_and_persist_without_hardware(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'gui.json'
            path.write_text('{invalid')
            prefs = Preferences(path)
            self.assertEqual(prefs.data['appearance'], 'system')
            prefs.save(appearance='dark', width=820)
            self.assertEqual(Preferences(path).data['appearance'], 'dark')
            path.write_text(json.dumps({'appearance': 'invalid', 'width': -1, 'height': True}))
            prefs = Preferences(path)
            self.assertEqual(prefs.data['appearance'], 'system')
            self.assertEqual(prefs.data['width'], 360)
            self.assertEqual(prefs.data['height'], 760)

    def test_initial_firmware_read_does_not_scan(self):
        hw = HardwareManager.__new__(HardwareManager)
        with patch('archer_daemon.shutil.which', return_value='/usr/bin/fwupdmgr'), patch('archer_daemon.subprocess.run') as run:
            self.assertEqual(hw.get_firmware_info(check=False)['status'], 'not_checked')
            run.assert_not_called()

    def test_firmware_failure_is_not_up_to_date(self):
        hw = HardwareManager.__new__(HardwareManager)
        with patch('archer_daemon.shutil.which', return_value='/usr/bin/fwupdmgr'), patch('archer_daemon.subprocess.run', return_value=SimpleNamespace(returncode=1, stdout='', stderr='Permission denied')):
            info = hw.get_firmware_info()
            self.assertEqual(info['status'], 'error')
            self.assertIn('Permission denied', info['error'])
            self.assertEqual(hw.get_firmware_info(check=False)['status'], 'error')

    def test_firmware_no_updates_and_malformed_output(self):
        hw = HardwareManager.__new__(HardwareManager)
        with patch('archer_daemon.shutil.which', return_value='/usr/bin/fwupdmgr'), patch('archer_daemon.subprocess.run') as run:
            run.return_value = SimpleNamespace(returncode=2, stdout='{}', stderr='')
            self.assertEqual(hw.get_firmware_info()['status'], 'current')
            run.return_value = SimpleNamespace(returncode=0, stdout='invalid', stderr='')
            self.assertEqual(hw.get_firmware_info()['status'], 'error')

    def test_graphics_restart_state_survives_service_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            store_path = str(Path(directory) / 'settings.json')
            hw = HardwareManager.__new__(HardwareManager)
            hw.settings = SettingsStore(store_path)
            with patch('archer_daemon.read_sysfs', return_value='boot-a'), patch('archer_daemon.run_cmd', return_value='success'), patch('archer_daemon._PROBE_CACHE.get_or_compute', return_value='hybrid'):
                self.assertTrue(hw.set_display_mode('nvidia')['success'])
                other = HardwareManager.__new__(HardwareManager)
                other.settings = SettingsStore(store_path)
                self.assertTrue(other.get_display_mode()['reboot_required'])
                self.assertEqual(other.get_display_mode()['mode'], 'nvidia')
            with patch('archer_daemon.read_sysfs', return_value='boot-b'), patch('archer_daemon._PROBE_CACHE.get_or_compute', return_value='nvidia'):
                self.assertFalse(other.get_display_mode()['reboot_required'])

    def test_fan_modes_stop_curves_before_setting_speed(self):
        client = ArcherClient(connect=False)
        with patch.object(client, '_send_command', return_value={'success': True}) as send:
            client.apply_fan_mode(20, 30)
            self.assertEqual([call.args[0] for call in send.call_args_list], ['set_fan_curve', 'set_fan_curve', 'set_fan_speed'])
        with patch.object(client, '_send_command', return_value={'success': False}) as send:
            self.assertFalse(client.apply_fan_mode(20, 30)['success'])
            self.assertEqual(send.call_count, 1)

    def test_numeric_telemetry_preserves_validity(self):
        hw = HardwareManager.__new__(HardwareManager)
        with patch.object(hw, '_read_cpu_temp', return_value=0), patch.object(hw, '_read_gpu_temp', return_value=None), patch.object(hw, '_read_cpu_usage', return_value=0), patch.object(hw, '_read_gpu_usage', return_value=None), patch.object(hw, '_read_fan_rpm', return_value=(0, None)), patch.object(hw, 'get_battery_info', return_value={}), patch.object(hw, 'get_power_source', return_value=True):
            data = hw.get_monitoring_data()
            self.assertEqual(data['gpu_temp'], 0)
            self.assertFalse(data['metric_validity']['gpu_temp'])
            self.assertTrue(data['metric_validity']['fan_rpm_cpu'])


if __name__ == '__main__':
    unittest.main()
