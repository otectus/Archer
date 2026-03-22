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

Install on Arch:
```bash
sudo pacman -S shellcheck bash-bats python
```

## Running Tests

```bash
# Run all tests
bats tests/

# Run a specific test file
bats tests/detect.bats

# Run with verbose output
bats -t tests/
```

## Running Lints

```bash
# Lint all shell scripts
find . -name '*.sh' -not -path './.git/*' -exec shellcheck -x -s bash {} \;

# Lint Python GUI code
flake8 gui/ --max-line-length=120
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
  modules/             # 13 independent modules (see below)
  gui/                 # GTK4/Adwaita application + D-Bus daemon
  tests/               # Bats test suite
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

Some modules are mutually exclusive:

- **driver** (Linuwu-Sense) and **thermal** (Kernel Thermal Profiles) both interact with `acer_wmi`. The driver blacklists it; thermal requires it. The installer enforces this conflict in `check_conflicts()`.

When adding modules that conflict with existing ones, update `check_conflicts()` in `install.sh` and add conflict tags in `display_menu()`.

## CI Pipeline

All PRs are checked by GitHub Actions:

- **ShellCheck**: Lints all `.sh` files
- **Bash syntax**: `bash -n` on all scripts
- **Bats tests**: Runs `tests/*.bats`
- **Python lint**: flake8 on `gui/`

Ensure all checks pass before submitting a PR.
