# Deployment and release checks

Archer can be installed as native Arch packages or from a reviewed checkout with `install.sh`. GUI dependencies come from system packages; `gui/requirements-gui.txt` documents them and is not a pip requirements list. The daemon and GUI require systemd integration. Selected script modules also implement OpenRC service lifecycle. The GUI requires Python 3.10+, GTK 4.12+, libadwaita 1.6+, PyGObject, pycairo, dbus-python, and an X11 or Wayland session. A panel with StatusNotifierItem/D-Bus menu support is needed for the tray.

## Install or upgrade the GUI

Run these commands from the checkout as your normal desktop user; the installer requests sudo for system changes:

```sh
bash install.sh --modules gui --dry-run
bash install.sh --modules gui
archer-gui
```

Before upgrading, use the tray's **Exit** or the GUI's **Quit** action and resolve any unapplied edits. Closing the window can leave the old GUI process in the tray. Launch Archer again after installation to load the new GUI code.

The GUI module validates its required sources before installation, installs the complete `gui/archer` package and assets under `/opt/archer`, and installs the launcher, desktop entry, SVG icon, system D-Bus policy, polkit actions, and systemd unit. It reloads D-Bus policy and systemd, enables the daemon, and restarts it so upgrades load the new daemon code. Restarting releases Archer-owned native fan controls to Auto. Review manual fan requests after the restart.

User preferences remain in `$XDG_CONFIG_HOME/archer/gui.json`, and hardware settings remain in `/etc/archer/settings.json`. A GUI upgrade retains these settings. Exit terminates the GUI and tray; the separately managed system daemon continues running.

Driver, battery, and camera changes may require a reboot and headers for every installed kernel. Follow the installer's reboot/error messages and the [kernel compatibility guide](kernel-compatibility.md). A GUI-only install does not establish driver or hardware compatibility.

## Verify the installed result

```sh
systemctl is-enabled archer-daemon.service
systemctl is-active archer-daemon.service
busctl introspect io.otectus.Archer1 /io/otectus/Archer1
journalctl -u archer-daemon.service -b --no-pager -n 100
```

The installer exits nonzero if the daemon cannot start, its D-Bus Ping fails, or polkit is unavailable. `bash install.sh --verify` also exits nonzero on failed checks. Resolve the reported error before testing controls; an installed file or enabled unit alone does not establish a working service. Lightweight desktop sessions need a polkit authentication agent for interactive authorization.

The hardware daemon retains `ProtectSystem=full`. After polkit authorization it requests fixed, allowlisted operations from `archer_system.py` through transient systemd services, outside the daemon's read-only `/etc` view. Audio edits are limited to Archer's filter; graphics changes require `/usr/bin/envycontrol`; driver recovery has one ordered stop/reload/start operation. Subprocess deadlines and transient-unit runtime limits bound these operations. Recovery records in `/etc/archer/recovery.json` let the reconnecting GUI distinguish completion from a failed reload.

The [Phase 1 implementation record](Archer-Phase-1-Update.md) maps all 48 audit findings to changes and validation. Upgrade details:

