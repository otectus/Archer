#!/usr/bin/env python3
"""Track reversible installer service/configuration changes (run via sudo)."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

STATE_ROOT = Path('/var/lib/archer/module-state')
LEGACY_AUDIO_FILTER_HASH = '6a3212b2a70e0c308a4375997a5944f4ff175f6e0e3bd0613cdbfe26f4d21ef4'
LEGACY_AMD_AUDIO_HASH = 'd42c42b029fe9e49f589d9fb06c7c243b665f6639b5ff311760569c73ef25db4'


def legacy_owned(module, path):
    if module == 'audio':
        return digest(path) == LEGACY_AMD_AUDIO_HASH
    if module == 'audio-enhance':
        return digest(path) == LEGACY_AUDIO_FILTER_HASH
    return b'Archer Compatibility Suite' in path.read_bytes()


def atomic(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.archer-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def service(*args, check=True):
    return subprocess.run(['systemctl', *args], capture_output=True, text=True, check=check, timeout=90)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('module')
    parser.add_argument('action', choices=('config', 'remove-config', 'service', 'restore-services'))
    parser.add_argument('args', nargs='*')
    options = parser.parse_args(argv)
    if not options.module.replace('-', '').isalnum():
        parser.error('Invalid module ID')
    state_path = STATE_ROOT / (options.module + '.json')
    if options.action == 'restore-services' and not state_path.exists():
        return
    state = json.loads(state_path.read_text()) if state_path.exists() else {'files': {}, 'services': {}}

    def save():
        atomic(state_path, (json.dumps(state, indent=2) + '\n').encode(), 0o600)

    if options.action == 'config':
        path = Path(options.args[0])
        content = options.args[1].encode() + b'\n'
        if path.is_symlink():
            raise ValueError(f'Refusing symlink configuration {path}')
        previous = state['files'].get(str(path))
        if options.module == 'audio-enhance' and not previous:
            sibling = str(path).removesuffix('.disabled') if str(path).endswith('.disabled') else str(path) + '.disabled'
            if sibling in state['files']:
                previous = dict(state['files'][sibling])
                state['files'][str(path)] = previous
        if previous and digest(path) not in (None, previous['installed'], previous.get('pending')):
            raise ValueError(f'Administrator edited {path}; merge it before retrying')
        if not previous:
            if path.exists() and b'Archer Compatibility Suite' not in path.read_bytes():
                raise ValueError(f'Refusing to overwrite administrator configuration {path}')
            previous = {'installed': None}
            state['files'][str(path)] = previous
        # Save intent before mutation; a failed write remains retryable.
        previous['pending'] = hashlib.sha256(content).hexdigest()
        save()
        atomic(path, content)
        previous['installed'] = previous.pop('pending')
        save()
    elif options.action == 'remove-config':
        path = Path(options.args[0])
        previous = state['files'].get(str(path))
        # The audio toggle renames a file without changing its contents.
        sibling = str(path).removesuffix('.disabled') if str(path).endswith('.disabled') else str(path) + '.disabled'
        if options.module == 'audio-enhance' and not previous:
            previous = state['files'].get(sibling)
        if path.is_symlink():
            raise ValueError(f'Retaining symlink {path}')
        if path.exists():
            if previous:
                if digest(path) not in (previous['installed'], previous.get('pending')):
                    raise ValueError(f'Retaining administrator-edited {path}')
            elif not legacy_owned(options.module, path):
                raise ValueError(f'Retaining unowned configuration {path}')
            path.unlink()
        state['files'].pop(str(path), None)
        if options.module == 'audio-enhance' and not Path(sibling).exists():
            state['files'].pop(sibling, None)
        if state_path.exists():
            save()
    elif options.action == 'service':
        unit, *arguments = options.args
        if unit not in state['services']:
            enabled = service('is-enabled', unit, check=False).stdout.strip()
            active = service('is-active', unit, check=False).stdout.strip() == 'active'
            state['services'][unit] = {'enabled': enabled, 'active': active}
            save()
        service(*arguments, unit)
    else:
        for unit, previous in reversed(list(state['services'].items())):
            enabled = previous['enabled']
            service('unmask', unit)
            if enabled in ('enabled', 'enabled-runtime'):
                service('enable', *(['--runtime'] if enabled.endswith('-runtime') else []), unit)
            elif enabled not in ('static', 'indirect', 'generated', 'alias'):
                service('disable', unit)
            service('start' if previous['active'] else 'stop', unit)
            if enabled in ('masked', 'masked-runtime'):
                service('mask', *(['--runtime'] if enabled.endswith('-runtime') else []), unit)
            del state['services'][unit]
            save()


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error))
