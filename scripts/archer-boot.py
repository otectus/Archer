#!/usr/bin/env python3
"""Fail-closed boot-source and initramfs adapter for Archer."""

import argparse
import glob
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


MIN_FREE = 32 * 1024 * 1024
TOKEN = re.compile(r'[A-Za-z0-9_.-]+(?:=[A-Za-z0-9_,.:/+@%-]+)?\Z')
ASSIGNMENT = re.compile(r'^\s*([A-Za-z0-9_]+)\s*=\s*(["\'])([^"\']*)\2\s*(?:#.*)?$')


def rooted(root, value):
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError(f'Unsafe boot target: {value}')
    return root / str(path).lstrip('/')


def executable(root, absolute):
    candidate = rooted(root, absolute)
    return candidate if candidate.is_file() and os.access(candidate, os.X_OK) else None


def literal_assignments(path):
    values = {}
    for line in path.read_text(errors='strict').splitlines():
        match = ASSIGNMENT.fullmatch(line)
        if match:
            values[match.group(1)] = match.group(3)
    return values


def installed_kernels(root):
    seen = set()
    kernels = []
    for base in (root / 'usr/lib/modules', root / 'lib/modules'):
        if not base.is_dir():
            continue
        for directory in sorted(base.iterdir()):
            if not directory.is_dir() or directory.name in seen:
                continue
            if not ((directory / 'vmlinuz').is_file() or (directory / 'kernel').is_dir()
                    or (directory / 'pkgbase').is_file()):
                continue
            seen.add(directory.name)
            pkgbase = (directory / 'pkgbase').read_text().strip() if (directory / 'pkgbase').is_file() else None
            kernels.append({'version': directory.name, 'pkgbase': pkgbase, 'directory': str(directory)})
    return kernels


def boot_roots(root):
    return [path for path in (root / 'boot', root / 'efi', root / 'boot/efi') if path.is_dir()]


def find_boot_sources(root):
    sources = []
    grub = root / 'etc/default/grub'
    if grub.is_file():
        sources.append({'name': 'grub', 'source': str(grub)})
    cmdline = root / 'etc/kernel/cmdline'
    if cmdline.is_file():
        sources.append({'name': 'kernel-cmdline', 'source': str(cmdline)})
    for base in boot_roots(root):
        loader = base / 'loader/loader.conf'
        if loader.is_file():
            entries = sorted(str(path) for path in (base / 'loader/entries').glob('*.conf'))
            sources.append({'name': 'systemd-boot', 'source': str(loader), 'entries': entries})
    limine_candidates = []
    for relative in ('boot/limine.conf', 'boot/limine/limine.conf', 'etc/limine-entry-tool.conf'):
        path = root / relative
        if path.is_file():
            limine_candidates.append(str(path))
    if limine_candidates:
        sources.append({'name': 'limine', 'source': limine_candidates[0],
                        'ambiguous': len(limine_candidates) != 1})
    refind_linux = []
    refind_config = []
    for base in boot_roots(root):
        refind_linux.extend(str(path) for path in base.glob('**/refind_linux.conf'))
        refind_config.extend(str(path) for path in base.glob('EFI/refind/refind.conf'))
    refind_linux = sorted(set(refind_linux))
    refind_config = sorted(set(refind_config))
    if refind_linux or refind_config:
        sources.append({'name': 'refind', 'source': refind_linux[0] if refind_linux else refind_config[0],
                        'linux_options': refind_linux, 'configs': refind_config,
                        'ambiguous': len(refind_linux) != 1})
    return sources