- Every installation/update checks existing GRUB ownership before package changes, including GUI-only updates. GRUB parameter edits accept a single literal quoted command-line assignment, preserve complete tokens, record only Archer-added tokens, and make a root-owned atomic replacement with a backup. If an earlier file was user-owned or group/other-writable, Archer repairs permissions and stops for administrator review of that file before proceeding or running `grub-mkconfig`. It does not execute content from the unsafe file to validate it.
- LCD override, boot animation/sound and backlight timeout keep saved `true`/`false` and legacy `1`/`0` values. A saved On is replayed once at daemon start. When the firmware reads `-1`, the GUI shows it as On requested. Archer cannot tell an intended On from one created by the old issue #10 loop, so it does not reset the value automatically; choose **Turn off and save** once if needed ([recovery](../README.md#lcd-override-keeps-turning-on-or-inverse-ghosting-returns)). Invalid saved values are never applied. An unreadable `settings.json` is kept as `settings.json.unreadable-<time>` before the first new save.
- Old binary driver state files are ignored. Saved ambiguous integer keyboard effects require one explicit reselection; stable effect names then survive backend changes.
- `/etc/archer/battery.json` is the charge-policy source of truth. The old always-80% rule is replaced on battery-module upgrade. Upgrade both `battery` and `gui` when both were installed.
- Audio enhancement moves to the running PipeWire service's configuration directory. Both enabled and `.disabled` legacy files are handled, retaining a disabled preference. Administrator-modified legacy files require review instead of silent deletion.
- Per-module state under `/var/lib/archer/module-state/` records original service enablement/activity and installed configuration hashes. Package dependencies remain installed on uninstall. Historical service states that were never recorded cannot be reconstructed; uninstall conservatively leaves them alone.
- Failed, missing, and unknown cleanup modules remain in `/var/lib/archer/install-manifest.json` for retry. Malformed manifests stop cleanup. Dry-run and help do not migrate or delete manifests or build packages.

On a supported desktop, confirm:

1. Archer opens from the application menu and connects to the service.
2. Right-clicking the tray shows **Open**, **Profile**, **Exit**, in that order. Open presents the full GUI.
3. Profile lists the device's supported choices and marks the current profile. Switching from either the tray or GUI updates both; a successful tray change keeps a hidden window hidden.
4. A denied or failed profile change reveals the Performance page and its error. Offline/stale state disables changes while Open and Exit remain usable.
5. Closing hides the window when close-to-tray is enabled. Exit removes the icon and ends the GUI process; dirty edits receive a discard confirmation. Starting Archer again works.
6. Removing/restarting the tray host recovers access to the GUI and registers the tray again when a host returns.
7. Collapse and expand the sidebar with its buttons and Ctrl+B; restart the GUI and resize through narrow overlay navigation to confirm the saved desktop preference.

