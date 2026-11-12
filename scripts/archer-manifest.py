#!/usr/bin/env python3
"""Validate and atomically update Archer's JSON cleanup manifest."""

import argparse
import datetime
import json
from pathlib import Path

from importlib.util import module_from_spec, spec_from_file_location

spec = spec_from_file_location('install_state', Path(__file__).with_name('archer-install-state.py'))
state = module_from_spec(spec)
spec.loader.exec_module(state)
ARRAYS = ('modules_installed', 'files_created', 'dkms_modules', 'packages_installed')


def load(path):
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or 'modules_installed' not in data:
        raise ValueError('Manifest must be an object with modules_installed')
    for key in ARRAYS:
        values = data.get(key, [])
        if not isinstance(values, list) or any(not isinstance(item, str) or any(c.isspace() for c in item) for item in values):
            raise ValueError(f'Invalid manifest field {key}')
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('path', type=Path)
    parser.add_argument('action', choices=('read', 'write', 'retain'))
    parser.add_argument('values', nargs='*')
    args = parser.parse_args()
    if args.action == 'read':
        data = load(args.path)
        value = data.get(args.values[0], '')
        print(' '.join(value) if isinstance(value, list) else value)
        return
    if args.action == 'retain':
        data = load(args.path)
        data['modules_installed'] = args.values[0].split()
    else:
        data = dict(zip(ARRAYS, [list(dict.fromkeys(value.split())) for value in args.values[:4]]))
        data.update(dict(zip(('installer_version', 'acer_model', 'model_family', 'kernel_version'), args.values[4:])))
        data['install_date'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if args.path.is_symlink():
        raise ValueError('Refusing manifest symlink')
    state.atomic(args.path, (json.dumps(data, indent=2) + '\n').encode())


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, IndexError) as error:
        raise SystemExit(f'Manifest update/read failed: {error}')
