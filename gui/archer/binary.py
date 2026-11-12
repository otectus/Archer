"""GTK-free presentation model for 0/1 hardware controls whose readback can be unknown.

The daemon reports three facts separately (issue #10): the preference saved for
startup, the last request the driver accepted in the daemon's current session,
and a strictly parsed hardware readback. These helpers validate that payload
and turn it into page text. Unknown is never shown as Off, and an accepted
request is never shown as verified hardware state.
"""

BINARY_KEYS = ("lcd_override", "boot_animation_sound", "backlight_timeout")

# "description" stays visible when the switch is hidden for an unknown state.
TEXTS = {
    "lcd_override": {
        "description": ("LCD override (panel overdrive) can reduce motion blur on some panels "
                        "and cause inverse ghosting, or overshoot, on others."),
        "title": "LCD override", "subtitle": "",
        "name": "LCD override", "inline": "LCD override", "subject": "LCD override state",
        "on_action": "Turn on", "off_action": "Turn off and save",
    },
    "boot_animation_sound": {
        "description": "Play Acer’s startup animation and sound when the laptop starts.",
        "title": "Boot animation and sound", "subtitle": "",
        "name": "Boot animation and sound", "inline": "boot animation and sound",
        "subject": "boot animation and sound setting",
        "on_action": "Turn on", "off_action": "Turn off and save",
    },
    "backlight_timeout": {
        "description": "Turn the keyboard backlight off after 30 seconds without input.",
        "title": "Turn off after 30 seconds of inactivity", "subtitle": "",
        "name": "Backlight timeout", "inline": "backlight timeout", "subject": "backlight timeout setting",
        "on_action": "Turn on timeout", "off_action": "Turn off timeout and save",
    },
}

_SOURCES = ("hardware", "last_write", "saved_preference", "unknown")
_READBACK = ("known", "unrecognized", "invalid", "unreadable", "unavailable")
_CONFIGURED = ("valid", "absent", "invalid", "unreadable")
_RESETS = (None, "resume", "interface_changed")
_VERIFICATION = ("matches", "unverified", "mismatch", "not_checked")
_OPERATION = {
    "origin": ("user", "startup"),
    "write_status": ("accepted", "failed", "not_attempted"),
    "save_status": ("saved", "unsynced", "failed", "not_attempted"),
    "verification": _VERIFICATION,
}
_RESTORE = {
    "status": ("accepted", "failed", "not_attempted"),
    "reason": (None, "no_preference", "invalid_preference", "settings_unreadable", "unavailable"),
    "verification": _VERIFICATION,
}
_TEXT_LIMIT = 300


class _Malformed(ValueError):
    pass


def _optional_bool(value):
    if value is None or isinstance(value, bool):
        return value
    raise _Malformed


def _choice(value, choices):
    if isinstance(value, (str, type(None))) and value in choices:
        return value
    raise _Malformed


def _text(value, limit=_TEXT_LIMIT):
    return value[:limit] if isinstance(value, str) else None


def _record(raw, fields):
    """A validated operation/restore record, or None when absent or malformed."""
    if not isinstance(raw, dict):
        return None
    try:
        record = {key: _choice(raw.get(key), choices) for key, choices in fields.items()}
        record["target"] = _optional_bool(raw.get("target"))
    except _Malformed:
        return None
    record["error"] = _text(raw.get("error"))
    return record


def _fallback(compatibility, saved):
    configured = saved if isinstance(saved, bool) else None
    return {"compatibility": compatibility, "available": None, "effective": None, "source": "unknown",
            "configured": configured, "configured_status": "valid" if configured is not None else "absent",
            "requested": None, "observed": None, "readback_status": None, "raw_readback": None,
            "evidence_reset": None, "last_operation": None, "restore": None}


def normalize_binary_state(raw, saved=None):
    """A validated copy of a daemon *_state object.

    An older service sends none ("legacy") and broken data is "malformed". Either
    way the result claims no current state and keeps only a valid saved value,
    as a preference rather than an observation.
    """
    if raw is None:
        return _fallback("legacy", saved)
    if not isinstance(raw, dict):
        return _fallback("malformed", saved)
    try:
        available = raw.get("available")
        if not isinstance(available, bool):
            raise _Malformed
        state = {
            "compatibility": None,
            "available": available,
            "effective": _optional_bool(raw.get("effective")),
            "source": _choice(raw.get("source"), _SOURCES),
            "configured": _optional_bool(raw.get("configured")),
            "observed": _optional_bool(raw.get("observed")),
            "readback_status": _choice(raw.get("readback_status"), _READBACK),
            "requested": _optional_bool(raw.get("requested")),
        }
        source, effective = state["source"], state["effective"]
        if (source == "hardware" and (state["observed"] is None or effective != state["observed"])) or \
                (source == "unknown") != (effective is None) or \
                (source == "last_write" and state["requested"] not in (None, effective)) or \
                (source == "saved_preference" and effective != state["configured"]):
            raise _Malformed
    except _Malformed:
        return _fallback("malformed", saved)
    status = raw.get("configured_status")
    state["configured_status"] = status if isinstance(status, str) and status in _CONFIGURED else (
        "valid" if state["configured"] is not None else "absent")
    if state["configured"] is None and state["configured_status"] == "valid":
        state["configured_status"] = "absent"
    if state["source"] == "last_write" and state["requested"] is None:
        state["requested"] = effective
    reset = raw.get("evidence_reset")
    state["evidence_reset"] = reset if isinstance(reset, (str, type(None))) and reset in _RESETS else None
    state["raw_readback"] = _text(raw.get("raw_readback"), 32)
    state["last_operation"] = _record(raw.get("last_operation"), _OPERATION)
    state["restore"] = _record(raw.get("restore"), _RESTORE)
    return state


