"""Hardware-free detection, control and lifecycle tests; never touch host sysfs."""
import importlib.util
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'gui'))
import archer_daemon as daemon


class HardwareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.module = self.root / 'module/linuwu_sense'
        self.base = self.module / 'drivers/platform:acer-wmi/acer-wmi'
        self.base.mkdir(parents=True)
        self.sense = self.base / 'nitro_sense'
        self.sense.mkdir()
        self.fan = self.sense / 'fan_speed'
        self.fan.write_text('0,0')
        self.product = self.root / 'product_name'
        self.product.write_text('Nitro ANV16S-41')
        for name, value in {
            'DRIVER_MODULE_PATH': str(self.module), 'DRIVER_BASE_PATHS': [str(self.base)],
            'DMI_PRODUCT': str(self.product), 'DMI_VENDOR': str(self.root / 'vendor'),
            'DMI_BOARD': str(self.root / 'board'), 'PLATFORM_PROFILE': str(self.root / 'profile'),
            'PLATFORM_PROFILE_CHOICES': str(self.root / 'choices'),
            'HWMON_DIR': str(self.root / 'hwmon'), 'THERMAL_DIR': str(self.root / 'thermal'),
            'POWER_SUPPLY_DIR': str(self.root / 'power'),
        }.items():
            patcher = patch.object(daemon, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.cmd = patch.object(daemon, 'run_cmd', return_value='').start()
        self.addCleanup(patch.stopall)
        daemon._PROBE_CACHE = daemon._TtlCache(5)
        self.settings = daemon.SettingsStore(str(self.root / 'settings.json'))

    def manager(self, restore=False):
        return daemon.HardwareManager(self.settings, restore_settings=restore)

    def test_anv16s_and_normal_nitro(self):
        for name in ('Nitro ANV16S-41', 'ANV16S-41', 'Nitro AN515-58', 'Nitro ANV15-51'):
            self.product.write_text(name)
            hw = self.manager()
            self.assertEqual(hw.laptop_type, 'nitro')
            self.assertEqual(hw.sense_base, str(self.sense))
            self.assertIn('fan_control', hw.features)
            self.assertEqual(hw.driver_status, 'supported')

    def test_predator_unchanged(self):
        self.sense.rename(self.base / 'predator_sense')
        hw = self.manager()
        self.assertEqual(hw.laptop_type, 'predator')
        self.assertIn('fan_control', hw.features)

    def test_missing_subtree_does_not_spoof_support(self):
        self.fan.unlink()
        self.sense.rmdir()
        hw = self.manager()
        self.assertEqual(hw.laptop_type, 'nitro')
        self.assertIsNone(hw.sense_base)
        self.assertNotIn('fan_control', hw.features)
        self.assertEqual(hw.driver_status, 'sense_interface_missing')
        self.assertFalse(hw.set_fan_speed(50, 50))
        self.assertFalse(hw.start_fan_curve('cpu', [[30, 40], [80, 90]]))

    def test_unsupported_dmi_diagnostic(self):
        self.fan.unlink()
        self.sense.rmdir()
        params = self.module / 'parameters'
        params.mkdir()
        (params / 'sense_quirk').write_text('none')
        self.assertEqual(self.manager().driver_status, 'dmi_unsupported')

    def test_missing_fan_keeps_profiles(self):
        self.fan.unlink()
        (self.root / 'profile').write_text('balanced')
        (self.root / 'choices').write_text('low-power balanced performance')
        hw = self.manager()
        self.assertNotIn('fan_control', hw.features)
        self.assertIn('thermal_profiles', hw.features)
        self.assertEqual(hw.driver_status, 'fan_node_missing')
        self.assertEqual(hw.set_thermal_profile('performance'), (True, None))

    def test_incompatible_or_partial_fan_abi(self):
        for value in ('50', '50,', '50,60,70', '101,50', '-1,50', 'auto', '50,NaN'):
            self.fan.write_text(value)
            hw = self.manager()
            self.assertNotIn('fan_control', hw.features)
            self.assertEqual(hw.driver_status, 'fan_abi_incompatible')
            self.assertEqual(self.fan.read_text(), value)

    def test_permission_diagnostic(self):
        with patch.object(daemon.os, 'access', return_value=False):
            hw = self.manager()
        self.assertEqual(hw.driver_status, 'permission_denied')
        self.assertNotIn('fan_control', hw.features)

    def test_stock_acer_wmi_not_claimed(self):
        stock = self.root / 'devices/platform/acer-wmi'
        stock.mkdir(parents=True)
        with patch.object(daemon, 'DRIVER_BASE_PATHS', [str(stock)]):
            hw = self.manager()
        self.assertIsNone(hw.driver_base)
        self.assertNotIn('fan_control', hw.features)

    def test_unloaded_and_not_installed(self):
        with patch.object(daemon, 'DRIVER_MODULE_PATH', str(self.root / 'absent')):
            self.assertEqual(self.manager().driver_status, 'not_installed')
            self.cmd.return_value = '/lib/modules/test/linuwu_sense.ko'
            self.assertEqual(self.manager().driver_status, 'module_not_loaded')

    def test_validate_all_fan_values_before_any_write(self):
        hw = self.manager()
        for bad in (-1, 101, True, False, 40.5, '50', '50,100', None, float('nan')):
            with patch.object(daemon, 'write_sysfs') as write:
                self.assertFalse(hw.set_fan_speed(bad, 50))
                self.assertFalse(hw.set_fan_speed(50, bad))
                write.assert_not_called()
        for cpu, gpu in ((0, 0), (1, 100), (65, 0), (0, 65), (100, 100)):
            self.assertTrue(hw.set_fan_speed(cpu, gpu))
            self.assertEqual(self.fan.read_text(), f'{cpu},{gpu}')

    def test_disappeared_node_never_created(self):
        hw = self.manager()
        self.fan.unlink()
        self.assertFalse(hw.set_fan_speed(65, 65))
        self.assertFalse(self.fan.exists())
        self.assertFalse(daemon.write_sysfs(self.fan, '0,0'))

    def test_error_restores_auto(self):
        hw = self.manager()
        writes = []

        def write(path, value):
            writes.append(value)
            return value == '0,0'
        with patch.object(daemon, 'write_sysfs', side_effect=write):
            self.assertFalse(hw.set_fan_speed(65, 65))
        self.assertEqual(writes, ['0,0', '65,65', '0,0'])

    def test_failed_auto_prevents_manual_transition(self):
        hw = self.manager()
        with patch.object(daemon, 'write_sysfs', return_value=False) as write:
            self.assertFalse(hw.set_fan_speed(65, 65))
            self.assertEqual([c.args[1] for c in write.call_args_list], ['0,0'])

    def test_shutdown_and_startup_restore_automatic(self):
        hw = self.manager()
        self.assertTrue(hw.set_fan_speed(65, 65))
        hw.shutdown_fan_curves()
        self.assertEqual(self.fan.read_text(), '0,0')
        self.fan.write_text('70,70')
        self.settings.set('fan_speed', {'cpu': 70, 'gpu': 70})
        self.manager(restore=True)
        self.assertEqual(self.fan.read_text(), '0,0')
        self.assertIsNone(self.settings.get('fan_speed'))

    def test_invalid_curves_do_not_start(self):
        hw = self.manager()
        for target, points in (
            ('bogus', [[30, 40], [80, 90]]), ('cpu', []), ('cpu', [[30, 20], [80, 90]]),
            ('cpu', [[30, 40], [30, 90]]), ('cpu', [[30, 40], [float('nan'), 90]]),
            ('cpu', [[30, 40], [80, 101]]), ('cpu', [[30, 40], ['80', 90]]),
        ):
            self.assertFalse(hw.start_fan_curve(target, points))
        self.assertIsNone(hw._fan_curve_engine._curve_thread)

    def test_profile_change_stops_curves_before_write(self):
        hw = self.manager()
        (self.root / 'profile').write_text('balanced')
        (self.root / 'choices').write_text('low-power balanced performance')
        hw._fan_curve_engine._active = {'cpu': True, 'gpu': True}
        hw._fan_curve_engine._curves = {'cpu': [[30, 40], [80, 90]]}
        self.assertEqual(hw.set_thermal_profile('low-power'), (True, None))
        self.assertEqual(self.fan.read_text(), '0,0')
        self.assertFalse(any(v['active'] for v in hw.get_fan_curve_state().values()))

    def test_curve_write_failure_reaches_watchdog(self):
        hw = self.manager()
        with patch.object(hw, '_write_fan_speed', return_value=False):
            with self.assertRaises(OSError):
                hw._fan_curve_set_fan('cpu', 65)

    def test_amd_cpu_temperature_uses_k10temp(self):
        sensor = self.root / 'hwmon/hwmon0'
        sensor.mkdir(parents=True)
        (sensor / 'name').write_text('k10temp')
        (sensor / 'temp1_input').write_text('64000')
        self.assertEqual(self.manager()._fan_curve_get_temp('cpu'), 64)
        (sensor / 'temp1_input').write_text('bad')
        self.assertIsNone(self.manager()._fan_curve_get_temp('cpu'))

    def test_arbitrary_thermal_zone_is_not_cpu_sensor(self):
        zone = self.root / 'thermal/thermal_zone0'
        zone.mkdir(parents=True)
        (zone / 'type').write_text('acpitz')
        (zone / 'temp').write_text('30000')
        self.assertIsNone(self.manager()._fan_curve_get_temp('cpu'))

    def test_nitro_gpu_temperature_precedes_integrated_gpu(self):
        for index, name, attribute, value in ((0, 'amdgpu', 'temp1_input', '35000'),
                                              (1, 'acer', 'temp3_input', '71000')):
            sensor = self.root / f'hwmon/hwmon{index}'
            sensor.mkdir(parents=True)
            (sensor / 'name').write_text(name)
            (sensor / attribute).write_text(value)
        self.assertEqual(self.manager()._fan_curve_get_temp('gpu'), 71)

    def test_watchdog_disables_saved_curves(self):
        hw = self.manager()
        self.settings.set('fan_curve_cpu', {'enabled': True, 'points': [[30, 50], [80, 90]]})
        engine = hw._fan_curve_engine
        engine._active = {'cpu': True}
        engine._curves = {'cpu': [[30, 50], [80, 90]]}
        engine._get_temp = lambda target: None
        for _ in range(3):
            engine._tick()
        self.assertFalse(self.settings.get('fan_curve_cpu')['enabled'])
        self.assertFalse(self.settings.get('fan_curve_gpu')['enabled'])


class CurveTests(unittest.TestCase):
    def engine(self, temp=60, write=True):
        engine = daemon.FanCurveEngine(Mock(return_value=temp), Mock(return_value=write), Mock(return_value=True))
        engine._active = {'cpu': True, 'gpu': True}
        engine._curves = {t: [[30, 40], [80, 90]] for t in ('cpu', 'gpu')}
        return engine

    def test_watchdog_catches_false_write(self):
        engine = self.engine(write=False)
        for _ in range(3):
            engine._tick()
        self.assertFalse(any(engine._active.values()))
        self.assertEqual(engine._restore_auto.call_count, 3)

    def test_missing_temperature_never_selects_minimum(self):
        for temp in (None, 0, float('nan'), -10, 200):
            engine = self.engine(temp=temp)
            engine._tick()
            engine._set_fan.assert_not_called()
            engine._restore_auto.assert_called_once()

    def test_stop_waits_for_inflight_write_then_restores(self):
        engine = self.engine()
        entered, release = threading.Event(), threading.Event()
        writes = []

        def write(target, pct):
            entered.set()
            release.wait(2)
            writes.append('manual')
        engine._set_fan = write
        engine._restore_auto = lambda: writes.append('auto')
        tick = threading.Thread(target=engine._tick)
        tick.start()
        self.assertTrue(entered.wait(2))
        stop = threading.Thread(target=engine.stop)
        stop.start()
        release.set()
        tick.join(2)
        stop.join(2)
        self.assertEqual(writes[-1], 'auto')
        engine._tick()
        self.assertEqual(writes[-1], 'auto')


class DiagnosticTests(unittest.TestCase):
    def test_report_excludes_private_dmi(self):
        spec = importlib.util.spec_from_file_location('diagnose', Path(__file__).resolve().parents[1] / 'scripts/archer-diagnose.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            dmi = root / 'class/dmi/id'
            dmi.mkdir(parents=True)
            (dmi / 'product_name').write_text('Nitro ANV16S-41')
            (dmi / 'product_serial').write_text('SECRET')
            report = mod.collect(root, commands=False)
            self.assertEqual(report['dmi']['product_name'], 'Nitro ANV16S-41')
            self.assertNotIn('SECRET', str(report))
            self.assertNotIn('dkms', report)


if __name__ == '__main__':
    unittest.main()
