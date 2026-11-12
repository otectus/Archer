# Phase 1 update implementation record

This unreleased update implements all 48 findings in [the Phase 1 audit](Archer-Phase-1-Audit.md), plus the requested collapsible left sidebar. The original audit remains the reference; this document records the changes and the limits of their validation.

## Sidebar

The sidebar has a collapse arrow and an expand button in the content header. **Ctrl+B** performs the same action. Desktop visibility is saved in the GUI preferences, and narrow overlay navigation preserves that preference. Navigation, drafts, focus, and the existing login/tray behavior survive toggling. GTK interaction and screenshot checks cover desktop and narrow layouts, both appearances, high contrast, and large text.

## Finding map

All entries below describe implemented changes. “Reviewed” identifies a code/configuration review where full installed-system or physical-device validation remains necessary.

| Finding | Change | Local evidence |
| --- | --- | --- |
| ARC-001 | GRUB is edited by a privileged atomic helper with trusted-directory checks, root ownership, backups, and a review barrier for previously writable/untrusted files. | GRUB ownership/mode, unsafe input, backup and failed-replace fixtures. |
| ARC-002 | Remove kernel binary settings-file loading and saving; userspace owns validated persistence. Restore fans to Auto at probe. | Prepared-source assertion: no kernel file I/O or legacy file paths; driver builds. |
| ARC-003 | Remove both unsafe persistence writers, eliminating their duplicate closes. | Prepared-source assertion and driver builds. |
| ARC-004 | Validate ACPI object type, buffer pointer and exact length before copying or logging battery/keyboard replies. | Compiled prepared C functions with null, wrong-type, missing-pointer, short, oversized and valid replies. |
| ARC-005 | Required package, file, service and image-rebuild failures propagate through installer conditionals. Record partial changes needing cleanup. | Real module package/copy failure injection, existing DKMS failures, Bats. |
| ARC-006 | Keep the daemon's read-only system configuration sandbox; dispatch fixed allowlisted configuration operations through transient systemd helpers after polkit. | Helper operation/failure tests; service boundary reviewed. Installed systemd/polkit integration remains a release check. |
| ARC-007 | Detect the installed audio plugin/config and include actual audio state in `GetAllSettings`. | Actual HardwareManager payload fixture and GTK contracts. |
| ARC-008 | Install the rnnoise plugin before configuring a filter under `pipewire.conf.d`; restart the owning user PipeWire service and check its source node. | Real private PipeWire server loads the actual module fragment and verifies source creation. |
| ARC-009 | Separate ENE/WMI mode lists, save stable effect IDs, migrate unambiguous integers, and require reselection for ambiguous legacy effects. | Both migration maps, actual WMI wire value, unsupported mode rejection, GTK selection test. |
| ARC-010 | Share `/etc/archer/battery.json` across daemon and battery-only service; boot/udev apply the saved choice instead of forcing 80%. | Repeated device events preserve disabled protection; native threshold fixture. |
| ARC-011 | Remove obsolete Archer AMD ACP `enable` options; let kernel driver selection apply. Retain edited legacy configurations for review. | Legacy content ownership check and source review; no generic AMD blacklist added. |
| ARC-012 | Verification and overall installation return failure when required checks fail; GUI verification checks activity, D-Bus Ping and polkit. | Broken verification fixture, GUI installation/verification tests. |
| ARC-013 | Shared validators check requests and each persisted setting; schema 2 migration isolates invalid entries and rejects unknown schemas. | Wrong top-level/nested types, invalid values, valid-entry preservation and migration tests. |
| ARC-014 | Lock settings mutation and atomic snapshot persistence together, fsync, and propagate save failures. | Concurrent writers and forced disk/replace failure tests. |
| ARC-015 | Bounded read/write/monitor workers keep GLib responsive; match client deadlines to authorization and operation limits. Run ENE in a bounded child process so SIGALRM stays on its main thread. | Private-bus slow authorization and firmware tests; Ping/read responsiveness and bounded write admission. |
| ARC-016 | Coalesce firmware scans with one in-flight lock and success/error TTLs; bound the shared read queue. | Concurrent scan/cache test; private-bus responsiveness. |
| ARC-017 | Structured subprocess results preserve status, stderr and timeout; kill process groups and bound transient units. Mutations report real failures. | Nonzero output, timeout, descendant cleanup, graphics readback and recovery failure tests. |
| ARC-018 | One helper orders daemon stop, module unload/load, and daemon start; persist request-specific recovery completion/failure for the reconnecting GUI. | Ordered command/failure tests and GTK recovery-state test. |
| ARC-019 | Enumerate CPU EPP policies individually and save/restore every changed policy. | Two distinct policy fixtures, including a high-numbered policy and readback. |
| ARC-020 | Game Mode saves originals before writes, validates supported controls, checks outcomes, rolls back partial changes, and preserves prior NVIDIA persistence by GPU identity. | Partial EPP failure rollback and retained failed NVIDIA restoration tests. |
| ARC-021 | Snapshot Game Mode originals with boot identity; restore after same-boot daemon restart and discard old-boot snapshots. | Same-boot crash recovery and old-boot discard tests. |
| ARC-022 | Publish observed thermal-profile changes from telemetry independently of ENE; GUI/tray refresh actual state. | Private-bus external profile signal test with no ENE controller. |
| ARC-023 | Verify bus/vendor/product with `HIDIOCGRAWINFO` on the opened HID descriptor before any report. | Reused hidraw node fixture asserts descriptor closure and zero writes to the wrong device. |
| ARC-024 | Counted scalar C parsing rejects signs, overflow, NUL and trailing input; D-Bus/daemon USB values are allowlisted. | Compiled checks for all six scalar stores and malformed userspace requests. |
| ARC-025 | Battery fallback stores return firmware/query/readback failures instead of claiming success or updating stale state. | Compiled setter/query failure and cache checks. |
| ARC-026 | Detect standalone battery calibration separately from Linuwu. | Calibration-only interface and unsupported `-1` fixtures. |
| ARC-027 | Check all external power supplies, including USB-PD; represent unknown power explicitly and require verified AC for calibration. | Multiple supplies, unplugged and missing-supply fixtures. |
| ARC-028 | Compute CPU utilization from differences between interval counters; first/reset samples are unknown and guest time is not double-counted. | Two-sample counter fixture. |
| ARC-029 | Parse help/dry-run before migration; dry-run skips manifest changes, package builds and verification. | Migration and real uninstaller help/dry-run byte-preservation tests. |
| ARC-030 | Parse a single literal quoted GRUB assignment without evaluation, edit whole tokens, and remove only recorded Archer additions. | Both quote styles, literal dotted tokens, duplicate/expanding assignment rejection and pre-existing token preservation. |
| ARC-031 | Write TLP configuration first, include only supported platform profiles, then explicitly run and check `tlp start`. | Ordering/configuration reviewed and package failure injected; live TLP status remains a release check. |
| ARC-032 | Record previous service enablement/activity/masks and configuration hashes. Retain packages, restore transitions, refuse administrator edits and distinguish skipped modules. | Service restoration order/state, unowned/edited configuration, interrupted write and declined transition tests. |
| ARC-033 | Validate manifests; preserve failed, unknown and missing cleanup entries, removing successful module entries incrementally. | Real isolated uninstaller failure/retry, malformed manifest and unknown/missing module tests. |
| ARC-034 | Catch directory iteration and per-node stat failures during diagnostics. | Attribute disappearance fixture retains the rest of the report. |
| ARC-035 | Rediscover ENE after enumeration changes, retry resume restoration at most five times, aggregate setter outcomes, and log actionable exhaustion. Serialize maintenance with user writes. | Descriptor failure tests and lifecycle code review; physical delayed enumeration/resume remains a release check. |
| ARC-036 | Cache successful and denied dmesg reads; denied diagnostics are unknown rather than healthy. | Repeated denied-read fixture asserts only one dmesg call. |
| ARC-037 | Point contributor/module instructions to `lib/modules.sh`; describe the daemon's mandatory D-Bus transport. | README, contributor guide and daemon docstring review. |
| ARC-038 | Restore the uninterrupted six-row destination table; move protocol prose below it. | Markdown source review. |
| ARC-039 | Rename camera setup and explain the separate producer requirement; remove background-blur claims and unused OpenCV dependency. | Installer/menu/README review. |
| ARC-040 | Remove the unused speculative MUX paths and dead force-parameter method/UI. | Source review; GTK controls tests. |
| ARC-041 | Use `/etc/gamemode.ini`, preserve administrator configuration, remove only owned old drop-ins, and verify intended governor configuration. | Canonical-path module fixture and ownership tests; effective per-user GameMode configuration remains a release check. |
| ARC-042 | Install polkit explicitly, create the system policy directory, and verify that its authority is reachable. | GUI dependency/preflight/failure fixtures and policy review; desktop authentication-agent check remains in deployment guidance. |
| ARC-043 | Require system-managed EnvyControl via repository/AUR package; remove `pip --user` fallback and use `/usr/bin/envycontrol` for root operations. | Preflight/path review and checked graphics helper test. |
| ARC-044 | Install native GameMode independently; add lib32 only when multilib provides it. | Native-only installation fixture. |
| ARC-045 | Reject unsupported init environments before migration or package mutation; remove Artix acceptance. | Early-failure fixture asserts zero migration/package calls. |
| ARC-046 | Persist loopback boot loading, let the kernel choose a free video number, verify label/driver identity, and defer incompatible already-loaded configurations to reboot. | DKMS failure fixture and lifecycle review; camera boot/streaming remains a release check. |
| ARC-047 | Migrate and remove enabled/disabled audio files, retain a disabled preference, and accept disabled installations during verification. Check hashes after a toggle rename. | Real PipeWire enable/disable/re-enable test and disabled-file cleanup/edit-protection fixture. |
| ARC-048 | Pass explicit helper roots/default paths so the existing CI ShellCheck version accepts the calls. | All shell scripts pass ShellCheck 0.9.0. |

