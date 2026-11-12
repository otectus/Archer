"""Single desired charge policy, shared by the daemon and battery-only service."""

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import tempfile

POLICY = Path('/etc/archer/battery.json')


@contextmanager
def policy_lock(path=POLICY):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix('.lock'), 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def read_policy(path=POLICY):
    if path.exists():
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or type(data.get('enabled')) is not bool:
            raise ValueError('Invalid desired battery policy')
        return data['enabled']
    # Adopt a previously saved GUI preference before applying the install default.
    try:
        saved = json.loads(path.with_name('settings.json').read_text())
        if isinstance(saved, dict) and type(saved.get('battery_limiter')) is bool:
            return saved['battery_limiter']
    except (OSError, ValueError):
        pass
    return True


def write_policy(enabled, path=POLICY):
    if type(enabled) is not bool:
        raise ValueError('Battery policy must be boolean')
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.battery-')
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o644)
            json.dump({'enabled': enabled}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def apply(root=Path('/sys'), policy=POLICY):
    with policy_lock(policy):
        enabled = read_policy(policy)
        write_policy(enabled, policy)
        paths = sorted((root / 'class/power_supply').glob('*/charge_control_end_threshold'))
        paths += [root / 'devices/platform/acer-wmi' / sense / 'battery_limiter'
                  for sense in ('predator_sense', 'nitro_sense')]
        paths += [root / 'bus/wmi/drivers/acer-wmi-battery/health_mode']
        for path in paths:
            if not path.exists():
                continue
            value = ('80' if enabled else '100') if path.name == 'charge_control_end_threshold' else ('1' if enabled else '0')
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_TRUNC), 'w') as stream:
                stream.write(value)
            if path.read_text().strip() != value:
                raise OSError(f'Battery policy readback failed at {path}')
            return True
    return False


if __name__ == '__main__':
    try:
        # Missing hardware is expected at early boot; device events retry later.
        apply()
    except (OSError, ValueError) as error:
        raise SystemExit(str(error))
