"""Shared request/persistence validation and stable keyboard effect identities."""

import copy
import json
import logging
import math
import os
from pathlib import Path
import re
import tempfile
import threading

EFFECT_IDS = ['static', 'breathing', 'neon', 'neon-fast', 'wave', 'meteor', 'zoom', 'shifting', 'twinkling']
WMI_EFFECT_IDS = ['static', 'breathing', 'neon', 'wave', 'shifting', 'zoom', 'meteor', 'twinkling']
EFFECT_LABELS = ['Static', 'Breathing', 'Neon', 'Neon (fast)', 'Wave', 'Meteor', 'Zoom', 'Shifting', 'Twinkling']
PROFILES = ('low-power', 'quiet', 'balanced', 'balanced-performance', 'performance')
logger = logging.getLogger('archer-daemon')


def integer(value, low, high):
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def validate(key, value):
    if key in ('battery_limiter', 'battery_calibration', 'backlight_timeout', 'lcd_override',
               'boot_animation_sound', 'game_mode_active', 'keyboard_migration_required'):
        valid = type(value) is bool
    elif key == 'usb_charging':
        valid = integer(value, 0, 30) and value in (0, 10, 20, 30)
    elif key == 'thermal_profile':
        valid = value in PROFILES if isinstance(value, str) else False
    elif key == 'per_zone_mode':
        valid = isinstance(value, dict) and integer(value.get('brightness'), 0, 100) and all(
            isinstance(value.get(f'zone{i}'), str) and re.fullmatch(r'[0-9a-fA-F]{6}', value[f'zone{i}']) for i in range(1, 5))
    elif key == 'four_zone_mode':
        limits = (('speed', 9), ('brightness', 100), ('direction', 2), ('red', 255), ('green', 255), ('blue', 255))
        valid = isinstance(value, dict) and all(integer(value.get(k), 0, limit) for k, limit in limits)
        valid = valid and (value.get('effect') in EFFECT_IDS or integer(value.get('mode'), 0, 8))
    elif key == 'last_keyboard_mode':
        valid = value in ('per_zone', 'effect')
    elif key == 'audio_enhancement':
        valid = isinstance(value, dict) and type(value.get('noise_suppression')) is bool
    elif key.startswith('fan_curve_'):
        valid = isinstance(value, dict) and type(value.get('enabled')) is bool
        points = value.get('points', []) if valid else None
        valid = valid and isinstance(points, (list, tuple)) and (2 <= len(points) <= 64 or (not value['enabled'] and not points))
        valid = valid and all(
            isinstance(p, (list, tuple)) and len(p) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in p)
            and 0 <= p[0] <= 125 and 30 <= p[1] <= 100 for p in points)
        valid = valid and len({p[0] for p in points}) == len(points)
    elif key == 'fan_speed':
        valid = isinstance(value, dict) and all(integer(value.get(k), 0, 100) and (value[k] == 0 or value[k] >= 30) for k in ('cpu', 'gpu'))
    elif key == 'button_colours':
        valid = isinstance(value, dict) and all(k in PROFILES and isinstance(v, str) and re.fullmatch(r'#?[a-fA-F0-9]{6}', v) for k, v in value.items())
    elif key == 'game_mode_saved_state':
        valid = isinstance(value, dict) and isinstance(value.get('boot_id'), str) and bool(value['boot_id'])
        valid = valid and (value.get('platform_profile') is None or value['platform_profile'] in PROFILES)
        epp = value.get('epp', {}) if valid else None
        epp_values = ('default', 'performance', 'balance_performance', 'balance_power', 'power')
        valid = valid and isinstance(epp, dict) and all(re.fullmatch(r'policy\d+', k) and v in epp_values for k, v in epp.items())
        gpu = value.get('nvidia', {}) if valid else None
        valid = valid and isinstance(gpu, dict) and all(re.fullmatch(r'GPU-[a-fA-F0-9-]+', k) and v in (0, 1) for k, v in gpu.items())
    elif key == 'display_configured_mode':
        valid = value in ('integrated', 'hybrid', 'nvidia')
    elif key in ('display_change_boot', 'daemon_version'):
        valid = isinstance(value, str) and bool(value)
    elif key == 'schema_version':
        valid = value == 2
    else:
        # Retain extension keys for compatibility; they never drive hardware here.
        valid = True
    if not valid:
        raise ValueError(f'Invalid setting: {key}')
    return value


def object_request(text, key):
    value = json.loads(text)
    validate(key, value)
    return value


class SettingsStore:
    """Lock mutation and durable atomic replacement as one transaction."""

    def __init__(self, path='/etc/archer/settings.json'):
        self.path = str(path)
        self._lock = threading.RLock()
        self._data = {}
        self.warnings = []
        try:
            loaded = json.loads(Path(path).read_text())
            if not isinstance(loaded, dict):
                raise ValueError('Settings must be an object')
            version = loaded.get('schema_version', 1)
            if type(version) is not int or version not in (1, 2):
                raise ValueError('Unsupported settings schema version; saved hardware settings were not replayed')
            for key, value in loaded.items():
                try:
                    if key == 'schema_version':
                        continue
                    validate(key, value)
                    self._data[key] = value
                except (ValueError, TypeError) as error:
                    self.warnings.append(str(error))
            effect = self._data.get('four_zone_mode')
            if effect and 'effect' not in effect:
                backend = effect.get('backend', loaded.get('keyboard_backend'))
                modes = EFFECT_IDS if backend == 'ene' else WMI_EFFECT_IDS if backend == 'wmi' else EFFECT_IDS[:3]
                if effect['mode'] < len(modes):
                    effect['effect'] = modes[effect['mode']]
                else:
                    del self._data['four_zone_mode']
                    self._data['keyboard_migration_required'] = True
                    self.warnings.append('Legacy keyboard effect is ambiguous; select and apply the intended effect once')
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError) as error:
            self.warnings.append(str(error))
        self._data['schema_version'] = 2
        for warning in self.warnings:
            logger.warning('Saved settings: %s', warning)

    def update(self, values=None, remove=()):
        with self._lock:
            snapshot = copy.deepcopy(self._data)
            for key, value in (values or {}).items():
                snapshot[key] = copy.deepcopy(validate(key, value))
            for key in remove:
                snapshot.pop(key, None)
            if snapshot == self._data:
                return True
            path = Path(self.path)
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.settings-')
            try:
                with os.fdopen(fd, 'w') as stream:
                    os.fchmod(stream.fileno(), 0o644)
                    json.dump(snapshot, stream, indent=2)
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
            self._data = snapshot
            return True

    def set(self, key, value):
        return self.update({key: value})

    def remove(self, key):
        return self.update(remove=(key,))

    def get(self, key, default=None):
        with self._lock:
            return copy.deepcopy(self._data.get(key, default))

    @property
    def data(self):
        with self._lock:
            return copy.deepcopy(self._data)