## Local validation

No running hardware driver, installed Archer daemon, host audio session, or host power policy was changed during this work. Dependencies and build trees used for checks were placed under `/tmp`.

- 73 Python unit/fault tests pass with both pinned source repositories supplied. Extracted C functions run with undefined-behavior/bounds sanitizers.
- 114 Bats installer, kernel, module and uninstall tests pass.
- 11 GUI data-contract tests pass. The GTK suite contains 29 interaction/layout tests; capture runs render all destinations and sidebar states in light/dark, high contrast and large text.
- Private-session D-Bus tests pass for exported methods/signals, slow authorization, denied authorization, firmware checks, busy writes, and external profile changes.
- The actual PipeWire filter creates its `Audio/Source` node in a fresh private server; disable removes it and re-enable restores it. No capture devices or session manager are attached.
- Flake8, ShellCheck 0.9.0, desktop/policy metadata, syntax checks and whitespace checks pass.
- All three external modules compile against Linux `6.8.9-arch1-2`, `6.12.75-1-lts`, `6.18.50-2-cachyos-lts` and `7.2.4-3-cachyos`: **12 successful module builds**, with pinned preparation repeated for each Linuwu/battery target. The modules are Linuwu `1.0.archer3`, battery `0.1.0.archer2`, and distro v4l2loopback `0.15.4`. Older header builds report the expected GCC-version mismatch with this host's newer compiler; no source compile errors occurred. Builds do not register, install, sign or load modules.

