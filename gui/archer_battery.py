"""Per-battery telemetry and charge policy shared by daemon and boot helper."""

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import tempfile
import time
import subprocess
import re
import sys

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


def read_policy_data(path=POLICY):
    """Return the versioned policy while accepting the Phase 1 boolean file."""
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {"schema_version": 2, "enabled": read_policy(path), "batteries": {}}
    if not isinstance(data, dict) or type(data.get("enabled")) is not bool:
        raise ValueError('Invalid desired battery policy')
    if data.get("schema_version", 1) == 1:
        return {"schema_version": 2, "enabled": data["enabled"], "scope": "legacy_all", "batteries": {}}
    if data.get("schema_version") != 2 or not isinstance(data.get("batteries"), dict):
        raise ValueError('Unsupported battery policy schema')
    return data


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


def write_policy_data(data, path=POLICY):
    if not isinstance(data, dict) or data.get("schema_version") != 2 or \
            type(data.get("enabled")) is not bool or not isinstance(data.get("batteries"), dict):
        raise ValueError('Invalid battery policy')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.battery-')
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o644)
            json.dump(data, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def initialize_policy(node, path=POLICY):
    """Record an explicitly selected standard battery before boot writes."""
    if not isinstance(node, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", node):
        raise ValueError('Invalid power-supply node')
    with policy_lock(path):
        data = read_policy_data(path)
        data.pop('scope', None)
        data['batteries'].setdefault('power_supply:' + node, {}).update(
            {'node': node, 'end': 80, 'owner': 'archer'})
        write_policy_data(data, path)


def discover(root=Path('/sys'), key_map=None):
    """Describe every battery without fabricating unavailable measurements."""
    supplies = root / 'class/power_supply'
    result = []
    try:
        candidates = sorted(supplies.iterdir())
    except OSError:
        return result
    for battery in candidates:
        if _read(battery / 'type') != 'Battery' and not battery.name.startswith('BAT'):
            continue
        key = (key_map or {}).get(battery.name, 'power_supply:' + battery.name)
        present = _integer(battery / 'present')
        entry = {
            'device_key': key, 'name': battery.name, 'node': battery.name,
            'present': present != 0 if present is not None else True,
            'status': _status(_read(battery / 'status')),
            'measurements': {}, 'charge_control': charge_control(battery),
        }
        fields = {
            'capacity': ('percent', 'capacity'), 'cycle_count': ('count', 'cycle_count'),
            'voltage_now': ('microvolt', 'voltage_now'), 'current_now': ('microampere', 'current_now'),
            'power_now': ('microwatt', 'power_now'), 'temperature': ('decicelsius', 'temp'),
            'energy_now': ('microwatt_hour', 'energy_now'),
            'energy_full': ('microwatt_hour', 'energy_full'),
            'energy_full_design': ('microwatt_hour', 'energy_full_design'),
            'charge_now': ('microampere_hour', 'charge_now'),
            'charge_full': ('microampere_hour', 'charge_full'),
            'charge_full_design': ('microampere_hour', 'charge_full_design'),
        }
        for name, (unit, filename) in fields.items():
            value = _integer(battery / filename)
            if value is not None:
                entry['measurements'][name] = {'value': value, 'unit': unit, 'valid': True}
        entry['time_remaining_seconds'] = estimate_seconds(entry)
        result.append(entry)
    return result


def charge_control(battery):
    start_path = battery / 'charge_control_start_threshold'
    end_path = battery / 'charge_control_end_threshold'
    start, end = _integer(start_path), _integer(end_path)
    if end is None:
        return {'kind': 'none', 'readable': False, 'writable': False}
    minimum = _integer(battery / 'charge_control_threshold_min')
    maximum = _integer(battery / 'charge_control_threshold_max')
    step = _integer(battery / 'charge_control_threshold_step')
    qualified = minimum is not None and maximum is not None and step is not None and \
        0 <= minimum < maximum <= 100 and step > 0
    writable = qualified and os.access(end_path, os.W_OK) and (start is None or os.access(start_path, os.W_OK))
    return {'kind': 'variable_threshold', 'readable': True, 'writable': writable,
            'start': start, 'end': end,
            'reason_code': None if qualified else 'constraints_unknown',
            'constraints': ({'minimum': minimum, 'maximum': maximum, 'step': step,
                             'ordering': 'start_less_than_end'} if qualified else {})}


def set_threshold(root, device_key, start, end):
    if not isinstance(device_key, str) or not device_key.startswith('power_supply:'):
        return False
    name = device_key.split(':', 1)[1]
    if not name or '/' in name or name in ('.', '..'):
        return False
    battery = Path(root) / 'class/power_supply' / name
    known = {item['device_key']: item for item in discover(Path(root))}
    control = known.get(device_key, {}).get('charge_control', {})
    if control.get('kind') != 'variable_threshold' or not control.get('writable'):
        return False
    constraints = control['constraints']
    minimum, maximum, step = constraints['minimum'], constraints['maximum'], constraints['step']
    if type(end) is not int or not minimum <= end <= maximum or (end - minimum) % step:
        return False
    invalid_start = start is not None and any((
        type(start) is not int,
        isinstance(start, int) and not minimum <= start < end,
        isinstance(start, int) and (start - minimum) % step,
    ))
    if invalid_start:
        return False
    writes = []
    if start is not None:
        writes.append((battery / 'charge_control_start_threshold', start))
    writes.append((battery / 'charge_control_end_threshold', end))
    previous = [(target, _read(target)) for target, _ in writes]
    try:
        for target, value in writes:
            _write_existing(target, value)
            if _read(target) != str(value):
                raise OSError('threshold readback failed')
        return True
    except OSError:
        for target, value in reversed(previous):
            if value is not None:
                try:
                    _write_existing(target, value)
                except OSError:
                    pass
        return False


def estimate_seconds(entry):
    measurements = entry.get('measurements', {})
    status = entry.get('status')
    if 'energy_now' in measurements and 'power_now' in measurements:
        now = measurements['energy_now']['value']
        rate = measurements['power_now']['value']
        full = measurements.get('energy_full', {}).get('value')
    elif 'charge_now' in measurements and 'current_now' in measurements:
        now = measurements['charge_now']['value']
        rate = abs(measurements['current_now']['value'])
        full = measurements.get('charge_full', {}).get('value')
    else:
        return None
    if rate <= 0:
        return None
    if status == 'discharging':
        quantity = now
    elif status == 'charging' and full is not None:
        quantity = max(0, full - now)
    else:
        return None
    return round(quantity / rate * 3600)


def _status(value):
    normalized = str(value or '').strip().lower()
    return normalized if normalized in ('charging', 'discharging', 'full', 'idle') else \
        ('idle' if normalized == 'not charging' else 'unknown')


def _read(path):
    try:
        return Path(path).read_text().strip()
    except (OSError, ValueError):
        return None


def _integer(path):
    try:
        return int(_read(path))
    except (TypeError, ValueError):
        return None


def _write_existing(path, value):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_TRUNC | os.O_CLOEXEC), 'w') as stream:
        stream.write(str(value))


