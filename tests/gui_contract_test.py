"""Pure contract tests for UI data and the daemon's supporting read APIs."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'gui'))

from archer.binary import TEXTS, describe, describe_operation, normalize_binary_state, response_state
from archer.client import ArcherClient, normalize_settings
from archer.autostart import Autostart, DESKTOP_FILE
from archer.preferences import Preferences
from archer_daemon import HardwareManager, SettingsStore
from archer_runtime import CommandResult


class ContractTests(unittest.TestCase):
    def test_autostart_default_override_and_upgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict('os.environ', XDG_CONFIG_HOME=str(root / 'user'), XDG_CONFIG_DIRS=str(root / 'system')):
                startup = Autostart()
                self.assertFalse(startup.is_enabled())
                system = startup.system_paths[0]
                system.parent.mkdir(parents=True)
                template = Path(__file__).resolve().parents[1] / 'gui' / DESKTOP_FILE
                system.write_text(template.read_text())
                self.assertTrue(startup.is_enabled())
                startup.set_enabled(False)
                self.assertFalse(Autostart().is_enabled())
                system.write_text(template.read_text())  # Installer upgrade.
                self.assertFalse(Autostart().is_enabled())
                startup.set_enabled(True)
                self.assertTrue(Autostart().is_enabled())
                self.assertIn('TryExec=/opt/archer/archer_gui.py', startup.path.read_text())
                self.assertEqual(system.read_text(), template.read_text())

    def test_autostart_failed_write_preserves_disabled_choice(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict('os.environ', XDG_CONFIG_HOME=directory, XDG_CONFIG_DIRS=directory):
                startup = Autostart()
                startup.set_enabled(False)
                with patch.object(Path, 'replace', side_effect=PermissionError('read-only')):
                    with self.assertRaises(PermissionError):
                        startup.set_enabled(True)
                self.assertFalse(startup.is_enabled())
                self.assertEqual(list(startup.path.parent.iterdir()), [startup.path])

    def test_autostart_reads_desktop_settings_override(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict('os.environ', XDG_CONFIG_HOME=directory, XDG_CONFIG_DIRS=directory):
                startup = Autostart()
                startup.path.parent.mkdir()
                startup.path.write_text('[Desktop Entry]\nHidden=true\n')
                self.assertFalse(startup.is_enabled())
                startup.path.write_text('[Desktop Entry]\nX-GNOME-Autostart-enabled=false\n')
                self.assertFalse(startup.is_enabled())
                startup.set_enabled(True)
                self.assertTrue(startup.is_enabled())

    def test_normalizes_legacy_and_current_payloads(self):
        for display in ('hybrid', {'mode': 'hybrid'}):
            data = normalize_settings({'display_mode': display, 'game_mode': {'active': False},
                                       'daemon_version': 'test', 'saved_settings': {'audio_enhancement': {'noise_suppression': True}}})
            self.assertEqual(data['display_mode']['mode'], 'hybrid')
            self.assertFalse(data['game_mode'])
            self.assertTrue(data['audio_enhancement']['noise_suppression'])
            self.assertEqual(data['system_info']['daemon_version'], 'test')
        self.assertEqual(normalize_settings(None), {})

    def test_binary_state_contract_preserves_unknown_and_rejects_malformed_data(self):
        state = {'effective': True, 'source': 'last_write', 'configured': True, 'configured_status': 'valid',
                 'requested': True, 'requested_origin': 'user', 'observed': None, 'readback_status': 'unrecognized',
                 'raw_readback': '-1', 'readback_error': None, 'available': True, 'evidence_reset': None,
                 'last_operation': {'origin': 'user', 'target': True, 'write_status': 'accepted', 'save_status': 'saved',
                                    'verification': 'unverified', 'error': None}, 'restore': None}
        data = normalize_settings({'lcd_override': True, 'lcd_override_state': state, 'saved_settings': {}})
        lcd = data['lcd_override_state']
        self.assertIsNone(lcd['compatibility'])
        self.assertEqual((lcd['effective'], lcd['source'], lcd['observed']), (True, 'last_write', None))
        self.assertEqual(lcd['last_operation']['verification'], 'unverified')
        # Older service: no state object; only a valid saved value survives, as a preference.
        legacy = normalize_settings({'lcd_override': False, 'saved_settings': {'lcd_override': True}})
        self.assertEqual(legacy['lcd_override_state']['compatibility'], 'legacy')
        self.assertIsNone(legacy['lcd_override_state']['effective'])
        self.assertIs(legacy['lcd_override_state']['configured'], True)
        for scalar in ({'enabled': True}, 'true', 1, [True]):
            self.assertIsNone(normalize_settings({'lcd_override': scalar})['lcd_override'], scalar)
        broken = (
            [], 'state', {**state, 'effective': 'true'}, {**state, 'source': 'guess'}, {**state, 'available': 1},
            {**state, 'source': 'hardware'}, {**state, 'source': 'unknown'}, {**state, 'effective': None},
            {**state, 'requested': False}, {**state, 'observed': -1}, {**state, 'readback_status': None},
        )
        for raw in broken:
            normalized = normalize_binary_state(raw, saved='on')
            self.assertEqual(normalized['compatibility'], 'malformed', raw)
            self.assertIsNone(normalized['effective'])
            self.assertIsNone(normalized['configured'])
        damaged = normalize_binary_state({**state, 'last_operation': {'write_status': 'accepted'}, 'restore': [],
                                          'raw_readback': {'x': 1}, 'configured_status': 7})
        self.assertIsNone(damaged['compatibility'])
        self.assertIsNone(damaged['last_operation'])
        self.assertIsNone(damaged['restore'])
        self.assertIsNone(damaged['raw_readback'])
        self.assertEqual(damaged['configured_status'], 'valid')
        absent = normalize_settings({'features': []})
        self.assertEqual(absent['lcd_override_state']['compatibility'], 'legacy')
        self.assertIsNone(absent['lcd_override'])

    def test_binary_presentation_never_claims_unknown_as_off(self):
        texts = TEXTS['lcd_override']
        base = {'available': True, 'configured_status': 'absent', 'configured': None, 'requested': None,
                'observed': None, 'readback_status': 'unrecognized', 'raw_readback': '-1', 'evidence_reset': None,
                'last_operation': None, 'restore': None}

        def view(**changes):
            state = normalize_binary_state({**base, **changes})
            self.assertIsNone(state['compatibility'], changes)
            return describe(state, texts)
        unknown = view(effective=None, source='unknown')
        self.assertEqual((unknown['mode'], unknown['value']), ('choices', None))
        self.assertEqual(unknown['status'], 'Current state unknown. This device does not report its LCD override state. '
                                            'Choose On or Off to send an explicit request.')
        requested = view(effective=True, source='last_write', requested=True, configured=True, configured_status='valid')
        self.assertEqual((requested['mode'], requested['value'], requested['saved']), ('switch', True, None))
        self.assertEqual(requested['status'], 'On requested. This device does not report its LCD override state.')
        failed_save = view(effective=False, source='last_write', requested=False, configured=True, configured_status='valid',
                           last_operation={'origin': 'user', 'target': False, 'write_status': 'accepted', 'save_status': 'failed',
                                           'verification': 'unverified', 'error': 'disk full'})
        self.assertEqual((failed_save['value'], failed_save['saved']), (False, 'On'))
        saved_only = view(effective=True, source='saved_preference', configured=True, configured_status='valid',
                          restore={'status': 'failed', 'reason': None, 'target': True, 'verification': 'not_checked',
                                   'error': 'Input/output error'})
        self.assertEqual((saved_only['mode'], saved_only['saved']), ('choices', 'On'))
        self.assertIn('Applying the saved On setting at startup failed: Input/output error.', saved_only['status'])
        mismatch = view(effective=True, source='hardware', observed=True, readback_status='known', raw_readback='1',
                        requested=False, configured=False, configured_status='valid')
        self.assertEqual(mismatch['status'], 'The driver reports a different state from your request: On reported, Off requested.')
        self.assertEqual(mismatch['saved'], 'Off')
        missing = view(effective=None, source='unknown', available=False, readback_status='unavailable',
                       configured=True, configured_status='valid')
        self.assertEqual((missing['status'], missing['actions'], missing['saved']),
                         ('LCD override control is unavailable.', False, 'On'))
        invalid = view(effective=None, source='unknown', configured_status='invalid')
        self.assertIn('invalid and will not be applied', invalid['saved'])
        legacy = describe(normalize_binary_state(None, saved=True), texts)
        self.assertEqual((legacy['mode'], legacy['saved']), ('choices', 'On'))
        self.assertIn('This Archer service version does not report', legacy['status'])

    def test_binary_operation_feedback_distinguishes_outcomes(self):
        texts = TEXTS['lcd_override']
        state = normalize_binary_state({'available': True, 'effective': False, 'source': 'last_write', 'configured': True,
                                        'configured_status': 'valid', 'requested': False, 'observed': None,
                                        'readback_status': 'unrecognized'})

        def message(write, save='not_attempted', verification='not_checked', error=None):
            operation = {'origin': 'user', 'target': False, 'write_status': write, 'save_status': save,
                         'verification': verification, 'error': error}
            return describe_operation(operation, state, texts)
        self.assertEqual(message('accepted', 'saved', 'unverified'), ('Preference saved. Hardware state could not be verified.', False))
        self.assertEqual(message('accepted', 'saved', 'matches'), ('Saved. Hardware reports Off.', False))
        self.assertEqual(message('accepted', 'failed', 'unverified', 'disk full'),
                         ('Off request accepted, but saving failed. The startup preference is still On. Details: disk full', True))
        self.assertTrue(message('accepted', 'unsynced', 'unverified')[0].startswith('Off request accepted and saved, but'))
        self.assertTrue(message('failed', error='Input/output error')[0].startswith(
            'Could not send the LCD override request. Your saved preference was not changed.'))
        self.assertEqual(message('accepted', 'saved', 'mismatch')[0],
                         'The driver reports a different state from your request. Off is saved for startup.')
        self.assertIn('unavailable', message('not_attempted')[0])
        self.assertEqual(response_state({'success': False, 'error': 'Authorization denied'}, 'lcd_override'), (None, None))
        self.assertEqual(response_state({'data': {'lcd_override_state': []}}, 'lcd_override'), (None, None))

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
        with patch('archer_daemon.shutil.which', return_value='/usr/bin/fwupdmgr'), patch('archer_daemon.run_command') as run:
            self.assertEqual(hw.get_firmware_info(check=False)['status'], 'not_checked')
            run.assert_not_called()

    def test_firmware_failure_is_not_up_to_date(self):
        hw = HardwareManager.__new__(HardwareManager)
        with patch('archer_daemon.shutil.which', return_value='/usr/bin/fwupdmgr'), patch('archer_daemon.run_command', return_value=CommandResult(1, stderr='Permission denied')):
            info = hw.get_firmware_info()
            self.assertEqual(info['status'], 'error')
            self.assertIn('Permission denied', info['error'])
            self.assertEqual(hw.get_firmware_info(check=False)['status'], 'error')

    def test_firmware_no_updates_and_malformed_output(self):
        hw = HardwareManager.__new__(HardwareManager)
        with patch('archer_daemon.shutil.which', return_value='/usr/bin/fwupdmgr'), patch('archer_daemon.run_command') as run:
            run.return_value = CommandResult(2, '{}')
            self.assertEqual(hw.get_firmware_info()['status'], 'no_devices')
            hw._firmware_checked = -float('inf')
            run.return_value = CommandResult(0, 'invalid')
            self.assertEqual(hw.get_firmware_info()['status'], 'error')

    def test_graphics_restart_state_survives_service_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            store_path = str(Path(directory) / 'settings.json')
            hw = HardwareManager.__new__(HardwareManager)
            hw.settings = SettingsStore(store_path)
            with patch('archer_daemon.read_sysfs', return_value='boot-a'), patch.object(hw, '_system_operation', return_value={'success': True}), patch('archer_daemon._PROBE_CACHE.get_or_compute', return_value='hybrid'):
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
