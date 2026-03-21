# Archer Compatibility Suite (Linuwu-DAMX Installer v2.0)

Have an Acer laptop running Arch Linux? You're in the right place.

![Archer Compatibility Suite](https://i.imgur.com/KvbGFQw.png)

A comprehensive, modular compatibility suite for Acer laptops running Arch Linux and Arch-based distributions. Originally built for Linuwu-Sense and DAMX (Div Acer Manager Max), the installer now provides hardware-aware detection and targeted fixes for a broad range of Acer laptop issues on Linux.

## Supported Hardware

| Model Family | Fan/RGB (DAMX) | Battery Limit | GPU Switching | Touchpad Fix | Audio Fix | WiFi/BT | Power Mgmt | Thermal Profiles |
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

## Available Modules

### 1. DAMX Fan & RGB Control (core-damx)
Installs the [Linuwu-Sense](https://github.com/0x7375646F/Linuwu-Sense) kernel driver via DKMS and the [DAMX](https://github.com/PXDiv/Div-Acer-Manager-Max) GUI daemon for fan speed control and RGB keyboard management. Blacklists the default `acer_wmi` module for exclusive hardware access.

### 2. Battery Charge Limit (battery)
Installs the [acer-wmi-battery](https://github.com/frederik-h/acer-wmi-battery) DKMS module to limit charging to 80%, extending battery lifespan. Persists across reboots via a udev rule. Prefers AUR installation when `paru` or `yay` is available.

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
Installs SOF firmware and ALSA UCM configuration. Platform-specific fixes:
- **AMD**: Configures SOF driver, disables legacy ACP PDM conflicts
- **Intel**: Validates SOF firmware loading, rebuilds initramfs if needed

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

### 8. Kernel Thermal Profiles (thermal)
Enables native `acer_wmi` thermal profile support on kernel 6.8+. Provides access to Eco, Silent, Balanced, Performance, and Turbo modes via the standard `platform_profile` sysfs interface and the physical mode button.

> **Conflict Warning**: This module requires `acer_wmi` to be loaded, which conflicts with the DAMX module (Linuwu-Sense blacklists `acer_wmi`). You cannot use both simultaneously.

### 9. Archer GUI (gui)
A GTK4/Adwaita control panel with a root daemon for real-time hardware management. Provides a dashboard with CPU/GPU monitoring, fan speed control, battery charge limiting, RGB keyboard configuration, and thermal profile switching. Communicates with the Linuwu-Sense kernel driver via a Unix socket daemon.

> **Note**: This module requires a display server (X11 or Wayland). For full functionality, install the DAMX module (`core-damx`) first — the GUI daemon uses the Linuwu-Sense driver for hardware control.

## Installation

Clone the repository and run the installer:

```bash
git clone https://github.com/otectus/Linuwu-DAMX-Installer.git
cd Linuwu-DAMX-Installer
./install.sh
```

The installer will:
1. Detect your hardware (model, GPU, WiFi chipset, battery, kernel, distro)
2. Recommend modules based on detected hardware
3. Present an interactive menu for module selection
4. Install shared dependencies and selected modules
5. Verify each installation and report results

### Non-Interactive Installation

```bash
# Install all recommended modules
./install.sh --all

# Install specific modules
./install.sh --modules "core-damx,battery,gpu"

# Skip confirmation prompts
./install.sh --all --no-confirm

# Preview without making changes
./install.sh --dry-run

# Show help
./install.sh --help
```

### CLI Flags

| Flag | Description |
|------|-------------|
| `--all` | Install all recommended modules (non-interactive) |
| `--modules LIST` | Comma-separated list of module IDs to install |
| `--no-confirm` | Skip all confirmation prompts |
| `--dry-run` | Show what would be done without making changes |
| `--help`, `-h` | Show help message |
| `--version`, `-v` | Show installer version |

## Verification

After installation, verify the state of installed modules:

```bash
# DAMX / Linuwu-Sense
dkms status                                    # linuwu-sense should show 'installed'
lsmod | grep linuwu_sense                      # Module should be loaded
systemctl --user status damx-daemon            # Daemon should be active

# Battery
cat /sys/bus/wmi/drivers/acer-wmi-battery/health_mode  # Should read '1'

# GPU
envycontrol --query                            # Should show configured mode

# TLP
sudo tlp-stat -s                               # Should show TLP active

# Thermal Profiles
cat /sys/firmware/acpi/platform_profile        # Should show current profile
```

## Uninstallation

The uninstaller reads the install manifest to selectively remove only what was installed:

```bash
./uninstall.sh
```

If no manifest is found (v1 installation), a legacy fallback removes all known components.

## Project Structure

```
Linuwu-DAMX-Installer/
  install.sh                    # Main entry point with interactive menu
  uninstall.sh                  # Manifest-aware uninstaller
  lib/
    utils.sh                    # Shared logging, error handling, helpers
    detect.sh                   # Hardware detection and recommendation engine
    manifest.sh                 # Install state tracking (JSON manifest)
  modules/
    core-damx.sh                # Linuwu-Sense + DAMX fan/RGB control
    battery.sh                  # acer-wmi-battery charge limiting
    gpu.sh                      # EnvyControl GPU switching
    touchpad.sh                 # I2C HID touchpad fixes
    audio.sh                    # SOF firmware and audio config
    wifi.sh                     # WiFi/Bluetooth troubleshooting
    power.sh                    # TLP power management
    thermal.sh                  # Kernel thermal profiles
    gui.sh                      # GTK4/Adwaita control panel + daemon
  gui/                          # GUI application source files
    archer_daemon.py            # Root daemon (Unix socket, sysfs control)
    archer_gui.py               # GTK4 application launcher
    archer/                     # GUI modules (pages, widgets, client, tray)
    assets/                     # Icons and images
```

## Technical Notes

- **Secure Boot**: If Secure Boot is enabled, you must manually sign DKMS kernel modules (linuwu-sense, acer-wmi-battery) or disable Secure Boot.
- **BIOS Configuration**: Some Acer laptops ship with RAID storage mode enabled. Switch to AHCI mode in BIOS for Linux compatibility. Disable Fast Startup for dual-boot setups.
- **CachyOS**: The installer automatically detects CachyOS kernels and installs the correct `-cachyos-headers` package. Clang/LLVM compiler flags are applied when a Clang-built kernel is detected.
- **AUR Helpers**: Modules that install AUR packages (battery, GPU) prefer `paru` or `yay` if available, with manual fallback otherwise. The installer never installs an AUR helper for you.
- **Install Manifest**: Stored at `~/.local/share/damx/install-manifest.json`. Tracks installed modules, files, DKMS modules, and packages for clean uninstallation.

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

Then add the module ID and label to the `MODULE_IDS` and `MODULE_LABELS` arrays in `install.sh`, and update the recommendation logic in `lib/detect.sh`.

---

*Maintained for the Acer Linux Community.*
