"""Issue #10: LCD override requests, saved preferences and readback stay separate.

Hardware-free: a fake node accepts writes while its readback stays -1, as reported
on the PT516-52S. An ordinary file that echoes its last write cannot reproduce
the bug, so it is used only where a normal 0/1 device is intended.
"""

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gui'))
sys.path.insert(0, str(ROOT / 'tests' / 'helpers'))
import archer_daemon as daemon
from archer_binary import BinaryControl, SysfsIO, parse_binary_readback, preference_value
from archer_settings import SettingsStore
from firmware_node import FirmwareNode

try:
    import dbus
    import archer_dbus
except ImportError:  # The D-Bus CI job covers the transport; unit jobs may lack dbus-python.
    dbus = archer_dbus = None

NODE = '/sys/devices/platform/acer-wmi/predator_sense/lcd_override'


class Fixture:
    def __init__(self, testcase, saved=None, text=None, readback='-1\n', echo=False):
        temp = tempfile.TemporaryDirectory()
        testcase.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        self.path = self.dir / 'settings.json'
        if text is not None:
            self.path.write_text(text)
        elif saved is not None:
            self.path.write_text(json.dumps(saved))
        self.node = FirmwareNode(readback, echo)
        self.restart()

    def restart(self):
        """A new daemon session: fresh store from disk and a fresh control."""
        self.store = SettingsStore(self.path)
        self.control = BinaryControl('lcd_override', lambda: NODE, lambda: self.store, 'LCD override', io=self.node)
        return self.control

    def disk(self):
        return json.loads(self.path.read_text())


def directory_fsync_fails(fd):
    if stat.S_ISDIR(os.fstat(fd).st_mode):
        raise OSError(5, 'Input/output error')
    return original_fsync(fd)


original_fsync = os.fsync


class ReadbackParsingTests(unittest.TestCase):
    def test_strict_parser(self):
        self.assertIs(parse_binary_readback('0'), False)
        self.assertIs(parse_binary_readback('1'), True)
        for raw in ('-1', '2', '01', 'true', 'garbage', '', None):
            self.assertIsNone(parse_binary_readback(raw), raw)

    def test_unknown_states_are_distinct_and_whitespace_is_normalized(self):
        cases = (('0\n', False, 'known'), (' 1 \n', True, 'known'), ('-1\n', None, 'unrecognized'),
                 ('2\n', None, 'invalid'), ('on\n', None, 'invalid'), ('', None, 'invalid'),
                 (OSError(19, 'No such device'), None, 'unreadable'))
        for readback, observed, status in cases:
            fixture = Fixture(self, readback=readback)
            state = fixture.control.snapshot()
            self.assertEqual((state['observed'], state['readback_status']), (observed, status), readback)
            self.assertTrue(state['available'])
        fixture.node.present = False
        state = fixture.control.snapshot()
        self.assertEqual((state['available'], state['readback_status'], state['effective']), (False, 'unavailable', None))
        unreadable = Fixture(self, readback=OSError(19, 'No such device')).control.snapshot()
        self.assertEqual(unreadable['readback_error'], 'No such device')

    def test_preference_values(self):
        for value, expected in ((True, True), (False, False), (1, True), (0, False)):
            self.assertIs(preference_value(value), expected)
        for value in (-1, 2, '1', '0', 'false', 1.0, 0.0, [1], {'on': True}, None):
            self.assertIsNone(preference_value(value), value)