def inspect(root):
    etc = root / 'etc'
    configured = []
    presets = sorted(glob.glob(str(etc / 'mkinitcpio.d/*.preset')))
    if presets:
        configured.append({'name': 'mkinitcpio', 'evidence': presets})
    dracut_evidence = ([etc / 'dracut.conf'] if (etc / 'dracut.conf').is_file() else [])
    dracut_evidence.extend(sorted((etc / 'dracut.conf.d').glob('*.conf')))
    dracut_evidence.extend(path for path in (
        root / 'usr/lib/kernel/install.d/50-dracut.install',
        root / 'usr/share/libalpm/hooks/90-dracut-install.hook') if path.is_file())
    if dracut_evidence:
        configured.append({'name': 'dracut', 'evidence': [str(path) for path in dracut_evidence]})
    booster_config = etc / 'booster.yaml'
    if booster_config.is_file():
        hooks = sorted(glob.glob(str(root / 'usr/lib/kernel/install.d/*booster*.install')))
        configured.append({'name': 'booster', 'evidence': [str(booster_config)], 'hooks': hooks})
    return {'schema': 2, 'root': str(root), 'kernels': installed_kernels(root),
            'initramfs': configured, 'boot': find_boot_sources(root)}


def resolve_generator(root):
    configured = inspect(root)['initramfs']
    if len(configured) != 1:
        names = ', '.join(item['name'] for item in configured) or 'none'
        raise ValueError(f'Expected one configured initramfs generator; found {names}')
    name = configured[0]['name']
    command = executable(root, {'mkinitcpio': '/usr/bin/mkinitcpio', 'dracut': '/usr/bin/dracut',
                                'booster': '/usr/bin/booster'}[name])
    if not command:
        raise ValueError(f'{name} is configured but its executable is unavailable')
    return name, command


def mkinitcpio_plan(root, command):
    presets = sorted((root / 'etc/mkinitcpio.d').glob('*.preset'))
    kernels = installed_kernels(root)
    by_pkgbase = {item['pkgbase']: item['version'] for item in kernels if item['pkgbase']}
    targets = []
    covered = set()
    for preset in presets:
        assignments = literal_assignments(preset)
        preset_targets = [(key, rooted(root, value)) for key, value in assignments.items()
                          if (key.endswith('_image') or key.endswith('_uki')) and value]
        if not preset_targets:
            raise ValueError(f'No literal image or UKI target was found in {preset}')
        kernel = by_pkgbase.get(preset.stem, preset.stem)
        if preset.stem in by_pkgbase:
            covered.add(kernel)
        for key, target in preset_targets:
            targets.append({'kernel': kernel, 'path': target,
                            'kind': 'uki' if key.endswith('_uki') else 'initramfs'})
    missing = [item['version'] for item in kernels if item['pkgbase'] and item['version'] not in covered]
    unknown = [item['version'] for item in kernels if not item['pkgbase']]
    if missing or unknown:
        raise ValueError('Installed kernels lack an unambiguous mkinitcpio preset mapping: '
                         + ', '.join(missing + unknown))
    return [([str(command), '-P'], targets)]


def dracut_plan(root, command):
    kernels = installed_kernels(root)
    if not kernels:
        raise ValueError('No installed kernel target was found for dracut')
    return [([str(command), '--force', str(root / 'boot' / f"initramfs-{kernel['version']}.img"),
              kernel['version']],
             [{'kernel': kernel['version'], 'path': root / 'boot' / f"initramfs-{kernel['version']}.img",
               'kind': 'initramfs'}]) for kernel in kernels]


def entry_token(root):
    for path in (root / 'etc/kernel/entry-token', root / 'etc/machine-id'):
        if path.is_file():
            value = path.read_text().strip()
            if re.fullmatch(r'[A-Za-z0-9_.-]+', value):
                return value
    return None


