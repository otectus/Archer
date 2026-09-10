# Deployment and release checks

Archer is installed from a complete repository checkout with `install.sh`. GUI dependencies come from system packages; `gui/requirements-gui.txt` documents them and is not a pip requirements list. Supported deployment targets are Arch-family systems with systemd. The GUI requires Python 3.10+, GTK 4.12+, libadwaita 1.6+, PyGObject, pycairo, dbus-python, and an X11 or Wayland session. A panel with StatusNotifierItem/D-Bus menu support is needed for the tray.

## Install or upgrade the GUI

Run these commands from the checkout as your normal desktop user; the installer requests sudo for system changes:

```sh
bash install.sh --modules gui --dry-run
bash install.sh --modules gui
archer-gui
```

Before upgrading, use the tray's **Exit** or the GUI's **Quit** action and resolve any unapplied edits. Closing the window can leave the old GUI process in the tray. Launch Archer again after installation to load the new GUI code.

The GUI module validates its required sources before installation, installs the complete `gui/archer` package and assets under `/opt/archer`, and installs the launcher, desktop entry, SVG icon, system D-Bus policy, polkit actions, and systemd unit. It reloads D-Bus policy and systemd, enables the daemon, and restarts it so upgrades load the new daemon code. Restarting restores automatic fan control through the existing shutdown hooks; reapply manual fan settings afterwards if needed.

User preferences remain in `$XDG_CONFIG_HOME/archer/gui.json`, and hardware settings remain in `/etc/archer/settings.json`. A GUI upgrade retains these settings. Exit terminates the GUI and tray; the separately managed system daemon continues running.

Driver, battery, and camera changes may require a reboot and headers for every installed kernel. Follow the installer's reboot/error messages and the [kernel compatibility guide](kernel-compatibility.md). A GUI-only install does not establish driver or hardware compatibility.

## Verify the installed result

```sh
systemctl is-enabled archer-daemon.service
systemctl is-active archer-daemon.service
busctl introspect io.otectus.Archer1 /io/otectus/Archer1
journalctl -u archer-daemon.service -b --no-pager -n 100
```

The installer can finish with a warning if the daemon fails to start. Treat an inactive daemon or failed D-Bus check as an incomplete deployment and resolve the journal error before testing controls.

On a supported desktop, confirm:

1. Archer opens from the application menu and connects to the service.
2. Right-clicking the tray shows **Open**, **Profile**, **Exit**, in that order. Open presents the full GUI.
3. Profile lists the device's supported choices and marks the current profile. Switching from either the tray or GUI updates both; a successful tray change keeps a hidden window hidden.
4. A denied or failed profile change reveals the Performance page and its error. Offline/stale state disables changes while Open and Exit remain usable.
5. Closing hides the window when close-to-tray is enabled. Exit removes the icon and ends the GUI process; dirty edits receive a discard confirmation. Starting Archer again works.
6. Removing/restarting the tray host recovers access to the GUI and registers the tray again when a host returns.

For desktop accessibility and hardware checks, use the [GUI validation matrix](gui.md#development-and-verification). Validate profile/fan response and AC/battery restrictions on real supported hardware.

## Release preparation

- Run all checks in [CONTRIBUTING](../CONTRIBUTING.md#running-tests) and the CI workflow, including the external kernel module matrix for driver changes. Inspect visual artifacts when GUI layouts change.
- Validate `gui/io.github.archer.desktop` with `desktop-file-validate`. Validate the service with `systemd-analyze verify gui/archer-daemon.service` on a machine with `/usr/bin/python3` installed.
- Review the final changes with `git diff --check` and `git status --short`. Include new files under `gui/`, `driver/`, `lib/`, `scripts/`, `tests/`, and `docs/` in the release commit. Exclude caches, generated modules, logs, and local test environments.
- Keep unreleased work under `[Unreleased]` in the changelog until a release version and date are chosen. Update the installer version in `lib/utils.sh`, daemon `VERSION` in `gui/archer_daemon.py`, and GUI `__version__` in `gui/archer/__init__.py` as appropriate. The GUI currently has its own version series; DKMS package versions are independent and must match their source/patch manifests.
- Test both a fresh install and an upgrade from the previous release on a disposable supported system. Check service startup after reboot, preserved settings, application-menu launch, tray behavior, and manifest-aware uninstall.

Local mock tests cannot establish physical hardware compatibility or the behavior of every desktop tray host. Record the systems and kernels actually checked in the release notes.
