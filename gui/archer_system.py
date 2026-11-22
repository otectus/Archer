#!/usr/bin/env python3
"""Fixed system-configuration operations dispatched after daemon polkit checks.

The service manager launches this helper outside the hardware daemon's read-only
/etc mount. Inputs are allowlisted; no caller-supplied path or shell is accepted.
"""

import base64
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time

from archer_runtime import run_command
from archer_graphics import build_plan, verify_kernel_modules
from archer_inventory import collect_inventory
from archer_capabilities import qualified_driver_overrides

AUDIO = Path('/etc/pipewire/pipewire.conf.d/archer-noise-suppress.conf')
AUDIO_PLUGIN = Path('/usr/lib/ladspa/librnnoise_ladspa.so')
OVERRIDE = Path('/etc/modprobe.d/linuwu-sense.conf')
RECOVERY = Path('/etc/archer/recovery.json')
ENVY_ROOT = Path('/')
ENVY_STATE = Path('/var/lib/archer/envycontrol-ownership.json')
ENVY_LOCK = Path('/run/archer/envycontrol.lock')
ENVY_MAX_FILE = 1024 * 1024
ENVY_MAX_TOTAL = 4 * 1024 * 1024
ENVY_MAX_JOURNAL = 12 * 1024 * 1024
ENVY_PATHS = (
    'etc/modprobe.d/blacklist-nvidia.conf',
    'etc/modprobe.d/nvidia.conf',
    'etc/X11/xorg.conf',
    'etc/X11/xorg.conf.d/10-nvidia.conf',
    'etc/X11/xorg.conf.d/90-nvidia.conf',
    'etc/udev/rules.d/50-remove-nvidia.rules',
    'etc/udev/rules.d/80-nvidia-pm.rules',
    # EnvyControl 3.5.2 calls these legacy /lib paths. Track the Arch
    # package location first and de-duplicate the /lib alias by real parent.
    'usr/lib/udev/rules.d/50-remove-nvidia.rules',
    'usr/lib/udev/rules.d/80-nvidia-pm.rules',
    'lib/udev/rules.d/50-remove-nvidia.rules',
    'lib/udev/rules.d/80-nvidia-pm.rules',
    'etc/lightdm/nvidia.sh',
    'etc/lightdm/lightdm.conf.d/20-nvidia.conf',
    'usr/share/sddm/scripts/Xsetup',
    'usr/share/sddm/scripts/Xsetup.bak',
    'var/cache/envycontrol/cache.json',
)
PERSISTENCED = 'nvidia-persistenced.service'
SERVICE_STATES = {
    'enabled', 'enabled-runtime', 'linked', 'linked-runtime', 'alias',
    'masked', 'masked-runtime', 'static', 'indirect', 'disabled',
    'generated', 'transient', 'not-found',
}


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
    packaged = Path(__file__).with_name('archer-boot.py')
    source = Path(__file__).resolve().parents[1] / 'scripts/archer-boot.py'
    helper = packaged if packaged.is_file() else source
    if not helper.is_file():
        raise OSError('Archer boot adapter is unavailable')
    checked(['/usr/bin/python3', str(helper), 'rebuild'], 330)


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