def response_state(response, key):
    """(state, operation) from a setter response, or (None, None) when it carries no valid state."""
    data = response.get("data") if isinstance(response, dict) else None
    if not isinstance(data, dict) or f"{key}_state" not in data:
        return None, None
    state = normalize_binary_state(data[f"{key}_state"])
    if state["compatibility"]:
        return None, None
    return state, _record(data.get("operation"), _OPERATION)


def relevant(state):
    """Whether a control without a detected interface still has a saved value to disclose."""
    return state.get("configured") is not None or state.get("configured_status") == "invalid"


def _word(value):
    return "On" if value else "Off"


def _readback_reason(state, texts):
    status = state.get("readback_status")
    if status == "unrecognized":
        return f"This device does not report its {texts['subject']}."
    if status in ("invalid", "unreadable"):
        return f"The {texts['subject']} could not be read from the driver."
    return ""


def _unknown_detail(state):
    reset = state.get("evidence_reset")
    if reset == "resume":
        return "Archer's last request was sent before the system went to sleep."
    if reset == "interface_changed":
        return "The driver was reloaded after Archer's last request."
    configured = state.get("configured")
    if configured is None:
        return ""
    restore = state.get("restore") or {}
    if restore.get("status") == "failed":
        detail = f": {restore['error']}" if restore.get("error") else ""
        return f"Applying the saved {_word(configured)} setting at startup failed{detail}."
    if restore.get("reason") == "unavailable":
        return f"The saved {_word(configured)} setting was not applied because the control was unavailable at startup."
    return f"The saved {_word(configured)} setting has not been applied since Archer started."


def _saved_text(state, view):
    configured, status = state.get("configured"), state.get("configured_status")
    save = (state.get("last_operation") or {}).get("save_status")
    if status == "invalid":
        return "The saved value is invalid and will not be applied. Choose On or Off to replace it."
    if status == "unreadable":
        return ("Saved settings could not be read, so nothing was applied at startup. "
                "Your next choice is saved, and the unreadable file is kept as a copy.")
    if configured is None:
        return "No startup preference is saved." if save == "failed" else None
    if save == "unsynced":
        return f"{_word(configured)}, but the system could not confirm that the save reached the disk."
    if save == "failed" or view["mode"] != "switch" or configured != view["value"]:
        return _word(configured)
    return None


def describe(state, texts):
    """Presentation for the current state.

    mode "switch" shows the switch at value; mode "choices" hides it and offers
    explicit On/Off requests because no current state can honestly be claimed.
    """
    view = {"mode": "choices", "value": None, "status": "", "saved": None, "actions": True}
    choose = "Choose On or Off to send an explicit request."
    if state.get("compatibility"):
        reason = ("This Archer service version does not report whether a request took effect."
                  if state["compatibility"] == "legacy" else "The Archer service sent unexpected status information.")
        view["status"] = f"Current state unknown. {reason} {choose}"
        view["saved"] = _word(state["configured"]) if state.get("configured") is not None else None
        return view
    if not state.get("available"):
        view.update(status=f"{texts['name']} control is unavailable.", actions=False)
        view["saved"] = _saved_text(state, view)
        return view
    source, effective, requested = state["source"], state["effective"], state.get("requested")
    if source == "hardware":
        view.update(mode="switch", value=effective)
        if requested is not None and requested != effective:
            view["status"] = (f"The driver reports a different state from your request: "
                              f"{_word(effective)} reported, {_word(requested)} requested.")
        else:
            view["status"] = f"Hardware reports {_word(effective)}."
    elif source == "last_write":
        view.update(mode="switch", value=effective)
        reason = _readback_reason(state, texts) or "Hardware state could not be verified."
        view["status"] = f"{_word(effective)} requested. {reason}"
    else:
        parts = ("Current state unknown.", _readback_reason(state, texts), _unknown_detail(state), choose)
        view["status"] = " ".join(part for part in parts if part)
    view["saved"] = _saved_text(state, view)
    return view


def _startup(state):
    configured, status = state.get("configured"), state.get("configured_status")
    if status == "invalid":
        return "The saved startup value is invalid and will not be applied."
    if status == "unreadable":
        return "Saved settings could not be read, so nothing will be applied at startup."
    if configured is None:
        return "No startup preference is saved."
    return f"The startup preference is still {_word(configured)}."


def describe_operation(operation, state, texts, error=None):
    """Feedback for a completed request as (message, is_error)."""
    if not operation:
        return error or "The service could not complete this change.", True
    target = operation.get("target")
    request = f"{_word(target)} request" if target is not None else "Request"
    detail = operation.get("error") or error
    suffix = f" Details: {detail}" if detail else ""
    write, save, verification = operation["write_status"], operation["save_status"], operation["verification"]
    if write == "not_attempted":
        return (f"{texts['name']} control is unavailable. No request was sent, "
                "and your saved preference was not changed."), True
    if write == "failed":
        return f"Could not send the {texts['inline']} request. Your saved preference was not changed.{suffix}", True
    if save == "failed":
        return f"{request} accepted, but saving failed. {_startup(state)}{suffix}", True
    if save == "unsynced":
        return (f"{request} accepted and saved, but the system could not confirm that the save reached the disk. "
                "Send the request again to retry."), True
    if verification == "mismatch":
        saved = f" {_word(target)} is saved for startup." if save == "saved" else ""
        return f"The driver reports a different state from your request.{saved}", True
    if verification == "matches":
        return f"Saved. Hardware reports {_word(target)}.", False
    return "Preference saved. Hardware state could not be verified.", False
