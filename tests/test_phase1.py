"""Fault and contract checks for the Phase 1 audit; no host hardware writes."""

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gui'))
import archer_battery
import archer_daemon as daemon
import archer_ene
import archer_system
from archer_runtime import CommandResult, run_command
from archer_settings import SettingsStore, validate


def script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


grub = script('archer-grub')
manifest = script('archer-manifest')
install_state = script('archer-install-state')
diagnose = script('archer-diagnose')


class InstallerStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.patch = patch.object(install_state, 'STATE_ROOT', self.root / 'state')
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_configuration_preserves_administrator_edits(self):
        path = self.root / 'tlp.conf'
        install_state.main(['power', 'config', '--', str(path), 'Archer setting'])
        path.write_text('Administrator replacement\n')
        for action in ('config', 'remove-config'):
            with self.assertRaisesRegex(ValueError, '[Aa]dministrator'):
                install_state.main(['power', action, '--', str(path), 'new'] if action == 'config'
                                   else ['power', action, str(path)])
            self.assertEqual(path.read_text(), 'Administrator replacement\n')

    def test_unowned_configuration_is_not_overwritten(self):
        path = self.root / 'gamemode.ini'
        path.write_text('[general]\ndesiredgov=powersave\n')
        with self.assertRaisesRegex(ValueError, 'administrator'):
            install_state.main(['gamemode', 'config', '--', str(path), '[general]\ndesiredgov=performance'])
        self.assertIn('powersave', path.read_text())

    def test_failed_config_write_can_be_retried(self):
        path = self.root / 'config'
        original = install_state.atomic

        def disk_full(target, *args):
            if target == path:
                raise OSError('disk full')
            return original(target, *args)
        with patch.object(install_state, 'atomic', side_effect=disk_full):
            with self.assertRaises(OSError):
                install_state.main(['power', 'config', '--', str(path), 'setting'])
        install_state.main(['power', 'config', '--', str(path), 'setting'])
        install_state.main(['power', 'remove-config', str(path)])
        self.assertFalse(path.exists())

    def test_disabled_audio_cleanup_checks_the_original_content_hash(self):
        path = self.root / 'archer-noise-suppress.conf'
        install_state.main(['audio-enhance', 'config', '--', str(path), '# Installed by Archer Compatibility Suite\nfilter'])
        disabled = path.with_name(path.name + '.disabled')
        path.rename(disabled)
        original = disabled.read_text()
        disabled.write_text(original + 'administrator edit')
        with self.assertRaisesRegex(ValueError, 'Administrator edited'):
            install_state.main(['audio-enhance', 'config', '--', str(disabled), 'replacement'])
        with self.assertRaisesRegex(ValueError, 'administrator-edited'):
            install_state.main(['audio-enhance', 'remove-config', str(disabled)])
        disabled.write_text(original)
        install_state.main(['audio-enhance', 'remove-config', str(disabled)])
        self.assertFalse(disabled.exists())

    def test_services_restore_original_states_in_reverse_order(self):
        commands = []

        def systemctl(*args, **kwargs):
            commands.append(args)
            value = ('enabled' if args[-1] == 'power-profiles-daemon.service' else 'static') if args[0] == 'is-enabled' else 'active'
            return subprocess.CompletedProcess(args, 0, value, '')
        with patch.object(install_state, 'service', side_effect=systemctl):
            install_state.main(['power', 'service', '--', 'power-profiles-daemon.service', 'mask', '--now'])
            install_state.main(['power', 'service', '--', 'fwupd.service', 'start'])
            commands.clear()
            install_state.main(['power', 'restore-services'])
        self.assertEqual(commands, [('unmask', 'fwupd.service'), ('start', 'fwupd.service'),
                                    ('unmask', 'power-profiles-daemon.service'), ('enable', 'power-profiles-daemon.service'),
                                    ('start', 'power-profiles-daemon.service')])

    def test_disappearing_diagnostic_attribute_does_not_abort_report(self):
        group = self.root / 'devices/platform/acer-wmi/nitro_sense'
        group.mkdir(parents=True)
        node = group / 'fan_speed'
        node.write_text('0,0')
        original = Path.stat

        def vanished(path, *args, **kwargs):
            if path == node:
                raise FileNotFoundError('device removed')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'stat', vanished):
            report = diagnose.collect(self.root, commands=False)
        self.assertIn('unavailable', report['sense'][str(group)]['fan_speed'])


