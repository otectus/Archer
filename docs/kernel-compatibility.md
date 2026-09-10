# Kernel compatibility and troubleshooting

Archer uses capabilities and matching target headers, not a kernel-version
allowlist. Numeric release parsing covers 6.x, 7.0, 7.1, 7.2, 7.10 and later major
versions. This is a forward-compatibility policy, **not a promise that an
unreleased kernel or every Acer firmware works**. Kernel internal APIs can change
at any release; the build matrix and explicit failures make those changes visible.

## Upstream audit (2026-09-10)

Issue [Archer #9](https://github.com/otectus/Archer/issues/9) requests kernel 7.2
support. The concrete compile failure is independently reported in
[Linuwu-Sense #129](https://github.com/0x7375646F/Linuwu-Sense/issues/129) and
[#135](https://github.com/0x7375646F/Linuwu-Sense/issues/135).

* **Linuwu-Sense:** current upstream HEAD remains
  `73a25ec243a44ba2b1703e8d0a76fa2735062506`. All three `strncpy` calls remain.
  PRs [#128](https://github.com/0x7375646F/Linuwu-Sense/pull/128),
  [#130](https://github.com/0x7375646F/Linuwu-Sense/pull/130) and
  [#134](https://github.com/0x7375646F/Linuwu-Sense/pull/134) are open;
  [#133](https://github.com/0x7375646F/Linuwu-Sense/pull/133) is closed but its
  change is absent from HEAD. Archer retains the pinned revision and ordered
  patches, versioned `1.0.archer2`, rather than consume an unmerged fork.
* **acer-wmi-battery:** pinned upstream
  `9f90d75cc9237aeed7964622d10dbdf4d2c7b518`, Archer package `0.1.0.archer1`.
  Upstream already handles the 6.12 `asm/unaligned.h` move. Its Makefile still
  builds for `uname -r`, and it has no merged DKMS configuration; proposed
  [DKMS support #155](https://github.com/frederik-h/acer-wmi-battery/pull/155)
  and [target selection #154](https://github.com/frederik-h/acer-wmi-battery/pull/154)
  remain open. Archer supplies packaging and invokes Kbuild directly. No local
  battery-driver C fork is needed.
* **v4l2loopback:** the maintained distro `v4l2loopback-dkms` package supplies
  sources and lifecycle hooks. The audit inspected upstream HEAD
  `e27cd86` (0.15.4 development tree), its recent locking fixes and current
  `strscpy`, timer and V4L2 compatibility adapters. Older failures such as
  [#654 (6.18)](https://github.com/v4l2loopback/v4l2loopback/issues/654) and
  [#669 (7.0)](https://github.com/v4l2loopback/v4l2loopback/issues/669) are closed.
  Archer no longer records a fictitious `0.13.2` version. It uses the installed
  package metadata and checks DKMS installation for every target. A small
  `/etc/dkms/v4l2loopback.conf` override passes upstream-supported `KERNEL_DIR`
  and target-specific LLVM settings, including on future upgrade hooks.

The C audit covered string handling, sysfs, WMI, platform-profile registration,
platform removal, timers/workqueues, PM and compiler diagnostics. These drivers
have no local procfs/class-create shim to update. Modern WMI APIs still compile;
the legacy global WMI calls remain an upstream dependency to monitor. Archer
retains the kernel and daemon fan Auto restoration and watchdog patches. It also
fixes `filp_open()` error-pointer handling in the two persistent-state writers.

All three removed string calls take counted sysfs input and produce temporary
C strings. Return values from `strncpy` were unused; zero padding was unnecessary.
The fan parser and both RGB parsers now reject empty, oversized or embedded-NUL
commands, copy exactly `count` bytes and explicitly append NUL. No source
termination is assumed and no hardware command is silently truncated. RGB parsers
also reject extra fields and missing colors before WMI calls. The userspace C
harness compiles the actual patched functions and exercises their boundaries.
The platform-profile adapter probes the target header's API shape, supporting
both the old single-handler API and the new device-managed API. Header existence
handles the unaligned include move; platform removal uses the target's callback. The 6.8 adapter also retrieves and
frees WMI event data for the old numeric notification callback, and uses the
legacy backlight power constant. Both old and new APIs retain the same behavior.

## Native functionality and sysfs

Standard battery `charge_control_end_threshold` is preferred for Archer's 80%
limit; existing Linuwu `battery_limiter` and battery WMI `health_mode` are fallbacks.
A working health-mode file alone does **not** prove a driver was mainlined.
Thresholds persist through udev. Driver-level limits use a service after
`systemd-modules-load`, since the upstream battery driver has no bound device
attribute on which the old udev rule could reliably trigger.

Thermal controls use the actual platform-profile interface and advertised choices.
The daemon can use the class interface when exactly one provider exists and the
aggregate ACPI path is absent. It refuses to guess between multiple providers.
See the [kernel platform-profile ABI](https://docs.kernel.org/userspace-api/sysfs-platform_profile.html).
Archer does not force a Predator generation based on a marketing model name.
An independent native provider is preserved when Linuwu is installed.

Current [stock acer-wmi](https://github.com/torvalds/linux/blob/master/drivers/platform/x86/acer-wmi.c)
contains profile and fan functionality, but that is not equivalent to Linuwu's
complete paired fan/RGB/battery ABI across supported models. Archer therefore
does not replace Linuwu solely on the presence of a native profile or RPM sensor.
The GUI continues to expose features through daemon capability responses. Missing
nodes return unavailable results; permission/write errors are logged, and missing
sysfs files are never created. A disappearing sensor or fan node still triggers
the fan watchdog's Auto restoration attempt. Firmware response and physical fan
behavior need hardware validation.

## Headers, upgrades, toolchains and DKMS

Install the headers for **each installed kernel**, including LTS or fallback
kernels. Archer resolves the kernel's `pkgbase` or owning package, e.g. `linux` →
`linux-headers`, `linux-lts` → `linux-lts-headers`, `linux-zen`, `linux-hardened`,
Manjaro's versioned packages and CachyOS variant packages. An arbitrary installed
CachyOS package is never substituted for a missing running kernel.

`/usr/lib/modules/RELEASE/build` must contain prepared headers, `.config` and
`include/config/kernel.release` equal to `RELEASE`. A symlink to another release
is an error even if its Makefile exists. Custom packages whose headers cannot be
resolved need an administrator-provided exact header tree.

The shared dependency transaction upgrades packages first, then Archer rescans
installed targets. If `uname -r` was removed by the upgrade, modules are built for
the installed kernels; Archer requests a reboot and does not load a mismatched
module into the stale running kernel. Missing headers for any installed target
stop migration. Reboot into the upgraded kernel before testing controls.

Each DKMS build uses its **target** configuration, never the running kernel's
compiler. GCC targets use GCC; `CONFIG_CC_IS_CLANG=y` targets use Clang/LLVM.
Clang and LLVM are installed with shared build dependencies because a package
upgrade can change the compiler. Special vendor toolchain requirements not
satisfied by distro tools are reported through Kbuild rather than suppressed.
Archer does not add arbitrary flags to upstream installation commands or run
upstream module signing scripts.

`AUTOINSTALL=yes` and distro DKMS hooks rebuild modules on kernel upgrades. Source
revisions, patch order and package versions are explicit. Repreparing identical
sources is a no-op; modified trees or mismatched patches fail before installation.
All new targets build before old Archer versions are retired. Failed builds retain
logs and source for retry. Partial successful steps retain manifest cleanup
records. Legacy unnamespaced DKMS versions are removed only when Archer's manifest
records ownership; package-managed or unrelated DKMS trees are retained.

Driver activation waits for reboot instead of hot-swapping competing WMI owners.
Boot-image changes rebuild **all** mkinitcpio presets or all dracut images, with
failures propagated. Custom initramfs generators require their own refresh.
NVIDIA remains distro-managed: Archer recognizes an existing NVIDIA module,
checks per-kernel rebuilds before GPU configuration, and leaves vendor/GPU support
to the distro's driver package. No NVIDIA source build or GPU runtime was validated
for this change.

## Reporting a future failure

Capture the exact release, package, compiler and failed target, not just “7.x”:

```sh
uname -r
cat /proc/version
pacman -Q | grep -E 'linux|headers|dkms|clang|gcc|v4l2loopback'
dkms status
cat /usr/lib/modules/"$(uname -r)"/build/include/config/kernel.release
sudo find /var/lib/dkms -name make.log -print
sudo journalctl -k -b
python3 scripts/archer-diagnose.py
```

Attach the **complete** `make.log` for the failed module/version/kernel. Depending
on DKMS version it is under `/var/lib/dkms/NAME/VERSION/build/make.log` or a
kernel/architecture-specific `log/make.log`. Archer prints existing log paths and
the final 40 lines on a build failure. Preserve logs before removing DKMS entries.

After repairing headers/toolchains, rerun Archer or rebuild the specific target:

```sh
sudo dkms install -m linuwu-sense -v 1.0.archer2 -k YOUR_INSTALLED_KERNEL_RELEASE
```

A compiler error is different from Secure Boot. `Key was rejected by service`
on `modprobe` generally means DKMS built the module but its signing certificate
is not trusted. Configure DKMS signing and enroll its key, then rebuild; changing
string APIs cannot fix a trust failure. Inspect `journalctl -k` for the actual
load error. A loaded module with absent controls may indicate unsupported DMI,
WMI or firmware capabilities rather than an Archer daemon outage.

## Validation evidence

Local compile checks (no modules installed or loaded by these tests):

| Header set | Compiler | Linuwu | acer-wmi-battery | v4l2loopback |
| --- | --- | --- | --- | --- |
| Arch 6.8.9-arch1-2 | GCC 16.2.1 | Pass | Pass | Pass |
| Arch LTS 6.12.75-1-lts | GCC 16.2.1 | Pass | Pass | Pass |
| CachyOS LTS 6.18.48-1-cachyos-lts | Clang 22.1.8 / LLVM | Pass | Pass | Pass |
| CachyOS 7.2.3-1-cachyos | Clang 22.1.8 / LLVM | Pass | Pass | Pass |

The 6.8 and 6.12 packages were built with GCC 14.1.1 and 15.2.1; local GCC
16.2.1 emits a compiler version warning but all three complete compilation/modpost/link/BTF successfully.
7.0, 7.1, 7.10 and 8.0 are exercised by mocked detection tests, not claimed as
local header-build or physical-hardware validations. Arch-family package variants
are tested using isolated metadata fixtures. CI adds real rolling Arch and LTS
module builds plus fixed 6.8 and 6.12 baselines. Physical Acer BIOS, suspend/resume,
fan RPM response, battery charging, RGB, virtual camera streaming and enrolled-key
Secure Boot loading remain hardware integration tests.

Validation commands (all passed; 98 Bats tests and 36 Python unit tests):

```sh
rg --files -g '*.sh' -0 | xargs -0 shellcheck -x -s bash
rg --files -g '*.sh' -0 | xargs -0 -n1 bash -n
bats tests/*.bats
LINUWU_TEST_REPO=/path/to/upstream python -m unittest discover -s tests -v
flake8 -j1 gui tests scripts --max-line-length=120 --ignore=E501,W503,E402
python -m compileall -q gui tests scripts
dbus-run-session -- bash tests/dbus_smoke.sh
bash scripts/check-kernel-modules.sh KERNEL_RELEASE /path/to/v4l2loopback-source
git diff --check
```

For extracted headers set `KERNEL_MODULES_ROOT` to the extracted
`usr/lib/modules` directory. The compile script accepts `LINUWU_TEST_REPO` and
`BATTERY_TEST_REPO` for existing offline clones; it prepares each source twice to
check idempotency before building. Logs were captured locally under
`/tmp/archer-final-build-RELEASE.log`. The validation intentionally did not modify
host DKMS registrations, load modules or change Acer hardware state.