def booster_plan(root, command):
    hooks = [path for path in sorted((root / 'usr/lib/kernel/install.d').glob('*booster*.install'))
             if path.is_file() and os.access(path, os.X_OK)]
    if len(hooks) != 1:
        raise ValueError(f'Expected one executable Booster kernel-install hook; found {len(hooks)}')
    kernels = installed_kernels(root)
    token = entry_token(root)
    if not kernels or not token:
        raise ValueError('Booster requires installed kernels and an authoritative kernel entry token')
    plans = []
    for kernel in kernels:
        entry_dirs = [base / token / kernel['version'] for base in boot_roots(root)
                      if (base / token / kernel['version']).is_dir()]
        image_candidates = (Path(kernel['directory']) / 'vmlinuz',
                            root / 'boot' / f"vmlinuz-{kernel['version']}")
        images = [path for path in image_candidates if path.is_file()]
        if len(entry_dirs) != 1 or len(images) != 1:
            raise ValueError(f"Booster target for {kernel['version']} is ambiguous or absent")
        target = entry_dirs[0] / 'initrd'
        # The configured executable proves Booster is present, but the
        # distribution hook owns its exact build invocation and output path.
        _ = command
        plans.append(([str(hooks[0]), 'add', kernel['version'], str(entry_dirs[0]), str(images[0])],
                      [{'kernel': kernel['version'], 'path': target, 'kind': 'initramfs'}]))
    return plans


def content_fingerprint(path, *, include_inode=False):
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        metadata = os.fstat(stream.fileno())
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    # mkinitcpio can atomically install a newly generated, byte-identical
    # image. An updated timestamp alone does not establish a rebuild.
    identity = (metadata.st_dev, metadata.st_ino) if include_inode else None
    return (metadata.st_size, digest.hexdigest(), identity)


def mount_for(root, path):
    resolved = path.resolve(strict=False)
    best = None
    try:
        fixture = os.environ.get('ARCHER_BOOT_MOUNTINFO') if root != Path('/') else None
        lines = Path(fixture or '/proc/self/mountinfo').read_text().splitlines()
    except OSError as error:
        raise ValueError(f'Cannot inspect mounted filesystems: {error}') from error
    for line in lines:
        fields = line.split()
        if len(fields) < 6:
            continue
        candidate = Path(fields[4].replace('\\040', ' ')).resolve(strict=False)
        try:
            resolved.relative_to(candidate)
        except ValueError:
            continue
        separator = fields.index('-') if '-' in fields else None
        filesystem = fields[separator + 1] if separator is not None and len(fields) > separator + 1 else 'unknown'
        if best is None or len(candidate.parts) > len(best['mountpoint'].parts):
            best = {'mountpoint': candidate, 'filesystem': filesystem}
    if best is None:
        raise ValueError(f'No mounted filesystem contains boot target {path}')
    return best


def verify_storage(targets):
    mounts = {}
    for item in targets:
        parent = item['path'].parent
        if not parent.is_dir():
            raise ValueError(f'Boot target directory is unavailable: {parent}')
        mount = mount_for(item['root'], parent)
        mounts[str(mount['mountpoint'])] = parent
        if item['kind'] == 'uki' or item['path'].suffix.lower() == '.efi':
            relative = item['path'].relative_to(item['root'])
            anchor = (item['root'] / 'boot/efi' if relative.parts[:2] == ('boot', 'efi')
                      else item['root'] / relative.parts[0])
            if mount['mountpoint'] != anchor.resolve(strict=False):
                raise ValueError(f'EFI/UKI target filesystem is not mounted at {anchor}')
            if relative.parts[0] == 'efi' or relative.parts[:2] == ('boot', 'efi'):
                if mount['filesystem'] not in ('vfat', 'fat', 'msdos'):
                    raise ValueError(f'ESP at {anchor} is not a FAT filesystem')
    for mount, path in mounts.items():
        free = shutil.disk_usage(path).free
        if free < MIN_FREE:
            raise ValueError(f'Boot filesystem {mount} has only {free} bytes free; at least 32 MiB is required')


def secure_boot_enabled(root):
    variables = list((root / 'sys/firmware/efi/efivars').glob('SecureBoot-*'))
    return any(path.read_bytes()[-1:] == b'\x01' for path in variables)


def verify_signature(root, artifact):
    verifier = executable(root, '/usr/bin/sbverify')
    if not verifier:
        raise ValueError('Secure Boot is enabled but sbverify is unavailable')
    result = subprocess.run([str(verifier), '--list', str(artifact)], capture_output=True,
                            text=True, timeout=30)
    listing = result.stdout + result.stderr
    # sbverify may exit zero for unsigned PE images; require certificate proof.
    signed = (result.returncode == 0
              and re.search(r'(?im)^image signature certificates:\s*$', listing)
              and re.search(r'(?im)^\s*-\s*subject:', listing))
    if not signed:
        raise ValueError(f'Generated EFI artifact has no verifiable signature certificate: {artifact}')


