# Archer's Linuwu-Sense package

`source.conf` is the single update point for the repository URL, full upstream
commit and Archer DKMS version. This package uses upstream commit
[`73a25ec243a44ba2b1703e8d0a76fa2735062506`](https://github.com/0x7375646F/Linuwu-Sense/commit/73a25ec243a44ba2b1703e8d0a76fa2735062506),
with DKMS version `1.0.archer2`. It does not install moving `main` content.

`prepare.sh DIRECTORY [LOCAL_GIT_REPO]` delegates to the reusable `driver/prepare.sh` and checks out that exact commit,
checks/applies each patch named in `patches/series` with `git apply --check` and `git apply`, and generates
the DKMS metadata. Any mismatch stops preparation before installation. It never
runs upstream's `make install`, module-signing shell commands, or service.
Reusing an identical destination is a no-op; a modified destination is an error. `modules/driver.sh` prepares a new
temporary tree, compares existing sources on reinstall, and copies validated
sources into root-owned `/usr/src/linuwu-sense-1.0.archer2`.

The patch series is ordered and intentionally separate from GUI code:

| Patch | Purpose |
| --- | --- |
| `0001` | Exact Acer / Nitro ANV16S-41 DMI entry reusing ANV16-41's Nitro v4 quirk, without WMI RGB; DMI logging. |
| `0002` | Optional `native_platform_profile=1`: leave an existing independent profile provider in charge, including during AC events and state replay. Initialize the supported-profile bitmap and guard failed profile registration. |
| `0003` | Serialize paired fan commands, reject malformed/extra input, attempt Auto after failed manual writes, restore Auto on remove/suspend/shutdown, stop replaying persistent manual values, and remove the correct legacy Nitro sysfs group. |
| `0004` | On ANV16S-41 only, validate GUID4 and the existing v4 supported-sensor query before exposing paired `fan_speed`. Missing either fan keeps manual control disabled. |
| `0005` | Getter-only `sense_quirk` diagnostic parameter and bounded-copy compatibility with kernels that removed `strncpy`. |
| `0006` | Unwind partially created sysfs groups on failed probe and serialize fan state reads. |
| `0007` | Reject empty, truncated, embedded-NUL and incomplete/extra RGB commands; check persistent file error pointers. |
| `0008` | Probe-selected legacy single-provider platform-profile adapter. |
| `0009` | Header and callback compatibility for older kernels. |

The driver protocol/quirk evidence and physical test procedure are in
[ANV16S-41 support](../../docs/anv16s-41.md). Source and derived driver patches
retain Linuwu-Sense's upstream license; Archer's application license does not
relicense the kernel driver.

## DKMS lifecycle

`archer-build.sh` receives DKMS's **target** `$kernelver`, detects Clang from that
kernel's `.config`, and invokes Kbuild directly. GCC targets use the default
compiler; Clang targets use `LLVM=1 CC=clang`. This avoids the old `KVERSION`
versus upstream `KVER` bug and delegates signing to DKMS. Install LLVM/Clang for
Clang-built kernels. Secure Boot still requires an enrolled DKMS signing key.

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
4. Run Bats and `LINUWU_TEST_REPO=/path/to/upstream python3 -m unittest discover -s tests -v`.
   This validates reproducible preparation, refusal on mismatched source/pins,
   unchanged existing DMI entries, and the real fan parser/rollback code with
   simulated WMI. CI fetches upstream explicitly to run these tests.
5. Compile the prepared tree with `bash archer-build.sh KERNEL_VERSION` for the
   supported kernel headers/toolchains, then obtain physical hardware results.

Never force a broad Nitro/Predator override to compensate for a failed DMI match.

The broader upstream audit, API semantics and actual header-build evidence are in
[the kernel compatibility guide](../../docs/kernel-compatibility.md).
