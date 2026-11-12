#!/usr/bin/env bash
# Shared utility functions for Archer Compatibility Suite (Bash)

# Colors
_CYAN="\033[0;36m"
_RED="\033[0;31m"
_YELLOW="\033[0;33m"
_GREEN="\033[0;32m"
_BOLD="\033[1m"
_RESET="\033[0m"

INSTALLER_VERSION="2.0.1"
DRY_RUN="${DRY_RUN:-0}"
NO_CONFIRM="${NO_CONFIRM:-0}"
VERBOSE="${VERBOSE:-0}"
LOG_FILE="${LOG_FILE:-}"
REBOOT_REQUIRED=0

# Internal logging helper: writes to stdout and optionally to a log file
_emit() {
    echo -e "$*"
    if [[ -n "${LOG_FILE:-}" ]]; then
        # Strip ANSI color codes for log file
        echo -e "$*" | sed 's/\x1b\[[0-9;]*m//g' >> "$LOG_FILE" 2>/dev/null || true
    fi
    return 0
}

log()     { _emit "${_CYAN:-}>>>${_RESET:-} $*"; return 0; }
warn()    { _emit "${_YELLOW:-}\u26a0${_RESET:-}  $*"; return 0; }
error()   { _emit "${_RED:-}\u274c${_RESET:-} $*"; exit 1; }
success() { _emit "${_GREEN:-}\u2705${_RESET:-} $*"; return 0; }
debug() {
    if [[ "${VERBOSE:-0}" -eq 1 ]]; then
        _emit "${_BOLD:-}[DEBUG]${_RESET:-} $*"
    fi
    return 0
}

# Execute a command, or print it in dry-run mode
run() {
    if [[ "${DRY_RUN:-0}" -eq 1 ]]; then
        echo -e "${_YELLOW:-}[DRY RUN]${_RESET:-} $*"
        return 0
    fi
    "$@"
}

# Execute a command with sudo, or print it in dry-run mode
run_sudo() {
    if [[ "${DRY_RUN:-0}" -eq 1 ]]; then
        echo -e "${_YELLOW:-}[DRY RUN]${_RESET:-} sudo $*"
        return 0
    fi
    sudo "$@"
}

# Execute a command with sudo and a timeout
# Usage: run_sudo_timeout <seconds> <command...>
run_sudo_timeout() {
    local timeout_secs="${1:-300}"
    shift
    if [[ "${DRY_RUN:-0}" -eq 1 ]]; then
        echo -e "${_YELLOW:-}[DRY RUN]${_RESET:-} sudo (timeout ${timeout_secs}s) $*"
        return 0
    fi
    timeout --signal=TERM --kill-after=30 "$timeout_secs" sudo "$@"
}

# Rebuild all installed kernel images, propagating failures.
rebuild_initramfs() {
    source "$(dirname "${BASH_SOURCE[0]}")/kernel.sh"
    kernel_refresh_initramfs /etc /usr/bin/mkinitcpio /usr/bin/dracut
}

# Prompt for confirmation (respects --no-confirm)
confirm() {
    local prompt="${1:-Continue?}"
    if [[ "${NO_CONFIRM:-0}" -eq 1 ]]; then
        return 0
    fi
    read -rp "$prompt [y/N]: " answer
    [[ "${answer,,}" == "y" ]]
}

# Mark that a reboot is needed
mark_reboot_required() {
    REBOOT_REQUIRED=1
}

# Detect the script's own directory
detect_script_dir() {
    cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd
}

# Print a section header
section() {
    echo ""
    echo -e "${_BOLD}=== $* ===${_RESET}"
    echo ""
    return 0
}

# Check if a command exists
has_cmd() {
    command -v "$1" &>/dev/null
}

# The privileged helper parses literal values, records added tokens and performs
# the complete root-owned atomic replacement. Exit 3 means no rebuild is needed.
_update_grub_params() {
    local action="$1" params="$2" status=0
    if [[ -f /etc/default/grub ]]; then
        run_sudo python3 "$(detect_script_dir)/scripts/archer-grub.py" "$action" "$params" || status=$?
        [[ "$status" -eq 3 ]] && return 0
        [[ "$status" -eq 0 ]] || return 1
        run_sudo grub-mkconfig -o /boot/grub/grub.cfg || return 1
    elif [[ "$action" == add && -d /boot/loader/entries ]]; then
        log "systemd-boot: add these parameters to the options line in /boot/loader/entries/: $params"
    fi
}

add_grub_params() { _update_grub_params add "$1"; }
remove_grub_params() { _update_grub_params remove "$1"; }

check_grub_ownership() {
    # Repair historical exposure on any update, even without the touchpad module.
    if [[ -f /etc/default/grub ]]; then
        local status=0
        run_sudo python3 "$(detect_script_dir)/scripts/archer-grub.py" check || status=$?
        [[ "$status" -eq 0 || "$status" -eq 3 ]]
    fi
}

# Records survive interrupted installs and are replayed only for this module.
module_service() {
    INSTALLED_FILES+=" /var/lib/archer/module-state/$MODULE_ID.json"
    run_sudo python3 "$(detect_script_dir)/scripts/archer-install-state.py" "$MODULE_ID" service -- "$@" || return 1
}
module_restore_services() {
    run_sudo python3 "$(detect_script_dir)/scripts/archer-install-state.py" "$MODULE_ID" restore-services || return 1
}
module_config() {
    INSTALLED_FILES+=" $1 /var/lib/archer/module-state/$MODULE_ID.json"
    run_sudo python3 "$(detect_script_dir)/scripts/archer-install-state.py" "$MODULE_ID" config -- "$1" "$2" || return 1
}
module_remove_config() {
    local path
    for path in "$@"; do
        run_sudo python3 "$(detect_script_dir)/scripts/archer-install-state.py" "$MODULE_ID" remove-config "$path" || return 1
    done
}

# Cache denied reads as well as successful reads. Unknown is not healthy.
read_kernel_log() {
    if [[ "${KERNEL_LOG_AVAILABLE:-unknown}" == unknown ]]; then
        if _CACHED_DMESG=$(dmesg 2>/dev/null); then
            KERNEL_LOG_AVAILABLE=1
        else
            KERNEL_LOG_AVAILABLE=0
            _CACHED_DMESG=""
            warn "Kernel log unavailable to this user; log-based diagnostics are unknown. Run sudo dmesg for details."
        fi
    fi
}