def rebuild(root, dry_run=False):
    name, command = resolve_generator(root)
    plans = {'mkinitcpio': mkinitcpio_plan, 'dracut': dracut_plan,
             'booster': booster_plan}[name](root, command)
    targets = [item for _, group in plans for item in group]
    for item in targets:
        item['root'] = root
    paths = [item['path'] for item in targets]
    if len(paths) != len(set(paths)):
        raise ValueError('Multiple selected kernels resolve to the same boot artifact')
    verify_storage(targets)
    if dry_run:
        print(json.dumps({'adapter': name, 'commands': [argv for argv, _ in plans],
                          'targets': [{key: str(value) if isinstance(value, Path) else value
                                       for key, value in item.items()}
                                      for item in targets]}, sort_keys=True))
        return
    before = {path: content_fingerprint(path, include_inode=True) for path in paths}
    for argv, _ in plans:
        result = subprocess.run(argv, timeout=300)
        if result.returncode:
            raise ValueError(f'{name} exited with status {result.returncode}')
    limine = executable(root, '/usr/bin/limine-mkinitcpio')
    if name == 'mkinitcpio' and limine and any(item['name'] == 'limine' for item in inspect(root)['boot']):
        result = subprocess.run([str(limine)], timeout=120)
        if result.returncode:
            raise ValueError(f'limine-mkinitcpio exited with status {result.returncode}')
    for item in targets:
        after = content_fingerprint(item['path'], include_inode=True)
        if after is None or after[0] == 0:
            raise ValueError(f"{name} returned success but required artifact is absent or empty: {item['path']}")
        if after == before[item['path']]:
            raise ValueError(f"{name} returned success but required artifact did not change: {item['path']}")
    if secure_boot_enabled(root):
        for item in targets:
            if item['kind'] == 'uki' or item['path'].suffix.lower() == '.efi':
                verify_signature(root, item['path'])


def parse_tokens(params):
    tokens = params.split()
    if not tokens or any(not TOKEN.fullmatch(token) for token in tokens):
        raise ValueError('Unsupported kernel parameter syntax')
    return tokens


def update_values(values, requested, action, owned):
    parsed = [value.split() for value in values]
    if any(not TOKEN.fullmatch(token) for tokens in parsed for token in tokens):
        raise ValueError('Existing kernel command line is not a literal token list')
    result_owned = set(owned)
    for token in requested:
        presence = [token in tokens for tokens in parsed]
        if action == 'add':
            if any(presence) and not all(presence):
                raise ValueError(f'Kernel parameter {token} is inconsistent across boot entries')
            if not any(presence):
                for tokens in parsed:
                    tokens.append(token)
                result_owned.add(token)
        elif token in owned:
            for tokens in parsed:
                while token in tokens:
                    tokens.remove(token)
            result_owned.discard(token)
    return [' '.join(tokens) for tokens in parsed], result_owned