class RequestTests(unittest.TestCase):
    def test_enable_with_unknown_readback_stays_on_requested(self):
        fixture = Fixture(self)
        result = fixture.control.apply(True)
        state = result['data']['lcd_override_state']
        self.assertTrue(result['success'])
        self.assertEqual(fixture.node.writes, ['1'])
        self.assertEqual((state['effective'], state['source'], state['observed'], state['configured']),
                         (True, 'last_write', None, True))
        self.assertEqual(state['readback_status'], 'unrecognized')
        self.assertEqual(state['raw_readback'], '-1')
        operation = state['last_operation']
        self.assertEqual((operation['write_status'], operation['save_status'], operation['verification']),
                         ('accepted', 'saved', 'unverified'))
        self.assertEqual(result['data']['lcd_override'], True)
        self.assertIs(fixture.disk()['lcd_override'], True)

    def test_next_toggle_sends_zero_and_saves_off(self):
        fixture = Fixture(self)
        fixture.control.apply(True)
        result = fixture.control.apply(False)
        state = result['data']['lcd_override_state']
        self.assertEqual(fixture.node.writes, ['1', '0'])
        self.assertEqual((state['effective'], state['source'], state['configured']), (False, 'last_write', False))
        self.assertIs(fixture.disk()['lcd_override'], False)
        self.assertTrue(result['success'])
        self.assertEqual(json.loads(json.dumps(state)), state)

    def test_reading_never_writes_or_invents_defaults(self):
        fixture = Fixture(self, saved={'thermal_profile': 'quiet'})
        before = fixture.path.read_bytes()
        for _ in range(5):
            state = fixture.control.snapshot()
        self.assertEqual(fixture.node.writes, [])
        self.assertEqual(fixture.path.read_bytes(), before)
        self.assertIsNone(fixture.store.get('lcd_override'))
        self.assertEqual((state['effective'], state['source'], state['configured_status']), (None, 'unknown', 'absent'))

    def test_normal_devices_report_hardware_and_verify(self):
        fixture = Fixture(self, readback='1\n', echo=True)
        self.assertEqual(fixture.control.snapshot()['source'], 'hardware')
        result = fixture.control.apply(False)
        state = result['data']['lcd_override_state']
        self.assertTrue(result['success'])
        self.assertEqual((state['effective'], state['source'], state['observed']), (False, 'hardware', False))
        self.assertEqual(state['last_operation']['verification'], 'matches')

    def test_confirmed_mismatch_keeps_request_and_observation_separate(self):
        fixture = Fixture(self, readback='1\n')
        result = fixture.control.apply(False)
        state = result['data']['lcd_override_state']
        self.assertFalse(result['success'])
        self.assertIn('reports On', result['error'])
        self.assertEqual((state['observed'], state['requested'], state['configured']), (True, False, False))
        self.assertEqual((state['effective'], state['source']), (True, 'hardware'))
        self.assertEqual(state['last_operation']['verification'], 'mismatch')
        self.assertEqual(fixture.node.writes, ['0'])
        self.assertIs(fixture.disk()['lcd_override'], False)

    def test_external_change_is_shown_without_rewriting_preference(self):
        fixture = Fixture(self, readback='0\n', echo=True)
        fixture.control.apply(True)
        before = fixture.path.read_bytes()
        fixture.node.readback = '0\n'
        state = fixture.control.snapshot()
        self.assertEqual((state['effective'], state['source'], state['requested'], state['configured']),
                         (False, 'hardware', True, True))
        self.assertEqual(fixture.path.read_bytes(), before)
        self.assertEqual(fixture.node.writes, ['1'])

    def test_invalid_requests_are_rejected_before_mutation(self):
        fixture = Fixture(self, saved={'lcd_override': True})
        before = fixture.path.read_bytes()
        for value in (-1, 0, 1, 2, '0', 'false', None, 1.0, [True], {'enabled': True}):
            result = fixture.control.apply(value)
            self.assertFalse(result['success'])
            self.assertIn('Invalid request', result['error'])
            self.assertIsNone(result['data']['operation'])
        self.assertEqual(fixture.node.writes, [])
        self.assertEqual(fixture.path.read_bytes(), before)
        self.assertIsNone(fixture.control.snapshot()['last_operation'])

    def test_missing_node_reports_unavailable_and_is_never_created(self):
        fixture = Fixture(self, saved={'lcd_override': True})
        fixture.node.present = False
        state = fixture.control.snapshot()
        self.assertEqual((state['available'], state['effective'], state['configured']), (False, None, True))
        result = fixture.control.apply(False)
        self.assertFalse(result['success'])
        self.assertEqual(result['data']['operation']['write_status'], 'not_attempted')
        self.assertEqual(fixture.node.writes, [])
        self.assertIs(fixture.disk()['lcd_override'], True)
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / 'lcd_override'
            control = BinaryControl('lcd_override', lambda: str(missing), lambda: fixture.store, 'LCD override')
            self.assertFalse(control.apply(True)['success'])
            self.assertFalse(missing.exists())
            self.assertIsNotNone(SysfsIO().write(str(missing), '1'))
            self.assertFalse(missing.exists())

    def test_write_error_keeps_saved_preference(self):
        fixture = Fixture(self, saved={'lcd_override': True})
        fixture.node.write_error = 'No such device'
        result = fixture.control.apply(False)
        operation = result['data']['operation']
        self.assertFalse(result['success'])
        self.assertIn('No such device', result['error'])
        self.assertEqual((operation['write_status'], operation['save_status']), ('failed', 'not_attempted'))
        self.assertIs(fixture.store.get('lcd_override'), True)
        self.assertIs(fixture.disk()['lcd_override'], True)
        self.assertIsNone(result['data']['lcd_override_state']['requested'])

    def test_save_failure_discloses_accepted_request_and_old_preference(self):
        fixture = Fixture(self)
        fixture.control.apply(True)
        with patch('archer_settings.os.replace', side_effect=OSError(28, 'No space left on device')):
            result = fixture.control.apply(False)
        state = result['data']['lcd_override_state']
        self.assertFalse(result['success'])
        self.assertIn('No space left', result['error'])
        self.assertEqual(fixture.node.writes, ['1', '0'])
        self.assertEqual((state['effective'], state['source'], state['configured']), (False, 'last_write', True))
        self.assertEqual(state['last_operation']['save_status'], 'failed')
        self.assertIs(fixture.store.get('lcd_override'), True)
        self.assertIs(fixture.disk()['lcd_override'], True)
        self.assertEqual([p.name for p in fixture.dir.iterdir()], ['settings.json'])
        retry = fixture.control.apply(False)
        self.assertTrue(retry['success'])
        self.assertEqual(fixture.node.writes, ['1', '0', '0'])
        self.assertIs(fixture.disk()['lcd_override'], False)

    def test_durability_failure_after_replace_keeps_memory_with_file(self):
        fixture = Fixture(self)
        with patch('archer_settings.os.fsync', side_effect=directory_fsync_fails):
            result = fixture.control.apply(False)
        state = result['data']['lcd_override_state']
        self.assertFalse(result['success'])
        self.assertEqual(state['last_operation']['save_status'], 'unsynced')
        self.assertIs(fixture.store.get('lcd_override'), False)
        self.assertIs(fixture.disk()['lcd_override'], False)
        self.assertEqual(state['configured'], False)
        self.assertTrue(fixture.control.apply(False)['success'])
        self.assertEqual(fixture.node.writes, ['0', '0'])

    def test_repeated_explicit_off_writes_again(self):
        fixture = Fixture(self, saved={'lcd_override': False})
        for _ in range(3):
            self.assertTrue(fixture.control.apply(False)['success'])
        self.assertEqual(fixture.node.writes, ['0', '0', '0'])

    def test_unrelated_settings_are_preserved(self):
        saved = {'schema_version': 2, 'thermal_profile': 'quiet', 'button_colours': {'quiet': '#112233'},
                 'fan_curve_cpu': {'enabled': False, 'points': [[40, 30], [80, 100]]},
                 'per_zone_mode': {'zone1': '112233', 'zone2': '445566', 'zone3': '778899', 'zone4': 'aabbcc', 'brightness': 40},
                 'last_keyboard_mode': 'per_zone', 'extension_key': {'kept': [1, 2]}}
        fixture = Fixture(self, saved=saved)
        fixture.control.apply(True)
        fixture.control.apply(False)
        self.assertEqual(fixture.disk(), {**saved, 'lcd_override': False})

    def test_concurrent_saves_keep_every_key(self):
        fixture = Fixture(self)

        def work(index):
            if index % 5 == 0:
                fixture.control.apply(index % 10 == 0)
            else:
                fixture.store.set(f'extension_{index}', index)
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(work, range(60)))
        disk = fixture.disk()
        self.assertEqual(disk, fixture.store.data)
        self.assertEqual(len([key for key in disk if key.startswith('extension_')]), 48)
        self.assertIn(disk['lcd_override'], (True, False))
        self.assertEqual([p.name for p in fixture.dir.iterdir()], ['settings.json'])