def _fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_private(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.archer-envy-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        _fsync_directory(path.parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def _envy_lock():
    ENVY_LOCK.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(ENVY_LOCK, flags, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError('EnvyControl transaction lock is not a regular file')
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('Another EnvyControl transaction is already running') from error
        yield
    finally:
        os.close(descriptor)


def _envy_files():
    root = ENVY_ROOT.resolve()
    result = {}
    seen = set()
    for relative in ENVY_PATHS:
        parent = (root / Path(relative).parent).resolve(strict=False)
        if not parent.is_relative_to(root):
            raise ValueError('EnvyControl ownership path escapes its root')
        path = parent / Path(relative).name
        canonical = str(path)
        if canonical in seen:
            continue
        seen.add(canonical)
        result['/' + relative] = path
    return result


def _file_identity(value):
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _read_regular(path, maximum):
    before = os.lstat(path)
    if stat.S_ISLNK(before.st_mode):
        raise ValueError(f'Refusing symlink in EnvyControl ownership path: {path}')
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f'Refusing non-file EnvyControl ownership path: {path}')
    if before.st_size > maximum:
        raise ValueError(f'EnvyControl ownership file is unexpectedly large: {path}')
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise OSError(f'EnvyControl ownership file changed while reading: {path}')
        chunks = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b''.join(chunks)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if len(content) > maximum:
        raise ValueError(f'EnvyControl ownership file is unexpectedly large: {path}')
    if _file_identity(opened) != _file_identity(after) or len(content) != after.st_size:
        raise OSError(f'EnvyControl ownership file changed while reading: {path}')
    return content, after


def _snapshot(path):
    try:
        content, after = _read_regular(path, ENVY_MAX_FILE)
    except FileNotFoundError:
        return {'exists': False}
    return {'exists': True, 'sha256': hashlib.sha256(content).hexdigest(),
            'data': base64.b64encode(content).decode('ascii'), 'size': len(content),
            'mode': stat.S_IMODE(after.st_mode), 'uid': after.st_uid, 'gid': after.st_gid}


def _persistenced_state():
    result = run_command(['/usr/bin/systemctl', 'is-enabled', PERSISTENCED], 10)
    value = result.stdout.splitlines()[0].strip() if result.stdout else ''
    if value not in SERVICE_STATES:
        raise OSError('Could not determine nvidia-persistenced enablement')
    return value


def _capture_envy_state():
    files = {name: _snapshot(path) for name, path in _envy_files().items()}
    if sum(item.get('size', 0) for item in files.values()) > ENVY_MAX_TOTAL:
        raise ValueError('EnvyControl ownership files exceed the recovery limit')
    return {'files': files, 'persistenced': _persistenced_state()}


def _same_snapshot(left, right):
    fields = ('exists', 'sha256', 'size', 'mode', 'uid', 'gid')
    return all(left.get(field) == right.get(field) for field in fields)


def _same_envy_state(left, right):
    return (left.get('persistenced') == right.get('persistenced')
            and left.get('files', {}).keys() == right.get('files', {}).keys()
            and all(_same_snapshot(left['files'][name], right['files'][name]) for name in left['files']))


def _validate_envy_snapshot(snapshot, expected_files):
    if not isinstance(snapshot, dict) or snapshot.get('persistenced') not in SERVICE_STATES:
        raise ValueError('Invalid EnvyControl ownership state')
    files = snapshot.get('files')
    if not isinstance(files, dict) or set(files) != expected_files:
        raise ValueError('Incomplete EnvyControl ownership state')
    for item in files.values():
        if not isinstance(item, dict) or not isinstance(item.get('exists'), bool):
            raise ValueError('Invalid EnvyControl file ownership state')
        if not item['exists']:
            if set(item) != {'exists'}:
                raise ValueError('Invalid absent-file ownership state')
            continue
        try:
            content = base64.b64decode(item['data'], validate=True)
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError('Invalid EnvyControl recovery content') from error
        if (len(content) != item.get('size') or len(content) > ENVY_MAX_FILE
                or hashlib.sha256(content).hexdigest() != item.get('sha256')
                or not isinstance(item.get('mode'), int) or not 0 <= item['mode'] <= 0o7777
                or not isinstance(item.get('uid'), int) or not isinstance(item.get('gid'), int)):
            raise ValueError('Invalid EnvyControl recovery content')


def _load_envy_state(required=True):
    try:
        content, _metadata = _read_regular(ENVY_STATE, ENVY_MAX_JOURNAL)
    except FileNotFoundError:
        if required:
            raise ValueError('No Archer-owned EnvyControl state exists; refusing broad cleanup')
        return None
    try:
        state = json.loads(content.decode())
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('EnvyControl ownership journal is unreadable') from error
    expected = set(_envy_files())
    if (not isinstance(state, dict) or state.get('schema_version') != 1
            or state.get('status') not in ('applying', 'complete', 'recovery_required',
                                           'restoring', 'restore_rebuild_required')):
        raise ValueError('Invalid EnvyControl ownership journal')
    _validate_envy_snapshot(state.get('baseline'), expected)
    _validate_envy_snapshot(state.get('last_result'), expected)
    return state


def _write_envy_state(state):
    state['updated'] = time.time()
    content = (json.dumps(state, sort_keys=True) + '\n').encode()
    if len(content) > ENVY_MAX_JOURNAL:
        raise ValueError('EnvyControl ownership journal exceeds the recovery limit')
    _atomic_private(ENVY_STATE, content)


def _prepare_envycontrol(mode):
    current = _capture_envy_state()
    state = _load_envy_state(False)
    if state:
        if state['status'] not in ('complete', 'recovery_required'):
            raise ValueError('Previous EnvyControl operation requires recovery before another mode change')
        if not _same_envy_state(current, state['last_result']):
            raise ValueError('EnvyControl-managed configuration changed outside Archer; refusing overwrite')
        baseline = state['baseline']
    else:
        baseline = current
    _write_envy_state({'schema_version': 1, 'status': 'applying', 'requested_mode': mode,
                       'baseline': baseline, 'last_result': current})


def _record_envycontrol(status, mode):
    state = _load_envy_state()
    if status == 'complete' and state['status'] != 'applying':
        raise ValueError('EnvyControl application journal is not pending')
    if status not in ('complete', 'recovery_required'):
        raise ValueError('Invalid EnvyControl ownership transition')
    state.update(status=status, requested_mode=mode, last_result=_capture_envy_state())
    _write_envy_state(state)


def _restore_file(path, snapshot):
    if not snapshot['exists']:
        try:
            path.unlink()
        except FileNotFoundError:
            return
        _fsync_directory(path.parent)
        return
    content = base64.b64decode(snapshot['data'], validate=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix='.archer-envy-restore-')
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            os.fchown(stream.fileno(), snapshot['uid'], snapshot['gid'])
            os.fchmod(stream.fileno(), snapshot['mode'])
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        _fsync_directory(path.parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _service_restore_command(state):
    if state == 'enabled':
        return ['/usr/bin/systemctl', 'enable', PERSISTENCED]
    if state == 'enabled-runtime':
        return ['/usr/bin/systemctl', 'enable', '--runtime', PERSISTENCED]
    if state == 'disabled':
        return ['/usr/bin/systemctl', 'disable', PERSISTENCED]
    raise ValueError(f'Cannot safely reconstruct prior {PERSISTENCED} state {state!r}')


def _restore_envycontrol():
    state = _load_envy_state()
    if state['status'] == 'applying':
        raise ValueError('EnvyControl application was interrupted before result capture; manual review is required')
    baseline = state['baseline']
    last = state['last_result']
    current = _capture_envy_state()
    changed_files = [name for name in baseline['files']
                     if not _same_snapshot(baseline['files'][name], last['files'][name])]
    for name in changed_files:
        if not (_same_snapshot(current['files'][name], last['files'][name])
                or _same_snapshot(current['files'][name], baseline['files'][name])):
            raise ValueError(f'Archer-owned graphics file was modified; refusing restore: {name}')
    service_changed = baseline['persistenced'] != last['persistenced']
    if service_changed and current['persistenced'] not in (last['persistenced'], baseline['persistenced']):
        raise ValueError(f'{PERSISTENCED} enablement changed outside Archer; refusing restore')
    if service_changed and current['persistenced'] != baseline['persistenced']:
        _service_restore_command(baseline['persistenced'])  # Validate before any file write.
    state['status'] = 'restoring'
    _write_envy_state(state)
    paths = _envy_files()
    for name in changed_files:
        if not _same_snapshot(current['files'][name], baseline['files'][name]):
            _restore_file(paths[name], baseline['files'][name])
    if service_changed and current['persistenced'] != baseline['persistenced']:
        checked(_service_restore_command(baseline['persistenced']), 30)
        if _persistenced_state() != baseline['persistenced']:
            raise OSError(f'Could not restore {PERSISTENCED} enablement')
    state['status'] = 'restore_rebuild_required'
    _write_envy_state(state)


def _verify_envycontrol_restored():
    state = _load_envy_state()
    if state['status'] != 'restore_rebuild_required':
        raise ValueError('EnvyControl files have not been restored')
    current = _capture_envy_state()
    baseline = state['baseline']
    last = state['last_result']
    for name in baseline['files']:
        if (not _same_snapshot(baseline['files'][name], last['files'][name])
                and not _same_snapshot(current['files'][name], baseline['files'][name])):
            raise ValueError(f'EnvyControl recovery is incomplete: {name}')
    if baseline['persistenced'] != last['persistenced'] and current['persistenced'] != baseline['persistenced']:
        raise ValueError(f'EnvyControl recovery is incomplete: {PERSISTENCED}')
    return state


def _finish_envycontrol_restore():
    _verify_envycontrol_restored()
    ENVY_STATE.unlink()
    _fsync_directory(ENVY_STATE.parent)


def _ownership_command(action, value=''):
    arguments = ['/usr/bin/python3', str(Path(__file__).resolve()), action]
    if value:
        arguments.append(value)
    checked(arguments, 15)


def _restore_display_ownership(conditional=False):
    with _envy_lock():
        state = _load_envy_state(not conditional)
        if state is None:
            return {'restored': False, 'reason': 'no_ownership_journal',
                    'message': 'No Archer EnvyControl ownership journal exists; legacy or already-restored '
                               'graphics configuration was left unchanged.'}
        if state['status'] != 'restore_rebuild_required':
            _restore_envycontrol()
        _verify_envycontrol_restored()
        rebuild()
        _finish_envycontrol_restore()
    return {'restored': True, 'reboot_required': True}


def operate(action, value=''):
    if action == 'envy-apply' and value in ('hybrid', 'integrated', 'nvidia'):
        with _envy_lock():
            _prepare_envycontrol(value)
            try:
                checked(['/usr/bin/envycontrol', '-s', value], 90)
                rebuild()
                actual = checked(['/usr/bin/envycontrol', '--query'], 10)
                if actual != value:
                    raise OSError(f'Graphics readback is {actual!r}, expected {value!r}')
                _record_envycontrol('complete', value)
            except OSError as error:
                try:
                    _record_envycontrol('recovery_required', value)
                except (OSError, ValueError) as journal_error:
                    raise OSError(f'{error}; EnvyControl recovery journal update failed: {journal_error}') from error
                raise
        return {'mode': value}
    if action == 'envy-failed' and value in ('hybrid', 'integrated', 'nvidia'):
        with _envy_lock():
            _record_envycontrol('recovery_required', value)
        return {}
    if action in ('display-restore', 'display-restore-if-owned') and not value:
        return _restore_display_ownership(action == 'display-restore-if-owned')
    if action == 'graphics-plan' and value in ('hybrid', 'integrated', 'nvidia'):
        return build_plan(collect_inventory(), value)
    if action == 'graphics-verify' and not value:
        return verify_kernel_modules()
    if action == 'display' and value in ('hybrid', 'integrated', 'nvidia'):
        plan = build_plan(collect_inventory(), value)
        if not plan.get('available') or plan.get('backend') != 'envycontrol':
            raise ValueError('Graphics plan is unavailable: ' + str(plan.get('reason_code')))
        verify_kernel_modules()
        transaction = checked(['/usr/bin/python3', str(Path(__file__).resolve()), 'envy-apply', value], 440)
        try:
            response = json.loads(transaction)
            data = response.get('data') if isinstance(response, dict) else None
            actual = data.get('mode') if isinstance(data, dict) else None
            if actual != value:
                raise OSError(f'Graphics readback is {actual!r}, expected {value!r}')
        except (OSError, ValueError, TypeError) as error:
            try:
                _ownership_command('envy-failed', value)
            except OSError as journal_error:
                raise OSError(f'{error}; EnvyControl recovery journal update failed: {journal_error}') from error
            raise OSError(f'Graphics transaction readback failed: {error}') from error
        return {'mode': value, 'reboot_required': True}
    if action in ('override', 'remove-override') and value in ('nitro_v4', 'predator_v4', 'enable_all', ''):
        if action == 'override' and not value:
            raise ValueError('Missing driver parameter')
        if action == 'override':
            product = Path('/sys/class/dmi/id/product_name').read_text().strip()
            board = Path('/sys/class/dmi/id/board_name').read_text().strip()
            if value not in qualified_driver_overrides(product, board):
                raise ValueError('No qualified driver override for this exact model and board')
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
