#!/usr/bin/env python3
"""Hardware-free integration tests. Run under xvfb-run and dbus-run-session in CI."""

import copy
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ['GSETTINGS_BACKEND'] = 'memory'
_config = tempfile.TemporaryDirectory(prefix='archer-gtk-test-')
os.environ['XDG_CONFIG_HOME'] = _config.name
if '--headless' in sys.argv:
    import atexit
    import shutil
    import subprocess
    sys.argv.remove('--headless')
    os.environ['GDK_BACKEND'] = 'x11'
    os.environ['GSK_RENDERER'] = 'cairo'
    os.environ['DISPLAY'] = ':177'
    if '--xvfb' in sys.argv:
        index = sys.argv.index('--xvfb')
        binary = sys.argv[index + 1]
        del sys.argv[index:index + 2]
    else:
        binary = shutil.which('Xvfb')
    if not binary:
        sys.exit('Install Xvfb or pass --xvfb /path/to/Xvfb')
    server = subprocess.Popen([binary, ':177', '-screen', '0', '1920x1200x24', '-nolisten', 'tcp'], stdout=subprocess.DEVNULL)

    def stop_server():
        server.terminate()
        server.wait(timeout=5)
    atexit.register(stop_server)
    time.sleep(.3)
HIGH_CONTRAST = '--high-contrast' in sys.argv
if HIGH_CONTRAST:
    sys.argv.remove('--high-contrast')
    os.environ['ADW_DEBUG_HIGH_CONTRAST'] = '1'
LARGE_TEXT = '--large-text' in sys.argv
if LARGE_TEXT:
    sys.argv.remove('--large-text')
CAPTURE = '--capture' in sys.argv
if CAPTURE:
    sys.argv.remove('--capture')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'gui'))

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk

from archer.client import normalize_settings
from archer.preferences import Preferences
from archer.window import ArcherWindow


SETTINGS = {
    'features': ['thermal_profiles', 'fan_control', 'game_mode', 'battery_info', 'battery_limiter',
                 'battery_calibration', 'usb_charging', 'display_mode', 'lcd_override',
                 'keyboard_per_zone', 'keyboard_effects', 'backlight_timeout', 'audio_enhancement', 'boot_animation_sound'],
    'system_info': {'product_name': 'Predator Helios 16', 'vendor': 'Acer', 'cpu_model': 'Intel Core i7-13700HX',
                    'gpu_model': 'NVIDIA GeForce RTX 4060', 'kernel': '6.18.0-arch1-1'},
    'daemon_version': '2.0.1', 'thermal_profile': 'balanced',
    'thermal_choices': ['low-power', 'quiet', 'balanced', 'balanced-performance', 'performance'],
    'fan_speed_cpu': 0, 'fan_speed_gpu': 0, 'power_source_ac': True,
    'battery_info': {'present': True, 'percentage': 78, 'status': 'Charging', 'time_remaining': '0h 24m'},
    'battery_limiter': True, 'battery_calibration': False, 'usb_charging': 20,
    'display_mode': {'mode': 'hybrid', 'reboot_required': False, 'available_modes': ['integrated', 'hybrid', 'nvidia']},
    'game_mode': {'active': False}, 'lcd_override': True, 'backlight_timeout': True, 'boot_animation_sound': False,
    'saved_settings': {'audio_enhancement': {'noise_suppression': True}},
    'firmware_info': {'bios_version': '1.12', 'vendor': 'Acer', 'fwupd_available': True, 'status': 'not_checked', 'updates': []},
    'fan_curve': {}, 'driver_override': 'Disabled',
}
LIVE = {'cpu_temp': 52, 'gpu_temp': 43, 'cpu_usage': 24, 'gpu_usage': 8, 'fan_rpm_cpu': 2100, 'fan_rpm_gpu': 1800,
        'power_source_ac': True, 'battery_info': SETTINGS['battery_info'],
        'metric_validity': {key: True for key in ('cpu_temp', 'gpu_temp', 'cpu_usage', 'gpu_usage', 'fan_rpm_cpu', 'fan_rpm_gpu')}}