class StartupTests(unittest.TestCase):
    def test_legacy_boolean_and_numeric_preferences_replay_once(self):
        for saved, written in ((True, '1'), (False, '0'), (1, '1'), (0, '0')):
            fixture = Fixture(self, saved={'lcd_override': saved, 'thermal_profile': 'quiet'})
            before = fixture.path.read_bytes()
            self.assertTrue(fixture.control.restore(True))
            state = fixture.control.snapshot()
            self.assertEqual(fixture.node.writes, [written], saved)
            self.assertEqual((state['effective'], state['source'], state['requested_origin']),
                             (written == '1', 'last_write', 'startup'))
            self.assertEqual(state['restore']['status'], 'accepted')
            self.assertEqual(state['restore']['verification'], 'unverified')
            self.assertEqual(state['last_operation']['save_status'], 'not_attempted')
            self.assertEqual(fixture.path.read_bytes(), before)

    def test_restart_after_disable_replays_zero(self):
        fixture = Fixture(self, saved={'lcd_override': 1})
        fixture.control.restore(True)
        fixture.control.apply(False)
        fixture.restart().restore(True)
        self.assertEqual(fixture.node.writes, ['1', '0', '0'])
        self.assertEqual(fixture.control.snapshot()['effective'], False)

    def test_invalid_saved_values_never_enable(self):
        for raw in (-1, 2, '1', '0', 'false', 'true', [1], {'on': True}, 1.0, 0.0):
            fixture = Fixture(self, saved={'lcd_override': raw, 'thermal_profile': 'quiet'})
            self.assertFalse(fixture.control.restore(True))
            state = fixture.control.snapshot()
            self.assertEqual(fixture.node.writes, [], raw)
            self.assertEqual((state['configured'], state['configured_status'], state['effective']), (None, 'invalid', None))
            self.assertEqual(state['restore']['reason'], 'invalid_preference')
            self.assertEqual(fixture.store.get('thermal_profile'), 'quiet')
            fixture.store.set('thermal_profile', 'balanced')
            self.assertEqual(fixture.disk()['lcd_override'], raw)
            fixture.control.apply(False)
            self.assertIs(fixture.disk()['lcd_override'], False)
            self.assertEqual(fixture.control.snapshot()['configured_status'], 'valid')

    def test_null_or_absent_preference_means_no_startup_write(self):
        for saved in ({}, {'lcd_override': None}):
            fixture = Fixture(self, saved=saved)
            self.assertFalse(fixture.control.restore(True))
            state = fixture.control.snapshot()
            self.assertEqual(fixture.node.writes, [])
            self.assertEqual((state['effective'], state['source'], state['configured_status']), (None, 'unknown', 'absent'))
            self.assertEqual(state['restore']['reason'], 'no_preference')

    def test_unavailable_or_failed_restore_keeps_preference_only(self):
        fixture = Fixture(self, saved={'lcd_override': True})
        self.assertFalse(fixture.control.restore(False))
        self.assertEqual(fixture.control.snapshot()['restore']['reason'], 'unavailable')
        fixture.node.write_error = 'Input/output error'
        self.assertFalse(fixture.restart().restore(True))
        state = fixture.control.snapshot()
        self.assertEqual((state['effective'], state['source'], state['configured']), (True, 'saved_preference', True))
        self.assertEqual(state['restore']['status'], 'failed')
        self.assertIn('Input/output error', state['restore']['error'])
        self.assertIs(fixture.disk()['lcd_override'], True)

    def test_unreadable_settings_are_kept_and_never_replayed(self):
        for text in ('[]', '"lcd_override"', 'null', '3', '{"lcd_override": true', '{"schema_version": 9, "lcd_override": true}'):
            fixture = Fixture(self, text=text)
            self.assertTrue(fixture.store.unreadable)
            self.assertFalse(fixture.control.restore(True))
            state = fixture.control.snapshot()
            self.assertEqual(fixture.node.writes, [], text)
            self.assertEqual((state['configured'], state['configured_status']), (None, 'unreadable'))
            self.assertEqual(state['restore']['reason'], 'settings_unreadable')
            self.assertEqual(fixture.path.read_text(), text)
            self.assertTrue(fixture.control.apply(False)['success'])
            copies = [p for p in fixture.dir.iterdir() if p.name.startswith('settings.json.unreadable-')]
            self.assertEqual(len(copies), 1)
            self.assertEqual(copies[0].read_text(), text)
            self.assertEqual(fixture.disk(), {'schema_version': 2, 'lcd_override': False})
            self.assertFalse(fixture.store.unreadable)

    def test_session_evidence_is_invalidated_by_resume_reload_and_removal(self):
        fixture = Fixture(self, saved={'lcd_override': False})
        fixture.control.apply(True)
        fixture.control.note_resume()
        state = fixture.control.snapshot()
        self.assertEqual((state['requested'], state['source'], state['effective'], state['evidence_reset']),
                         (None, 'saved_preference', True, 'resume'))
        self.assertNotEqual(state['last_operation']['verification'], 'matches')
        fixture.control.apply(False)
        fixture.node.generation += 1
        state = fixture.control.snapshot()
        self.assertEqual((state['requested'], state['source'], state['evidence_reset']),
                         (None, 'saved_preference', 'interface_changed'))
        fixture.control.apply(True)
        fixture.node.present = False
        self.assertEqual(fixture.control.snapshot()['source'], 'unknown')
        fixture.node.present = True
        self.assertEqual(fixture.control.snapshot()['source'], 'saved_preference')
        self.assertEqual(fixture.node.writes, ['1', '0', '1'])
        self.assertEqual(fixture.restart().snapshot()['requested'], None)


class HardwareManagerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.sense = self.root / 'predator_sense'
        self.sense.mkdir()
        self.settings = self.root / 'settings.json'

    def manager(self, saved=None, features=('lcd_override', 'boot_animation_sound', 'backlight_timeout')):
        if saved is not None:
            self.settings.write_text(json.dumps(saved))
        hw = daemon.HardwareManager.__new__(daemon.HardwareManager)
        hw.settings = SettingsStore(self.settings)
        hw.sense_base = str(self.sense)
        hw.driver_base = None
        hw.features = list(features)
        hw.ene_ready = False
        hw._game_mode_active = False
        hw._game_mode_saved = {}
        self.nodes = {}
        for key in daemon.BINARY_CONTROLS:
            (self.sense / key).write_text('-1\n')
            self.nodes[key] = FirmwareNode()
            hw._binary_control(key)._io = self.nodes[key]
        return hw

    def test_startup_replays_each_valid_binary_preference_through_one_owner(self):
        hw = self.manager({'lcd_override': 1, 'boot_animation_sound': False, 'backlight_timeout': 'yes'})
        with patch.object(hw, '_recover_game_mode'):
            hw._restore_saved_settings()
        self.assertEqual({key: node.writes for key, node in self.nodes.items()},
                         {'lcd_override': ['1'], 'boot_animation_sound': ['0'], 'backlight_timeout': []})
        self.assertIs(hw.get_lcd_override(), True)
        self.assertIs(hw.get_boot_animation_sound(), False)
        self.assertIsNone(hw.get_backlight_timeout())
        self.assertEqual(hw.binary_state('backlight_timeout')['configured_status'], 'invalid')

    def test_user_requests_use_each_control_and_persist(self):
        hw = self.manager({})
        for key in daemon.BINARY_CONTROLS:
            result = hw.apply_binary(key, False)
            self.assertTrue(result['success'], key)
            self.assertEqual(self.nodes[key].writes, ['0'])
            self.assertIs(hw.settings.get(key), False)
            self.assertEqual(result['data'][key], False)

    def test_settings_payload_keeps_scalar_compatible_with_state(self):
        hw = self.manager({'lcd_override': True})
        hw.apply_binary('lcd_override', True)
        state = hw.binary_state('lcd_override')
        self.assertEqual(json.loads(json.dumps(state)), state)
        self.assertIs(hw.get_lcd_override(), state['effective'])
        self.assertIs(state['effective'], True)
        hw.note_resume()
        self.assertEqual(hw.binary_state('lcd_override')['source'], 'saved_preference')

    def test_actual_settings_payload_contains_binary_states(self):
        conf = self.root / 'missing.conf'
        with patch.object(daemon, 'DRIVER_BASE_PATHS', []), patch.object(daemon, 'archer_ene', None), \
                patch.object(daemon, 'run_cmd', return_value=''), patch.object(daemon, 'AUDIO_CONF', str(conf)):
            hw = daemon.HardwareManager(settings_store=SettingsStore(self.settings), restore_settings=False)
            payload = hw.get_all_settings()
        for key in daemon.BINARY_CONTROLS:
            self.assertIsNone(payload[key])
            self.assertEqual(payload[f'{key}_state']['source'], 'unknown')
            self.assertFalse(payload[f'{key}_state']['available'])
        self.assertEqual(json.loads(json.dumps(payload['lcd_override_state'])), payload['lcd_override_state'])

    def test_battery_calibration_unknown_status_can_still_be_stopped(self):
        hw = self.manager({}, features=('battery_calibration',))
        node = self.sense / 'battery_calibration'
        node.write_text('-1\n')
        with patch.object(daemon, 'BATTERY_CALIBRATION_PATH', str(self.root / 'absent')), \
                patch.object(hw, 'get_power_source', return_value=True):
            self.assertIsNone(hw.get_battery_calibration())
            self.assertFalse(hw.set_battery_calibration(True))
            self.assertEqual(node.read_text(), '-1\n')
            self.assertTrue(hw.set_battery_calibration(False))
            self.assertEqual(node.read_text(), '0')
            self.assertIs(hw.get_battery_calibration(), False)


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('archer_diagnose', ROOT / 'scripts' / 'archer-diagnose.py')
        self.diagnose = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.diagnose)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_report_includes_only_binary_preferences(self):
        settings = self.root / 'settings.json'
        settings.write_text(json.dumps({'lcd_override': 1, 'boot_animation_sound': [1] * 50, 'thermal_profile': 'quiet',
                                        'per_zone_mode': {'zone1': 'ffffff'}}))
        report = self.diagnose.collect(self.root, commands=False, settings=settings)
        self.assertEqual(report['archer'], {'saved_preferences': {
            'lcd_override': 1, 'boot_animation_sound': '<invalid list>', 'backlight_timeout': '<absent>'}})
        settings.write_text('[]')
        self.assertEqual(self.diagnose.collect(self.root, commands=False, settings=settings)['archer']['saved_preferences'],
                         '<unreadable: not an object>')
        self.assertEqual(self.diagnose.saved_preferences(self.root / 'missing.json'), '<absent>')

    def test_daemon_metadata_is_bounded_and_nonfatal(self):
        state = {'effective': True, 'source': 'last_write'}
        payload = json.dumps({'success': True, 'data': {'lcd_override': True, 'lcd_override_state': state,
                                                        'saved_settings': {'thermal_profile': 'quiet'}}})
        with patch.object(self.diagnose, 'command', return_value=json.dumps({'type': 's', 'data': [payload]})) as run:
            result = self.diagnose.daemon_states()
        self.assertEqual(run.call_args.args[0][:3], ['busctl', '--system', '--json=short'])
        self.assertEqual(result['lcd_override'], {'value': True, 'state': state})
        self.assertNotIn('saved_settings', json.dumps(result))
        with patch.object(self.diagnose, 'command', return_value='<unavailable>'):
            self.assertEqual(self.diagnose.daemon_states(), '<unavailable>')


