# Archer's Linuwu-Sense package

`source.conf` is the single update point for the repository URL, full upstream
commit and Archer DKMS version. This package uses upstream commit
[`4483b6c38b9bb963ac9ada9ca476578e0c63cc08`](https://github.com/0x7375646F/Linuwu-Sense/commit/4483b6c38b9bb963ac9ada9ca476578e0c63cc08),
with DKMS version `1.0.archer4`. It does not install moving `main` content.

`prepare.sh DIRECTORY [LOCAL_GIT_REPO]` delegates to the reusable `driver/prepare.sh` and checks out that exact commit,
checks/applies each patch named in `patches/series` with `git apply --check` and `git apply`, and generates
the DKMS metadata. Any mismatch stops preparation before installation. It never
runs upstream's `make install`, module-signing shell commands, or service.
Reusing an identical destination is a no-op; a modified destination is an error. `modules/driver.sh` prepares a new
temporary tree, compares existing sources on reinstall, and copies validated
sources into root-owned `/usr/src/linuwu-sense-1.0.archer4`.

The patch series is ordered and intentionally separate from GUI code:

| Patch | Purpose |
| --- | --- |
| `0001` | Forward-port Archer's stable safety policy onto the rebased source: gate post-pin DMI profiles and transports behind `experimental_profiles=1`, retain the qualified ANV16S-41 WMI mapping/probe, preserve native profile ownership, remove kernel file persistence, validate firmware buffers and counted inputs, unwind failed probes, serialize fan commands, fall back to Auto after partial failure, and restore Auto on lifecycle boundaries. |
| `0002` | Restore header-probed compatibility for Linux 6.8-era platform-profile, WMI notify and platform remove APIs, with the historical unaligned/backlight header fallbacks. The native-profile ownership guard still runs before either registration API. |

The final driver no longer reads or writes `/etc/predator_state`, `/etc/nitro_state`, `/etc/four_zone_kb_state`, or `/etc/per_zone_kb_state`. Existing files are ignored and retained for manual archival. Standalone driver users must reapply desired lighting/profile settings after boot; Archer owns validated userspace persistence. The pre-rebase eleven-patch series is retired rather than force-applied to changed upstream model tables and APIs. Its still-required safety intents are represented by the forward-port patch; upstream-equivalent model/build changes come from the immutable pin.

### Rebase intent audit

| Previous patch | Rebase disposition |
| --- | --- |
| `0001` ANV16S-41 DMI | Retained as an exact stable profile using the prior WMI quirk; upstream HID RGB remains experimental. |
| `0002` native profile owner | Retained through the read-only `native_platform_profile` parameter and profile-write guards. |
| `0003` fan safety | Retained: counted input, capability/probe gates, mutex, Auto fallback and lifecycle restoration. |
| `0004` ANV16S probe | Retained: GUID4 and paired supported-sensor validation hide manual fan control on incomplete topology. |
| `0005` diagnostics/build | Getter-only `sense_quirk` retained; upstream has removed the old `strncpy` calls. |
| `0006` probe unwind | Retained: created sysfs groups unwind in reverse and fan readback is serialized. |
| `0007` RGB input | Retained: empty, truncated, embedded-NUL and extra fields are rejected before firmware calls. |
| `0008` legacy profile API | Retained in `0002`: header-shape probing selects `platform_profile_handler` on older kernels while keeping the native-provider guard and failure isolation. |
| `0009` older headers | Retained in `0002`: Linux 6.8-era unaligned/backlight, WMI notify and `remove_new` APIs are selected from actual target headers rather than a kernel-version guess. |
| `0010` persistence/firmware | Retained: kernel file persistence is removed and buffer type/pointer/length is validated. |
| `0011` scalar input | Retained as the counted unsigned parser across firmware scalar stores. |

New upstream DMI profiles and the HID RGB, AN515-58 special RGB, keyboard-zone
arming, and EC shared-memory profile transports default off in Archer stable.
`experimental_profiles=1` exists for isolated qualification; the installer does
not enable it and its use is not hardware-support evidence.

The driver protocol/quirk evidence and physical test procedure are in
[ANV16S-41 support](../../docs/anv16s-41.md). Source and derived driver patches
retain Linuwu-Sense's upstream license; Archer's application license does not
relicense the kernel driver.

## DKMS lifecycle

`archer-build.sh` receives DKMS's **target** `$kernelver`, detects Clang from that
kernel's `.config`, probes the target headers for platform-profile, WMI notify
and platform-driver API shape, and invokes Kbuild directly. GCC targets use the
default compiler; Clang targets use `LLVM=1 CC=clang`. This avoids the old
`KVERSION` versus upstream `KVER` bug and delegates signing to DKMS. Install
LLVM/Clang for Clang-built kernels. Secure Boot still requires an enrolled DKMS
signing key.

All installed kernels need matching headers. Every target is built before
retiring the old driver. Migration recognizes manifest-owned historical `1.0` and Archer's
`1.0.archerN` versions, retains old sources until installation succeeds, and
attempts to reinstall old versions if installation fails. Failures remain
visible in the installer exit status. Orphaned sources belonging to these
versions are cleaned; unrelated DKMS packages/versions are left alone.

The installer adds a blacklist for stock `acer_wmi` and an explicit modules-load
entry only after successful installation. It does not hot-swap WMI ownership;
a reboot activates the replacement. Installation and removal refresh all mkinitcpio
presets (and Limine entries where used), or dracut images. A refresh failure is
reported as a failure requiring repair before reboot. Custom early-boot setups
without either tool require their own image refresh. `AUTOINSTALL=yes` rebuilds on subsequent
kernel upgrades. Uninstall stops the daemon, removes Archer's DKMS registrations
and source/configuration files, and requests a reboot to restore stock behavior.

If `/sys/class/platform-profile/*/name` identifies an independent provider (for
example `amd-pmf`), the installer writes
`/etc/modprobe.d/archer-native-profile.conf`. This persists across reinstalls and
suppresses competing Linuwu gaming-profile changes. A stock `acer-wmi` provider
is replaced by Linuwu's own profile implementation instead. A missing/unknown
provider is not assumed to be AMD PMF: use the before/after diagnostic report.

## Updating/removing the patch series

1. Inspect upstream changes and any hardware reports; pin the new full commit.
2. Rebase only the patches still needed, in numbered order; remove equivalent
   patches once merged upstream. Keep the standard `nitro_sense/fan_speed` ABI.
3. Increment `DKMS_VERSION` whenever the source, patch set or build logic changes.
   Do not silently replace the contents of an already registered version.
4. Run Bats and `LINUWU_TEST_REPO=/path/to/linuwu BATTERY_TEST_REPO=/path/to/battery python3 -m unittest discover -s tests -v`.
   This validates reproducible preparation, refusal on mismatched source/pins,
   unchanged existing DMI entries, and the real fan parser/rollback code with
   simulated WMI. CI fetches upstream explicitly to run these tests.
5. Compile the prepared tree with `bash archer-build.sh KERNEL_VERSION` for the
   supported kernel headers/toolchains, then obtain physical hardware results.

Never force a broad Nitro/Predator override to compensate for a failed DMI match.

The broader upstream audit, API semantics and actual header-build evidence are in
[the kernel compatibility guide](../../docs/kernel-compatibility.md).
