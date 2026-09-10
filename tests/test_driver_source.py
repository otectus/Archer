"""Integration tests against the pinned upstream Git tree; set LINUWU_TEST_REPO.

CI downloads the repository explicitly. Local offline runs may use an existing
clone. All mutation, builds and simulated WMI calls stay in a TemporaryDirectory.
"""
import difflib
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
UPSTREAM = os.environ.get('LINUWU_TEST_REPO')


def run(*args, **kwargs):
    return subprocess.run(args, capture_output=True, text=True, **kwargs)


class BuildHelperTests(unittest.TestCase):
    def test_target_kernel_selects_gcc_or_clang(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            build = root / 'modules/7.0-test-zen/build'
            build.mkdir(parents=True)
            (build / 'Makefile').touch()
            helper = root / 'build.sh'
            helper.write_text((REPO / 'driver/build.sh').read_text())
            shutil.copy(REPO / 'lib/kernel.sh', root / 'archer-kernel.sh')
            (build / 'include/config').mkdir(parents=True)
            (build / 'include/config/kernel.release').write_text('7.0-test-zen')
            binary = root / 'bin'
            binary.mkdir()
            make = binary / 'make'
            make.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$MOCK_MAKE_LOG"\n')
            make.chmod(0o755)
            log = root / 'make.log'
            env = {**os.environ, 'PATH': str(binary) + os.pathsep + os.environ['PATH'], 'MOCK_MAKE_LOG': str(log), 'KERNEL_MODULES_ROOT': str(root / 'modules')}
            for clang in (False, True):
                (build / '.config').write_text('CONFIG_CC_IS_CLANG=y' if clang else 'CONFIG_CC_IS_GCC=y')
                result = run('bash', str(helper), '7.0-test-zen', cwd=root, env=env)
                self.assertEqual(result.returncode, 0, result.stderr)
                arguments = log.read_text().splitlines()
                self.assertIn(str(build), arguments)
                self.assertIn('M=' + str(root), arguments)
                self.assertEqual('LLVM=1' in arguments, clang)
                self.assertEqual('CC=clang' in arguments, clang)
                self.assertEqual(arguments[-1], 'modules')
            result = run('bash', str(helper), '../unexpected', cwd=root, env=env)
            self.assertNotEqual(result.returncode, 0)


@unittest.skipUnless(UPSTREAM, 'Set LINUWU_TEST_REPO to validate the real pinned driver source')
class DriverSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.package = REPO / 'driver/linuwu-sense'
        cls.tree = cls.root / 'prepared'
        result = run('bash', str(cls.package / 'prepare.sh'), str(cls.tree), UPSTREAM)
        if result.returncode:
            raise AssertionError(result.stderr)
        cls.source = (cls.tree / 'src/linuwu_sense.c').read_text()
        cls.revision = re.search(r'DRIVER_REVISION="([a-f0-9]+)"', (cls.package / 'source.conf').read_text())[1]
        cls.original = run('git', '-C', UPSTREAM, 'show', f'{cls.revision}:src/linuwu_sense.c', check=True).stdout

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_preparation_reproducible(self):
        second = self.root / 'second'
        result = run('bash', str(self.package / 'prepare.sh'), str(second), UPSTREAM)
        self.assertEqual(result.returncode, 0, result.stderr)
        for file in ('src/linuwu_sense.c', 'archer-build.sh', 'dkms.conf', 'archer-source'):
            self.assertEqual((second / file).read_bytes(), (self.tree / file).read_bytes())
        self.assertFalse((second / '.git').exists())
        config = (second / 'dkms.conf').read_text()
        self.assertIn('archer-build.sh $kernelver', config)
        self.assertIn('AUTOINSTALL="yes"', config)

    def test_exact_dmi_addition_keeps_existing_entries(self):
        def table(source):
            return source.split('static const struct dmi_system_id acer_quirks[]')[1].split('static const struct dmi_system_id')[0]
        diff = list(difflib.ndiff(table(self.original).splitlines(), table(self.source).splitlines()))
        self.assertFalse(any(line.startswith('- ') for line in diff))
        additions = '\n'.join(line[2:] for line in diff if line.startswith('+ '))
        self.assertIn('DMI_EXACT_MATCH(DMI_PRODUCT_NAME, "Nitro ANV16S-41")', additions)
        self.assertIn('DMI_EXACT_MATCH(DMI_SYS_VENDOR, "Acer")', additions)
        self.assertIn('&quirk_acer_nitro_anv16_41', additions)
        for model in ('Nitro ANV15-51', 'Nitro ANV15-41', 'Nitro ANV16-41', 'Predator PHN16-71'):
            self.assertIn(model, table(self.source))

    def test_source_mismatch_fails_before_dkms_config(self):
        package = self.root / 'bad-patch-package'
        shutil.copytree(self.package, package)
        shutil.copy(REPO / 'driver/prepare.sh', package.parent / 'prepare.sh')
        patch = package / 'patches/0001-anv16s-41-dmi.patch'
        patch.write_text(patch.read_text().replace('"Nitro ANV16-41"', '"THIS SOURCE DOES NOT MATCH"'))
        destination = self.root / 'failed-patch'
        result = run('bash', str(package / 'prepare.sh'), str(destination), UPSTREAM)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((destination / 'dkms.conf').exists())

    def test_wrong_revision_rejected(self):
        package = self.root / 'bad-pin-package'
        shutil.copytree(self.package, package)
        shutil.copy(REPO / 'driver/prepare.sh', package.parent / 'prepare.sh')
        config = package / 'source.conf'
        config.write_text(config.read_text().replace(self.revision, '0' * 40))
        result = run('bash', str(package / 'prepare.sh'), str(self.root / 'failed-pin'), UPSTREAM)
        self.assertNotEqual(result.returncode, 0)

    def test_prepare_existing_identical_tree_is_idempotent(self):
        result = run('bash', str(self.package / 'prepare.sh'), str(self.tree), UPSTREAM)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_all_removed_string_calls_and_rgb_bounds(self):
        self.assertEqual(len(re.findall(r'\bstrncpy\s*\(', self.original)), 3)
        self.assertNotRegex(self.source, r'\bstrncpy\s*\(')

        def function(name):
            start = self.source.index(' static ssize_t ' + name)
            return self.source[start:self.source.index('\n }', start) + 3]
        body = function('four_zoned_rgb_kb_store') + '\n' + function('per_zoned_rgb_kb_store')
        harness = Path(__file__).with_name('helpers') / 'rgb_harness.c'
        source = self.root / 'rgb-test.c'
        source.write_text(harness.read_text().replace('/* DRIVER_RGB_IMPLEMENTATION */', body))
        result = run('cc', '-std=gnu11', '-fsanitize=undefined,bounds', '-o', str(self.root / 'rgb-test'), str(source))
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run(str(self.root / 'rgb-test'))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_fan_c_parser_and_rollback_with_simulated_wmi(self):
        # Compile the actual patched fan implementation, substituting only kernel
        # services and WMI transport. This cannot access an EC or the host sysfs.
        start = self.source.index(' static int cpu_fan_speed = 0;')
        end = self.source.index(' /*\n  * persistent predator states.', start)
        body = self.source[start:end]
        harness = Path(__file__).with_name('helpers') / 'fan_harness.c'
        cfile = self.root / 'fan-test.c'
        cfile.write_text(harness.read_text().replace('/* DRIVER_FAN_IMPLEMENTATION */', body))
        result = run('cc', '-std=gnu11', '-o', str(self.root / 'fan-test'), str(cfile))
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run(str(self.root / 'fan-test'))
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