def _external_charge_owner():
    for unit in ('tlp.service', 'tlp-pd.service'):
        try:
            result = subprocess.run(['/usr/bin/systemctl', 'is-enabled', '--quiet', unit],
                                    timeout=3, check=False, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            return unit.removesuffix('.service')
    return None


def apply(root=Path('/sys'), policy=POLICY, respect_owners=True):
    with policy_lock(policy):
        data = read_policy_data(policy)
        if respect_owners and Path(root).resolve() == Path('/sys') and _external_charge_owner():
            return False
        enabled = data['enabled']
        data['schema_version'] = 2
        data.setdefault('batteries', {})
        write_policy_data(data, policy)
        paths = sorted((root / 'class/power_supply').glob('*/charge_control_end_threshold'))
        paths += [root / 'devices/platform/acer-wmi' / sense / 'battery_limiter'
                  for sense in ('predator_sense', 'nitro_sense')]
        paths += [root / 'bus/wmi/drivers/acer-wmi-battery/health_mode']
        applied = False
        for path in paths:
            if not path.exists():
                continue
            device_key = 'power_supply:' + path.parent.name
            saved = data['batteries'].get(device_key)
            if saved is None:
                saved = next((item for item in data['batteries'].values()
                              if isinstance(item, dict) and item.get('node') == path.parent.name), {})
            if path.name == 'charge_control_end_threshold' and not saved and data.get('scope') != 'legacy_all':
                continue
            if saved.get('owner') not in (None, 'archer'):
                continue
            override = saved.get('full_override', {})
            override_active = isinstance(override, dict) and float(override.get('expires_at', 0)) > time.time()
            desired = saved.get('end')
            if path.name == 'charge_control_end_threshold':
                value = str(100 if override_active else desired if type(desired) is int else 80 if enabled else 100)
            else:
                value = '1' if enabled else '0'
            _write_existing(path, value)
            if path.read_text().strip() != value:
                raise OSError(f'Battery policy readback failed at {path}')
            applied = True
        return applied


if __name__ == '__main__':
    try:
        if len(sys.argv) == 4 and sys.argv[1] == 'initialize':
            initialize_policy(sys.argv[2], Path(sys.argv[3]))
            raise SystemExit(0)
        if len(sys.argv) != 1:
            raise ValueError('Unsupported battery helper arguments')
        # Missing hardware is expected at early boot; device events retry later.
        apply()
    except (OSError, ValueError) as error:
        raise SystemExit(str(error))
