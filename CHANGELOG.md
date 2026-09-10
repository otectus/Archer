# Changelog

All notable changes to Archer Compatibility Suite are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Keyboard lighting (ENE K5130)

- Drive keyboard backlight through the ENE K5130 controller, which is the only
  path that applies colour and effect mode on PHN16S-71; the sysfs/WMI path
  remains the fallback and reports success without changing the LEDs.
- Decode the lid logo and colour the performance-button LED after the active
  profile, following writers outside Archer so the LED cannot drift out of step.
- Reapply lighting after resume, which neither the driver nor the controller
  restores on its own.
- Replace the WMI-documented effect list with the nine modes verified on the
  controller, and clamp effect indexes saved by older builds.
- Install `archer_ene.py` alongside the daemon; without it the optional import
  fails silently and colour support disappears.

### Tray profiles and deployment

- Add the right-click Open → Profile submenu → Exit menu with supported performance profiles, checked state, and synchronized GUI/tray changes.
- Reuse asynchronous profile saves and authorization/error handling; disable unavailable or pending writes and reveal failed changes in the GUI.
- Correct recursive D-Bus menu variants, subtree/depth/property requests, update signals, and one-shot event dispatch.
- Test the menu over a private session bus, hidden-window profile changes, capability changes, failure recovery, and exit cleanup.
- Validate required GUI package files before installation, restart the daemon on upgrades, and simplify boot ordering to depend on D-Bus.
- Refresh user/contributor documentation and add a deployment and release checklist.

### Kernel compatibility (issue #9)

- Pin and validate external driver sources with an idempotent shared patch layer.
- Fix all removed Linuwu string calls and counted RGB input hazards; preserve
  older platform-profile, WMI, backlight and platform-removal APIs.
- Validate exact headers and select compilers for every installed kernel; retain
  DKMS diagnostics and partial-install cleanup records, with ownership checks.
- Prefer native battery/profile capabilities, preserve fan safeguards, handle
  disappearing sysfs nodes, and rebuild all initramfs targets with errors visible.
- Add kernel/module regression tests and real CI builds, including older baselines.


### ANV16S-41 fan support

- Add exact Acer Nitro ANV16S-41 driver support using the existing Nitro v4 quirk, guarded by the existing sensor protocol. Physical BIOS V1.12 validation remains pending.
- Pin Linuwu-Sense to `73a25ec243a44ba2b1703e8d0a76fa2735062506` with isolated, validated patches and DKMS `1.0.archer2`; migrate legacy versions and build against each target kernel's headers/toolchain.
- Preserve independent native platform profiles and remove the driver/thermal blacklist conflict. Follow provider choices in the GUI.
- Validate paired fan writes and curves; repair watchdog error handling and restore firmware Auto on stop, failed manual writes, daemon restart/exit and driver lifecycle transitions. Do not replay stale manual fan settings.
- Add explicit driver diagnostics, a privacy-scoped support report, mock sysfs/C/DKMS tests and a progressive hardware validation guide.
- Retain other modules' uninstall records during driver updates and report installer/cleanup failures accurately.

### Control center overhaul

- Replace the ten-tab GUI with six adaptive destinations and task-focused subpages.
- Follow system appearance by default; add Light/Dark overrides and respect high contrast and system accents.
- Unify guarded loading, staged edits, pending actions, error recovery, and stale-service handling.
- Add native monitoring cards, theme-aware charts, keyboard previews, and persistent graphics restart status.
- Move firmware scans out of initial settings loading and run explicit scans asynchronously.
- Correct graphics/audio payload handling and label persistent driver overrides accurately.
- Add hardware-free GTK, data-contract, visual-layout and firmware-responsiveness checks. GTK 4.12/libadwaita 1.6 are now the minimum GUI versions.


## [2.0.1] — 2026-05-01