@unittest.skipIf(archer_dbus is None, 'dbus-python and PyGObject are required')
class DBusMethodTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture(self, saved={'lcd_override': True})
        hw = daemon.HardwareManager.__new__(daemon.HardwareManager)
        hw.settings = self.fixture.store
        hw.sense_base = '/sys/fake'
        hw.__dict__['_binary_controls'] = {'lcd_override': self.fixture.control}
        self.service = archer_dbus.ArcherDBusService.__new__(archer_dbus.ArcherDBusService)
        self.service.hw = hw
        self.service._stopping = False
        self.service._bus = None

    def call(self, value, authorized=True):
        with patch.object(archer_dbus, '_check_polkit', return_value=authorized), \
                patch.object(archer_dbus.GLib, 'idle_add') as idle:
            response = json.loads(self.service._do_SetLcdOverride(value, sender=':1.7'))
        return response, idle

    def test_wrapped_boolean_is_accepted_and_serialized_as_real_json_types(self):
        response, idle = self.call(dbus.Boolean(False))
        state = response['data']['lcd_override_state']
        self.assertIs(response['success'], True)
        self.assertIs(state['effective'], False)
        self.assertIsNone(state['observed'])
        self.assertEqual(self.fixture.node.writes, ['0'])
        self.assertEqual(idle.call_args.args[1], 'lcd_override')
        self.assertEqual(json.loads(idle.call_args.args[2]), state)

    def test_denial_touches_neither_sysfs_nor_settings(self):
        before = self.fixture.path.read_bytes()
        response, idle = self.call(dbus.Boolean(False), authorized=False)
        self.assertEqual(response, {'success': False, 'error': 'Authorization denied'})
        self.assertEqual(self.fixture.node.writes, [])
        self.assertEqual(self.fixture.path.read_bytes(), before)
        idle.assert_not_called()

    def test_non_boolean_arguments_are_rejected(self):
        for value in (1, 0, 'false', None):
            response, _ = self.call(value)
            self.assertFalse(response['success'])
            self.assertIn('Invalid request', response['error'])
        self.assertEqual(self.fixture.node.writes, [])


if __name__ == '__main__':
    unittest.main()
