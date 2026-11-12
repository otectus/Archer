# Archer Compatibility Suite

Have an Acer laptop running Arch Linux? You're in the right place.

![Archer Compatibility Suite](https://i.imgur.com/KvbGFQw.png)

A modular compatibility suite for Acer laptops running Arch Linux and Arch-based distributions. Provides hardware-aware detection, kernel driver installation, a GTK4 control panel with D-Bus IPC and polkit authorization, and targeted fixes for a broad range of Acer laptop issues on Linux.

## Supported Hardware

| Model Family | Fan/RGB (Driver) | Battery Limit | GPU Switching | Touchpad Fix | Audio Fix | WiFi/BT | Power Mgmt | Thermal Profiles |
|-------------|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| **Nitro**       | R | R | R | O | O | O | O | O |
| **Predator**    | R | R | R | O | O | O | O | O |
| **Helios**      | R | R | R | O | O | O | O | O |
| **Triton**      | R | R | R | O | O | O | O | O |
| **Swift**       | O | R | - | O | O | O | O | - |
| **Aspire**      | O | R | O | O | O | O | O | - |
| **Spin**        | O | R | - | O | O | O | O | - |

**R** = Recommended, **O** = Optional/Available, **-** = Not applicable

## Supported Distributions

- Arch Linux
- CachyOS (auto-detected kernel headers)
- EndeavourOS
- Manjaro
- Garuda Linux

A running systemd system manager is required. Artix and other init systems are not supported by this installer.

## Architecture

Archer uses a root daemon with a D-Bus system service for secure hardware control:

```
Archer GUI (GTK4/Adwaita)  ──  D-Bus (io.otectus.Archer1)  ──  Archer Daemon (root)
       │                              │                              │
  6 destinations                     polkit auth                    sysfs / hwmon
  system tray               session-cached                  Linuwu-Sense driver
```

- **Daemon** (`archer-daemon.service`): Runs as root, communicates with hardware via sysfs/hwmon, exposes a D-Bus interface with polkit-protected methods.
- **D-Bus service** (`io.otectus.Archer1`): Read-only methods (telemetry, settings) are unprivileged. Mutating methods (fan control, profile switching, display mode) require polkit authorization, cached per session.
- **GUI**: GTK4/Adwaita application with system tray support (close-to-tray via D-Bus StatusNotifierItem). Connects to daemon exclusively through D-Bus.
- **Installer**: Bash-based modular system with 13 modules, hardware detection, manifest tracking, and interactive menu.

## Available Modules

### 1. Linuwu-Sense Kernel Driver (driver)
Installs the [Linuwu-Sense](https://github.com/0x7375646F/Linuwu-Sense) kernel driver via DKMS for fan speed control, RGB keyboard access, and battery management at the hardware level. Blacklists the default `acer_wmi` module for exclusive hardware access.

ANV16S-41 fan support is implemented through an exact driver DMI match and the existing Nitro v4 WMI protocol. It is **awaiting physical validation on the issue reporter's BIOS V1.12 hardware**. Archer installs a pinned upstream revision with an isolated patch series as `linuwu-sense/1.0.archer3`; DKMS 1.0 is migrated, matching kernel headers are required, and activation requires a reboot. See the [driver package/update guide](driver/linuwu-sense/README.md) and [ANV16S-41 diagnostics and safe validation procedure](docs/anv16s-41.md).

Manual speeds are not restored across daemon starts. Stopping manual control or either curve restores both fans to firmware Auto; failed writes trigger an Auto attempt. Independent native platform-profile providers are preserved.

### 2. Battery Charge Limit (battery)
Sets a persistent charge policy, defaulting to an 80% limit. Prefers an existing standard battery threshold or Linuwu interface; otherwise installs the pinned [acer-wmi-battery](https://github.com/frederik-h/acer-wmi-battery) fallback. The GUI and battery-only boot/device-event service share `/etc/archer/battery.json`, so disabling the limit survives reboot and device events.

### 3. GPU Switching (gpu)
Installs [EnvyControl](https://github.com/bayasdev/envycontrol) for NVIDIA Optimus hybrid graphics management. Supports three modes:
- **hybrid** — Integrated GPU by default, NVIDIA on demand (recommended)
- **nvidia** — Always use discrete GPU
- **integrated** — Disable NVIDIA entirely for maximum battery life

### 4. Touchpad Fix (touchpad)
Addresses I2C HID touchpad detection failures common on several Acer models. Applies up to three strategies:
- AMD `pinctrl_amd` module load ordering fix
- Systemd service for I2C HID module reload on boot
- GRUB kernel parameters (`i8042.reset i8042.nomux`)

### 5. Audio Fix (audio)
Installs SOF firmware, ALSA UCM configuration, and diagnostic tools. Uses the kernel's AMD driver selection and removes Archer's obsolete ACP `enable` directives. On Intel, readable kernel logs can identify missing SOF firmware that needs an initramfs rebuild. Unavailable logs are reported as unknown.

### 6. WiFi/Bluetooth Troubleshooting (wifi)
Chipset-aware diagnostics and fixes:
- **MediaTek** (MT7921/MT7922/MT7925): Firmware checks, PCIe device reset, compatibility warnings
- **Intel** (AX200/AX210/AX211/BE200): rfkill unblocking, firmware validation
- **Realtek**: Guidance for AUR out-of-tree drivers
- Common: Bluetooth service setup, NetworkManager enablement

### 7. Power Management (power)
Installs [TLP](https://linrunner.de/tlp/) with an Acer-optimized configuration:
- Performance governor on AC, powersave on battery
- WiFi power management, USB autosuspend
- Runtime PM for PCI devices (NVIDIA GPU power saving)

Writes configuration before running `tlp start`; platform-profile overrides are emitted only when supported. Switching from power-profiles-daemon retains its package and records its service state. Uninstall restores recorded service/mask states and preserves administrator-edited configuration. Archer's manual Game Mode is unavailable while TLP owns power policy.

### 8. Kernel Thermal Profiles (thermal)
Reuses working native or Linuwu-Sense platform profiles. It never removes the Linuwu blacklist to load a second Acer WMI owner, and does not force Predator generation flags on Nitro. The driver module preserves an existing independent provider such as AMD PMF; available choices come from sysfs.

### 9. Archer GUI (gui)
GTK4/Adwaita control panel with a root daemon for real-time hardware management. The daemon exposes a D-Bus service (`io.otectus.Archer1`) with polkit authorization for secure access. Features include:
- **Overview** — Live CPU/GPU, fan and battery summaries, with temperature history
- **Performance** — Thermal profiles, automatic/manual fan control, and Game Mode
- **Battery** — Charge protection, calibration, and off-state USB charging
- **Display & Keyboard** — Graphics configuration with persistent reboot status, panel response, zone colors and lighting effects
- **Audio** — Noise suppression with clear setup and audio restart feedback
- **System** — Device information, startup behavior, firmware checks, and Advanced driver/recovery tools
- **Appearance** — System theme by default, optional Light/Dark overrides, high contrast and supported system accent colors
- **Collapsible sidebar** — Collapse/expand buttons and **Ctrl+B**, with the desktop preference saved across launches
- **System tray** — Right-click for **Open**, **Profile** (Eco, Quiet, Balanced, Performance, Turbo as supported), and **Exit**; current profile stays synchronized with the GUI. Close-to-tray and tray-host recovery are supported.
- **Automatic startup** — The GUI and tray start at desktop login by default. Disable or re-enable this for your account in **Preferences → Start Archer at login**.

Requires **GTK 4.12+ and libadwaita 1.6+**. See the [control center guide](docs/gui.md) for navigation, shortcuts, saving behavior, and GUI verification.

Keyboard lighting is driven directly through the ENE K5130 controller where it is present, because the WMI path applies brightness but silently discards colour and effect mode. See the [ENE lighting protocol notes](docs/ENE_PROTOCOL.md).

For installation, upgrades, and release checks, see the [deployment guide](docs/deployment.md).

> **Note**: Requires a display server (X11 or Wayland). Install the driver module first for full hardware control.

### 10. Game Mode (gamemode)
Installs [GameMode](https://github.com/FeralInteractive/gamemode) with governor and scheduling settings in `/etc/gamemode.ini`; an existing administrator configuration is preserved. Native GameMode works without multilib; 32-bit support is installed only when available. Archer's separate manual toggle temporarily changes supported platform profiles, all available CPU EPP policies, and identified NVIDIA persistence settings, then restores their prior values. Failed changes roll back and retain recovery state if restoration fails.

### 11. Audio Enhancement (audio-enhance)
Sets up microphone noise suppression through PipeWire and [rnnoise](https://github.com/xiph/rnnoise). Requires the system LADSPA plugin and loads a filter from `/etc/pipewire/pipewire.conf.d/`. After restarting PipeWire, select **Archer Noise Suppression** as the application's input. Disabled configurations survive upgrades and are removed by uninstall.

### 12. Loopback Camera Setup (camera-enhance)
Installs [v4l2loopback](https://github.com/v4l2loopback/v4l2loopback) with boot loading and an **Archer Camera** device using a free video number. An external capture/effect application must feed it frames before applications can use it. Archer does not install background blur or a frame producer. Existing loopback users may require a reboot to apply the new device options.

### 13. Firmware Advisor (firmware)
Installs [fwupd](https://fwupd.org/) for firmware update detection. Displays current BIOS version and checks the Linux Vendor Firmware Service (LVFS) for available updates. Advisory only — never performs automatic updates.

## Installation

Clone the repository and run the installer:

```bash
git clone https://github.com/otectus/Archer.git
cd Archer
./install.sh
```

The installer will:
1. Detect your hardware (model, GPU, WiFi chipset, battery, kernel, distro)
2. Recommend modules based on detected hardware
3. Present an interactive menu for module selection (13 modules)
4. Install shared dependencies and selected modules
5. Verify each installation and report results

### Non-Interactive Installation

```bash
# Install all recommended modules
./install.sh --all

# Install specific modules
./install.sh --modules "driver,battery,gpu,gui"

# Skip confirmation prompts
./install.sh --all --no-confirm

# Preview without making changes
./install.sh --dry-run

# Check status of installed modules
./install.sh --verify

# Show help
./install.sh --help
```

### CLI Flags

| Flag | Description |
|------|-------------|
| `--all` | Install all recommended modules (non-interactive) |
| `--modules LIST` | Comma-separated list of module IDs to install |
| `--verify` | Check status of previously installed modules |
| `--no-confirm` | Skip all confirmation prompts |
| `--dry-run` | Show what would be done without making changes |
| `--verbose` | Enable debug output for troubleshooting |
| `--log FILE` | Write all output to FILE (in addition to console) |
| `--help`, `-h` | Show help message |
| `--version`, `-v` | Show installer version |

## Verification

After installation, verify the state of installed modules:

```bash
# Linuwu-Sense Driver
dkms status                                    # linuwu-sense should show 'installed'
lsmod | grep linuwu_sense                      # Module should be loaded

# Archer Daemon (D-Bus)
sudo systemctl status archer-daemon            # Daemon should be active
busctl introspect io.otectus.Archer1 /io/otectus/Archer1  # D-Bus methods visible

# Archer GUI
archer-gui                                     # Launch the control panel

# Battery (use the node provided by your device)
cat /sys/class/power_supply/BAT*/charge_control_end_threshold  # 80 when native charge protection is enabled
# Driver fallbacks: Linuwu battery_limiter or acer-wmi-battery health_mode reads 1

# GPU
envycontrol --query                            # Should show configured mode

# TLP
sudo tlp-stat -s                               # Should show TLP active

# Thermal Profiles
cat /sys/firmware/acpi/platform_profile        # Should show current profile

# Audio Enhancement
pactl list sources | grep "Archer Noise"       # Virtual source should appear

# Firmware
fwupdmgr get-devices                           # Should list detected devices
```

## Configuration

The Archer daemon persists user settings to `/etc/archer/settings.json`. This file is managed automatically and survives reboots. Settings include:

- Thermal profile selection
- Fan speed and custom curve definitions
- Keyboard RGB colors and effect modes
- Battery charge limit and USB charging level
- Game mode state
- Audio enhancement toggles
- LCD override and boot sound preferences

Settings are restored automatically when the daemon starts. LCD override, boot animation/sound and backlight timeout are each replayed once, without being saved again. Some firmware cannot report their state, and the driver then reads `-1`. Archer shows the last accepted request as unverified rather than as Off, and **Turn off and save** always sends and saves Off. See [LCD override recovery](#lcd-override-keeps-turning-on-or-inverse-ghosting-returns).

## Uninstallation

The uninstaller reads the install manifest to selectively remove only what was installed:

```bash
./uninstall.sh
```

If no manifest is found (legacy installation), a fallback removes all known components.

## Project Structure

```
Archer/
  install.sh                      # Main entry point with interactive menu
  uninstall.sh                    # Manifest-aware uninstaller
  lib/
    utils.sh                      # Shared logging, error handling, helpers
    detect.sh                     # Hardware detection and recommendation engine
    manifest.sh                   # Install state tracking (JSON manifest)
  modules/
    driver.sh                     # Linuwu-Sense kernel driver (DKMS)
    battery.sh                    # acer-wmi-battery charge limiting
    gpu.sh                        # EnvyControl GPU switching
    touchpad.sh                   # I2C HID touchpad fixes
    audio.sh                      # SOF firmware and audio config
    wifi.sh                       # WiFi/Bluetooth troubleshooting
    power.sh                      # TLP power management
    thermal.sh                    # Kernel thermal profiles
    gui.sh                        # Archer GUI + daemon + D-Bus + polkit
    gamemode.sh                   # Game Mode (GameMode + governor switching)
    audio-enhance.sh              # Audio noise suppression (PipeWire/rnnoise)
    camera-enhance.sh             # Virtual camera (v4l2loopback)
    firmware.sh                   # Firmware update advisor (fwupd)
  gui/
    archer_daemon.py              # Root daemon (D-Bus, sysfs, fan curves, game mode)
    archer_dbus.py                # D-Bus service with polkit authorization
    archer_gui.py                 # GTK4 application launcher
    io.otectus.Archer1.conf       # D-Bus system bus policy
    io.otectus.Archer1.policy     # Polkit action definitions
    archer-daemon.service         # Systemd service unit
    io.github.archer.desktop      # Desktop entry
    archer/                       # GUI modules (6 destinations, client, tray, widgets)
    assets/                       # Icons (SVG, PNG)
```

## Technical Notes

- **Secure Boot**: DKMS handles module signing, but its certificate must be enrolled. A rejected signing key is a module-load failure, separate from a compiler/API failure; see the [diagnostic guide](docs/kernel-compatibility.md#reporting-a-future-failure).
- **BIOS Configuration**: Some Acer laptops ship with RAID storage mode enabled. Switch to AHCI mode in BIOS for Linux compatibility. Disable Fast Startup for dual-boot setups.
- **Kernel compatibility**: Capability detection supports continued kernel evolution without a version allowlist. Matching headers are required for every installed kernel, including CachyOS variants. Toolchains follow each build target. After a kernel upgrade, reboot into the installed kernel before loading new modules. See [kernel support, validation and DKMS troubleshooting](docs/kernel-compatibility.md).
- **AUR Helpers**: Modules that install AUR packages (GPU, audio-enhance) prefer `paru` or `yay` if available, with manual fallback otherwise. Battery driver sources use pinned DKMS packaging. The installer never installs an AUR helper for you.
- **D-Bus / Polkit**: The daemon registers as `io.otectus.Archer1` on the system bus. Read-only methods are unprivileged. Mutating methods require polkit authorization, cached per session (`auth_admin_keep`). System-level operations (restart, modprobe) always prompt (`auth_admin`).
- **Install Manifest**: Stored at `/var/lib/archer/install-manifest.json` (root-owned, 0644). Tracks installed modules, files, DKMS modules, and packages for clean uninstallation. Manifests from older user-home locations (`~/.local/share/archer/`, legacy `~/.local/share/damx/`) are migrated automatically on the next install or uninstall run.
- **Fan Curve Safety**: The curve worker attempts firmware Auto after three failed control ticks. Daemon shutdown, systemd's `ExecStopPost`, and driver lifecycle hooks also attempt Auto restoration. No kernel file-based settings or manual speeds are replayed.

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

To add a new module, create `modules/<id>.sh` implementing the module interface:

```bash
MODULE_NAME="Display Name"
MODULE_ID="module-id"
MODULE_DESCRIPTION="What this module does"

module_detect()          # Return 0 if relevant to current hardware
module_check_installed() # Return 0 if already installed
module_install()         # Perform installation
module_uninstall()       # Reverse installation
module_verify()          # Return 0 if working correctly
```

Then add the module ID and label to the `MODULE_IDS` and `MODULE_LABELS` arrays in `lib/modules.sh`, and update the recommendation logic in `lib/detect.sh`.

---

*Maintained for the Acer Linux Community.*