Reproduce the principal checks from the checkout:

```sh
bats tests/*.bats
LINUWU_TEST_REPO=/path/to/linuwu BATTERY_TEST_REPO=/path/to/battery python -m unittest discover -s tests -v
python tests/gui_contract_test.py
dbus-run-session -- python tests/dbus_smoke.py
python tests/audio_smoke.py
xvfb-run -a dbus-run-session -- python tests/gui_test.py --capture
xvfb-run -a dbus-run-session -- python tests/gui_test.py --capture --high-contrast
xvfb-run -a dbus-run-session -- python tests/gui_test.py --capture --large-text
```

The audio test requires PipeWire and the system `noise-suppression-for-voice` plugin; `ARCHER_TEST_LADSPA` can point at an extracted package's LADSPA directory. CI installs that prerequisite and fetches both pinned driver repositories.

## Release checks requiring an installed system

The code changes are implemented; the following are not claims of completed physical validation:

- Fresh install and upgrade with the actual hardened system service, polkit acceptance/cancellation, audio/graphics/driver helper actions and reboot. Confirm denied actions make no configuration changes.
- Real Acer firmware responses, battery events/calibration, fan Auto/manual/curve transitions, ENE suspend/resume and late enumeration. The C harness is not an in-kernel KASAN fault-injection run.
- TLP status before/after activation, effective GameMode configuration with user overrides, multiple CPU policies, and NVIDIA persistence/recovery on supported hardware.
- Camera boot loading, existing loopback users/video-number conflicts, and streaming from an explicitly chosen external producer.
- Desktop-specific tray, keyboard/screen-reader navigation, portals and window scaling, following [the deployment guide](deployment.md) and [GUI matrix](gui.md#development-and-verification).

Legacy binary driver settings are intentionally retired. Ambiguous keyboard effects are intentionally not replayed. Previously unrecorded service states cannot be reconstructed; cleanup retains them conservatively. These migration decisions are part of this update, not deferred implementations.
