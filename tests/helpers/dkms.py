#!/usr/bin/env python3
"""Stateful DKMS double: no root, system modules or network."""
import os
import sys
from pathlib import Path

args = sys.argv[1:]
action = args[0]


def arg(flag):
    return args[args.index(flag) + 1] if flag in args else ''


version, kernel = arg('-v'), arg('-k')
state = Path(os.environ['TEST_DKMS_STATE'])
lines = state.read_text().splitlines() if state.exists() else []
with Path(os.environ['TEST_DKMS_LOG']).open('a') as stream:
    stream.write(' '.join(args) + '\n')
if action == os.environ.get('TEST_FAIL_ACTION') and version == os.environ.get('TEST_FAIL_VERSION'):
    sys.exit(1)
prefix = 'linuwu-sense/' + version
selected = [line for line in lines if not version or line.startswith(prefix + ',') or line.startswith(prefix + ':')]
if kernel:
    selected = [line for line in selected if f', {kernel},' in line]
if action == 'status':
    print('\n'.join(selected))
elif action == 'remove':
    state.write_text('\n'.join(line for line in lines if line not in selected) + '\n')
elif action == 'add':
    state.write_text('\n'.join(lines + [prefix + ': added']) + '\n')
elif action in ('build', 'install'):
    row = f'{prefix}, {kernel}, x86_64: '
    previous = next((line for line in lines if line.startswith(row)), '')
    status = 'installed' if action == 'install' or previous.endswith(': installed') else 'built'
    lines = [line for line in lines if not line.startswith(row) and line != prefix + ': added']
    state.write_text('\n'.join(lines + [row + status]) + '\n')
else:
    sys.exit('Unexpected mock DKMS operation')
