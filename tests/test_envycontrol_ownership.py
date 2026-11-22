"""Ownership-safe EnvyControl application and recovery regressions."""
import base64
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gui'))

import archer_system


class EnvyControlOwnership(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state_path = self.root / 'var/lib/archer/envycontrol-ownership.json'
        self.service = {'state': 'disabled'}
        self.patches = [
            patch.object(archer_system, 'ENVY_ROOT', self.root),
            patch.object(archer_system, 'ENVY_STATE', self.state_path),
            patch.object(archer_system, 'ENVY_LOCK', self.root / 'run/archer/envycontrol.lock'),
            patch.object(archer_system, '_persistenced_state', side_effect=lambda: self.service['state']),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def file(self, relative, content=None, mode=0o644):
        path = self.root / relative
        if content is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            path.chmod(mode)
        return path

    def service_command(self, arguments, *_unused):
        if arguments[:2] == ['/usr/bin/systemctl', 'disable']:
            self.service['state'] = 'disabled'
            return ''
        if arguments[:2] == ['/usr/bin/systemctl', 'enable']:
            self.service['state'] = 'enabled-runtime' if '--runtime' in arguments else 'enabled'
            return ''
        raise AssertionError(arguments)

    def test_conditional_restore_preserves_package_files_and_unowned_edits(self):
        xsetup = self.file('usr/share/sddm/scripts/Xsetup', b'package Xsetup\n', 0o755)
        modprobe = self.file('etc/modprobe.d/nvidia.conf', b'administrator options\n')
        untouched = self.file('etc/X11/xorg.conf.d/90-nvidia.conf', b'preexisting legacy\n')
        generated = self.file('etc/X11/xorg.conf')
        archer_system._prepare_envycontrol('nvidia')
        prepared = json.loads(self.state_path.read_text())
        self.assertEqual(prepared['status'], 'applying')
        self.assertEqual(self.state_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(base64.b64decode(
            prepared['baseline']['files']['/usr/share/sddm/scripts/Xsetup']['data']), b'package Xsetup\n')

        xsetup.write_bytes(b'envy Xsetup\n')
        modprobe.unlink()
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_bytes(b'envy xorg\n')
        self.service['state'] = 'enabled'
        archer_system._record_envycontrol('complete', 'nvidia')
        untouched.write_bytes(b'administrator changed an untouched file\n')

        generated.write_bytes(b'administrator conflict\n')
        with patch.object(archer_system, 'rebuild') as rebuild:
            with self.assertRaisesRegex(ValueError, 'modified'):
                archer_system.operate('display-restore-if-owned')
            rebuild.assert_not_called()
        self.assertEqual(xsetup.read_bytes(), b'envy Xsetup\n')
        self.assertFalse(modprobe.exists())

        generated.write_bytes(b'envy xorg\n')
        with patch.object(archer_system, 'checked', side_effect=self.service_command):
            archer_system._restore_envycontrol()
        self.assertEqual(xsetup.read_bytes(), b'package Xsetup\n')
        self.assertEqual(xsetup.stat().st_mode & 0o777, 0o755)
        self.assertEqual(modprobe.read_bytes(), b'administrator options\n')
        self.assertFalse(generated.exists())
        self.assertEqual(untouched.read_bytes(), b'administrator changed an untouched file\n')
        self.assertEqual(self.service['state'], 'disabled')
        self.assertEqual(json.loads(self.state_path.read_text())['status'], 'restore_rebuild_required')

        with patch.object(archer_system, 'rebuild', side_effect=OSError('rebuild failed')):
            with self.assertRaisesRegex(OSError, 'rebuild failed'):
                archer_system.operate('display-restore')
        self.assertTrue(self.state_path.exists())
        xsetup.write_bytes(b'administrator changed restored file\n')
        with patch.object(archer_system, 'rebuild') as rebuild:
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                archer_system.operate('display-restore')
            rebuild.assert_not_called()
        xsetup.write_bytes(b'package Xsetup\n')
        xsetup.chmod(0o755)
        with patch.object(archer_system, 'rebuild'):
            result = archer_system.operate('display-restore')
        self.assertEqual(result, {'restored': True, 'reboot_required': True})
        self.assertFalse(self.state_path.exists())
        with patch.object(archer_system, 'rebuild') as rebuild:
            result = archer_system.operate('display-restore-if-owned')
        self.assertEqual(result['reason'], 'no_ownership_journal')
        self.assertIn('legacy or already-restored', result['message'])
        rebuild.assert_not_called()

    def test_absent_journal_does_not_probe_service_files_or_boot(self):
        with patch.object(archer_system, '_persistenced_state',
                          side_effect=AssertionError('service probe must not run')), \
                patch.object(archer_system, '_envy_files',
                             side_effect=AssertionError('graphics file probe must not run')), \
                patch.object(archer_system, 'rebuild',
                             side_effect=AssertionError('boot rebuild must not run')):
            result = archer_system.operate('display-restore-if-owned')
        self.assertEqual(result['reason'], 'no_ownership_journal')

    def _failed_display(self, rebuild_failure=False):
        managed = self.file('etc/modprobe.d/nvidia.conf', b'administrator baseline\n')

        def command(arguments, *_unused):
            if arguments == ['/usr/bin/envycontrol', '-s', 'nvidia']:
                state = json.loads(self.state_path.read_text())
                self.assertEqual(state['status'], 'applying')
                self.assertEqual(base64.b64decode(
                    state['baseline']['files']['/etc/modprobe.d/nvidia.conf']['data']),
                    b'administrator baseline\n')
                managed.write_bytes(b'envy result\n')
                self.service['state'] = 'enabled'
                return ''
            if arguments == ['/usr/bin/envycontrol', '--query']:
                return 'hybrid'
            raise AssertionError(arguments)

        rebuild = OSError('rebuild failed') if rebuild_failure else None
        with patch.object(archer_system, 'checked', side_effect=command), \
                patch.object(archer_system, 'rebuild', side_effect=rebuild):
            expected = 'rebuild failed' if rebuild_failure else 'readback'
            with self.assertRaisesRegex(OSError, expected):
                archer_system.operate('envy-apply', 'nvidia')
        state = json.loads(self.state_path.read_text())
        self.assertEqual(state['status'], 'recovery_required')
        self.assertEqual(base64.b64decode(
            state['baseline']['files']['/etc/modprobe.d/nvidia.conf']['data']),
            b'administrator baseline\n')
        self.assertEqual(managed.read_bytes(), b'envy result\n')
        baseline = state['baseline']
        archer_system._prepare_envycontrol('nvidia')
        retried = json.loads(self.state_path.read_text())
        self.assertEqual(retried['status'], 'applying')
        self.assertEqual(retried['baseline'], baseline)

    def test_readback_failure_retains_original_recovery_state(self):
        self._failed_display()

    def test_rebuild_failure_retains_original_recovery_state(self):
        self._failed_display(rebuild_failure=True)

    def test_external_edit_blocks_retry_before_another_envycontrol_run(self):
        generated = self.file('etc/X11/xorg.conf')
        archer_system._prepare_envycontrol('nvidia')
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_bytes(b'envy result\n')
        archer_system._record_envycontrol('complete', 'nvidia')
        baseline = json.loads(self.state_path.read_text())['baseline']
        generated.write_bytes(b'administrator replacement\n')
        with self.assertRaisesRegex(ValueError, 'outside Archer'):
            archer_system._prepare_envycontrol('hybrid')
        self.assertEqual(json.loads(self.state_path.read_text())['baseline'], baseline)
        self.assertEqual(generated.read_bytes(), b'administrator replacement\n')

    def test_symlink_and_interrupted_journal_are_never_guessed(self):
        target = self.file('administrator.conf', b'keep\n')
        candidate = self.file('etc/modprobe.d/nvidia.conf')
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            archer_system._prepare_envycontrol('hybrid')
        self.assertFalse(self.state_path.exists())
        candidate.unlink()
        archer_system._prepare_envycontrol('hybrid')
        with self.assertRaisesRegex(ValueError, 'interrupted'):
            archer_system.operate('display-restore-if-owned')

    def test_gui_dispatch_and_concurrent_transaction_use_the_locked_apply_path(self):
        commands = []

        def command(arguments, *_unused):
            commands.append(arguments)
            if arguments[-2:] == ['envy-apply', 'nvidia']:
                return json.dumps({'success': True, 'data': {'mode': 'nvidia'}})
            raise AssertionError(arguments)

        with patch.object(archer_system, 'checked', side_effect=command), \
                patch.object(archer_system, 'build_plan', return_value={
                    'available': True, 'backend': 'envycontrol'}), \
                patch.object(archer_system, 'collect_inventory'), \
                patch.object(archer_system, 'verify_kernel_modules'):
            self.assertEqual(archer_system.operate('display', 'nvidia'), {
                'mode': 'nvidia', 'reboot_required': True})
        self.assertTrue(any(command[-2:] == ['envy-apply', 'nvidia'] for command in commands))
        self.assertNotIn(['/usr/bin/envycontrol', '-s', 'nvidia'], commands)

        with archer_system._envy_lock(), patch.object(archer_system, 'checked') as execute:
            with self.assertRaisesRegex(ValueError, 'already running'):
                archer_system.operate('envy-apply', 'nvidia')
            execute.assert_not_called()

    def test_shell_uninstall_uses_conditional_restore_not_envy_reset(self):
        source = (ROOT / 'modules/gpu.sh').read_text()
        uninstall = source.split('module_uninstall() {', 1)[1].split('\n}', 1)[0]
        self.assertIn('display-restore-if-owned', uninstall)
        self.assertNotIn('--reset', source)
        self.assertNotIn('envycontrol_path', uninstall)
        tracked = set(archer_system.ENVY_PATHS)
        self.assertTrue({'etc/modprobe.d/blacklist-nvidia.conf', 'etc/modprobe.d/nvidia.conf',
                         'etc/X11/xorg.conf', 'etc/X11/xorg.conf.d/10-nvidia.conf',
                         'etc/X11/xorg.conf.d/90-nvidia.conf', 'etc/udev/rules.d/50-remove-nvidia.rules',
                         'etc/udev/rules.d/80-nvidia-pm.rules',
                         'usr/lib/udev/rules.d/50-remove-nvidia.rules',
                         'usr/lib/udev/rules.d/80-nvidia-pm.rules', 'etc/lightdm/nvidia.sh',
                         'etc/lightdm/lightdm.conf.d/20-nvidia.conf', 'usr/share/sddm/scripts/Xsetup',
                         'usr/share/sddm/scripts/Xsetup.bak', 'var/cache/envycontrol/cache.json'} <= tracked)

    def test_gui_removal_restores_before_delete_unless_gpu_module_remains(self):
        helper = self.file('opt/archer/archer_system.py', b'fixture\n')

        def uninstall(modules):
            script = f'''
SCRIPT_DIR={str(ROOT)!r}
source "$SCRIPT_DIR/modules/gui.sh"
_GUI_INSTALLED_SYSTEM_HELPER={str(helper)!r}
_GUI_SERVICE={str(self.root / 'missing-service')!r}
_gui_check_ownership() {{ return 0; }}
run_sudo() {{ printf 'CALL:%s\\n' "$*"; return 0; }}
log() {{ printf 'LOG:%s\\n' "$*"; return 0; }}
warn() {{ printf 'WARN:%s\\n' "$*"; return 0; }}
has_manifest() {{ {'return 0' if modules is not None else 'return 1'}; }}
read_manifest_modules() {{ printf '%s\\n' {str(modules or '')!r}; }}
module_uninstall
'''
            return subprocess.run(['bash', '-c', script], check=True, text=True,
                                  capture_output=True).stdout

        output = uninstall(None)
        restore = output.index('display-restore-if-owned')
        remove = output.index('archer-gui-files.py remove')
        self.assertLess(restore, remove)
        self.assertIn(str(ROOT / 'gui/archer_system.py'), output)
        self.assertNotIn(str(helper) + ' display-restore-if-owned', output)
        retained = uninstall('gui gpu')
        self.assertNotIn('display-restore-if-owned', retained)
        self.assertIn('GPU module remains installed', retained)

    def test_package_removal_hook_is_pretransaction_fail_closed_and_upgrade_safe(self):
        hook = (ROOT / 'packaging/hooks/30-archer-envycontrol-restore.hook').read_text()
        self.assertIn('Operation = Remove', hook)
        self.assertNotIn('Operation = Upgrade', hook)
        self.assertIn('Target = archer-daemon', hook)
        self.assertIn('Target = archer-core', hook)
        self.assertIn('When = PreTransaction', hook)
        self.assertIn('AbortOnFail', hook)
        self.assertIn('Exec = /usr/bin/python3 /usr/lib/archer/app/archer_system.py '
                      'display-restore-if-owned', hook)


if __name__ == '__main__':
    unittest.main()
