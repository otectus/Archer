#!/usr/bin/env python3
"""Load the actual Archer filter in a private PipeWire server, without devices."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'gui'))
import archer_system


def main():
    plugin_dir = Path(os.environ.get('ARCHER_TEST_LADSPA', '/usr/lib/ladspa'))
    assert (plugin_dir / 'librnnoise_ladspa.so').is_file(), 'Install noise-suppression-for-voice for this integration test'
    with tempfile.TemporaryDirectory(prefix='archer-audio-test-') as temporary:
        root = Path(temporary)
        config = root / 'config'
        dropins = config / 'pipewire.conf.d'
        dropins.mkdir(parents=True)
        shutil.copy('/usr/share/pipewire/pipewire.conf', config / 'pipewire.conf')
        source = (ROOT / 'modules/audio-enhance.sh').read_text().split("<<'FILTER_EOF'\n", 1)[1].split('\nFILTER_EOF', 1)[0]
        fragment = dropins / 'archer-noise-suppress.conf'
        fragment.write_text(source)
        archer_system.AUDIO = fragment
        archer_system.AUDIO_PLUGIN = plugin_dir / 'librnnoise_ladspa.so'
        environment = {**os.environ, 'XDG_RUNTIME_DIR': temporary, 'XDG_CONFIG_HOME': str(root / 'user'),
                       'PIPEWIRE_CONFIG_DIR': str(config), 'LADSPA_PATH': str(plugin_dir),
                       'DBUS_SYSTEM_BUS_ADDRESS': 'unix:path=' + str(root / 'no-system-bus')}
        environment.pop('PIPEWIRE_REMOTE', None)
        client_environment = dict(environment)
        client_environment.pop('PIPEWIRE_CONFIG_DIR', None)

        def verify(enabled):
            with open(root / 'pipewire.log', 'w+') as log:
                server = subprocess.Popen(['pipewire'], env=environment, stdout=log, stderr=log)
                try:
                    found, dump_error = False, 'pw-dump never ran'
                    for _ in range(30):
                        if server.poll() is not None:
                            break
                        result = subprocess.run(['pw-dump'], env=client_environment, capture_output=True, text=True, timeout=5)
                        dump_error = result.stderr.strip() or f'pw-dump exited {result.returncode} with {len(result.stdout)} bytes'
                        try:
                            # pw-dump also exits 0 without output when the server drops the connection.
                            objects = json.loads(result.stdout) if result.returncode == 0 else None
                        except ValueError:
                            objects = None
                        if objects is not None:
                            found = any(item.get('info', {}).get('props', {}).get('node.name') == 'archer_noise_suppressed'
                                        and item.get('info', {}).get('props', {}).get('media.class') == 'Audio/Source' for item in objects)
                            if found == enabled:
                                return
                        time.sleep(.1)
                    log.seek(0)
                    raise AssertionError(f'Expected virtual source enabled={enabled}, found={found}, '
                                         f'server exit status={server.poll()}\n{dump_error}\n{log.read()}')
                finally:
                    server.terminate()
                    server.wait(timeout=5)

        verify(True)
        archer_system.operate('audio', 'off')
        verify(False)
        archer_system.operate('audio', 'on')
        verify(True)
    print('PASS: real PipeWire source appears, disappears when disabled, and returns when enabled')


if __name__ == '__main__':
    main()
