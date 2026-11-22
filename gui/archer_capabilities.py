"""Typed capability/state helpers shared by Archer's privileged APIs.

The schema is deliberately small and additive.  A capability describes what
the running machine exposes; it never treats a successful probe as hardware
verification.
"""

from __future__ import annotations

import time


AVAILABILITY = frozenset(("absent", "available", "blocked", "unavailable", "unknown"))
VALIDATION = frozenset(("discovered", "upstream-reported", "automated-tested",
                        "hardware-verified", "regression-observed"))

# Forced WMI quirks are intentionally unavailable until an exact model/board
# pair has dated hardware evidence. Removal of an old Archer-owned override is
# still allowed by the daemon and helper.
QUALIFIED_DRIVER_OVERRIDES = {}


def qualified_driver_overrides(product, board):
    return list(QUALIFIED_DRIVER_OVERRIDES.get((str(product), str(board)), ()))


def capability(capability_id, device_key, backend, *, availability="available",
               readable=False, writable=False, value_kind="boolean", constraints=None,
               owner="archer", validation="discovered", reason_code=None, generation=0):
    if availability not in AVAILABILITY:
        raise ValueError("invalid capability availability")
    if validation not in VALIDATION:
        raise ValueError("invalid capability validation")
    if not all(isinstance(value, str) and value for value in
               (capability_id, device_key, backend, value_kind, owner)):
        raise ValueError("capability identifiers must be non-empty strings")
    if type(readable) is not bool or type(writable) is not bool:
        raise ValueError("readable and writable must be booleans")
    valid_generation = any((
        type(generation) is int and generation >= 0,
        isinstance(generation, str) and bool(generation),
    ))
    if not valid_generation:
        raise ValueError("inventory generation must be a non-negative integer or stable token")
    return {
        "schema_version": 1,
        "id": capability_id,
        "device_key": device_key,
        "backend": backend,
        "availability": availability,
        "readable": readable,
        "writable": writable,
        "value_kind": value_kind,
        "constraints": dict(constraints or {}),
        "control_owner": owner,
        "validation": validation,
        "reason_code": reason_code,
        "inventory_generation": generation,
    }


def state(*, desired=None, persisted=None, applied=None, observed=None,
          verification="unknown", error=None, timestamp=None):
    return {
        "schema_version": 1,
        "desired": desired,
        "persisted": persisted,
        "applied": applied,
        "observed": observed,
        "verification": verification,
        "timestamp": time.time() if timestamp is None else float(timestamp),
        "error": error,
    }


def metric(device_key, name, value, unit, source, *, valid=True, reason=None,
           timestamp=None):
    if type(valid) is not bool:
        raise ValueError("metric validity must be boolean")
    return {
        "device_key": device_key,
        "name": name,
        "value": value if valid else None,
        "unit": unit,
        "source": source,
        "valid": valid,
        "reason": reason,
        "timestamp": time.time() if timestamp is None else float(timestamp),
    }
