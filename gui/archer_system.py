#!/usr/bin/env python3
"""Fixed system-configuration operations dispatched after daemon polkit checks.

The service manager launches this helper outside the hardware daemon's read-only
/etc mount. Inputs are allowlisted; no caller-supplied path or shell is accepted.
"""

import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time

from archer_runtime import run_command

AUDIO = Path('/etc/pipewire/pipewire.conf.d/archer-noise-suppress.conf')
AUDIO_PLUGIN = Path('/usr/lib/ladspa/librnnoise_ladspa.so')
OVERRIDE = Path('/etc/modprobe.d/linuwu-sense.conf')
RECOVERY = Path('/etc/archer/recovery.json')


def recovery_record(request, status, error=''):
    data = {'request': request, 'status': status, 'error': error,
            'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(), 'updated': time.time()}
    atomic(RECOVERY, json.dumps(data))


def checked(args, timeout=90):
    result = run_command(args, timeout)
    if not result.success:
        raise OSError(result.stderr or result.stdout or ('Command timed out' if result.timed_out else f'Command exited {result.returncode}'))
    return result.stdout


def rebuild():
    # Match the installer's all-kernel image contract without shell helpers or sudo.
    if os.access('/usr/bin/mkinitcpio', os.X_OK) and list(Path('/etc/mkinitcpio.d').glob('*.preset')):
        checked(['/usr/bin/mkinitcpio', '-P'], 300)
        limine = shutil.which('limine-mkinitcpio', path='/usr/bin:/bin')
        if limine:
            checked([limine], 120)
    elif os.access('/usr/bin/dracut', os.X_OK):
        checked(['/usr/bin/dracut', '--regenerate-all', '--force'], 300)


def atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.archer-')
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o644)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def operate(action, value=''):
    if action == 'display' and value in ('hybrid', 'integrated', 'nvidia'):
        checked(['/usr/bin/envycontrol', '-s', value], 90)
        rebuild()
        actual = checked(['/usr/bin/envycontrol', '--query'])
        if actual != value:
            raise OSError(f'Graphics readback is {actual!r}, expected {value!r}')
        return {'mode': value, 'reboot_required': True}
    if action in ('override', 'remove-override') and value in ('nitro_v4', 'predator_v4', 'enable_all', ''):
        if action == 'override' and not value:
            raise ValueError('Missing driver parameter')
        if OVERRIDE.is_symlink():
            raise ValueError('Driver override is a symlink')
        if OVERRIDE.exists():
            content = OVERRIDE.read_text()
            legacy = [f'options linuwu_sense {param}=1\n' for param in ('nitro_v4', 'predator_v4', 'enable_all')]
            if 'Archer Compatibility Suite' not in content and content not in legacy:
                raise ValueError('Driver override is administrator-managed')
        if action == 'override':
            atomic(OVERRIDE, f'# Installed by Archer Compatibility Suite\noptions linuwu_sense {value}=1\n')
        else:
            OVERRIDE.unlink(missing_ok=True)
        rebuild()
        return {'reboot_required': True}
    if action == 'audio' and value in ('on', 'off'):
        enabled = value == 'on'
        disabled = AUDIO.with_name(AUDIO.name + '.disabled')
        if not AUDIO_PLUGIN.is_file():
            raise ValueError('Install the rnnoise LADSPA plugin first')
        source, destination = (disabled, AUDIO) if enabled else (AUDIO, disabled)
        if source.exists() and destination.exists():
            raise ValueError('Both enabled and disabled audio configurations exist; resolve the conflict first')
        candidate = source if source.exists() else destination
        if candidate.is_symlink() or not candidate.is_file() or 'archer_noise_capture' not in candidate.read_text():
            raise ValueError('Archer audio configuration is missing or unrecognized')
        if source.exists():
            source.rename(destination)
        return {'noise_suppression': enabled}
    if action in ('restart', 'reload') and (not value or re.fullmatch(r'[a-f0-9]{32}', value)):
        recovery_record(value, 'running')
        try:
            checked(['systemctl', 'stop', 'archer-daemon.service'])
            try:
                if action == 'reload':
                    checked(['modprobe', '-r', 'linuwu_sense'])
                    checked(['modprobe', 'linuwu_sense'], 90)
            finally:
                checked(['systemctl', 'start', 'archer-daemon.service'])
        except OSError as error:
            recovery_record(value, 'failed', str(error))
            raise
        recovery_record(value, 'complete')
        return {}
    raise ValueError('Unsupported system operation')


if __name__ == '__main__':
    try:
        result = operate(*sys.argv[1:])
        print(json.dumps({'success': True, 'data': result}))
    except (OSError, ValueError, TypeError) as error:
        print(json.dumps({'success': False, 'error': str(error)}))
        raise SystemExit(1)
