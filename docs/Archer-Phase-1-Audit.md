# Archer — Phase 1 audit

Audited on 14 September 2026. **This revision has release-blocking privilege and kernel-driver safety defects.** Four P0 findings need attention before further hardware testing or feature expansion. This report contains 48 findings: 4 P0, 7 P1, 25 P2, 4 P3 and 8 P4. Severity follows the categories in the request; P4 identifies packaging/distribution defects, rather than implying that their effects are always minor.

## Baseline and scope

| Context | Audited baseline |
| --- | --- |
| Repository | [otectus/Archer](https://github.com/otectus/Archer) |
| Branch / commit | `main` at [`dca6a2ba2f0aab70e1b1568878f8894d4a8aab3a`](https://github.com/otectus/Archer/tree/dca6a2ba2f0aab70e1b1568878f8894d4a8aab3a), committed 10 September 2026 |
| Implementation | Bash installer and 13 modules; Python daemon using system D-Bus/polkit; GTK4/libadwaita GUI; systemd services; pinned external C modules built with Kbuild/DKMS. Registry: `lib/modules.sh:7-34`; daemon: `gui/archer_daemon.py:1432-1537`; build entry points: `driver/prepare.sh:1-42`, `driver/build.sh:1-35`. No PKGBUILD is tracked. |
| Declared distributions | Arch, CachyOS, EndeavourOS, Manjaro and Garuda (`README.md:23-29`). Artix is also accepted in code (`lib/detect.sh:182-198`); see ARC-045. |
| Kernel evidence | The repository records past builds against Arch 6.8.9, Arch LTS 6.12.75 and CachyOS 6.18.48/7.2.3 (`docs/kernel-compatibility.md:167-183`). For the audited commit, GitHub CI passed `linux`, `linux-lts`, `baseline-6.8` and `baseline-6.12` build jobs. These are compile results, not hardware validation. |
| Hardware evidence | The family matrix is a recommendation matrix, not a tested-model list (`README.md:9-21`). PHN16S-71 ENE lighting has reported human-observed testing (`docs/ENE_PROTOCOL.md:1-13`). ANV16S-41 / BIOS V1.12 explicitly remains awaiting physical validation (`docs/anv16s-41.md:1-18`). I did not validate either laptop. |
| Compatibility constraints | No maintainer constraints were supplied. Proposed fixes should preserve existing CLI flags, `io.otectus.Archer1` method signatures, and settings compatibility where practical (`install.sh:20-146`, `gui/archer/client.py:21-65`, `gui/archer_dbus.py:20-45`). Keyboard effect migration and driver-state persistence need explicit maintainer decisions. |

I read all **97 tracked text files, 13,422 lines**, including tests, documentation, all nine Linuwu patches, service/policy files and helper scripts, and inspected the three tracked PNGs. The accompanying evidence archive contains the complete file inventory. I also prepared both pinned driver sources without installing them, read the complete 398-line prepared battery driver, and traced the relevant Linuwu firmware, fan, profile, keyboard, persistence and lifecycle paths. **I have not audited every line of the external Linuwu repository, EnvyControl, GameMode, TLP, PipeWire, or the kernel.** Their complete dependency trees are outside this audit.

Line references without a prefix refer to Archer at the commit above. `Linuwu/src/linuwu_sense.c` means the exact source produced by Archer's nine-patch series from [Linuwu commit 73a25ec](https://github.com/0x7375646F/Linuwu-Sense/blob/73a25ec243a44ba2b1703e8d0a76fa2735062506/src/linuwu_sense.c), selected by `driver/linuwu-sense/source.conf:2-7`. These prepared line numbers differ from upstream; relevant upstream ranges are supplied in the findings. `Battery/acer-wmi-battery.c` means the prepared [battery commit 9f90d75](https://github.com/frederik-h/acer-wmi-battery/blob/9f90d75cc9237aeed7964622d10dbdf4d2c7b518/acer-wmi-battery.c), selected by `driver/acer-wmi-battery/source.conf:1-8`; its cited C line numbers are unchanged by preparation.

Verified results:

- 100/100 Bats tests passed locally.
- 36/36 Python discovery tests passed locally, including compiled harnesses for the patched fan and RGB parsers; 8/8 GUI contract tests also passed.
- Additional isolated checks reproduced unchecked state replay, both double-close paths, a null-response crash, invalid settings-schema failure, a settings-save race, swallowed installer errors, dry-run manifest deletion and GRUB parsing corruption.
- [CI run 34516120551](https://github.com/otectus/Archer/actions/runs/34516120551) has 12 successful jobs and one failed ShellCheck job. The GTK and private-bus smoke jobs passed there. They were not rerun locally because this container lacks GI/GTK and the required system integration environment.
- No modules were loaded; no EC, ACPI, WMI, HID, host boot configuration or laptop firmware was written. Kernel crashes and physical thermal damage were **not** exercised. P0 hardware consequences below identify reachable unsafe code paths and conditional failure scenarios, not observed bricking.

## P0 findings

ARC-001 | P0 | lib/utils.sh:111-131,142-164

What's wrong: Both GRUB helpers create a temporary file as the invoking user and replace `/etc/default/grub` with `sudo mv`. Elevating `awk` does not elevate the shell redirection. Moving the file preserves its user ownership and normally its `0600` mode.

Why it matters (concrete failure scenario): After the documented non-root installation changes a GRUB parameter, the installer user owns a configuration file that a later root `grub-mkconfig` sources as shell code. That user can subsequently insert commands without obtaining new authorization. This creates a persistent privilege-escalation path and can compromise boot configuration. The sourcing operation is visible in [the GRUB configuration-generator source](https://github.com/rhboot/grub2/blob/master/util/grub-mkconfig.in).

Suggested fix: Create and validate a root-owned temporary file in `/etc/default`, explicitly set root ownership and safe permissions, then atomically replace the destination. Repair ownership of files affected by prior installs. Do not source the existing file in an unprivileged-to-root handoff without first establishing its trust.

Confidence: high — shell ownership flow is explicit. A scratch fixture confirmed `mv` preserves `0600`; this container only maps UID 0, so a distinct unprivileged-owner fixture could not run. Confirm the complete escalation in an isolated VM using the documented user installation, then inspect owner/mode and a harmless root-execution marker.

ARC-002 | P0 | driver/linuwu-sense/source.conf:2-7; Linuwu/src/linuwu_sense.c:3541-3587,3797-3807,4038-4051,4178-4208

What's wrong: Binary state files bypass the validated sysfs store paths. Thermal restoration forwards an unchecked saved integer to the firmware setter; a short read partially overwrites the defaults and still restores it. Keyboard restoration sends unchecked mode, brightness, speed, direction and packed color values. Relevant upstream persistence ranges are `src/linuwu_sense.c:3393-3441,4023-4053`; Archer changes the thermal restore body through its patch series.

Why it matters (concrete failure scenario): A stale, truncated, corrupted or manually edited `/etc/predator_state` or `/etc/four_zone_kb_state` is replayed during driver probe. The extracted C harness forwarded thermal profile `255`, keyboard mode/speed/brightness/direction `255`, and per-zone brightness `-1`. These are firmware writes outside the normal accepted ranges. Firmware reaction is unverified; under the requested severity definition, this is P0 even without demonstrated physical damage.

Suggested fix: Prefer removing kernel file persistence and using the validated userspace settings path. If retained, read into a temporary structure; require exact length, version and field validity; check supported profile/effect choices; only then replace active state. Route all writes through common validators. Reject the whole record on any error and leave fans in Auto. **Maintainer decision:** retire or migrate the two legacy binary formats.

Confidence: high — reproduced with extracted, compiled functions and stubbed firmware calls. Real firmware outcomes require controlled hardware testing after validation is fixed.

ARC-003 | P0 | driver/linuwu-sense/source.conf:2-7; Linuwu/src/linuwu_sense.c:3622-3628,4160-4166

What's wrong: Both state-save functions call `filp_close()` inside the negative-write branch and then call it again unconditionally on the same file. Upstream ranges: `src/linuwu_sense.c:3476-3482,4005-4011`.

Why it matters (concrete failure scenario): Removing/reloading the module while a state write returns ENOSPC or EIO closes the same kernel file twice. The harness counted two closes for each failure. On the supported 6.12 kernel API, [filp_close drops the file reference](https://github.com/torvalds/linux/blob/v6.12/fs/open.c#L1536-L1544); a duplicate close violates kernel object lifetime and can cause refcount or use-after-close faults. This is not an ordinary settings-save error.

Suggested fix: Close exactly once after the write, then test its result and return the write error or a short-write error. Apply the fix to both functions. Preserve the previous state file on failure rather than truncating it first.

Confidence: high — both double closes reproduced using actual extracted functions. Kernel crash manifestation depends on kernel version, context and fault timing; verify with kernel fault injection/KASAN in a VM, not by filling a user's laptop disk.

ARC-004 | P0 | Linuwu/src/linuwu_sense.c:3164-3169,3215-3220,3845-3850; Battery/acer-wmi-battery.c:148-162

What's wrong: Three Linuwu error branches explicitly accept a null ACPI result in their condition and then dereference it in the error log. Separately, the battery driver copies an eight-byte reply before checking its reported length. Linuwu's upstream keyboard instance is `src/linuwu_sense.c:3690-3695`; both dependencies are installed through the source manifests identified above.

Why it matters (concrete failure scenario): A firmware method that returns no object can turn a battery or keyboard query into a kernel null dereference. The extracted keyboard getter crashed with SIGSEGV on a stubbed successful call returning a null object. A short battery reply also permits an invalid kernel read before validation. On a kernel configured to panic on an oops, these paths can stop the machine. No actual Acer firmware response or kernel panic was induced.

Suggested fix: Handle a missing object separately, without reading any member. Check object type, buffer pointer and sufficient/exact length before copying. Return an explicit error for every malformed response. Apply the same discipline to all three Linuwu branches and the battery getter; add malformed-response tests.

Confidence: high for the code defects; the Linuwu null path was reproduced. Which Acer BIOS versions produce the triggering responses remains unverified. Inspect generated code and use a kernel test harness for the battery short-buffer case.

## P1 findings

ARC-005 | P1 | install.sh:254-265,444-446; modules/audio.sh:20-23,66-74; modules/gui.sh:70-112

What's wrong: `module_install` is called in a conditional, and its caller is also used in an OR-list. Bash consequently does not apply `errexit` to commands inside those functions. Several modules leave essential package/copy/configuration commands unchecked and finish with a successful log or assignment.

Why it matters (concrete failure scenario): A failed package transaction or file copy is followed by a success result and an installed-module manifest entry. An isolated call to the real audio module, with package installation forced to fail, returned success and recorded the audio packages. The same unchecked pattern occurs in GUI deployment, power, GameMode, Wi-Fi, firmware and audio enhancement (`modules/power.sh:24-39`, `modules/gamemode.sh:20-46`, `modules/wifi.sh:18-59`, `modules/firmware.sh:17-34`, `modules/audio-enhance.sh:19-80`).

Suggested fix: Explicitly propagate every essential command failure with `|| return 1`, recording completed steps as they occur. Preflight selected modules before mutation; replace process-wide `error` exits inside module functions with module failures where continuation is intended. Test actual module functions under the installer's conditional calling convention.

Confidence: high — failure propagation was reproduced, not inferred from `set -e` alone.

ARC-006 | P1 | gui/archer-daemon.service:20-31; gui/archer_daemon.py:1198-1216,1399-1415; gui/archer_dbus.py:316-333

What's wrong: The installed service makes `/etc` read-only and only allows writes beneath `/etc/archer`. Its driver-override operations write `/etc/modprobe.d`, its audio toggle renames files beneath `/etc/pipewire`, and it invokes a graphics configuration tool from the same sandbox.

Why it matters (concrete failure scenario): The default installed service cannot save/remove driver overrides or rename an installed audio filter; these operations fail with a read-only-filesystem error. Graphics configuration cannot modify protected configuration files either: the inspected [EnvyControl source](https://github.com/bayasdev/envycontrol/blob/main/envycontrol.py) defines `/etc` configuration paths at `envycontrol.py:18-65` and writes files at `envycontrol.py:525-539`. [systemd's own documentation](https://github.com/systemd/systemd/blob/main/man/systemd.exec.xml) defines this `ProtectSystem=full` behavior. Running the Python script directly can hide the deployment failure.

Suggested fix: Separate controlled configuration changes into narrowly authorized helpers, or allow only the specific required paths after defining the write contract. Keep broad system protection. Test through the actual service unit, including graphics-tool configuration and image-rebuild requirements.

Confidence: high for the direct file writes and inherited sandbox; test in a systemd VM to verify the complete EnvyControl transaction.

ARC-007 | P1 | gui/archer_daemon.py:438-497,1071-1102; gui/archer/pages/controls.py:313-318

What's wrong: The GUI requires an `audio_enhancement` feature and a top-level audio settings object. The daemon never adds that capability or returns that object; persisted audio settings only appear inside `saved_settings`.

Why it matters (concrete failure scenario): Installing the audio enhancement module still leaves the Audio page unavailable. Adding only the feature flag would leave its switch reading the wrong payload and showing disabled after refresh. The GUI fixture supplies the missing contract, masking the production mismatch (`tests/gui_test.py:65`).

Suggested fix: Detect the installed enabled/disabled filter and its plugin, advertise the capability, and return its actual enabled state at the documented payload location. Add a contract test against the real daemon's settings construction.

Confidence: high — both producer and consumer were read; no hardware is needed to confirm the absent fields.

ARC-008 | P1 | modules/audio-enhance.sh:9-10,41-77; gui/archer/pages/controls.py:320-328

What's wrong: The module places the filter in `filter-chain.conf.d` but only tells users, and the GUI, to restart the ordinary PipeWire service. It creates no process or user service that runs the filter-chain configuration.

Why it matters (concrete failure scenario): On a normal PipeWire setup without an independently configured filter-chain process, the advertised virtual microphone is never created. [PipeWire's documentation](https://docs.pipewire.org/page_man_pipewire-filter-chain_conf_5.html) requires starting PipeWire with the filter-chain configuration for those fragments to be consumed.

Suggested fix: Either install the filter as a supported drop-in for the running PipeWire configuration or provide and manage the appropriate user filter-chain service. Restart the component that owns the filter and verify that the source node appears. Make the plugin's installation a prerequisite, not a warning followed by success.

Confidence: high — installation/runtime paths disagree with the documented configuration mechanism. Confirm end-to-end in a clean user PipeWire session.

ARC-009 | P1 | gui/archer/pages/controls.py:21-29,247-251; gui/archer_daemon.py:847-859; Linuwu/src/linuwu_sense.c:3959-3995

What's wrong: The GUI always sends indexes from the new nine-entry ENE effect list. On the WMI fallback, the daemon forwards those indexes unchanged to the old eight-mode ABI. Existing saved indexes also have no backend/schema migration. The pinned [Linuwu effect mapping](https://github.com/0x7375646F/Linuwu-Sense/blob/73a25ec243a44ba2b1703e8d0a76fa2735062506/README.md#four-zone-rgb-keyboard) is defined in upstream `README.md:222-236` and C `src/linuwu_sense.c:3803-3839`.

Why it matters (concrete failure scenario): On a supported WMI keyboard, selecting Wave sends index 4, which means Shifting; selecting Twinkling sends 8, which the driver rejects. An upgrade can change the meaning of a previously saved effect even if the user does not edit it.

Suggested fix: Expose backend-specific supported effects and translate stable effect identifiers to each wire ABI. Migrate legacy saved integers using an explicit schema/backend version. **Maintainer decision:** define how to migrate ambiguous existing settings and preserve old D-Bus clients.

Confidence: high — mappings and the unchanged forwarding path are explicit. Physical effect appearance still needs model-specific validation.

ARC-010 | P1 | modules/battery.sh:20-35,44-57; gui/archer_daemon.py:544-549,716-722

What's wrong: The battery module enforces 80% on every matching native battery add/change event, while the GUI disables the limit by writing 100%. The alternative boot service unconditionally writes enabled state and has no ordering or settings agreement with the daemon.

Why it matters (concrete failure scenario): A user disables charge limiting to prepare for travel; a later battery change event re-enables 80%. On the fallback path, boot ordering decides whether the service or the saved GUI preference wins. Both writers are installed by Archer.

Suggested fix: Give one component ownership of the desired charge limit. Have device events request reapplication of that saved preference, or restrict the installer rule to a one-time default. Remove obsolete persistence backends during transitions. Preserve a functioning battery-only installation if the GUI is optional.

Confidence: high for the conflicting writes; event timing and fallback boot order require integration tests.

ARC-011 | P1 | modules/audio.sh:27-38; README.md:71-74

What's wrong: The AMD fix writes `enable` parameters for three ACP modules that do not expose those parameters. It does not select the SOF driver as its comments and documentation claim.

Why it matters (concrete failure scenario): An AMD Acer user runs Audio Fix to resolve an ACP/SOF conflict, reboots, and receives the same binding behavior. The supposed disable directives do not disable the legacy PDM drivers. In the supported 6.12 baseline, the [Raven PCI driver](https://github.com/torvalds/linux/blob/v6.12/sound/soc/amd/raven/pci-acp3x.c) has no such parameter, while the [Renoir](https://github.com/torvalds/linux/blob/v6.12/sound/soc/amd/renoir/acp3x-pdm-dma.c#L18-L23) and [YC](https://github.com/torvalds/linux/blob/v6.12/sound/soc/amd/yc/acp6x-pdm-dma.c#L19-L24) PDM drivers expose a gain parameter instead.

Suggested fix: Remove the ineffective directives. Select any replacement only from the actual PCI/ACPI device, kernel driver and a validated model quirk; do not replace this with a blanket AMD blacklist. Verify supported module parameters before writing configuration.

Confidence: high for the 6.12 source checked; validate other supported kernel branches before choosing a replacement fix.

## P2 findings

ARC-012 | P2 | install.sh:288-313,331-362,448-457; modules/gui.sh:116-128,196-214

What's wrong: Verification counts failures but never returns them. `--verify` explicitly exits zero even if every check fails. GUI verification only checks two files and whether the service is enabled; a crashed daemon passes those tests.

Why it matters (concrete failure scenario): Automated deployment treats an unusable installation as verified. A GUI service that repeatedly crashes can still produce a passing GUI check and an overall successful installer result. The startup warning incorrectly suggests a missing Linuwu module prevents the daemon from starting, although limited operation is supported (`gui/archer_daemon.py:363-380,382-393`).

Suggested fix: Return a nonzero status when a required verification fails, propagate it from both installation and `--verify`, and distinguish pending-reboot checks from runtime failures. Check service activity and D-Bus Ping for GUI readiness; report the actual startup error.

Confidence: high — return paths and checks are explicit. Add an installed-but-crashed service fixture.

ARC-013 | P2 | gui/archer_daemon.py:196-205,217-222,501-522,572-578; gui/archer_dbus.py:257-278,311-325

What's wrong: Settings loading accepts any valid JSON value and restoration assumes particular nested dictionaries and required keys. Only JSON syntax and file errors are handled. Keyboard/audio D-Bus methods also parse JSON and access fields without the shape validation used by the fan-curve method.

Why it matters (concrete failure scenario): A settings file containing `[]` causes an AttributeError on the first lookup. A partially migrated keyboard record raises KeyError during startup and prevents the service from becoming available. The empty-list case was reproduced using the actual SettingsStore class. Malformed client JSON produces an uncaught D-Bus exception rather than the documented structured operation failure.

Suggested fix: Share validators between requests and persisted settings. Validate the top-level object and each stored setting independently. Ignore/quarantine invalid entries with a precise log, preserve valid settings, and introduce a schema version for migrations. Do not let one malformed optional feature prevent daemon startup.

Confidence: high — wrong-shape failure reproduced; nested restoration assumptions are explicit.

ARC-014 | P2 | gui/archer_daemon.py:207-227,305-314,1165-1168; gui/archer_dbus.py:209-215

What's wrong: SettingsStore has no synchronization and every save uses the same temporary filename. The fan worker and D-Bus thread both modify it. Save failures are logged but not reported to callers, which continue returning success after an in-memory update.

Why it matters (concrete failure scenario): A fan watchdog disables saved curves while a user changes another setting. Concurrent saves truncate/replace the same file; one rename fails, and interleaved writes can corrupt or lose the persisted state. The isolated two-writer test reproduced a failed rename. Separately, a full filesystem makes an apparently saved setting disappear after reboot.

Suggested fix: Lock mutation and persistence together; serialize a snapshot to a uniquely named temporary file; replace atomically and return persistence failures to the caller. Use fsync if durability across power loss is promised. Consider one transaction for settings that must change together.

Confidence: high — concurrent rename failure reproduced; lost/corrupted-file manifestations depend on interleaving.

ARC-015 | P2 | gui/archer_dbus.py:48-57,75-83,158-167; gui/archer/client.py:21-30; gui/archer_daemon.py:942-947,978-983

What's wrong: Authorization and most hardware probes run synchronously in the daemon's GLib/D-Bus main loop. Ordinary client calls time out after five seconds. Two uncached NVIDIA probes can each consume three seconds, and interactive authentication can legitimately take longer than five seconds.

Why it matters (concrete failure scenario): A user takes several seconds to authenticate a fan/profile change: the GUI reports a timeout, telemetry stops, and the authorized operation can still execute afterward. A slow NVIDIA driver similarly stalls unrelated controls and freshness signals. Caching does not eliminate the blocking cache-miss path.

Suggested fix: Make authorization and slow work asynchronous with bounded execution and explicit pending/completion state. Match client deadlines to interactive authorization. Keep telemetry publication on the main loop while collecting probes elsewhere. **Implementation constraint:** the ENE helper's SIGALRM timeout only works on the main thread (`gui/archer_ene.py:240-252`), so moving it to a generic worker requires a different timeout design.

Confidence: high for blocking paths and incompatible deadlines; validate a delayed polkit response and timed-out NVIDIA probes on a private bus.

ARC-016 | P2 | gui/archer_dbus.py:143-154; gui/archer_daemon.py:1338-1369; gui/io.otectus.Archer1.conf:8-12

What's wrong: Every unauthenticated firmware-info request launches a new thread and potentially another 30-second root `fwupdmgr` process. There is no shared in-flight request, concurrency limit, rate limit or result TTL for this entry point.

Why it matters (concrete failure scenario): Any local client allowed onto the system bus can flood this read method and accumulate threads and child processes, degrading the root hardware-control service. Repeated legitimate clients can also run redundant scans.

Suggested fix: Coalesce concurrent scans, use one bounded worker, cache results with an explicit age, and return the current in-progress state. Keep read-only status accessible without allowing unbounded work.

Confidence: high for unbounded scheduling; resource-exhaustion thresholds were not stress-tested.

ARC-017 | P2 | gui/archer_daemon.py:122-143,1209-1216,1373-1397

What's wrong: `run_cmd` discards exit status and stderr, reducing success, failure and timeout to text. Graphics switching decides success from nonempty output without the word “error”; restart methods discard scheduling results. Shell-based timeout handling also lacks explicit descendant cleanup.

Why it matters (concrete failure scenario): A graphics tool exits nonzero with “Failed to update configuration”, yet Archer records a successful mode change. A failed `systemd-run` request is likewise reported as scheduled. A timed-out shell command can leave child work running after the GUI considers it failed.

Suggested fix: Return a structured result containing exit status, stdout, stderr and timeout state. Use argument lists for fixed commands, check return codes, and query actual graphics configuration before declaring success. Where descendants are possible, use a bounded process group/helper with explicit cleanup.

Confidence: high for the discarded-status and false-success paths; descendant behavior depends on the command and shell and should be tested with a harmless delayed child.

ARC-018 | P2 | gui/archer_daemon.py:1383-1397,363-365; Linuwu/src/linuwu_sense.c:2416-2429

What's wrong: Driver reload and daemon restart are scheduled as separate timers, one and three seconds in the future. There is no dependency requiring module loading to finish before hardware detection runs.

Why it matters (concrete failure scenario): A slow module probe or profile-registration retry lasts beyond the assumed gap. The daemon starts while the driver is incomplete, caches missing capabilities, and remains unable to expose controls after the module finishes. Linuwu's retry loop alone can exceed the gap.

Suggested fix: Use one sequenced recovery helper/job: restore Auto, unload/load with checked results, then restart or explicitly re-detect the daemon. Report completion/failure instead of assuming elapsed time establishes readiness.

Confidence: high for the race; confirm with an intentionally delayed mocked module-load command.

ARC-019 | P2 | gui/archer_daemon.py:1244-1249,1257-1265,1277-1282

What's wrong: Game Mode selects only the first CPU's EPP file and writes/restores that single path. It does not enumerate independent cpufreq policies.

Why it matters (concrete failure scenario): On a CPU with multiple policies, only one policy receives the requested EPP; the rest retain their previous values. The GUI nevertheless claims Game Mode is active.

Suggested fix: Enumerate unique policy directories under `cpufreq/policy*`, save each supported EPP, apply the desired value to each, and restore per-policy values while tolerating CPU hotplug. Avoid assuming that 16 CPU indexes cover the machine.

Confidence: high — only one path is returned and written. A multi-policy sysfs fixture confirms the affected configuration.

ARC-020 | P2 | gui/archer_daemon.py:1252-1288; gui/archer_dbus.py:292-299

What's wrong: Game Mode ignores failures from profile/EPP/NVIDIA operations, marks itself active unconditionally, and the D-Bus method always returns success. Deactivation also unconditionally turns NVIDIA persistence off without saving its prior state.

Why it matters (concrete failure scenario): A machine rejects `performance`, denies an EPP write or has no functioning NVIDIA driver. The GUI reports successful optimization anyway. If persistence was already enabled for another application, switching Archer Game Mode off changes that application's prior configuration.

Suggested fix: Define the supported component set from capabilities, check each operation, save every changed prior value, and report partial failure or roll back completed changes. Return the actual outcome through D-Bus. **Maintainer decision:** define whether Game Mode is best-effort or transactional.

Confidence: high — unchecked calls and unconditional results are explicit.

ARC-021 | P2 | gui/archer_daemon.py:354-356,580-583,1257-1270,1277-1287

What's wrong: The daemon persists `game_mode_saved_state` but never loads it into its runtime snapshot. Restart restoration calls activation again and overwrites the saved original values with whatever the machine is currently using.

Why it matters (concrete failure scenario): The daemon crashes while Game Mode is active. After restart, the still-active performance settings become the new “original” values, so turning Game Mode off no longer restores the pre-game state. Graceful shutdown has a different path that deactivates it (`gui/archer_daemon.py:1514-1532`).

Suggested fix: Restore and validate the saved transaction snapshot before resuming active mode, or reconcile/roll back the previous transaction at startup. Distinguish same-boot crash recovery from a new boot using boot identity. Do not overwrite the original snapshot on recovery.

Confidence: high — the saved key has a write and removal path but no recovery read.

ARC-022 | P2 | gui/archer_daemon.py:800-807,1104-1117; gui/archer_dbus.py:160-166; gui/archer/state.py:116-118,136-145

What's wrong: External profile changes only update the ENE button LED. The daemon emits `ProfileChanged` for its own setter, and regular telemetry omits the profile. The GUI refreshes settings on its own signal or an AC-state transition, not an external profile transition.

Why it matters (concrete failure scenario): A hardware hotkey, TLP or another profile client changes the profile while AC state stays unchanged. Archer's tray and profile controls continue displaying the previous profile even though the machine and possibly the button LED have changed.

Suggested fix: Observe the active profile provider and emit a change signal for every actual transition, regardless of writer or ENE availability. Include enough state/version information to refresh controls without disturbing staged edits.

Confidence: high — producer and refresh paths were traced. Confirm with an external profile writer on a test provider.

ARC-023 | P2 | gui/archer_ene.py:188-220,298-309,313-331,335-371

What's wrong: HID identity is checked through sysfs before opening `/dev/hidrawN`; the opened file descriptor is not checked again. The application lock does not prevent kernel removal and reassignment of that device number.

Why it matters (concrete failure scenario): Between resolution and open, an I2C-HID device rebinds and the same node number refers to the touchpad or input keyboard. Archer can then send vendor reports to a device it intended to forbid. The source itself documents node-number reuse and input-loss risk (`gui/archer_ene.py:42-47,67-72`). I did not reproduce reassignment on hardware and am not asserting a demonstrated hard lock.

Suggested fix: Open the candidate and validate its bus/vendor/product identity using the fd's HID information before any write; verify the required report layout as appropriate. Fail closed on mismatch/disconnect and retry discovery with a bounded policy. Add a mocked node-reuse test.

Confidence: high for the check/use gap; medium for the hardware failure scenario, which needs a controlled hotplug/rebind test.

ARC-024 | P2 | gui/archer_dbus.py:217-225; gui/archer_daemon.py:730-734; Linuwu/src/linuwu_sense.c:3096-3105,3253-3263,3282-3292

What's wrong: Several scalar sysfs setters use `sscanf` into an eight-bit value and do not require a complete valid token. USB charging also accepts an unrestricted D-Bus integer before forwarding it. Post-parse checks only validate the narrowed result.

Why it matters (concrete failure scenario): Trailing garbage can be accepted, and oversized numeric input can narrow to an allowed value rather than be rejected. An invalid charging level can therefore be applied as a different valid level and the original invalid integer saved by the daemon. This is incorrect request handling; the resulting firmware value is still from the bounded scalar allowlist, so it is not classified as P0.

Suggested fix: Use `kstrtou8`/`kstrtobool` or an equivalent counted full-token parser before range checks; validate USB charging against `{0,10,20,30}` in the daemon too. Cover overflow, signs and trailing data across the remaining scalar attributes.

Confidence: high for partial-token acceptance and missing API validation; exact overflow behavior is parser/compiler dependent and should be tested with the target kernel parser.

ARC-025 | P2 | Battery/acer-wmi-battery.c:258-268,299-314,326-342

What's wrong: The independent battery driver's health and calibration stores discard the firmware setter's status and always report the byte count after parsing. The subsequent status refresh also discards its own error.

Why it matters (concrete failure scenario): Firmware rejects a charge-limit or calibration operation. Sysfs still reports a successful write; Archer's `write_sysfs` sees success and its D-Bus setter persists the requested preference (`gui/archer_daemon.py:82-91,716-722`, `gui/archer_dbus.py:209-215`). The actual battery state can remain unchanged.

Suggested fix: Translate ACPI failure into a negative sysfs errno, propagate refresh errors, and verify the resulting setting before reporting success where the interface permits it. Do not overwrite the cached state on a failed query.

Confidence: high — failure statuses are explicitly ignored. A stubbed failed WMI setter and readback would confirm the full caller chain.

ARC-026 | P2 | gui/archer_daemon.py:460-484,680-702; Battery/acer-wmi-battery.c:317-353

What's wrong: The daemon supports the independent battery driver's charge-limit fallback but only detects/uses calibration beneath a Linuwu Sense subtree. It never exposes the independent driver's calibration node, even when that driver reports calibration support.

Why it matters (concrete failure scenario): A supported Acer uses `acer-wmi-battery` without Linuwu. Charge limiting appears in Archer, while the working kernel calibration interface is unavailable through its Battery page.

Suggested fix: Discover calibration independently, check that its reported state indicates support, and route reads/writes through that backend. Keep unsupported `-1` states unavailable. Use capability fixtures with no Linuwu device.

Confidence: high — the independent driver exports the node and the daemon has no corresponding fallback path.

ARC-027 | P2 | gui/archer_daemon.py:1027-1035; gui/archer/pages/controls.py:151-157

What's wrong: AC detection returns the state of the first `Mains` supply, ignores other online supply types, and reports AC when it cannot determine a state.

Why it matters (concrete failure scenario): With an offline barrel-jack supply and an online USB-C supply, the GUI reports battery power. With no recognized supply, it reports AC even while discharging. Controls using that flag, including the calibration guard, receive incorrect information.

Suggested fix: Evaluate all relevant external supplies, recognize their actual power-supply types, and represent unknown separately from true. Use battery status as a cautious fallback and require positively verified external power for operations that depend on it.

Confidence: high for the selection/default logic; validate on multi-supply fixtures and the affected laptop's exported supply types.

ARC-028 | P2 | gui/archer_daemon.py:963-970,1104-1114

What's wrong: CPU utilization is calculated from one cumulative `/proc/stat` reading. It is a since-boot ratio rather than usage over the sampling interval, and the arithmetic omits several CPU time categories.

Why it matters (concrete failure scenario): After a long idle period, a game fully loads the CPU while Archer continues showing a low usage percentage. The monitoring display cannot reliably describe current load.

Suggested fix: Keep consecutive total/idle counter snapshots and calculate deltas, accounting for the documented fields without double-counting guest time. Mark the first sample unavailable. Read the file directly rather than spawning awk every update.

Confidence: high — the formula uses cumulative counters with no previous sample.

ARC-029 | P2 | uninstall.sh:16-38; lib/manifest.sh:25-48; modules/audio-enhance.sh:30-35

What's wrong: Uninstall migrates the manifest before parsing `--dry-run` or `--help`. Even when migration sees dry-run mode, its legacy-file deletion bypasses the dry-run wrapper. Audio enhancement also runs `makepkg` directly rather than through the wrapper.

Why it matters (concrete failure scenario): A dry-run migration leaves no destination but deletes the only legacy manifest; this was reproduced with an isolated fixture. `uninstall.sh --help` can also perform migration before showing help. A dry-run audio install still invokes a package-building command, although a fresh empty build directory will normally make it fail.

Suggested fix: Parse options before all migration, gate the entire migration transaction on dry-run, and remove the source only after verified destination creation. Route the build subshell through the same dry-run-aware execution mechanism.

Confidence: high — manifest deletion with no destination was reproduced.

ARC-030 | P2 | lib/utils.sh:114-130,147-163

What's wrong: GRUB editing assumes one exact double-quoted assignment, detects parameters by substring and removes them using an unescaped regular expression. It does not track whether Archer added the parameter.

Why it matters (concrete failure scenario): A valid single-quoted setting becomes a command line containing the literal `GRUB_CMDLINE_LINUX_DEFAULT=` assignment; the fixture reproduced this corruption. A dotted parameter can match unintended text during removal. Existing user parameters may be removed during uninstall, including a thermal parameter that the current thermal install never adds (`modules/thermal.sh:29-49`).

Suggested fix: Parse the supported shell assignment safely without evaluating arbitrary user text, operate on complete command-line tokens, and refuse unsupported/ambiguous syntax. Record precisely which tokens Archer added and remove only those. Preserve the original file for recovery; apply ARC-001's ownership fix simultaneously.

Confidence: high — single-quoted parsing failure reproduced; substring/regex and ownership behavior are explicit.

ARC-031 | P2 | modules/power.sh:38-65

What's wrong: TLP is enabled/started before Archer writes its configuration, and the module never reapplies settings after the write.

Why it matters (concrete failure scenario): A new TLP process applies the previous/default configuration. An already-active TLP service does not get restarted by `enable --now`. The module reports configured power management while the new values wait for another TLP activation or power event.

Suggested fix: Write the validated configuration first, then explicitly apply it using [TLP's supported activation command](https://linrunner.de/tlp/usage/tlp.html) and check the result. Report settings rejected by the current provider rather than assuming all profile names exist.

Confidence: high for command ordering and missing activation; confirm with TLP status before and after an installation.

ARC-032 | P2 | modules/power.sh:20-39,74-85; modules/firmware.sh:17-39; modules/wifi.sh:47-55,132-146

What's wrong: Service changes are not recorded as owned transitions. Uninstall disables existing TLP/fwupd and unmasks rfkill regardless of their previous state; it does not restore a removed power-profiles-daemon. Wi-Fi enables services but its uninstaller claims there were no persistent changes. Several optional modules also overwrite/remove fixed configuration paths without the ownership checks used by newer modules (`modules/audio.sh:29-39,76-80`, `modules/touchpad.sh:26-31,65-79`).

Why it matters (concrete failure scenario): A user with working, independently configured TLP or firmware updates tries Archer and then uninstalls it. Existing services are disabled or administrator masks are undone. Declining the power module's conflict prompt returns success and can still cause that module to be recorded for later uninstall (`modules/power.sh:23-27`, `install.sh:263-265`).

Suggested fix: Record prior package/service/configuration state and only undo Archer's changes. Distinguish “skipped” from “installed”. Preserve administrator-edited files with explicit ownership/backups and restore a replaced service only if it was previously enabled. **Maintainer decision:** define reversible behavior when switching between power managers.

Confidence: high — install/uninstall operations and absent state records were traced.

ARC-033 | P2 | uninstall.sh:50-84; lib/manifest.sh:124-143; modules/gui.sh:163-193

What's wrong: Uninstall does not check whether manifest parsing succeeded. It also skips unknown/missing module files without retaining their cleanup record. Several uninstall functions ignore essential removal failures and finish successfully, so the caller deletes the manifest anyway.

Why it matters (concrete failure scenario): A malformed manifest produces an empty module list, then is deleted. A read-only installation directory prevents GUI removal, yet the successful final log lets uninstall discard the record needed for retry. Components can remain installed after “Cleanup Complete”.

Suggested fix: Abort manifest-based cleanup on parse/schema errors while retaining the original. Track remaining modules and update the manifest only after each verified cleanup; keep skipped/failed records. Add explicit failure returns to destructive cleanup steps while still attempting safe independent cleanup.

Confidence: high — unchecked parsing and success paths are explicit.

ARC-034 | P2 | scripts/archer-diagnose.py:47-56

What's wrong: The diagnostic helper catches failures while reading values but performs directory iteration and `p.stat()` outside those protections.

Why it matters (concrete failure scenario): A driver unloads or a sysfs node disappears during collection—the situation this support report is meant to diagnose. Collection raises OSError instead of producing a partial report, losing otherwise useful evidence.

Suggested fix: Catch disappearance/permission errors around enumeration and metadata access per node; record unavailable entries and continue. Keep path/value/mode collection in one protected block.

Confidence: high — metadata accesses lack error handling. Confirm with a fixture removed between discovery and stat.

ARC-035 | P2 | gui/archer_daemon.py:443-459,809-845,1487-1495

What's wrong: ENE availability is cached at startup. Resume performs one fixed-delay attempt using that cached flag; it does not rediscover an initially missing controller. Reapplication also ignores false return values from the lighting setters and logs success afterward.

Why it matters (concrete failure scenario): The controller enumerates after the daemon starts or later than the two-second resume delay. Lighting remains absent or on the ineffective WMI fallback until restart. If an ENE setter catches a write error and returns false, the resume log still says lighting was reapplied.

Suggested fix: Rediscover the controller after relevant device events and resume, retry only while it is not yet ready within a bounded window, and aggregate setter results. Emit an actionable failure after retries expire. Coordinate this with fd identity validation in ARC-023.

Confidence: high for cached detection, one-shot retry policy and ignored return values; exact enumeration timing needs hardware validation.

ARC-036 | P2 | lib/detect.sh:122-145,209-212; modules/wifi.sh:62-77; modules/audio.sh:41-47

What's wrong: Kernel-log probes run as the ordinary installer user, suppress permission errors and treat unavailable logs as empty logs. Several diagnostic decisions depend on finding error strings in that output.

Why it matters (concrete failure scenario): On a kernel restricting unprivileged dmesg access, touchpad failures are not recommended for repair, MediaTek recovery never sees the triggering error, and the Intel audio path can claim firmware appears correct despite having read no log. The documented user installation encounters this whenever that restriction is enabled.

Suggested fix: Represent unavailable diagnostics explicitly and report the missing permission. Obtain a narrowly scoped privileged read when needed, or use accessible device state; do not equate missing evidence with healthy hardware. Avoid repeatedly rereading the same unavailable log.

Confidence: high for restricted-log environments; no claim is made that every supported distribution enables that restriction by default.

## P3 findings

ARC-037 | P3 | README.md:334; CONTRIBUTING.md:92-96; lib/modules.sh:7-34; gui/archer_daemon.py:1-6,1450-1456

What's wrong: Contributor instructions say to edit module registry arrays in `install.sh`, but the canonical arrays are in `lib/modules.sh`. The daemon's leading documentation still claims it exposes a Unix socket, despite the mandatory D-Bus implementation.

Why it matters (concrete failure scenario): A contributor registers a module in the wrong place or a support/debugging tool searches for an obsolete socket. These instructions contradict the source of truth.

Suggested fix: Correct both registry references and the daemon docstring. Keep registry/transport guidance in one authoritative location and link to it.

Confidence: high — direct documentation/implementation contradictions.

ARC-038 | P3 | docs/gui.md:5-14

What's wrong: A paragraph interrupts the destination table before its Audio and System rows.

Why it matters (concrete failure scenario): Markdown renders the last two destinations outside the table, making the documented six-destination interface incomplete or malformed.

Suggested fix: Move the ENE explanatory paragraph below the complete table, preserving the six contiguous rows.

Confidence: high — the table is structurally interrupted by non-table text.

ARC-039 | P3 | modules/camera-enhance.sh:5-7,34-68; README.md:117-118

What's wrong: The module advertises background blur and tells users to select Archer Camera, but only installs OpenCV and a loopback device. It configures no capture/effect/frame-producing process. The README calls it a foundation, but does not explain the missing producer in the installation flow.

Why it matters (concrete failure scenario): A user selects Camera Enhancement expecting usable processed video. There are no frames or blur, and with exclusive capabilities the device may not appear as a capture source until a producer attaches. [v4l2loopback's own documentation](https://github.com/v4l2loopback/v4l2loopback) describes the separate producing application requirement.

Suggested fix: Rename/describe the module as loopback setup and state the required external producer before instructing users to select it. Do not claim background blur until a working producer is integrated and verified.

Confidence: high — no producer is installed or launched by the module.

ARC-040 | P3 | gui/archer_daemon.py:1218-1241,1417-1420; gui/archer/pages/controls.py:191-206

What's wrong: `detect_mux` is never called or included in a response; the GUI's MUX row remains a fixed unavailable placeholder. `force_driver_parameter` is also unreferenced and targets a `force_parameter` sysfs node absent from the prepared driver's exported attributes (`Linuwu/src/linuwu_sense.c:3744-3788,4220-4225`).

Why it matters (concrete failure scenario): Future maintainers can mistake these methods for usable recovery/detection paths. The visible MUX row suggests a detector exists even though it cannot update.

Suggested fix: Remove dead code/placeholders or deliberately wire a tested capability into the payload. If exposing topology, do not infer the existence of a switch solely from the current panel connection; require evidence for the hardware-MUX claim.

Confidence: high for unused code and the fixed row; actual MUX capability is unverified.

## P4 findings

ARC-041 | P4 | modules/gamemode.sh:24-46,49-59

What's wrong: Archer writes `/etc/gamemode.d/archer.ini`, which is not one of GameMode's documented configuration locations. Its verification only checks that the executable exists.

Why it matters (concrete failure scenario): GameMode runs, but Archer's governor, GPU and custom-hook settings are ignored. [GameMode's configuration documentation](https://github.com/FeralInteractive/gamemode/blob/master/README.md?plain=1) lists `gamemode.ini` in the working directory, user configuration directory, `/etc`, or its shared-data directory; it does not load this drop-in path.

Suggested fix: Use a supported configuration location and merge only Archer-owned settings without overwriting an administrator's configuration. Verify that GameMode actually reads the resulting configuration, and remove the obsolete file during migration.

Confidence: high — generated path compared with the project's documented search locations.

ARC-042 | P4 | modules/gui.sh:59-87; gui/archer_dbus.py:48-57; install.sh:190-192

What's wrong: GUI installation does not explicitly install/check polkit despite making it mandatory for every mutating D-Bus action. It also assumes the polkit actions directory exists when copying the policy.

Why it matters (concrete failure scenario): A minimal Arch desktop can run the GUI and root daemon but cannot authorize hardware changes; the policy copy can fail and be swallowed by ARC-005. An authentication agent may also be absent from a lightweight desktop session.

Suggested fix: Declare/install the polkit dependency, create the required destination directory with root ownership, and verify that the authority is available. Diagnose missing session authentication agents without arbitrarily selecting a desktop-specific agent for the user.

Confidence: high for the undeclared requirement; verify a GUI-only installation in a clean minimal Arch image.

ARC-043 | P4 | modules/gpu.sh:47-53,72-80; gui/archer_daemon.py:1186-1189,1202-1209

What's wrong: Without an AUR helper, EnvyControl is installed with the ordinary user's `pip --break-system-packages`, while subsequent execution is privileged and the daemon resolves a bare command in its system environment.

Why it matters (concrete failure scenario): A user-local executable/package is found during installation but is unavailable to the root daemon's PATH/import environment. Alternatively pip fails, only a warning is emitted, and installation proceeds until later resolution fails. The fallback does not establish a reproducible system dependency.

Suggested fix: Install a system-managed EnvyControl package, or a dedicated root-owned environment/helper with an explicit executable path and dependency manifest. Do not modify the user's Python installation as a fallback for a root service dependency; propagate failure immediately.

Confidence: high for the user/system environment mismatch; the precise failure depends on pip configuration and EnvyControl's packaging.

ARC-044 | P4 | modules/gamemode.sh:20-22

What's wrong: The mandatory package transaction includes `lib32-gamemode` without checking whether the multilib repository is enabled.

Why it matters (concrete failure scenario): On an Arch installation with multilib disabled, the whole transaction fails even though the native GameMode package could be installed. [Arch's package record](https://archlinux.org/packages/multilib/x86_64/lib32-gamemode/) places this package in multilib. ARC-005 can then conceal the failed transaction.

Suggested fix: Install native GameMode independently. Install the 32-bit component only when available/needed and report the repository prerequisite. Do not silently modify the user's repository configuration.

Confidence: high — package location and unconditional transaction verified.

ARC-045 | P4 | lib/detect.sh:182-198; install.sh:366-386; modules/gui.sh:108-116

What's wrong: Distribution detection accepts Artix as compatible without checking the required init/service environment. The installer then uses systemd operations throughout.

Why it matters (concrete failure scenario): An accepted installation with no functioning systemd manager proceeds into package/configuration changes and then cannot enable/run Archer's services. Distro-family recognition is insufficient for this runtime requirement.

Suggested fix: Before mutation, check the supported init/runtime contract. Either reject unsupported service environments with a precise message or define an explicit offline-install mode. **Maintainer decision:** remove Artix acceptance or implement a genuinely supported init backend.

Confidence: high for acceptance and required operations; confirm in an accepted environment without systemd.

ARC-046 | P4 | modules/camera-enhance.sh:49-68

What's wrong: Camera setup loads v4l2loopback once and only persists its options, not boot loading. It also hardcodes `/dev/video10` and treats mere existence of that path as proof of successful camera creation.

Why it matters (concrete failure scenario): The virtual device disappears after reboot unless something else loads the module. An existing video10 or previously loaded loopback with different parameters can make installation report the wrong device. The [Arch DKMS package file list](https://archlinux.org/packages/extra/any/v4l2loopback-dkms/files/) supplies source files, not a modules-load configuration.

Suggested fix: Install an owned boot-load entry or a producer service that loads/creates the device on demand. Check available device numbers and confirm the resulting driver/card identity. Handle an already-loaded module explicitly rather than assuming modprobe reapplies parameters.

Confidence: high for missing boot integration and identity checks; verify reboot and occupied-video10 cases.

ARC-047 | P4 | gui/archer_dbus.py:316-324; modules/audio-enhance.sh:83-90

What's wrong: Disabling noise suppression renames its configuration with a `.disabled` suffix, but module uninstall only removes the enabled filename. Verification also treats a deliberately disabled installation as missing.

Why it matters (concrete failure scenario): After the audio toggle works, a user disables suppression and uninstalls the module. Its system configuration remains behind; `--verify` also cannot distinguish installed-and-disabled from absent.

Suggested fix: Track both states as one owned configuration, remove either variant during uninstall, and make verification test installation separately from desired enabled state. Include migration cleanup when fixing the filter's configuration location.

Confidence: high — rename and cleanup paths disagree. This becomes directly user-visible once ARC-006 and ARC-007 are corrected.

ARC-048 | P4 | lib/utils.sh:70-74; .github/workflows/ci.yml:14-28

What's wrong: The audited commit fails its ShellCheck CI gate at the bare `kernel_refresh_initramfs` call with SC2119 under the CI-installed ShellCheck 0.9.0. The analogous suppression on `install_shared_deps` does not cover this wrapper (`install.sh:184-190`).

Why it matters (concrete failure scenario): The exact revision has a red CI result even though its module-build and other test jobs pass. This blocks confidence in a clean release gate; it is a lint compatibility failure, not evidence of a failed kernel build. [Failing job and log](https://github.com/otectus/Archer/actions/runs/34516120551/job/103001847960).

Suggested fix: Add a narrowly justified suppression at the intended no-argument wrapper, or pass explicit intended arguments; do not forward arbitrary installer arguments. Run the complete lint gate with the CI version afterward.

Confidence: high — verified from the exact commit's job log, including diagnostic and exit status.

## Cross-cutting assessment

**Test coverage.** There is useful coverage of shell helper contracts, pinned-source preparation, kernel/header selection, parser bounds, fan Auto restoration, GUI state transitions and D-Bus signatures (`tests/driver.bats:1-218`, `tests/kernel.bats:1-190`, `tests/test_driver_source.py:1-154`, `tests/test_fans.py:1-292`, `tests/gui_test.py:1-539`, `tests/dbus_smoke.py:1-176`). Its main weakness is the boundary between layers: mocks can invent capabilities the real daemon never returns, and parser tests do not cover binary state restoration, malformed firmware replies or the real service sandbox. Passing compilation cannot establish EC safety. Prioritize adversarial tests for the P0 paths, real conditional-module failure injection, actual daemon-produced GUI payloads, and service-unit integration before adding broad test counts. No percentage coverage is claimed because coverage instrumentation was not run.

**Error handling and logging.** The newer DKMS/header paths preserve diagnostics and propagate failures (`lib/dkms.sh:46-79`, `lib/kernel.sh:87-109`), and fan restoration logs a clear safety error (`gui/archer_daemon.py:1134-1141`). Elsewhere, empty-string command results, suppressed stderr, unconditional success logs and swallowed settings failures obscure the actual outcome (`gui/archer_daemon.py:122-143,207-215`, `modules/gui.sh:116-128`). Hardware-return validation is especially inconsistent across the pinned drivers (ARC-002–004 and ARC-025). Use consistent structured operation results, retain the original error, and distinguish unavailable, unsupported, rejected, partially applied and pending-reboot states. Command/JSON-input schema validation also needs consistency: the fan-curve method validates its JSON, while keyboard/audio methods do not (`gui/archer_dbus.py:180-195,257-278,311-325`; covered with ARC-013's validation work).

**Model/kernel assumptions.** Header validation and legacy/current platform-profile adaptation are stronger than simple version-string guessing (`lib/kernel.sh:14-84`, `driver/linuwu-sense/patches/0008-legacy-platform-profile.patch:17-79`). Firmware assumptions remain much narrower than the family labels: an exact ANV16S-41 quirk does not validate other Nitro generations; ENE effect numbering cannot be reused as WMI numbering; all AMD ACP devices cannot receive one audio recipe; battery operations assume one selected battery; and EPP control currently assumes one relevant policy (`driver/linuwu-sense/patches/0001-anv16s-41-dmi.patch:1-44`, `gui/archer/pages/controls.py:21-29`, `modules/audio.sh:27-38`, `gui/archer_daemon.py:694-702,739-768,1244-1249`). No claim is made that the documented family matrix establishes compatibility with every BIOS, graphics topology or future kernel.

**Privilege boundaries.** The GUI runs as the user, the daemon requires root, kernel modules execute in kernel context, and the installer elevates package/configuration operations while AUR/pip steps run in the invoking environment (`gui/archer_gui.py:1-27`, `gui/archer_daemon.py:1432-1448`, `lib/utils.sh:49-55`, `modules/audio-enhance.sh:23-34`, `modules/gpu.sh:47-53`). Firmware/sysfs mutation needs privilege; most telemetry, preferences and audio-session management do not. Existing mutating D-Bus methods check polkit, and the shipped policy requires administrator authorization (`gui/archer_dbus.py:24-57,158-175`, `gui/io.otectus.Archer1.policy:9-66`). I found no demonstrated direct polkit bypass in those methods. The GRUB ownership handoff is a separate privilege escalation. Reduce the root daemon's general command-execution responsibilities, bound unauthenticated work, and use explicit narrowly scoped helpers where service sandboxing and configuration changes conflict. The assertion that polkit requires `NoNewPrivileges=false` is not established by this code; review that setting as part of helper design (`gui/archer-daemon.service:28-31`).

## Maintainer decisions and limits

| Decision | Why it needs an explicit choice |
| --- | --- |
| Legacy kernel state files | Retaining their ABI requires validation/versioning and safe migration. Removing persistence avoids duplicate policy but changes behavior for users who use the driver independently. ARC-002/003. |
| Keyboard effect compatibility | Old integer indexes are ambiguous after a backend switch. Define stable identifiers and a migration policy; do not silently reinterpret saved choices. ARC-009. |
| Power-policy owner | TLP, the battery service/udev rule and Archer Game Mode currently overlap. Decide who owns each setting and how external changes interact with temporary overrides. ARC-010, ARC-020/021, ARC-031/032. |
| Root helper boundary | Audio filter changes, graphics configuration and driver recovery need a coherent privilege/sandbox contract. Moving ENE writes to workers also requires replacing its main-thread-only timeout. ARC-006, ARC-015, ARC-018, ARC-023. |
| Supported hardware claim | Keep code/compile support distinct from model/BIOS hardware validation, especially ANV16S-41. Define a release matrix and promote support only after the documented progressive hardware checks (`docs/anv16s-41.md:1-5,146-228`). |

Unverified hypotheses for later validation, **not additional findings**: the exact firmware meaning of Linuwu's combined battery return/reserved-byte test (`Linuwu/src/linuwu_sense.c:3224-3229`); safety of prolonged low manual fan settings on each BIOS; ENE report compatibility across other laptops sharing the same HID identity; and the effect of concurrent non-Archer firmware writers. The source does include bounded fan writes, a paired-command lock, Auto restoration, sensor-invalidity handling and an NVIDIA-versus-iGPU guard (`driver/linuwu-sense/patches/0003-fan-safety.patch:24-58`, `gui/archer_daemon.py:291-316,930-958`). Those safeguards are real, but they do not establish a tested hardware safety envelope.

I also checked and rejected a tempting false positive: `After=multi-user.target` together with `WantedBy=multi-user.target` in the touchpad service is not, by itself, proof of an ordering cycle. systemd's implementation avoids adding contradictory default ordering when explicit ordering exists ([source](https://github.com/systemd/systemd/blob/main/src/core/target.c)); no cycle finding is asserted here.

## Prioritized Phase 1 fix list

1. **Close the privilege escalation and unsafe kernel paths:** ARC-001–004. Repair existing GRUB ownership, prevent unchecked state replay, eliminate duplicate closes, and validate firmware results before dereferencing. Require passing hostile-input/fault-injection checks before hardware testing resumes.
2. **Make installation and recovery outcomes trustworthy:** ARC-005, ARC-012, ARC-029, ARC-030, ARC-033. Propagate actual failures, preserve retry records, make dry-run read-only and safely edit GRUB. Remove the ineffective AMD audio directives (ARC-011) in this early corrective release.
3. **Repair the root-service contract:** ARC-006, ARC-013–018, ARC-042. Validate settings/requests, serialize persistence, bound firmware work, fix command results and sequence recovery. Verify using the installed service and polkit, not only a directly launched Python process.
4. **Stop contradictory battery and power writes:** ARC-010, ARC-025–027, ARC-031–032. Establish one owner per policy and reversible service transitions. These decisions constrain Game Mode's transaction design.
5. **Restore the complete audio path:** ARC-007, ARC-008, ARC-047, together with ARC-006/042. Capability detection, payload state, filter loading, authorization and cleanup must work in one clean-session integration test.
6. **Correct keyboard compatibility and device lifecycle:** ARC-009, ARC-023, ARC-035. Migrate effect identities, validate the opened HID device and handle delayed enumeration/resume. Test WMI and ENE backends separately.
7. **Make Game Mode and monitoring reflect reality:** ARC-019–022, ARC-028, ARC-041, ARC-044. Apply/restore every policy, preserve crash-recovery state, follow external changes, calculate current utilization and load the real GameMode configuration. Coordinate with TLP rather than allowing competing writers.
8. **Finish distribution/reboot behavior and diagnostics:** ARC-034, ARC-036, ARC-043, ARC-045–046, ARC-048. Use system-managed GPU dependencies, define init support, verify camera identity/loading and restore a passing CI gate.
9. **Correct the remaining documentation/dead interface paths:** ARC-037–040. Describe what is actually implemented and where contributors should change it.

Phase 1 is complete for this commit. No tracked repository files were modified. Expansion research and the combined Phase 3 roadmap have not started; they remain behind the requested Phase 1 review boundary.