For desktop accessibility and hardware checks, use the [GUI validation matrix](gui.md#development-and-verification). Validate profile/fan response and AC/battery restrictions on real supported hardware.

## Release preparation

- Run all checks in [CONTRIBUTING](../CONTRIBUTING.md#running-tests) and the CI workflow, including the external kernel module matrix for driver changes. Inspect visual artifacts when GUI layouts change.
- Validate `gui/io.github.archer.desktop` with `desktop-file-validate`. Validate the service with `systemd-analyze verify gui/archer-daemon.service` on a machine with `/usr/bin/python3` installed.
- Review the final changes with `git diff --check` and `git status --short`. Include new files under `gui/`, `driver/`, `lib/`, `scripts/`, `tests/`, and `docs/` in the release commit. Exclude caches, generated modules, logs, and local test environments.
- Set the application version in `gui/VERSION`. DKMS versions remain independent and must match their source/patch manifests. Record working-tree evidence and outstanding gates before publishing a version.
- For issue #10, ask a tester with the affected PT516-52S to run the LCD checks in the [GUI validation matrix](gui.md#development-and-verification). Record the Archer and Linuwu versions, kernel, BIOS, resolved LCD path and raw readback, and keep the tester's preferred final setting.
- Test both a fresh install and an upgrade from the previous release on a disposable supported system. Check service startup after reboot, preserved settings, application-menu launch, tray behavior, and manifest-aware uninstall.

Local mock tests cannot establish physical hardware compatibility or the behavior of every desktop tray host. Record the systems and kernels actually checked in the release notes.

## Building 2.1 packages

The three application packages share `gui/VERSION`. Drivers have separate DKMS versions. Each preparation command emits a standalone, checksum-pinned PKGBUILD, source archive, source epoch and `.SRCINFO`. Prepare as a login user from a reviewed checkout. Use empty directories.

```sh
bash scripts/prepare-package.sh /tmp/archer-app
bash scripts/prepare-dkms-package.sh linuwu-sense /tmp/archer-linuwu
bash scripts/prepare-dkms-package.sh acer-wmi-battery /tmp/archer-battery
(cd /tmp/archer-app && SOURCE_DATE_EPOCH="$(cat SOURCE_DATE_EPOCH)" makepkg)
```

Run the same makepkg command in either driver directory if you need that driver. You may supply an existing upstream Git clone as the third argument to the driver preparation script. The source pin and patches are verified before the recipe is emitted. Build the prepared archives offline in a clean Arch build root with the dependencies installed. Keep the PKGBUILD, `.SRCINFO`, source archive and package together. Pinned drivers include the upstream license and Archer's license. Build inputs identify the license and upstream origin.

## Transferring an existing GUI installation to pacman

Use the complete 2.1 checkout. Quit the GUI and complete or recover any interrupted script install first. The transfer checks the legacy manifest and release hashes or the 2.1 file journal. Locally changed payloads require review. Unrelated files and modules keep their previous owners.

```sh
python3 scripts/archer-package-migration.py plan
sudo python3 scripts/archer-package-migration.py prepare
sudo pacman -U /tmp/archer-app/archer-{core,daemon,gui}-*.pkg.tar.zst
sudo python3 scripts/archer-package-migration.py finish
```

Preparation stops the service, backs up its files and records the settings and service state in `/var/lib/archer/package-migration`. Finish checks package installation, retires the GUI manifest entry, then restores the previous enabled and active states. Re-running an interrupted stage resumes from the journal. If a transfer must be reversed, remove the new application packages then run `sudo python3 scripts/archer-package-migration.py restore`. Inspect edited or conflicting paths before retrying. The settings file remains in `/etc/archer`.

For a fresh package installation enable the daemon with `sudo systemctl enable --now archer-daemon`. For removal, quit the GUI, disable the service, and remove `archer-gui archer-daemon archer-core` with pacman. Remove optional driver packages separately only after reviewing the active driver and boot implications. Use the checkout's `uninstall.sh` for modules still recorded in the script manifest. Keep configuration and migration backups until the restored or upgraded system has been verified after reboot. [Transactions and boot recovery](platform-adapters.md)

### Recover a graphics mode change

When Archer applies an EnvyControl mode, the privileged helper saves its original configuration and the resulting state in `/var/lib/archer/envycontrol-ownership.json`. The script uninstaller checks that record when removing the module that owns graphics configuration. Pacman checks it before removing the daemon or core package. If it finds an administrator edit, it stops the transaction while the recovery helper is still installed. To restore the prior mode while keeping the package, run `sudo python3 /usr/lib/archer/app/archer_system.py display-restore`. Restart after the helper confirms the boot images were rebuilt.

If the helper reports an administrator edit, an interrupted application, or a missing journal from an older installation, retain the current configuration and review the affected paths. A failed rebuild leaves the recovery record available for a retry.

## Offline delivery

An offline target needs a consistent, reviewed transaction resolved against its installed package database, distribution, architecture and repository snapshot. In a matching staging system, include the signed Archer packages in a configured local repo and save the package manager's full upgrade plan. Download those exact packages and their detached signatures into one cache. The keyring must contain only authorized distribution and Archer signers.

```bash
pacman -Sup archer-core archer-daemon archer-gui --print-format $'%n\t%v\t%l' > transaction.tsv
python3 scripts/prepare-offline-bundle.py transaction.tsv verified-cache trusted-signers.gpg archer-offline.tar --sources dist/2.1.0
```

The bundle builder verifies every package signature and checks its package name and version against the plan. It includes the versioned docs, schemas and diagnostics inside archer-core. The bundle includes the matching source archives, recipes and build metadata for each Archer package. It checks each recipe against the hash in the signed package and each source archive against that recipe. Verify that the real target still matches the staged package database before applying its transaction. Local development builds require publisher signing before this workflow can accept them.

The local 2.1.0 artifact directory contains application and optional DKMS packages, corresponding PKGBUILDs, source archives, `.SRCINFO` files and `SHA256SUMS`. From that directory, run `sha256sum -c SHA256SUMS`. Locally built artifacts are unsigned until a publisher signs them with the distribution key.
