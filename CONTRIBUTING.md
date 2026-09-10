# Contributing to Archer Compatibility Suite

## Development Setup

```bash
git clone https://github.com/otectus/Archer.git
cd Archer
```

### Requirements

- Bash 5.0+
- [ShellCheck](https://www.shellcheck.net/) for linting
- [Bats](https://github.com/bats-core/bats-core) for testing
- Python 3.10+ (for GUI development)
- GTK 4.12+, libadwaita 1.6+, PyGObject, pycairo, and dbus-python for GUI tests

Install on Arch:
```bash
sudo pacman -S shellcheck bash-bats python python-flake8 python-gobject python-cairo python-dbus gtk4 libadwaita xorg-server-xvfb xorg-xauth dbus desktop-file-utils
```

## Running Tests

```bash
# Shell tests
bats tests/

# Mock sysfs, fan safety, C harness, and pinned source preparation
git clone --no-checkout https://github.com/0x7375646F/Linuwu-Sense.git /tmp/linuwu-upstream
LINUWU_TEST_REPO=/tmp/linuwu-upstream python -m unittest discover -s tests -v

# GUI data contracts, interactions, tray D-Bus protocol, and daemon smoke test
python tests/gui_contract_test.py
xvfb-run -a dbus-run-session -- python tests/gui_test.py
dbus-run-session -- bash tests/dbus_smoke.sh
```

Run Bats files individually with `bats tests/detect.bats`. See [GUI verification](docs/gui.md#development-and-verification) for visual, contrast, and text-scaling runs, and [kernel validation](docs/kernel-compatibility.md#validation-evidence) for external module builds. These suites use mocks/private sessions and do not require changing host hardware settings.

## Running Lints

```bash
# Lint all shell scripts
rg --files -g '*.sh' -0 | xargs -0 shellcheck -x -s bash
rg --files -g '*.sh' -0 | xargs -0 -n1 bash -n

# Match CI's Python lint configuration
flake8 gui tests scripts --max-line-length=120 --ignore=E501,W503,E402
python -m compileall -q gui tests scripts
desktop-file-validate gui/io.github.archer.desktop
systemd-analyze verify gui/archer-daemon.service
git diff --check
```

## Project Architecture

```
Archer/
  install.sh           # Main entry point, CLI parsing, interactive menu
  uninstall.sh         # Manifest-aware uninstaller
  lib/
    utils.sh           # Logging, run/run_sudo, helpers (shared by all scripts)
    detect.sh          # Hardware detection engine (DMI, GPU, WiFi, kernel, distro)
    manifest.sh        # JSON manifest for tracking installed state
    kernel.sh          # Target headers, toolchains, and initramfs helpers
    dkms.sh            # Shared driver packaging and lifecycle helpers
  modules/             # 13 independent modules (see below)
  gui/                 # GTK4/Adwaita application + D-Bus daemon
  driver/              # Pinned sources, ordered patches, and source preparation
  scripts/             # Diagnostics and external kernel module build checks
  docs/                # GUI, deployment, and hardware validation guides
  tests/               # Bats, Python, GTK, C harness, and D-Bus tests
```

## Adding a New Module

1. Create `modules/<id>.sh` implementing the module interface:

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

2. Register in `install.sh`:
   - Add the ID to `MODULE_IDS` array
   - Add the label to `MODULE_LABELS` array

3. Add recommendation logic in `lib/detect.sh` `build_recommendations()`.

4. Add a test in `tests/modules.bats` verifying the module defines all required functions.

5. Update the README with a description of the module.

## Coding Standards

### Bash

- Use `#!/usr/bin/env bash` shebang
- Always `set -euo pipefail` in entry scripts
- Use `[[ ]]` for conditionals (not `[ ]`)
- Quote all variables: `"$var"` not `$var`
- Use `local` for function-scoped variables
- Use `has_cmd` (from `utils.sh`) instead of `command -v` directly
- Use `run` / `run_sudo` for commands that should respect `--dry-run`
- Prefix module-private variables with `_` (e.g., `_BATTERY_DKMS_NAME`)
- Keep modules self-contained: each module sources `utils.sh` globals but defines its own functions

### Python (GUI)

- Follow PEP 8 with max line length of 120
- Use type hints where practical
- GTK4/Adwaita patterns: `Adw.Application`, `Adw.ApplicationWindow`
- D-Bus client calls go through `archer/client.py`

## Commit Messages

- Use imperative mood: "Add battery module" not "Added battery module"
- First line: concise summary (under 72 chars)
- Body: explain *why*, not just *what*

## Module Conflicts

The **driver** module owns the Acer WMI device. **thermal** reuses a native or
Linuwu profile provider and must never remove the blacklist to load a second
WMI owner. Independent providers such as AMD PMF must retain their profiles.

Hardware support belongs in the driver layer. See the [pinned driver package](driver/linuwu-sense/README.md)
for revision/patch updates and DKMS version migration. Do not infer fan capability
from a marketing model name or force a generation in the GUI.

## CI Pipeline

All PRs are checked by GitHub Actions:

- **ShellCheck**: Lints all `.sh` files
- **Bash syntax**: `bash -n` on all scripts
- **Bats tests**: Runs `tests/*.bats`
- **Python lint and syntax**: flake8 and compilation of `gui/`, `tests/`, and `scripts/`
- **Desktop integration metadata**: desktop entry validation and D-Bus/polkit XML parsing
- **GTK UI and data contracts**: simulated interactions, tray protocol/lifecycle checks, and visual captures at multiple sizes, high contrast, and large text
- **D-Bus smoke**: service introspection, telemetry, and firmware responsiveness on a private session bus
- **Fan/driver tests**: `LINUWU_TEST_REPO=/path/to/upstream python3 -m unittest discover -s tests -v`
  validates mock sysfs, the actual patched C fan code, deterministic preparation
  and refusal on source mismatch. CI fetches upstream before this test.
- **Kernel modules**: compile Linuwu-Sense, acer-wmi-battery, and v4l2loopback against rolling Arch/LTS and fixed 6.8/6.12 headers

Ensure all checks pass before submitting a PR. Follow the [deployment guide](docs/deployment.md) for upgrades and release validation; automated tests do not replace desktop and Acer hardware checks.
