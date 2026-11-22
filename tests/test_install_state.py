import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
HELPER = REPO / 'scripts/archer-install-state.py'


class OpenRCStateTests(unittest.TestCase):
    def test_openrc_lifecycle_and_external_change_guard(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            binary = root / 'bin'
            binary.mkdir()
            enabled = root / 'enabled'
            active = root / 'active'
            (binary / 'rc-update').write_text(
                '#!/bin/sh\n'
                'if [ "$1" = show ]; then [ -e "$ENABLED" ] && echo "fwupd | default"; exit 0; fi\n'
                'if [ "$1" = add ]; then touch "$ENABLED"; else rm -f "$ENABLED"; fi\n'
            )
            (binary / 'rc-service').write_text(
                '#!/bin/sh\n'
                'case "$2" in status) [ -e "$ACTIVE" ];; start|restart) touch "$ACTIVE";; stop) rm -f "$ACTIVE";; esac\n'
            )
            for path in binary.iterdir():
                path.chmod(0o755)
            state_root = root / 'state'
            env = {**os.environ, 'PATH': str(binary) + os.pathsep + os.environ['PATH'],
                   'ARCHER_INIT_ADAPTER': 'openrc', 'ARCHER_MODULE_STATE_ROOT': str(state_root),
                   'ENABLED': str(enabled), 'ACTIVE': str(active)}
            command = ['python3', str(HELPER), 'firmware']
            result = subprocess.run([*command, 'service', '--', 'fwupd.service', 'enable', '--now'],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(enabled.exists())
            self.assertTrue(active.exists())
            record = json.loads((state_root / 'firmware.json').read_text())['services']['fwupd.service']
            self.assertEqual(record['adapter'], 'openrc')
            self.assertEqual(record['installed'], {'enabled': 'enabled', 'active': True})

            active.unlink()  # another owner changed the service after Archer
            result = subprocess.run([*command, 'restore-services'], env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('externally changed', result.stderr)
            active.touch()
            result = subprocess.run([*command, 'restore-services'], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(enabled.exists())
            self.assertFalse(active.exists())
            self.assertFalse((state_root / 'firmware.json').exists())


if __name__ == '__main__':
    unittest.main()