class FakeClient:
    def __init__(self):
        self.data = copy.deepcopy(SETTINGS)
        self.calls = []
        self.response = {'success': True}
        self.init_error = None
        self.dbus_iface = None
        self.delay = 0

    def reconnect(self):
        return True

    def get_all_settings(self):
        return normalize_settings(copy.deepcopy(self.data))

    def get_monitoring_data(self):
        return {**copy.deepcopy(LIVE), 'power_source_ac': self.data['power_source_ac'],
                'battery_info': copy.deepcopy(self.data['battery_info'])}

    def get_firmware_info(self):
        return {'success': True, 'data': self.data['firmware_info']}

    def __getattr__(self, name):
        if name.startswith(('set_', 'apply_', 'restart_', 'remove_')):
            def call(*args):
                self.calls.append((name, args))
                time.sleep(self.delay)
                response = copy.deepcopy(self.response)
                if response.get('success'):
                    mapping = {'set_battery_limiter': 'battery_limiter', 'set_thermal_profile': 'thermal_profile',
                               'set_usb_charging': 'usb_charging', 'set_backlight_timeout': 'backlight_timeout'}
                    if name in mapping:
                        self.data[mapping[name]] = args[0]
                return response
            return call
        raise AttributeError(name)


def drain(until=lambda: False, timeout=.1):
    context = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        while context.pending() and time.monotonic() < deadline:
            context.iteration(False)
        if until():
            return
        time.sleep(.005)


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        if HIGH_CONTRAST:
            assert Adw.StyleManager.get_default().get_high_contrast()
        if LARGE_TEXT:
            Gtk.Settings.get_default().set_property('gtk-xft-dpi', 192 * 1024)
        if not Gtk.init_check():
            raise RuntimeError('A display is required. Use xvfb-run -a dbus-run-session -- python tests/gui_test.py')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.client = FakeClient()
        self.window = ArcherWindow(client=self.client, autostart=False,
                                   preferences=Preferences(Path(self.temp.name) / 'gui.json'))
        self.state = self.window.state
        self.load()

    def load(self):
        self.state.settings = self.client.get_all_settings()
        self.state._status('Connected')
        self.state.emit('settings-changed', self.state.settings)
        self.state._sample(self.client.get_monitoring_data())

    def tearDown(self):
        self.window.dispose_resources()
        self.window.destroy()
        drain(timeout=.01)
        self.temp.cleanup()

    def test_loading_never_writes(self):
        for _ in range(3):
            self.load()
        self.assertEqual(self.client.calls, [])
        self.assertTrue(self.window.pages['audio'].noise.values()['enabled'])
        self.assertEqual(self.window.pages['display'].configured.get_subtitle(), 'Hybrid')

    def test_failed_immediate_change_reverts(self):
        form = self.window.pages['battery'].limit
        self.client.response = {'success': False, 'error': 'Authorization denied'}
        form.fields['enabled'][1](False)
        drain(lambda: not form.pending, timeout=2)
        self.assertTrue(form.values()['enabled'])
        self.assertIn('Authorization denied', form.feedback.get_label())
        self.assertEqual(len(self.client.calls), 1)

    def test_pending_change_cannot_submit_twice(self):
        form = self.window.pages['battery'].limit
        self.client.delay = .08
        form.fields['enabled'][1](False)
        form.submit()
        self.assertFalse(form.group.get_sensitive())
        drain(lambda: not form.pending, timeout=2)
        self.assertEqual(len(self.client.calls), 1)
        self.assertFalse(form.values()['enabled'])

    def test_drafts_survive_navigation_refresh_and_reset(self):
        form = self.window.pages['keyboard'].zones
        form.fields['brightness'][1](42)
        self.assertTrue(form.dirty)
        self.window.navigate('keyboard')
        self.window.navigate('battery')
        self.load()
        self.assertEqual(form.values()['brightness'], 42)
        form.reset()
        self.assertEqual(form.values()['brightness'], 100)
        self.assertFalse(form.dirty)
        self.assertEqual(self.client.calls, [])

    def test_invalid_color_does_not_write(self):
        form = self.window.pages['keyboard'].zones
        form.fields['zone1'][1]('oops')
        form.submit()
        drain(lambda: not form.pending, timeout=2)
        self.assertTrue(form.dirty)
        self.assertIn('#RRGGBB', form.feedback.get_label())
        self.assertEqual(self.client.calls, [])

    def test_stale_disables_writes(self):
        self.state.last_sample = time.monotonic() - 10
        self.state._check()
        self.assertEqual(self.state.status, 'Stale')
        self.assertFalse(self.window.pages['battery'].limit.group.get_sensitive())
        retry = self.state._retry
        self.state._check()
        self.assertEqual(self.state._retry, retry)

    def test_no_battery_and_limited_features(self):
        self.client.data['features'] = ['game_mode']
        self.client.data['battery_info'] = {'present': False}
        self.load()
        self.assertTrue(self.window.pages['battery'].unavailable.get_visible())
        self.assertFalse(self.window.pages['keyboard'].zones.get_visible())
        self.assertTrue(self.window.pages['keyboard'].unavailable.get_visible())
        self.assertEqual(len(self.window.navigation), 6)

    def test_firmware_rows_are_replaced(self):
        page = self.window.pages['firmware']
        info = {'fwupd_available': True, 'status': 'updates', 'updates': [
            {'Name': 'System Firmware', 'Releases': [{'Version': '1.13', 'Summary': 'Device improvements'}]}]}
        page._render(info)
        page._render(info)
        self.assertEqual(len(page.update_rows), 1)
        self.assertEqual(page.update_rows[0].get_title(), 'System Firmware 1.13')
        page._render({'status': 'error', 'updates': []})
        self.assertEqual(len(page.update_rows), 0)
        self.assertIn('failed', page.status.get_subtitle())

    def test_theme_and_navigation_keep_state(self):
        form = self.window.pages['keyboard'].zones
        form.fields['brightness'][1](35)
        for mode in ('light', 'dark', 'system'):
            self.window.preferences.save(appearance=mode)
            self.window.preferences.apply_appearance()
            self.window.navigate('keyboard')
            self.assertEqual(self.window.navigation['devices'].get_visible_page().get_tag(), 'keyboard')
            self.window.go_back()
        self.assertEqual(form.values()['brightness'], 35)

    def test_successful_save_and_offline_recovery(self):
        form = self.window.pages['battery'].limit
        self.state._status('Offline')
        form.submit()
        self.assertEqual(self.client.calls, [])
        self.state.refresh(reconnect=True)
        drain(lambda: self.state.writable, timeout=2)
        form.fields['enabled'][1](False)
        drain(lambda: not form.pending and not self.state._fetching, timeout=2)
        self.assertFalse(form.values()['enabled'])
        self.assertFalse(form.dirty)
        self.assertEqual(form.feedback.get_label(), 'Saved')

    def test_restart_waits_before_reporting_connected(self):
        form = self.window.pages['advanced'].restart
        form.submit()
        drain(lambda: not form.pending, timeout=2)
        self.assertEqual(self.state.status, 'Restarting')
        self.assertIn('Waiting', form.feedback.get_label())
        self.state._sample(copy.deepcopy(LIVE))
        self.assertEqual(self.state.status, 'Restarting')
        self.state.refresh(reconnect=True)
        drain(lambda: self.state.status == 'Connected', timeout=2)
        self.assertEqual(form.feedback.get_label(), 'Service connected.')

    def test_stale_inflight_read_cannot_overwrite_new_state(self):
        stale = self.client.get_all_settings()
        stale['battery_limiter'] = False
        self.state.invalidate()
        self.state._loaded(stale, None, '', 0, False)
        self.assertTrue(self.window.pages['battery'].limit.values()['enabled'])
        drain(lambda: not self.state._fetching, timeout=2)

    def test_power_source_updates_related_pages_without_writes(self):
        self.client.data['power_source_ac'] = False
        self.load()
        self.state._sample({**LIVE, 'power_source_ac': False})
        drain(timeout=.05)
        self.assertFalse(self.window.pages['battery'].calibration.allowed)
        self.assertTrue(self.window.pages['performance'].profile_rows['performance'][0].get_sensitive())
        self.assertEqual(self.client.calls, [])

    def test_app_dialogs_open_without_hardware_calls(self):
        self.window.present()
        for show in (self.window.show_preferences, self.window.show_about, self.window.show_shortcuts):
            dialog = show()
            drain(timeout=.02)
            dialog.force_close()
        self.assertEqual(self.client.calls, [])

    def test_tray_icon_has_a_legible_fallback(self):
        from archer.tray import _load_icon_pixmap
        icons = _load_icon_pixmap()
        self.assertEqual(len(icons), 1)
        width, height, pixels = icons[0]
        self.assertEqual(len(pixels), width * height * 4)
        self.assertGreater(sum(1 for index in range(0, len(pixels), 4) if pixels[index]), width * height // 2)

    def make_tray(self):
        from archer.application import ArcherApplication
        from archer.tray import StatusNotifierItem
        app = ArcherApplication()
        app.window = self.window
        app._tray = StatusNotifierItem(app.activate, app._request_quit, app._select_profile)
        app._bind_tray_profiles()
        self.addCleanup(app._tray.stop)
        return app, app._tray

    def test_tray_layout_over_session_bus(self):
        from archer.tray import DBUSMENU_XML, SNI_XML
        app, tray = self.make_tray()
        bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        tray._bus = bus
        tray._menu_reg_id = bus.register_object('/Menu', Gio.DBusNodeInfo.new_for_xml(DBUSMENU_XML).interfaces[0],
                                                tray._menu_method_call, tray._menu_get_property, None)
        tray._sni_reg_id = bus.register_object('/StatusNotifierItem', Gio.DBusNodeInfo.new_for_xml(SNI_XML).interfaces[0],
                                               tray._sni_method_call, tray._sni_get_property, None)

        def call(method, params, path='/Menu', interface='com.canonical.dbusmenu'):
            results = []

            def done(connection, result):
                try:
                    results.append(connection.call_finish(result))
                except GLib.Error as error:
                    results.append(error)
            bus.call(bus.get_unique_name(), path, interface, method, params, None,
                     Gio.DBusCallFlags.NONE, 1000, None, done)
            drain(lambda: bool(results), timeout=2)
            self.assertTrue(results, method)
            if isinstance(results[0], GLib.Error):
                raise results[0]
            return results[0]

        layout = call('GetLayout', GLib.Variant('(iias)', (0, -1, [])))
        root = layout.get_child_value(1)
        children = root.get_child_value(2)
        # A real host expects v -> tuple, not v -> v -> tuple.
        self.assertEqual(children.get_child_value(0).get_variant().get_type_string(), '(ia{sv}av)')
        rows = layout.unpack()[1][2]
        self.assertEqual([row[1]['label'] for row in rows], ['Open', 'Profile', 'Exit'])
        self.assertEqual([row[1]['label'] for row in rows[1][2]], ['Eco', 'Quiet', 'Balanced', 'Performance', 'Turbo'])
        self.assertEqual([row[1]['toggle-state'] for row in rows[1][2]], [0, 0, 1, 0, 0])
        submenu = call('GetLayout', GLib.Variant('(iias)', (3, 1, ['label']))).unpack()[1]
        self.assertEqual(submenu[0], 3)
        self.assertTrue(all(set(row[1]) == {'label'} for row in submenu[2]))
        self.assertEqual(call('GetLayout', GLib.Variant('(iias)', (0, 0, []))).unpack()[1][2], [])
        shallow = call('GetLayout', GLib.Variant('(iias)', (0, 1, []))).unpack()[1]
        self.assertEqual(shallow[2][1][2], [])
        props = call('GetGroupProperties', GLib.Variant('(aias)', ([3], ['children-display']))).unpack()[0]
        self.assertEqual(props, [(3, {'children-display': 'submenu'})])
        menu = call('Get', GLib.Variant('(ss)', ('org.kde.StatusNotifierItem', 'Menu')),
                    '/StatusNotifierItem', 'org.freedesktop.DBus.Properties')
        self.assertEqual(menu.unpack(), ('/Menu',))
        events = []
        watch = bus.signal_subscribe(None, 'com.canonical.dbusmenu', None, '/Menu', None, Gio.DBusSignalFlags.NONE,
                                     lambda _bus, _sender, _path, _iface, name, params: events.append((name, params.unpack())))
        self.addCleanup(bus.signal_unsubscribe, watch)
        call('ContextMenu', GLib.Variant('(ii)', (20, 30)), '/StatusNotifierItem', 'org.kde.StatusNotifierItem')
        drain(lambda: bool(events), timeout=1)
        self.assertIn(('ItemActivationRequested', (0, 0)), events)
        call('Event', GLib.Variant('(isvu)', (tray._profile_ids['quiet'], 'clicked', GLib.Variant('i', 0), 0)))
        drain(lambda: not app._tray_profile_pending and not self.state._fetching, timeout=2)
        self.assertEqual(self.client.calls, [('set_thermal_profile', ('quiet',))])
        self.assertTrue(any(name == 'LayoutUpdated' for name, _ in events))
        self.assertEqual(tray._get_item_properties(tray._profile_ids['quiet'])['toggle-state'].unpack(), 1)
        with self.assertRaises(GLib.Error):
            call('GetLayout', GLib.Variant('(iias)', (999, -1, [])))

    def test_tray_profile_changes_while_hidden_and_rejects_duplicate_clicks(self):
        app, tray = self.make_tray()
        self.window.set_visible(False)
        self.client.delay = .08
        item_id = tray._profile_ids['quiet']
        tray._event(item_id, 'clicked')
        tray._event(tray._profile_ids['performance'], 'clicked')
        drain(lambda: app._tray_profile_pending, timeout=1)
        self.assertFalse(tray._get_item_properties(item_id)['enabled'].unpack())
        drain(lambda: not app._tray_profile_pending and not self.state._fetching, timeout=2)
        self.assertEqual(self.client.calls, [('set_thermal_profile', ('quiet',))])
        self.assertFalse(self.window.get_visible())
        self.assertTrue(self.window.pages['performance'].profile_rows['quiet'][1].get_active())

    def test_tray_profile_failure_opens_feedback_and_keeps_confirmed_choice(self):
        app, tray = self.make_tray()
        self.client.response = {'success': False, 'error': 'Authorization denied'}
        tray._event(tray._profile_ids['performance'], 'clicked')
        drain(lambda: bool(self.client.calls) and not app._tray_profile_pending and not self.state._fetching, timeout=2)
        self.assertTrue(self.window.get_visible())
        self.assertIn('Authorization denied', self.window.pages['performance'].profiles.feedback.get_label())
        self.assertEqual(tray._get_item_properties(tray._profile_ids['balanced'])['toggle-state'].unpack(), 1)
        self.assertEqual(tray._get_item_properties(tray._profile_ids['performance'])['toggle-state'].unpack(), 0)

    def test_tray_follows_gui_profiles_and_capability_recovery(self):
        app, tray = self.make_tray()
        self.window.pages['performance'].profile_rows['low-power'][1].set_active(True)
        drain(lambda: not self.window.pages['performance'].profiles.pending and not self.state._fetching, timeout=2)
        self.assertEqual(tray._get_item_properties(tray._profile_ids['low-power'])['toggle-state'].unpack(), 1)
        self.state._status('Offline')
        tray._event(tray._profile_ids['quiet'], 'clicked')
        drain()
        self.assertEqual(len(self.client.calls), 1)
        self.client.data['thermal_choices'] = ['quiet', 'balanced']
        self.client.data['thermal_profile'] = 'quiet'
        self.load()
        self.assertEqual([row[1]['label'].unpack() for row in
                          (tray._build_layout(item_id) for item_id in tray._menu_items[3]['children'])], ['Quiet', 'Balanced'])
        self.assertTrue(tray._get_item_properties(3)['enabled'].unpack())
        self.client.data['features'] = []
        self.load()
        tray._event(tray._profile_ids['balanced'], 'clicked')
        drain()
        self.assertFalse(tray._get_item_properties(3)['enabled'].unpack())
        self.assertEqual(len(self.client.calls), 1)

    def test_tray_event_group_and_open_exit_are_one_shot(self):
        from archer.tray import StatusNotifierItem
        opened, exited = Mock(return_value=True), Mock(return_value=True)
        tray = StatusNotifierItem(opened, exited)
        invocation = Mock()
        events = [(1, 'clicked', GLib.Variant('i', 0), 0), (2, 'clicked', GLib.Variant('i', 0), 0),
                  (999, 'clicked', GLib.Variant('i', 0), 0)]
        tray._menu_method_call(None, None, None, None, 'EventGroup', GLib.Variant('(a(isvu))', (events,)), invocation)
        drain()
        opened.assert_called_once_with()
        exited.assert_called_once_with()
        self.assertEqual(invocation.return_value.call_args.args[0].unpack(), ([999],))

    def test_tray_exit_closes_resources_and_removes_icon(self):
        app, tray = self.make_tray()
        with patch.object(self.window, 'get_application', return_value=app), patch.object(app, 'release') as release, \
                patch.object(app, 'quit') as quit_app, patch.object(tray, 'stop') as stop:
            app._request_quit()
            self.assertTrue(self.state.closed)
            self.assertTrue(self.window._closing)
            stop.assert_called_once_with()
            release.assert_called_once_with()
            quit_app.assert_called_once_with()
            self.assertIsNone(app._tray)

    def test_tray_exit_preserves_existing_draft_confirmation(self):
        app, _tray = self.make_tray()
        self.window.pages['keyboard'].zones.fields['brightness'][1](42)
        with patch('archer.window.confirm') as confirm, patch.object(app, 'finish_quit') as finish:
            app._request_quit()
            confirm.assert_called_once()
            finish.assert_not_called()
            self.assertTrue(self.window.get_visible())
            self.assertFalse(self.state.closed)

    @unittest.skipUnless(CAPTURE, 'Run with --capture for visual review')
    def test_visual_review(self):
        import math
        import gi
        gi.require_version('Graphene', '1.0')
        from gi.repository import Graphene
        output = Path('/tmp/archer-ui-review')
        output.mkdir(exist_ok=True)
        Gtk.Settings.get_default().set_property('gtk-enable-animations', False)
        self.window.present()
        drain(timeout=.25)
        for mode in ('light', 'dark'):
            self.window.preferences.save(appearance=mode)
            self.window.preferences.apply_appearance()
            for width, height in ((360, 600), (768, 700), (1100, 760), (1440, 900)):
                self.window.set_default_size(width, height)
                drain(timeout=.3)
                # Illustrative simulated history; no real sensors or setters are used.
                history = self.window.pages['overview'].history
                history.clear()
                now = time.monotonic()
                history.extend((now - (59 - i) * 2, 52 + 5 * math.sin((i - 59) / 6),
                                43 + 4 * math.sin((i - 59) / 8)) for i in range(60))
                for name in self.window.pages:
                    self.window.navigate(name)
                    drain(timeout=.3)
                    paintable = Gtk.WidgetPaintable.new(self.window)
                    snapshot = Gtk.Snapshot.new()
                    actual_width, actual_height = self.window.get_width(), self.window.get_height()
                    paintable.snapshot(snapshot, actual_width, actual_height)
                    node = snapshot.to_node()
                    self.assertEqual(self.window.split.get_collapsed(), actual_width <= (1800 if LARGE_TEXT else 900))
                    if self.window.toast_overlay.measure(Gtk.Orientation.HORIZONTAL, -1)[0] > actual_width:
                        def diagnose(widget, depth=0):
                            minimum = widget.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
                            if minimum > actual_width and widget.get_visible():
                                print('  ' * depth, type(widget).__name__, minimum,
                                      widget.get_label() if isinstance(widget, Gtk.Label) else '')
                            child = widget.get_first_child()
                            while child:
                                diagnose(child, depth + 1)
                                child = child.get_next_sibling()
                        diagnose(self.window.toast_overlay)
                    self.assertLessEqual(self.window.toast_overlay.measure(Gtk.Orientation.HORIZONTAL, -1)[0], actual_width)
                    bounds = Graphene.Rect().init(0, 0, actual_width, actual_height)
                    renderer = self.window.get_native().get_renderer()
                    texture = renderer.render_texture(node, bounds)
                    texture.save_to_png(str(output / f'{name}-{mode}-{width}{"-contrast" if HIGH_CONTRAST else ""}{"-large" if LARGE_TEXT else ""}.png'))
                    self.assertLessEqual(self.window.pages[name].get_hadjustment().get_upper(),
                                         self.window.pages[name].get_hadjustment().get_page_size() + 1)
                if width in (360, 1100):
                    for name, show in (('preferences', self.window.show_preferences), ('about', self.window.show_about),
                                       ('shortcuts', self.window.show_shortcuts),
                                       ('graphics-confirmation', self.window.pages['display'].graphics.submit)):
                        dialog = show()
                        drain(timeout=.1)
                        paintable = Gtk.WidgetPaintable.new(self.window)
                        snapshot = Gtk.Snapshot.new()
                        actual_width, actual_height = self.window.get_width(), self.window.get_height()
                        paintable.snapshot(snapshot, actual_width, actual_height)
                        bounds = Graphene.Rect().init(0, 0, actual_width, actual_height)
                        texture = self.window.get_native().get_renderer().render_texture(snapshot.to_node(), bounds)
                        texture.save_to_png(str(output / f'{name}-{mode}-{width}{"-contrast" if HIGH_CONTRAST else ""}{"-large" if LARGE_TEXT else ""}.png'))
                        dialog.force_close()
                        drain(timeout=.03)
        print('Visual captures:', output)

    def test_zero_reading_and_missing_sensor_are_distinct(self):
        from archer.pages.overview import reading
        self.assertEqual(reading({'gpu_usage': 0, 'metric_validity': {'gpu_usage': True}}, 'gpu_usage'), 0)
        self.assertIsNone(reading({'gpu_usage': 0, 'metric_validity': {'gpu_usage': False}}, 'gpu_usage'))
        self.assertIsNone(reading({'cpu_temp': float('nan')}, 'cpu_temp'))
        self.assertEqual(len(self.window.pages['overview'].history), 1)


if __name__ == '__main__':
    unittest.main(warnings="ignore")