Hardening sweep triggered by [#4](https://github.com/otectus/Archer/issues/4)
("Archer GUI not updating and some other problems"). Closes that issue and a
broad set of correctness/security bugs surfaced by audit.

GUI package version bumped in lockstep: `1.0.0` → `1.0.1`.

### Fixed

- **Installer no longer requires a reboot to connect.** `modules/gui.sh` now
  reloads `dbus.service` (with `kill -HUP $(pidof dbus-daemon)` fallback) after
  copying `io.otectus.Archer1.conf`, so the new policy takes effect immediately.
- **GUI no longer freezes when the daemon is slow.** Every D-Bus call from
  `gui/archer/client.py` now passes a 5s `timeout=` (longer for `envycontrol`,
  `fwupdmgr`, restart). Without it, dbus-python defaulted to no timeout and a
  single hung sysfs read in the daemon would freeze the polling thread forever.
- **Status label now reflects reality.** The window's "Connected" label was set
  once at startup and never updated. It now flips to "Daemon Offline" or
  "Stale" with exponential-backoff reconnect (5/10/20/60s) and surfaces the
  underlying error in a toast.
- **Audio noise-suppression toggle now actually takes effect.** The daemon's
  `systemctl --user restart pipewire.service` ran inside the *root* user
  manager, never touching the user's pipewire. Replaced with an
  `AudioEnhancementChanged` D-Bus signal that the GUI handles in the user's
  own session.
- **`restart_daemon` no longer "loses" the D-Bus reply.** The synchronous
  `systemctl restart` killed the daemon before the reply flushed. Now uses
  `systemd-run --on-active=2s --no-block` so the GUI sees a clean response.
- **`get_fan_rpm` now picks the right hwmon device.** Allowlists Acer-relevant
  chipsets (`linuwu_sense`, `acer_wmi`, `nct67xx`, `it87`, `dell_smm_hwmon`)
  before falling back to "first device with `fan1_input`".
- **Setter UI controls revert on daemon failure.** Battery limit switch, USB
  charging combo, LCD override switch, boot animation switch, backlight
  timeout switch now flip back to the previous value with an explanatory
  toast when the daemon refuses (was fire-and-forget — UI lied about state).
- **Tray init failures are no longer silent.** Logged at WARNING and surfaced
  in a toast on first window show so the close-to-tray hint isn't bogus.
- **`uninstall.sh` no longer rm-rfs `/.local/...` if `$HOME` is unset under
  sudo.** Guard added.

### Changed

- **Telemetry now arrives via D-Bus signal (`TelemetryUpdated`) instead of
  polling.** The daemon emits every 2s; the GUI subscribes and watches for
  staleness. The previous polling loop spawned a new thread every 2s and
  would silently pile up zombies if any single call hung. `GetMonitoringData`
  remains for the initial settings fetch and backward compat.
- **D-Bus is now mandatory.** The Unix-socket fallback in
  `gui/archer_daemon.py` (`DaemonServer` class, ~340 LOC) was unreachable
  from the user-mode GUI anyway (socket was 0o660 root:root). Removed.
  `gui/archer/client.py` similarly drops the socket-fallback path. A failed
  D-Bus startup now logs an actionable error pointing at the policy file
  and `dbus reload`.
- **Service unit hardened.** `gui/archer-daemon.service` now uses
  `RuntimeDirectory=archer` (so systemd creates `/run/archer/` with
  0755 root:root) and `ReadWritePaths=/etc/archer` (required by
  `ProtectSystem=full`). PID file moved from `/var/run/archer-daemon.pid`
  to `/run/archer/daemon.pid`. `Requires=dbus.service` added.
- **Install manifest moved to `/var/lib/archer/install-manifest.json`**
  (root-owned, 0644). Resolves the install-as-user / uninstall-as-sudo
  `$HOME` mismatch and stops local users tampering with manifest entries
  that `uninstall.sh` later sources. Manifests at the previous user-home
  paths (and the legacy DAMX path) are migrated automatically on the next
  install or uninstall run.
- **Per-module install rollback.** `INSTALLED_FILES` / `INSTALLED_DKMS` /
  `INSTALLED_PACKAGES` are snapshotted before each `module_install` and
  restored on failure, so the saved manifest only lists modules that
  actually installed cleanly.
- **Daemon hot-path probes are cached** (5s TTL). `nvidia-smi`, `lspci`,
  and `which envycontrol` results are reused across the GUI's monitoring
  ticks so a hung NVIDIA driver no longer drives the daemon thread into
  the ground.

### Security

- **Path-traversal in `uninstall.sh` closed.** The uninstall manifest source
  loop now refuses to `source` any module name that fails the
  `^[a-z][a-z0-9_-]+$` allowlist *and* isn't in the canonical `MODULE_IDS`
  array. A tampered `~/.local/share/archer/install-manifest.json` could
  previously inject e.g. `mod="../../tmp/evil"` and gain code execution
  under `sudo` when the user ran uninstall.
- **`run_cmd` shell-meta guard.** `gui/archer_daemon.py:run_cmd` now refuses
  any command containing `;`, `&&`, `||`, `$(`, or backticks unless the
  caller passes `shell_meta_ok=True`. No present callsite is exploitable;
  the guard catches future regressions where a user-supplied value flows
  into a shell string.
- **`MODULE_IDS` is now a single source of truth** in `lib/modules.sh`,
  consumed by both `install.sh` (validating `--modules`) and `uninstall.sh`
  (validating manifest entries before `source`).

### Added

- **`tests/dbus_smoke.py` + `tests/dbus_smoke.sh`.** Headless smoke harness
  that boots `ArcherDBusService` against a private session bus with a mocked
  `HardwareManager`, asserts the required methods + signals appear in the
  introspection XML, and waits for the first `TelemetryUpdated` emit.
- **CI: `python-syntax` and `dbus-introspect-smoke` jobs.**
  `python -m py_compile` for every `gui/**/*.py`; smoke harness via
  `dbus-run-session` so CI fails loudly on signal/method regressions.
- **`.github/ISSUE_TEMPLATE/gui-not-updating.md`** with required fields:
  distro+kernel, daemon status, journal, `busctl introspect`, GUI logs.
  Would have closed #4 in one round trip.
- **README → Troubleshooting** section covering "Daemon Offline / Stale"
  and "GUI hangs forever" with the exact triage commands.
- **`is_known_module` allowlist with ~25 lines of bats tests** covering
  path-traversal, shell-metacharacter, and well-formed-but-unknown IDs.

### Removed

- **`gui/install-gui.sh`** legacy wrapper. Use `./install.sh --modules gui`.
- **`DaemonServer` class and `/var/run/archer.sock`** from the daemon.
- **Unix-socket fallback path** from `gui/archer/client.py`.

### Migration notes for upgraders from 2.0.0

- The first run of `./install.sh` after upgrading copies your existing
  `~/.local/share/archer/install-manifest.json` (and any legacy
  `~/.local/share/damx/install-manifest.json`) into `/var/lib/archer/`.
  Nothing else needed.
- If the daemon is currently running with a `/var/run/archer-daemon.pid`
  PID file, the new unit's `RuntimeDirectory=archer` will create
  `/run/archer/` on next start. Old `/var/run/archer.sock` is removed by
  the uninstall path; no manual cleanup required.
- A short `systemctl reload dbus.service` happens during the GUI module
  install. On most desktops this is invisible; you may briefly see your DE's
  panel/applets reconnect.

## [2.0.0] — 2026-04-20

Initial release with D-Bus IPC + polkit authorization, 13 installer modules,
GTK4/Adwaita control panel, install manifest, and CI/test scaffolding.