def atomic_write(path, content):
    fd, temporary = tempfile.mkstemp(prefix='.archer-boot-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o644)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def commandline_owner(root):
    sources = find_boot_sources(root)
    cmdline = [item for item in sources if item['name'] == 'kernel-cmdline']
    consumers = [item for item in sources if item['name'] in ('systemd-boot', 'limine', 'refind')]
    uki_configured = any(key.endswith('_uki') and value
                         for preset in (root / 'etc/mkinitcpio.d').glob('*.preset')
                         for key, value in literal_assignments(preset).items())
    if cmdline and consumers and (uki_configured or not any(item['name'] == 'systemd-boot'
                                                            and item['entries'] for item in consumers)):
        return {'name': 'kernel-cmdline', 'paths': [Path(cmdline[0]['source'])]}
    candidates = []
    for item in sources:
        if item['name'] == 'grub':
            candidates.append({'name': 'grub', 'paths': [Path(item['source'])]})
        elif item['name'] == 'systemd-boot' and item['entries']:
            candidates.append({'name': 'systemd-boot', 'paths': [Path(path) for path in item['entries']]})
        elif item['name'] == 'limine' and not item.get('ambiguous'):
            candidates.append({'name': 'limine', 'paths': [Path(item['source'])]})
        elif item['name'] == 'refind' and not item.get('ambiguous') and item['linux_options']:
            candidates.append({'name': 'refind', 'paths': [Path(item['linux_options'][0])]})
    if len(candidates) != 1:
        names = ', '.join(item['name'] for item in candidates) or 'none'
        raise ValueError(f'Expected one authoritative non-GRUB kernel-command-line owner; found {names}')
    return candidates[0]


def edit_grub(root, action, params, dry_run):
    helper = Path(__file__).with_name('archer-grub.py')
    config = root / 'etc/default/grub'
    output = root / 'boot/grub/grub.cfg'
    generator = executable(root, '/usr/bin/grub-mkconfig')
    if not helper.is_file() or not generator or not output.parent.is_dir():
        raise ValueError('GRUB requires archer-grub.py, grub-mkconfig, and an existing /boot/grub directory')
    edit_argv = [os.environ.get('PYTHON', 'python3'), str(helper), action, params,
                 '--config', str(config)]
    generate_argv = [str(generator), '-o', str(output)]
    if dry_run:
        print(json.dumps({'owner': 'grub', 'commands': [edit_argv, generate_argv]}, sort_keys=True))
        return True
    config_before = config.read_bytes()
    output_before = output.read_bytes() if output.is_file() else None
    output_fingerprint = content_fingerprint(output)
    result = subprocess.run(edit_argv, timeout=30)
    if result.returncode == 3:
        return False
    if result.returncode:
        raise ValueError(f'GRUB source update exited with status {result.returncode}')
    try:
        result = subprocess.run(generate_argv, timeout=120)
        if result.returncode:
            raise ValueError(f'grub-mkconfig exited with status {result.returncode}')
        if not output.is_file() or output.stat().st_size == 0:
            raise ValueError('grub-mkconfig returned success without a non-empty grub.cfg')
        if content_fingerprint(output) == output_fingerprint:
            raise ValueError('grub-mkconfig returned success without changing grub.cfg')
    except Exception:
        atomic_write(config, config_before.decode())
        if output_before is None:
            output.unlink(missing_ok=True)
        else:
            atomic_write(output, output_before.decode())
        raise
    return True


def edit_commandline(root, action, params, dry_run=False):
    requested = parse_tokens(params)
    owner = commandline_owner(root)
    if owner['name'] == 'grub':
        return edit_grub(root, action, params, dry_run)
    state_path = root / 'var/lib/archer/boot-arguments.json'
    state_original = state_path.read_bytes() if state_path.is_file() else None
    state = json.loads(state_path.read_text()) if state_path.is_file() else {'schema': 1, 'owners': {}}
    record = state.setdefault('owners', {}).setdefault(owner['name'], {'sources': {}})
    source_names = [str(path) for path in owner['paths']]
    if set(record.get('sources', {})) - set(source_names):
        raise ValueError('Recorded Archer kernel arguments belong to different boot sources')
    originals = {}
    updates = {}
    for path in owner['paths']:
        content = path.read_text()
        originals[path] = content
        path_owned = set(record.setdefault('sources', {}).get(str(path), []))
        if owner['name'] == 'kernel-cmdline':
            lines = content.splitlines()
            if len(lines) != 1:
                raise ValueError('/etc/kernel/cmdline must contain exactly one literal line')
            values, path_owned = update_values([lines[0]], requested, action, path_owned)
            updates[path] = values[0] + '\n'
        elif owner['name'] == 'systemd-boot':
            pattern = re.compile(r'^(\s*options\s+)(.*)$', re.IGNORECASE)
            lines = content.splitlines(keepends=True)
            indexes = [index for index, line in enumerate(lines) if pattern.match(line.rstrip('\n'))]
            if len(indexes) != 1:
                raise ValueError(f'Expected exactly one options line in {path}')
            match = pattern.match(lines[indexes[0]].rstrip('\n'))
            values, path_owned = update_values([match.group(2)], requested, action, path_owned)
            lines[indexes[0]] = match.group(1) + values[0] + '\n'
            updates[path] = ''.join(lines)
        elif owner['name'] == 'limine':
            lines = content.splitlines(keepends=True)
            matches = []
            for index, line in enumerate(lines):
                plain = line.rstrip('\n')
                tool_match = re.fullmatch(r'(\s*KERNEL_CMDLINE\[[^]]+\]\s*=\s*")([^"]*)("\s*(?:#.*)?)',
                                          plain, re.IGNORECASE)
                config_match = re.fullmatch(r'(\s*CMDLINE\s*=\s*)(.*)', plain, re.IGNORECASE)
                match = tool_match or config_match
                if match:
                    matches.append((index, match, bool(tool_match), line.endswith('\n')))
            if not matches:
                raise ValueError(f'No authoritative command-line assignment was found in {path}')
            current_values = [match.group(2) for _, match, _, _ in matches]
            values, path_owned = update_values(current_values, requested, action, path_owned)
            for value, (index, match, tool_style, newline) in zip(values, matches):
                lines[index] = (match.group(1) + value + (match.group(3) if tool_style else '')
                                + ('\n' if newline else ''))
            updates[path] = ''.join(lines)
        else:
            lines = content.splitlines(keepends=True)
            matches = []
            for index, line in enumerate(lines):
                if not line.strip() or line.lstrip().startswith('#'):
                    continue
                match = re.fullmatch(r'(\s*"[^"]+"\s+")([^"]*)("\s*(?:#.*)?\n?)', line)
                if not match:
                    raise ValueError(f'Unsupported refind_linux.conf syntax in {path}')
                matches.append((index, match))
            if not matches:
                raise ValueError(f'No boot entry was found in {path}')
            current_values = [match.group(2) for _, match in matches]
            values, path_owned = update_values(current_values, requested, action, path_owned)
            for value, (index, match) in zip(values, matches):
                lines[index] = match.group(1) + value + match.group(3)
            updates[path] = ''.join(lines)
        record['sources'][str(path)] = sorted(path_owned)
    if dry_run:
        print(json.dumps({'owner': owner['name'], 'paths': [str(path) for path in owner['paths']],
                          'action': action, 'tokens': requested}, sort_keys=True))
        return
    if all(updates[path] == originals[path] for path in updates):
        return False
    state_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        for path, content in updates.items():
            atomic_write(path, content)
        atomic_write(state_path, json.dumps(state, indent=2, sort_keys=True) + '\n')
        if owner['name'] == 'kernel-cmdline':
            rebuild(root)
    except Exception:
        for path, content in originals.items():
            atomic_write(path, content)
        if state_original is None:
            state_path.unlink(missing_ok=True)
        else:
            atomic_write(state_path, state_original.decode())
        raise
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/'))
    commands = parser.add_subparsers(dest='action', required=True)
    commands.add_parser('inspect')
    rebuild_parser = commands.add_parser('rebuild')
    rebuild_parser.add_argument('--dry-run', action='store_true')
    args_parser = commands.add_parser('kernel-args')
    args_parser.add_argument('operation', choices=('add', 'remove'))
    args_parser.add_argument('params')
    args_parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    if args.action == 'inspect':
        print(json.dumps(inspect(args.root), indent=2, sort_keys=True))
    elif args.action == 'rebuild':
        rebuild(args.root, args.dry_run)
    else:
        return 0 if edit_commandline(args.root, args.operation, args.params, args.dry_run) is not False else 3


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as error:
        raise SystemExit(f'Boot adapter refused: {error}')
