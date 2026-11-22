# Installer platform adapters

Archer 2.1.0 resolves platform facilities from active configuration. Merely
having a command installed does not select it.

## Init and service lifecycle

The installer supports full systemd integration. It also implements an OpenRC
service-state adapter for existing distribution services: their enabled and
active state is recorded before a change, the post-change state is recorded,
and uninstall refuses to overwrite a later external change. OpenRC operations
use `rc-update` and `rc-service`; systemd operations use `systemctl`.

Only `audio`, `wifi`, `touchpad`, `thermal`, and `firmware` may currently be
selected on OpenRC, and they must be selected explicitly with `--modules`.
Modules that ship Archer systemd units, depend on logind/polkit integration, or
have no OpenRC shutdown/resume lifecycle fail before package or configuration
changes. Runit, dinit, and s6 remain diagnostics-only because no complete
service lifecycle is implemented for them. This is an implementation boundary,
not a claim that those systems cannot run Archer in the future.

The OpenRC service-state adapter received two disposable operational lanes on
2026-11-21 using the signed official Artix
`artix-base-openrc-20261102-x86_64.iso` and OpenRC 0.64.1. An unprivileged
Bubblewrap lane used a real `openrc-run` test service and the actual
`rc-update`/`rc-service` tools to verify enable-and-start, journaled state,
stopped-and-disabled restoration, empty-journal removal, and refusal to
overwrite an externally stopped service. A no-network QEMU/KVM lane then
booted that verified root twice with OpenRC as PID 1. It completed an Archer
audio plan, installed and verified the module against an emulated HDA device,
persisted its manifest, performed an orderly OpenRC reboot, passed post-reboot
verification, committed targeted uninstall, and performed an orderly poweroff.
Evidence and exact source/disk hashes are retained under
`/var/tmp/archer-artix-210/`.

The booted lane loads the kernel directly, uses pre-staged signed packages, and
has no NIC or hardware passthrough. It therefore does not qualify firmware or
bootloader integration, an online full-upgrade transaction, suspend/resume,
authorization/session behavior, or physical Acer hardware. An initial run
exposed an uninitialized dry-run inventory generation; after production
initialization was corrected, the disk was rebuilt and the complete two-boot
lane passed without a harness override.

| Artix/OpenRC qualification step | Result | Evidence or boundary |
|---|---|---|
| Real `rc-update`/`rc-service` enable, start, guarded restore | Pass | `openrc-validation.log` |
| OpenRC 0.64.1 as PID 1 through sysinit/boot/default | Pass, two boots | `qemu-final-boot1.log`, `qemu-final-boot2.log` |
| Production installer dry-run plan without harness variable overrides | Pass | `INSTALLER_PLAN_STATUS=0` in boot 1 |
| Real audio module on emulated HDA, manifest save, orderly reboot | Pass | Boot 1 persisted phase 2 and manifest |
| Post-reboot `install.sh --verify` | Pass, 1/1 | `POST_REBOOT_VERIFY_STATUS=0` in boot 2 |
| Targeted `uninstall.sh`, manifest removal, transaction commit | Pass | `UNINSTALL_STATUS=0`; committed journal `20261122T001834.781596Z` |
| OpenRC shutdown/poweroff and final filesystem check | Pass | Phase 3, clean read-only `e2fsck` |
| Online full upgrade, firmware/bootloader, suspend/resume, physical Acer I/O | Not qualified | No NIC or hardware passthrough; direct kernel boot |

## Wayland rendering qualification

On 2026-11-21 the GTK interaction/capture suite passed all 49 tests and
produced all 148 requested captures through native Wayland `xdg_surface`
windows. The isolated lane used Sway 1.12 with wlroots 0.20.1, a headless
backend and Pixman renderer, plus `GDK_BACKEND=wayland`; representative 360 x
600 and 1440 x 900 captures were inspected successfully. The compositor,
application and private session bus ran inside new user, mount, PID, IPC, UTS,
cgroup and network namespaces with a synthetic `/dev`, no DRM or input
devices, a read-only source tree and no host service or hardware access.
Evidence is retained under `/tmp/archer-210-checks/wayland-*`, including the
compositor and test logs and `wayland-release-captures/`. Run `bash tests/gui-wayland.sh` to repeat the native lane with Sway installed.

This qualifies GTK/libadwaita interaction, responsive layout and software
rendering on the Wayland protocol. It does not qualify DRM scanout, GPU-driver
rendering, physical input, HDR/VRR/scaling or multi-monitor behavior, or
portal/PipeWire integration; those services were intentionally unavailable in
the sandbox.