class GrubTests(unittest.TestCase):
    def test_literal_assignment_tokens_and_provenance(self):
        for quote in ('"', "'"):
            original = f'GRUB_CMDLINE_LINUX_DEFAULT={quote}quiet preexisting i8042Xnomux{quote} # retained\n'
            edited = grub.transform(original, 'add', 'i8042.nomux preexisting')
            self.assertIn('quiet preexisting i8042Xnomux i8042.nomux', edited)
            self.assertEqual(grub.transform(edited, 'remove', 'i8042.nomux preexisting'), original)
            self.assertEqual(grub.transform(original, 'remove', 'preexisting'), original)

    def test_unsupported_shell_is_never_evaluated(self):
        for text in ('GRUB_CMDLINE_LINUX_DEFAULT="$HOME"\n', 'GRUB_CMDLINE_LINUX_DEFAULT="`id`"\n',
                     'GRUB_CMDLINE_LINUX_DEFAULT="quiet"\nGRUB_CMDLINE_LINUX_DEFAULT="splash"\n'):
            with self.assertRaises(ValueError):
                grub.transform(text, 'add', 'i8042.nomux')

    def test_atomic_owner_mode_backup_and_untrusted_review(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'grub'
            original = 'GRUB_CMDLINE_LINUX_DEFAULT="quiet"\n'
            path.write_text(original)
            path.chmod(0o600)
            self.assertTrue(grub.edit(path, 'add', 'i8042.nomux'))
            self.assertEqual(path.stat().st_uid, os.geteuid())
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)
            self.assertEqual(next(path.parent.glob('grub.archer-backup-*')).read_text(), original)
            path.chmod(0o666)
            with self.assertRaisesRegex(ValueError, 'Administrator review'):
                grub.edit(path, 'check', '')
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)
            with self.assertRaisesRegex(ValueError, 'review marker'):
                grub.edit(path, 'check', '')

    def test_failed_replace_preserves_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'grub'
            path.write_text('GRUB_CMDLINE_LINUX_DEFAULT="quiet"\n')
            before = path.read_bytes()
            with patch.object(grub.os, 'replace', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    grub.edit(path, 'add', 'i8042.nomux')
            self.assertEqual(path.read_bytes(), before)


class SettingsTests(unittest.TestCase):
    def test_invalid_entries_do_not_discard_valid_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            path.write_text(json.dumps({'battery_limiter': False, 'per_zone_mode': {'brightness': 20},
                                        'usb_charging': 256, 'fan_curve_cpu': []}))
            store = SettingsStore(path)
            self.assertFalse(store.get('battery_limiter'))
            self.assertIsNone(store.get('per_zone_mode'))
            self.assertEqual(len(store.warnings), 3)
            path.write_text('[]')
            self.assertEqual(SettingsStore(path).data, {'schema_version': 2})

    def test_serialized_save_and_failed_write_report(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            store = SettingsStore(path)
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda index: store.set(f'extension_{index}', index), range(40)))
            self.assertEqual(json.loads(path.read_text()), store.data)
            before = store.data
            with patch('archer_settings.os.replace', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    store.set('battery_limiter', True)
            self.assertEqual(store.data, before)
            self.assertEqual(json.loads(path.read_text()), before)

    def test_ambiguous_keyboard_integer_requires_explicit_reselection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            effect = {'mode': 4, 'speed': 2, 'brightness': 50, 'direction': 2, 'red': 1, 'green': 2, 'blue': 3}
            path.write_text(json.dumps({'four_zone_mode': effect}))
            store = SettingsStore(path)
            self.assertIsNone(store.get('four_zone_mode'))
            self.assertTrue(store.get('keyboard_migration_required'))
            for backend, expected in (('wmi', 'shifting'), ('ene', 'wave')):
                path.write_text(json.dumps({'four_zone_mode': {**effect, 'backend': backend}}))
                self.assertEqual(SettingsStore(path).get('four_zone_mode')['effect'], expected)

    def test_invalid_requests(self):
        for key, value in (('per_zone_mode', []), ('four_zone_mode', {}), ('audio_enhancement', {'noise_suppression': 'false'}),
                           ('usb_charging', 256), ('usb_charging', True), ('game_mode_saved_state', {'epp': []})):
            with self.assertRaises((TypeError, ValueError)):
                validate(key, value)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.record = patch.object(archer_system, 'recovery_record').start()
        self.addCleanup(patch.stopall)

    def test_nonzero_output_is_failure_and_timeout_is_explicit(self):
        result = run_command([sys.executable, '-c', 'import sys; print("Failed to update configuration"); sys.exit(2)'])
        self.assertFalse(result.success)
        self.assertEqual(result.returncode, 2)
        result = run_command([sys.executable, '-c', 'import time; time.sleep(5)'], timeout=.05)
        self.assertTrue(result.timed_out)

    def test_timeout_terminates_child_processes(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'child-finished'
            child = 'import time, pathlib; time.sleep(.4); pathlib.Path(' + repr(str(marker)) + ').touch()'
            parent = 'import subprocess, sys, time; subprocess.Popen([sys.executable, "-c", ' + repr(child) + ']); time.sleep(5)'
            result = run_command([sys.executable, '-c', parent], timeout=.15)
            self.assertTrue(result.timed_out)
            threading.Event().wait(.5)
            self.assertFalse(marker.exists())

    def test_hid_node_reuse_never_writes_to_the_wrong_device(self):
        def identity(_fd, _request, buffer, _mutable):
            buffer[:] = struct.pack('IHH', 0x18, 0x06CB, 0xCFE4)
        with patch.object(archer_ene, '_resolve', return_value='/dev/hidraw0'), \
                patch.object(archer_ene.os, 'open', return_value=123), \
                patch.object(archer_ene.os, 'close') as close, \
                patch.object(archer_ene.fcntl, 'ioctl', side_effect=identity) as ioctl, \
                patch.object(archer_ene, '_apply') as write:
            with self.assertRaises(archer_ene.EneError):
                archer_ene.set_per_zone('ffffff', 'ffffff', 'ffffff', 'ffffff', 100)
            write.assert_not_called()
            close.assert_called_once_with(123)
            self.assertEqual(ioctl.call_count, 1)

    def test_recovery_orders_reload_before_start_and_checks_errors(self):
        commands = []
        with patch.object(archer_system, 'checked', side_effect=lambda args, *rest: commands.append(args)):
            archer_system.operate('reload')
        self.assertEqual(commands, [['systemctl', 'stop', 'archer-daemon.service'], ['modprobe', '-r', 'linuwu_sense'],
                                    ['modprobe', 'linuwu_sense'], ['systemctl', 'start', 'archer-daemon.service']])
        with patch.object(archer_system, 'checked', side_effect=[None, OSError('unload failed'), None]) as command:
            with self.assertRaisesRegex(OSError, 'unload failed'):
                archer_system.operate('reload')
            self.assertEqual(command.call_args.args[0], ['systemctl', 'start', 'archer-daemon.service'])
            self.record.assert_called_with('', 'failed', 'unload failed')

    def test_graphics_helper_requires_success_and_actual_readback(self):
        with patch.object(archer_system, 'checked', return_value='hybrid'), patch.object(archer_system, 'rebuild'):
            with self.assertRaisesRegex(OSError, 'readback'):
                archer_system.operate('display', 'nvidia')
        with self.assertRaises(ValueError):
            archer_system.operate('display', 'nvidia; touch /tmp/invalid')

    def test_malformed_manifest_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'manifest'
            for content in ('[]', '{}', '{"modules_installed":"gui"}', '{"modules_installed":[{}]}'):
                path.write_text(content)
                with self.assertRaises(ValueError):
                    manifest.load(path)
                self.assertEqual(path.read_text(), content)


class HardwareRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.hw = daemon.HardwareManager.__new__(daemon.HardwareManager)
        self.hw.settings = SettingsStore(self.root / 'settings.json')
        self.hw.sense_base = None
        self.hw.driver_base = None
        self.hw.features = []
        self.hw.ene_ready = False
        self.hw._game_mode_active = False
        self.hw._game_mode_saved = {}

    def node(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
        return path

    def test_cpu_usage_is_an_interval_and_first_sample_is_unknown(self):
        samples = ['cpu 100 10 20 1000 20 10 10 10 50 5\n', 'cpu 190 10 30 1100 20 10 10 10 90 5\n']
        with patch.object(Path, 'read_text', side_effect=samples):
            self.assertIsNone(self.hw._read_cpu_usage())
            self.assertEqual(self.hw._read_cpu_usage(), 50)

    def test_all_external_supplies_and_unknown_power(self):
        self.node('barrel/type', 'Mains')
        self.node('barrel/online', '0')
        self.node('usb/type', 'USB_PD')
        usb = self.node('usb/online', '1')
        with patch.object(daemon, 'POWER_SUPPLY_DIR', str(self.root)):
            self.assertIs(self.hw.get_power_source(), True)
            usb.write_text('0')
            self.assertIs(self.hw.get_power_source(), False)
        with patch.object(daemon, 'POWER_SUPPLY_DIR', str(self.root / 'missing')):
            self.assertIsNone(self.hw.get_power_source())

    def test_independent_battery_calibration_and_ac_guard(self):
        path = self.node('calibration', '0')
        with patch.object(daemon, 'BATTERY_CALIBRATION_PATH', str(path)), \
                patch.object(self.hw, 'get_power_source', return_value=None):
            self.assertFalse(self.hw.get_battery_calibration())
            self.assertFalse(self.hw.set_battery_calibration(True))
            self.assertEqual(path.read_text(), '0')
            path.write_text('-1')
            self.assertIsNone(self.hw._battery_calibration_path())

    def test_device_event_preserves_disabled_charge_policy(self):
        threshold = self.node('class/power_supply/BAT0/charge_control_end_threshold', '80')
        policy = self.root / 'battery.json'
        archer_battery.write_policy(False, policy)
        self.assertTrue(archer_battery.apply(self.root, policy))
        self.assertEqual(threshold.read_text(), '100')
        self.assertTrue(archer_battery.apply(self.root, policy))
        self.assertEqual(threshold.read_text(), '100')

    def test_daemon_battery_restore_uses_canonical_policy_and_preserves_unset_state(self):
        self.hw.features = ['battery_limiter']
        with patch.object(self.hw, 'set_battery_limiter', return_value=True) as write:
            self.hw._restore_saved_settings()
            write.assert_not_called()
            self.hw.settings.set('battery_limiter', True)
            archer_battery.write_policy(False, self.root / 'battery.json')
            self.hw._restore_saved_settings()
            write.assert_called_once_with(False)

    def test_wmi_effect_identity_and_unsupported_mode(self):
        path = self.node('four_zoned_kb/four_zone_mode', '')
        self.hw.driver_base = str(self.root)
        self.assertTrue(self.hw.set_four_zone_mode('wave', 5, 100, 2, 0, 0, 255))
        self.assertTrue(path.read_text().startswith('3,'))
        self.assertFalse(self.hw.set_four_zone_mode('neon-fast', 5, 100, 2, 0, 0, 255))
        self.assertTrue(path.read_text().startswith('3,'))

    def test_audio_capability_and_actual_state(self):
        conf = self.node('noise.conf', '# Archer')
        plugin = self.node('rnnoise.so', '')
        with patch.object(daemon, 'AUDIO_CONF', str(conf)), patch.object(daemon, 'AUDIO_PLUGIN', str(plugin)):
            self.assertTrue(self.hw.audio_available())
            self.assertTrue(self.hw.get_audio_enhancement()['noise_suppression'])
            conf.rename(str(conf) + '.disabled')
            self.assertTrue(self.hw.audio_available())
            self.assertFalse(self.hw.get_audio_enhancement()['noise_suppression'])
            plugin.unlink()
            self.assertFalse(self.hw.audio_available())

    def test_actual_settings_payload_contains_audio_capability_and_state(self):
        conf = self.node('noise.conf', '# Archer')
        plugin = self.node('rnnoise.so', '')
        with patch.object(daemon, 'AUDIO_CONF', str(conf)), patch.object(daemon, 'AUDIO_PLUGIN', str(plugin)), \
                patch.object(daemon, 'DRIVER_BASE_PATHS', []), patch.object(daemon, 'archer_ene', None), \
                patch.object(daemon, 'run_cmd', return_value=''):
            hw = daemon.HardwareManager(settings_store=self.hw.settings, restore_settings=False)
            payload = hw.get_all_settings()
        self.assertIn('audio_enhancement', payload['features'])
        self.assertEqual(payload['audio_enhancement'], {'noise_suppression': True})

    def test_firmware_scans_are_coalesced_and_cached(self):
        entered, release = threading.Event(), threading.Event()

        def check(*args, **kwargs):
            entered.set()
            release.wait(2)
            return CommandResult(2, '{}')
        with patch.object(daemon.shutil, 'which', return_value='/usr/bin/fwupdmgr'), \
                patch.object(daemon, 'run_command', side_effect=check) as command:
            thread = threading.Thread(target=self.hw.get_firmware_info)
            thread.start()
            self.assertTrue(entered.wait(1))
            self.assertEqual(self.hw.get_firmware_info()['status'], 'checking')
            release.set()
            thread.join(2)
            self.assertEqual(self.hw.get_firmware_info()['status'], 'current')
            self.assertEqual(command.call_count, 1)

    def test_game_mode_restores_every_policy_after_same_boot_crash(self):
        first = self.node('cpufreq/policy0/energy_performance_preference', 'balance_performance')
        second = self.node('cpufreq/policy64/energy_performance_preference', 'power')
        for path in (first, second):
            path.with_name('energy_performance_available_preferences').write_text('performance balance_performance power')
        self.hw.get_thermal_profile_choices = Mock(return_value=[])
        with patch.object(daemon, 'CPUFREQ_DIR', str(self.root / 'cpufreq')), \
                patch.object(daemon, 'run_command', return_value=CommandResult(1)), \
                patch.object(daemon, 'read_sysfs', wraps=daemon.read_sysfs) as read:
            # Supply only the boot identity; read policy files normally.
            read.side_effect = lambda path: 'boot-a' if str(path).endswith('/boot_id') else Path(path).read_text().strip()
            self.assertTrue(self.hw.activate_game_mode()['success'])
            self.assertEqual(first.read_text(), 'performance')
            self.assertEqual(second.read_text(), 'performance')
            self.hw.settings = SettingsStore(self.root / 'settings.json')
            self.hw._game_mode_saved = {}
            self.hw._game_mode_active = False
            self.hw._recover_game_mode()
            self.assertEqual(first.read_text(), 'balance_performance')
            self.assertEqual(second.read_text(), 'power')
            self.assertFalse(self.hw.get_game_mode()['active'])

    def test_game_mode_rolls_back_partial_epp_failure(self):
        first = self.node('cpufreq/policy0/energy_performance_preference', 'power')
        second = self.node('cpufreq/policy2/energy_performance_preference', 'balance_power')
        for path in (first, second):
            path.with_name('energy_performance_available_preferences').write_text('performance power balance_power')
        self.hw.get_thermal_profile_choices = Mock(return_value=[])
        original = daemon.write_sysfs

        def write(path, value):
            return False if path == second and value == 'performance' else original(path, value)
        with patch.object(daemon, 'CPUFREQ_DIR', str(self.root / 'cpufreq')), \
                patch.object(daemon, 'run_command', return_value=CommandResult(1)), \
                patch.object(daemon, 'write_sysfs', side_effect=write):
            response = self.hw.activate_game_mode()
        self.assertFalse(response['success'])
        self.assertTrue(response['rollback']['success'])
        self.assertEqual(first.read_text(), 'power')
        self.assertEqual(second.read_text(), 'balance_power')
        self.assertFalse(self.hw.get_game_mode()['active'])

    def test_game_mode_retains_failed_restore_and_discards_old_boot(self):
        saved = {'boot_id': 'previous-boot', 'nvidia': {'GPU-abc': 1}, 'epp': {}}
        self.hw.settings.update({'game_mode_active': True, 'game_mode_saved_state': saved})
        self.hw._game_mode_active = True
        self.hw._game_mode_saved = saved
        self.hw.get_thermal_profile_choices = Mock(return_value=[])
        with patch.object(daemon, 'CPUFREQ_DIR', str(self.root / 'cpufreq')), \
                patch.object(daemon, 'run_command', return_value=CommandResult(1)) as command:
            self.assertFalse(self.hw.deactivate_game_mode()['success'])
            command.assert_called_once_with(['nvidia-smi', '-i', 'GPU-abc', '-pm', '1'], 5)
            self.assertEqual(self.hw.settings.get('game_mode_saved_state'), saved)
            command.reset_mock()
            self.hw._game_mode_active = False
            self.hw._game_mode_saved = {}
            self.hw._recover_game_mode()
            command.assert_not_called()
            self.assertIsNone(self.hw.settings.get('game_mode_saved_state'))

    def test_tlp_ownership_prevents_game_mode_writes(self):
        with patch.object(daemon, 'run_command', return_value=CommandResult(0)), \
                patch.object(daemon, 'write_sysfs') as write:
            self.assertIn('TLP', self.hw.activate_game_mode()['error'])
            write.assert_not_called()


if __name__ == '__main__':
    unittest.main()
