# Archer

Archer 2.1.0 is a Linux hardware control center and a modular installer for Acer laptops. You can inspect a machine without installing a driver, then enable individual controls that its kernel, firmware and running services expose.

## Getting started

From the checkout, run a passive scan as your login user. The commands work without GTK, the Archer daemon, or a network connection.

```sh
python3 gui/archer_cli.py status
python3 gui/archer_cli.py inventory
python3 gui/archer_cli.py report > archer-report.json
./install.sh --help
./install.sh --dry-run --modules gui
```

The inventory is for local inspection. Preview and edit the support report before sharing it. More commands, field definitions, and a rescue-root example appear in the [diagnostics guide](docs/diagnostics.md).

## Installation

For an Arch/systemd desktop, build the split packages as a regular user from a reviewed checkout. Use an empty build directory.

```sh
bash scripts/prepare-package.sh /tmp/archer-build
(cd /tmp/archer-build && SOURCE_DATE_EPOCH="$(cat SOURCE_DATE_EPOCH)" makepkg)
sudo pacman -U /tmp/archer-build/archer-{core,daemon,gui}-*.pkg.tar.zst
sudo systemctl enable --now archer-daemon.service
archer-gui
```

Install only `archer-core` for read-only inventory and command-line diagnostics. Optional media and desktop integrations have separate runtime requirements. Existing script installations need an ownership transfer before installing the GUI packages. The script installer remains available for individual compatibility modules. Run it as your login user and review the full system upgrade and module selection before granting elevation. [Deployment, migration and recovery](docs/deployment.md)

## Supported hardware and distributions

A model name identifies a machine. The individual battery, fan controller, display connector, radio or camera interface determines what Archer can offer. The kernel and BIOS version matter even on machines sold under the same name. The compatibility matrix records the difference between discovery, fixture coverage, successful compilation and a physical test. [Feature and distribution evidence](docs/compatibility-2.1.md)

Archer prefers working native interfaces. Keep existing battery, graphics, power and network owners unless there is a specific missing capability to address. Script plans restrict modules on OpenRC. Other init systems can use the read-only Python CLI. Review boot and distribution limits before modifying a target. [Platform adapters](docs/platform-adapters.md)

## Using Archer

| Task | Entry point |
| --- | --- |
| Check temperatures, fans and performance profiles | **Performance**. Review the provider, supported choices and current readings. Use firmware Auto to return cooling to firmware control. |
| Inspect batteries or change charging | **Battery** lists individual packs. Available actions depend on the threshold ABI and its policy owner. A full-charge override expires. |
| Review GPU mode or panel response | **Display & Keyboard → Display**. Check affected outputs and the restart requirement. A configured graphics mode can differ from the active renderer. |
| Use the microphone or camera | **Audio → Microphone** or **System → Camera**. Choose a physical source and start capture explicitly. Apps select the processed source through their own device selector. |
| Review firmware, health or release readiness | **System → Firmware** and **Hardware checks**, or `archer readiness`. |
| Use session-owned preferences or inspect a peripheral | **System → Session tools** and **Input**, or `archer-session --help`. |
| Compare two boots | Save `archer snapshot`, then run `archer compare FILE`. For sleep battery measurements use `sleep-sample` and `sleep-compare`. |

The six destinations also work on a narrow screen. **Ctrl+B** toggles desktop navigation. The tray offers Open, Profile and Exit when a StatusNotifier host is available. [Desktop guide](docs/gui.md)

## Optional modules

Run `./install.sh --help` for the module catalog. Driver, GPU, power, thermal, battery, input, wireless, GameMode, firmware, audio and camera modules have independent checks. A virtual camera requires a functioning capture source. The green-backdrop blur uses a chroma key. Camera controls and the rnnoise microphone chain run in your session while capture is active. [Feature behavior and limitations](docs/compatibility-2.1.md)

## Architecture

The script installer and Python tools share a sysfs inventory. A polkit-authorized system D-Bus service performs hardware operations. Desktop settings, media capture and temporary profile holds run in the user session. The installer records system changes below `/var/lib/archer`. Driver packages use independent, pinned upstream revisions. [API and schemas](docs/backend-api.md) · [Driver build evidence](docs/kernel-compatibility.md)

## Troubleshooting

### GUI shows "Offline" or "Stale"

