import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
TRANSACTION = REPO / 'scripts/archer-transaction.py'
BOOT = REPO / 'scripts/archer-boot.py'


def run(*args, **kwargs):
    return subprocess.run(args, capture_output=True, text=True, **kwargs)


class TransactionTests(unittest.TestCase):
    def test_pointer_transitions_recovery_and_reviewed_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'journals'
            plan = Path(temp) / 'plan.json'
            plan.write_text(json.dumps({'modules': ['driver'], 'inventory_generation': 'one'}))
            command = ['python3', str(TRANSACTION), '--root', str(root)]
            result = run(*command, 'begin', str(plan), '--approval', 'interactive')
            self.assertEqual(result.returncode, 0, result.stderr)
            transaction_id = result.stdout.strip()
            self.assertEqual(json.loads((root / 'current').read_text()), transaction_id)
            for state in ('preflight-complete', 'applying'):
                result = run(*command, 'transition', state)
                self.assertEqual(result.returncode, 0, result.stderr)
            result = run(*command, 'recover', '--message', 'inspect boot and DKMS state')
            self.assertEqual(result.returncode, 0, result.stderr)
            journal = json.loads((root / f'{transaction_id}.json').read_text())
            self.assertEqual(journal['state'], 'recovery-required')
            self.assertEqual(journal['recovery'][-1]['instruction'], 'inspect boot and DKMS state')
            plan.write_text(json.dumps({'modules': ['driver'], 'inventory_generation': 'two'}))
            result = run(*command, 'begin', str(plan), '--approval', 'no-confirm', '--resume')
            self.assertEqual(result.returncode, 0, result.stderr)
            journal = json.loads((root / f'{transaction_id}.json').read_text())
            self.assertEqual(journal['state'], 'planned')
            self.assertEqual(journal['plan']['inventory_generation'], 'two')

    def test_invalid_transition_does_not_change_journal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'journals'
            plan = Path(temp) / 'plan.json'
            plan.write_text('{"modules": []}')
            command = ['python3', str(TRANSACTION), '--root', str(root)]
            self.assertEqual(run(*command, 'begin', str(plan), '--approval', 'interactive').returncode, 0)
            before = (root / (json.loads((root / 'current').read_text()) + '.json')).read_bytes()
            result = run(*command, 'transition', 'committed')
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((root / (json.loads((root / 'current').read_text()) + '.json')).read_bytes(), before)

    def test_uninstall_can_supersede_reviewed_pending_install(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'journals'
            plan = Path(temp) / 'plan.json'
            plan.write_text('{"modules": ["driver"]}')
            command = ['python3', str(TRANSACTION), '--root', str(root)]
            install = run(*command, 'begin', str(plan), '--approval', 'interactive')
            self.assertEqual(install.returncode, 0, install.stderr)
            install_id = install.stdout.strip()
            for state in ('preflight-complete', 'applying', 'pending-restart'):
                self.assertEqual(run(*command, 'transition', state).returncode, 0)
            uninstall = run(*command, 'begin', str(plan), '--approval', 'interactive',
                            '--kind', 'uninstall', '--supersede')
            self.assertEqual(uninstall.returncode, 0, uninstall.stderr)
            self.assertNotEqual(uninstall.stdout.strip(), install_id)
            old = json.loads((root / f'{install_id}.json').read_text())
            self.assertEqual(old['state'], 'recovery-required')


class BootAdapterTests(unittest.TestCase):
    def fixture(self, root, relative, content=''):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def test_inactive_installed_tool_is_not_selected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tool = self.fixture(root, 'usr/bin/dracut', '#!/bin/sh\nexit 0\n')
            tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
            report = run('python3', str(BOOT), '--root', str(root), 'inspect')
            self.assertEqual(report.returncode, 0, report.stderr)
            self.assertEqual(json.loads(report.stdout)['initramfs'], [])
            result = run('python3', str(BOOT), '--root', str(root), 'rebuild')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('found none', result.stderr)

    def test_ambiguous_generators_fail_before_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root, 'etc/mkinitcpio.d/linux.preset')
            self.fixture(root, 'etc/dracut.conf')
            for name in ('mkinitcpio', 'dracut'):
                tool = self.fixture(root, f'usr/bin/{name}', '#!/bin/sh\ntouch "$MARKER"\n')
                tool.chmod(0o755)
            marker = root / 'ran'
            result = run('python3', str(BOOT), '--root', str(root), 'rebuild', env={**os.environ, 'MARKER': str(marker)})
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('found mkinitcpio, dracut', result.stderr)
            self.assertFalse(marker.exists())

    def test_success_without_generated_artifact_is_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root, 'etc/mkinitcpio.d/linux.preset',
                         "default_image='/boot/initramfs-linux.img'\n")
            (root / 'boot').mkdir()
            tool = self.fixture(root, 'usr/bin/mkinitcpio', '#!/bin/sh\nexit 0\n')
            tool.chmod(0o755)
            result = run('python3', str(BOOT), '--root', str(root), 'rebuild')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('required artifact is absent or empty', result.stderr)

    def test_changed_artifact_is_verified(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root, 'etc/mkinitcpio.d/linux.preset',
                         "default_image='/boot/initramfs-linux.img'\n")
            artifact = self.fixture(root, 'boot/initramfs-linux.img', 'old')
            tool = self.fixture(root, 'usr/bin/mkinitcpio', f'#!/bin/sh\nprintf new-content > "{artifact}"\n')
            tool.chmod(0o755)
            result = run('python3', str(BOOT), '--root', str(root), 'rebuild')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(artifact.read_text(), 'new-content')

    def test_rebuilt_identical_image_needs_evidence_of_replacement(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root, 'etc/mkinitcpio.d/linux.preset', "default_image='/boot/linux.img'\n")
            artifact = self.fixture(root, 'boot/linux.img', 'reproducible')
            tool = self.fixture(root, 'usr/bin/mkinitcpio', f'#!/bin/sh\ntouch "{artifact}"\n')
            tool.chmod(0o755)
            result = run('python3', str(BOOT), '--root', str(root), 'rebuild')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('required artifact did not change', result.stderr)
            tool.write_text(f'#!/bin/sh\nprintf reproducible > "{artifact}.tmp" && mv "{artifact}.tmp" "{artifact}"\n')
            result = run('python3', str(BOOT), '--root', str(root), 'rebuild')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(artifact.read_text(), 'reproducible')

    def test_dracut_rebuilds_and_verifies_every_installed_kernel(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root, 'etc/dracut.conf')
            for kernel in ('6.18-test', '7.3-test'):
                (root / f'usr/lib/modules/{kernel}/kernel').mkdir(parents=True)
            tool = self.fixture(root, 'usr/bin/dracut',
                                '#!/bin/sh\nprintf generated-%s "$3" > "$2"\n')
            tool.chmod(0o755)
            (root / 'boot').mkdir()
            result = run('python3', str(BOOT), '--root', str(root), 'rebuild')
            self.assertEqual(result.returncode, 0, result.stderr)
            for kernel in ('6.18-test', '7.3-test'):
                self.assertEqual((root / f'boot/initramfs-{kernel}.img').read_text(), f'generated-{kernel}')

    def test_booster_uses_unambiguous_distribution_hook(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            kernel = '7.3-test'
            self.fixture(root, 'etc/booster.yaml')
            self.fixture(root, 'etc/kernel/entry-token', 'machine-token\n')
            image = self.fixture(root, f'usr/lib/modules/{kernel}/vmlinuz', 'kernel')
            self.assertTrue(image.is_file())
            (root / f'boot/machine-token/{kernel}').mkdir(parents=True)
            booster = self.fixture(root, 'usr/bin/booster', '#!/bin/sh\nexit 0\n')
            booster.chmod(0o755)
            hook = self.fixture(root, 'usr/lib/kernel/install.d/50-booster.install',
                                '#!/bin/sh\nprintf initrd-%s "$2" > "$3/initrd"\n')
            hook.chmod(0o755)
            result = run('python3', str(BOOT), '--root', str(root), 'rebuild')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / f'boot/machine-token/{kernel}/initrd').read_text(), f'initrd-{kernel}')

    def test_secure_boot_rejects_zero_exit_without_certificate_listing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            artifact = root / 'efi/EFI/Linux/arch.efi'
            self.fixture(root, 'etc/mkinitcpio.d/linux.preset',
                         "default_uki='/efi/EFI/Linux/arch.efi'\n")
            artifact.parent.mkdir(parents=True)
            artifact.write_text('old')
            tool = self.fixture(root, 'usr/bin/mkinitcpio',
                                f'#!/bin/sh\nprintf new-uki > "{artifact}"\n')
            tool.chmod(0o755)
            verifier = self.fixture(root, 'usr/bin/sbverify', '#!/bin/sh\necho "No signature table present"\n')
            verifier.chmod(0o755)
            variable = root / 'sys/firmware/efi/efivars/SecureBoot-test'
            variable.parent.mkdir(parents=True)
            variable.write_bytes(b'header\x01')
            mountinfo = self.fixture(root, 'mountinfo',
                                     f'36 25 0:32 / {root / "efi"} rw - vfat test rw\n')
            environment = {**os.environ, 'ARCHER_BOOT_MOUNTINFO': str(mountinfo)}
            result = run('python3', str(BOOT), '--root', str(root), 'rebuild', env=environment)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('no verifiable signature certificate', result.stderr)

    def test_uki_target_requires_its_esp_or_xbootldr_mount(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root, 'etc/mkinitcpio.d/linux.preset',
                         "default_uki='/efi/EFI/Linux/arch.efi'\n")
            (root / 'efi/EFI/Linux').mkdir(parents=True)
            tool = self.fixture(root, 'usr/bin/mkinitcpio', '#!/bin/sh\nexit 0\n')
            tool.chmod(0o755)
            result = run('python3', str(BOOT), '--root', str(root), 'rebuild')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('not mounted', result.stderr)

    def test_systemd_boot_entries_are_edited_across_efi_layout_and_only_owned_tokens_removed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root, 'efi/loader/loader.conf', 'default arch.conf\n')
            first = self.fixture(root, 'efi/loader/entries/arch.conf', 'title Arch\noptions quiet admin=1\n')
            second = self.fixture(root, 'efi/loader/entries/lts.conf', 'title LTS\noptions quiet admin=1\n')
            add = run('python3', str(BOOT), '--root', str(root), 'kernel-args', 'add', 'i8042.reset')
            self.assertEqual(add.returncode, 0, add.stderr)
            self.assertIn('i8042.reset', first.read_text())
            self.assertIn('i8042.reset', second.read_text())
            remove = run('python3', str(BOOT), '--root', str(root), 'kernel-args', 'remove',
                         'i8042.reset admin=1')
            self.assertEqual(remove.returncode, 0, remove.stderr)
            self.assertNotIn('i8042.reset', first.read_text())
            self.assertIn('admin=1', first.read_text())

    def test_limine_and_refind_require_authoritative_literal_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            limine = self.fixture(root, 'etc/limine-entry-tool.conf',
                                  'KERNEL_CMDLINE[default]="quiet"\n')
            result = run('python3', str(BOOT), '--root', str(root), 'kernel-args', 'add', 'test=1')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('quiet test=1', limine.read_text())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            refind = self.fixture(root, 'boot/refind_linux.conf', '"Boot default" "quiet splash"\n')
            result = run('python3', str(BOOT), '--root', str(root), 'kernel-args', 'add', 'test=1')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('quiet splash test=1', refind.read_text())

    def test_grub_requires_the_only_authoritative_owner_and_changed_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = self.fixture(root, 'etc/default/grub', 'GRUB_CMDLINE_LINUX_DEFAULT="quiet"\n')
            output = self.fixture(root, 'boot/grub/grub.cfg', 'old config\n')
            generator = self.fixture(root, 'usr/bin/grub-mkconfig',
                                     f'#!/bin/sh\nprintf generated > "{output}"\n')
            generator.chmod(0o755)
            result = run('python3', str(BOOT), '--root', str(root), 'kernel-args', 'add', 'test=1')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('quiet test=1', config.read_text())
            self.assertEqual(output.read_text(), 'generated')
            self.fixture(root, 'boot/loader/loader.conf', 'default arch.conf\n')
            self.fixture(root, 'boot/loader/entries/arch.conf', 'options quiet\n')
            result = run('python3', str(BOOT), '--root', str(root), 'kernel-args', 'add', 'second=1')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('found grub, systemd-boot', result.stderr)


if __name__ == '__main__':
    unittest.main()
