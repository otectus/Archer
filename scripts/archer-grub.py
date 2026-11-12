#!/usr/bin/env python3
"""Edit literal GRUB command-line tokens without evaluating shell configuration.

Invoked through sudo by the installer. Ownership, backup, token provenance and
the configuration update stay inside the same privileged, locked operation.
"""

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import tempfile

OWNED = '# Archer added kernel parameters: '
REVIEW = '# Archer: administrator review required before grub-mkconfig'
TOKEN = re.compile(r'[A-Za-z0-9_.-]+(?:=[A-Za-z0-9_,.:/+@%-]+)?\Z')


def transform(content, action, params):
    requested = params.split()
    if not requested or any(not TOKEN.fullmatch(token) for token in requested):
        raise ValueError('Unsupported kernel parameter syntax')
    if REVIEW in content:
        raise ValueError('Review the repaired GRUB file as root and remove its Archer review marker first')
    lines = content.splitlines(keepends=True)
    indexes = [i for i, line in enumerate(lines)
               if re.match(r'\s*(?:export\s+)?GRUB_CMDLINE_LINUX_DEFAULT\s*=', line)]
    if len(indexes) != 1:
        raise ValueError('Expected exactly one GRUB_CMDLINE_LINUX_DEFAULT assignment')
    index = indexes[0]
    match = re.fullmatch(r'''(\s*(?:export\s+)?GRUB_CMDLINE_LINUX_DEFAULT\s*=\s*)(["'])(.*)\2([ \t]*(?:\#.*)?)(?:\n)?''', lines[index])
    if not match or any(char in match[3] for char in '$`\\\'"\n\r'):
        raise ValueError('GRUB command line must be one literal quoted value without shell expansion')
    tokens = match[3].split()
    owned_lines = [line for line in lines if line.startswith(OWNED)]
    if len(owned_lines) > 1:
        raise ValueError('Duplicate Archer parameter records')
    owned = json.loads(owned_lines[0][len(OWNED):]) if owned_lines else []
    if not isinstance(owned, list) or any(not isinstance(token, str) or not TOKEN.fullmatch(token) for token in owned):
        raise ValueError('Invalid Archer parameter record')
    before = list(tokens)
    for token in requested:
        if action == 'add' and token not in tokens:
            tokens.append(token)
            if token not in owned:
                owned.append(token)
        elif action == 'remove' and token in owned:
            tokens = [item for item in tokens if item != token]
            owned.remove(token)
    if tokens == before:
        return content
    lines[index] = f'{match[1]}{match[2]}{" ".join(tokens)}{match[2]}{match[4]}\n'
    lines = [line for line in lines if not line.startswith(OWNED)]
    if lines and not lines[-1].endswith('\n'):
        lines[-1] += '\n'
    if owned:
        lines.append(OWNED + json.dumps(owned) + '\n')
    return ''.join(lines)


def atomic_write(path, content, mode=0o644):
    fd, name = tempfile.mkstemp(prefix='.archer-grub-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), mode)
            if os.geteuid() == 0:
                os.fchown(stream.fileno(), 0, 0)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def edit(path, action, params):
    # Lock the trusted directory inode, which survives atomic file replacement.
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(directory, fcntl.LOCK_EX)
        parent = os.fstat(directory)
        if parent.st_uid != os.geteuid() or parent.st_mode & 0o022:
            raise ValueError('GRUB directory must be owned by the invoking administrator and not writable by others')
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd) as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError('GRUB configuration must be a regular file')
            content = stream.read()
        untrusted = metadata.st_uid != os.geteuid() or bool(metadata.st_mode & 0o022)
        if untrusted:
            updated = REVIEW + '\n' + content
        elif action == 'check':
            if REVIEW in content:
                raise ValueError('Review the repaired GRUB file as root and remove its Archer review marker first')
            updated = content
        else:
            updated = transform(content, action, params)
        if updated == content and stat.S_IMODE(metadata.st_mode) == 0o644 and metadata.st_gid == os.getegid():
            return False
        fd, backup = tempfile.mkstemp(prefix='grub.archer-backup-', dir=path.parent)
        with os.fdopen(fd, 'w') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        atomic_write(path, updated)
        os.fsync(directory)
        print(f'GRUB backup: {backup}')
        if untrusted:
            raise ValueError('Repaired unsafe GRUB ownership/mode. Administrator review is required; configuration was not executed')
        return updated != content
    finally:
        os.close(directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('add', 'remove', 'check'))
    parser.add_argument('params', nargs='?', default='')
    parser.add_argument('--config', type=Path, default=Path('/etc/default/grub'))
    args = parser.parse_args()
    try:
        return 0 if edit(args.config, args.action, args.params) else 3
    except (OSError, ValueError) as error:
        parser.exit(1, f'GRUB update refused: {error}\n')


if __name__ == '__main__':
    raise SystemExit(main())