1. Confirm the daemon is running:
   ```bash
   systemctl status archer-daemon
   ```
   If it's not active, start it: `sudo systemctl restart archer-daemon`.

2. Confirm the D-Bus name is claimable:
   ```bash
   busctl list | grep io.otectus.Archer1
   busctl introspect io.otectus.Archer1 /io/otectus/Archer1
   ```
   If the name isn't visible, the policy file may not be loaded. The installer reloads `dbus.service` automatically, but you can do it manually:
   ```bash
   sudo systemctl reload dbus.service
   sudo systemctl restart archer-daemon
   ```

3. Tail the daemon log for the actual failure:
   ```bash
   sudo journalctl -u archer-daemon -n 100 --no-pager
   ```

If the GUI shows the status flipping between "Connected" and "Stale", the daemon is up but not emitting telemetry on schedule — check `journalctl` for `TelemetryUpdated emit failed` messages (often a transient sysfs/nvidia-smi hang).

### LCD override keeps turning on, or inverse ghosting returns

Before the issue #10 fix, Archer showed an unreadable LCD override state as Off while saving and replaying On, so each attempt to turn it off sent On again. On affected firmware, `cat .../lcd_override` prints `-1` even after a working command. `-1` means the driver could not decode the firmware reply, not that the request failed.

After upgrading, open **Display & Keyboard → Display → Panel response** and choose **Turn off and save**. Off is then saved for startup. On firmware that cannot report the state, the status reads “Off requested”.

Without the GUI, send the same request through the daemon, which also saves it:

```bash
sudo busctl --system call io.otectus.Archer1 /io/otectus/Archer1 io.otectus.Archer1 SetLcdOverride b false
```

On a fixed daemon, `"success": true` means the request was accepted and saved. Older daemons can report success even when saving failed, so also check the saved value. It should be `false` (`0` in older files):

```bash
grep '"lcd_override"' /etc/archer/settings.json
```

If the daemon cannot run or save, repair the setting by hand in this order:

1. Exit the GUI from the tray, then stop the daemon so its in-memory copy cannot overwrite your edit: `sudo systemctl stop archer-daemon`.
2. Back up the file: `sudo cp -a /etc/archer/settings.json /etc/archer/settings.json.bak`.
3. Change only the LCD key, keep every other setting, and replace the file atomically:
   ```bash
   sudo python3 - <<'EOF'
   import json, os, tempfile
   path = "/etc/archer/settings.json"
   with open(path) as stream:
       data = json.load(stream)
   data["lcd_override"] = False
   fd, temporary = tempfile.mkstemp(dir="/etc/archer", prefix=".settings-")
   with os.fdopen(fd, "w") as stream:
       json.dump(data, stream, indent=2)
       stream.flush()
       os.fsync(stream.fileno())
   os.chmod(temporary, 0o644)
   os.replace(temporary, path)
   EOF
   ```
   If the file is not valid JSON, keep the backup and repair it deliberately. Do not replace it with an LCD-only file.
4. Find the LCD override node on your machine, then write `0` to the path that exists:
   ```bash
   ls /sys/devices/platform/acer-wmi/predator_sense/lcd_override \
      /sys/module/linuwu_sense/drivers/platform:acer-wmi/acer-wmi/predator_sense/lcd_override 2>/dev/null
   echo 0 | sudo tee /sys/devices/platform/acer-wmi/predator_sense/lcd_override
   ```
5. Start the daemon and check that it runs, that the saved value is still Off, and that the panel no longer shows inverse ghosting: `sudo systemctl start archer-daemon && systemctl is-active archer-daemon`. If the service does not start, recovery is incomplete; read `journalctl -u archer-daemon -n 100`.

A sysfs write alone is not enough while On is still saved, because the daemon replays it at its next start. Deleting the key stops the replay but does not turn the current setting off.

### GUI hangs forever / never opens

Check the terminal output from `archer-gui` for missing dependencies or startup errors, then use the D-Bus checks above. Hardware calls run asynchronously with bounded timeouts (normally 5 seconds, longer for operations such as firmware checks). Service failures appear in connection status and control feedback. Use **Exit** or **Quit** before relaunching after an upgrade so an old tray process does not keep serving the window.

## Changelog

Release notes and migration guidance: [CHANGELOG.md](CHANGELOG.md).

## Contributing

See the module interface, test commands, and hardware evidence requirements in the [contribution guide](CONTRIBUTING.md).