Desktop-specific follow-up used privately downloaded, repository-signed
packages without installing them on the host. KWin 6.8.3 started its native
virtual Wayland backend and the same suite completed 47 interaction tests; its
only error was the visual review, where `Gtk.WidgetPaintable` returned no render
node under both NGL and Cairo, so the KWin lane produced no review PNGs. GNOME
Shell 51.1 with Mutter 51.1 reached a GPU-less surfaceless renderer, virtual
monitor and named Wayland socket, but its shell UI could not remain running in
the isolated environment: user mode failed on the unavailable time-limits
system-bus service and GDM mode failed on unavailable locale/input-source
system settings. Thus neither KDE nor GNOME receives full capture
qualification from this lane. Their exact commands and logs are retained in
`/tmp/archer-210-checks/wayland-{kwin,gnome}*`; full qualification requires a
disposable desktop image with those session services, not access to the host
graphical session.

## Boot and initramfs

`python3 scripts/archer-boot.py inspect` prints the configured initramfs and
boot sources as JSON, including staged target roots selected with `--root`.
`rebuild` selects exactly one configured generator. Mkinitcpio targets come
from literal preset image/UKI assignments and must cover every installed kernel
that declares a `pkgbase`. The preset-free CachyOS Limine layout is also
recognized only when the active `/etc/pacman.d/hooks/90-mkinitcpio-install.hook`
uses the packaged `limine-mkinitcpio-install` action, `/boot` is the mounted FAT
ESP, the machine ID and every kernel package mapping are literal, and effective
configuration keeps `ENABLE_UKI=no` and `ESP_PATH=/boot`. Its regular
`vmlinuz`, `initramfs`, and configured fallback targets follow the upstream
per-machine-ID and per-package layout; presets alongside that hook, UKIs,
custom ESP paths, unknown fallback selectors, and incomplete layouts are
refused. Dracut is invoked once per installed kernel with an
explicit output. Booster is invoked through exactly one executable distribution
kernel-install hook, and only when the entry token, kernel image, existing entry
directory, and resulting `initrd` path are unambiguous.

Before a command runs, every target directory must be on an accessible mounted
filesystem with at least 32 MiB free. Afterward, every selected kernel's
required artifact must be non-empty and show a change in content or replacement
of the file. A reproducible mkinitcpio build can install identical bytes under a
new inode. The supported Limine hook deliberately leaves byte-identical outputs
untouched, so Archer additionally requires its exact per-kernel build evidence,
rejects reported build errors even after a zero hook exit, verifies the installed
kernel against its source, and checks every expected initramfs or fallback
result. If an initramfs inode and content remain unchanged, Archer performs a
fresh mkinitcpio build and requires its measured bytes to equal the installed
target before accepting the upstream identical-output optimization. With Secure Boot
enabled, every generated UKI/EFI target must have certificate evidence in
`sbverify --list` output; a zero exit status without a listed certificate is a
failure. Installed but unconfigured tools are ignored. Missing, ambiguous, or
shared targets and commands that leave a required image untouched or merely update its timestamps are rejected.

GRUB edits continue through the locked `archer-grub.py` helper and regenerate
`grub.cfg`. For non-GRUB systems, `archer-boot.py kernel-args` supports the
authoritative `/etc/kernel/cmdline` used by configured UKIs, explicit
systemd-boot loader entries below `/boot`, `/efi`, or `/boot/efi`, a single
literal Limine configuration, or a single `refind_linux.conf`. Archer records
the tokens it added in `/var/lib/archer/boot-arguments.json` and removes only
those tokens. Inconsistent entries, shell-expanded values, multiple candidate
owners, generated-only rEFInd configuration, and custom syntax are refused.
An `/etc/kernel/cmdline` change is rolled back if its required image rebuild
fails.

The adapter proves local configuration and artifact invariants; it does not
claim a firmware boot test. Rescue/staged-root execution still requires the
target filesystems to be mounted into that root, and dynamically linked target
tools must be runnable in the rescue environment. Limine's optional
`limine-mkinitcpio` integration is supported when configured; custom
distribution boot-manager wrappers remain a fail-closed boundary until their
target contract is explicit.

## Transactions and recovery

Mutating installs and uninstalls create root-owned journals below
`/var/lib/archer/transactions/`. Plans record selected modules, environment and
boot evidence, operations, rollback limits, approval mode, and (for installs)
the inventory generation. Journal states are `planned`,
`preflight-complete`, `applying`, `pending-restart`, `verifying`, `committed`,
`failed`, and `recovery-required`.

Inspect the active journal with:

```sh
sudo python3 scripts/archer-transaction.py show
```

An interrupted applying transaction must be moved to explicit recovery before
retrying:

```sh
sudo python3 scripts/archer-transaction.py recover \
  --message "Reviewed package, manifest, DKMS and boot state"
```

A retry replaces the old plan only after it is reviewed again. Package
downgrades are not automatic, administrator-edited files are retained, and a
boot-sensitive transaction remains `pending-restart` until
`./install.sh --verify` succeeds after reboot.
