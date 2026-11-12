#!/usr/bin/env bash
# Module: Game Mode Support
# Installs Feral Interactive's GameMode for performance optimization

MODULE_NAME="Game Mode Support"
MODULE_ID="gamemode"
MODULE_DESCRIPTION="Linux Game Mode with performance governor switching"

module_detect() {
    case "$MODEL_FAMILY" in
        nitro|predator|helios|triton) return 0 ;;
        *) return 1 ;;
    esac
}

module_check_installed() {
    has_cmd gamemoded
}

module_install() {
    log "Installing native GameMode..."
    run_sudo pacman -S --needed --noconfirm gamemode || return 1
    INSTALLED_PACKAGES+=" gamemode"
    if pacman -Si lib32-gamemode >/dev/null 2>&1; then
        run_sudo pacman -S --needed --noconfirm lib32-gamemode || return 1
        INSTALLED_PACKAGES+=" lib32-gamemode"
    else
        log "32-bit GameMode is optional; enable multilib if your games require it."
    fi
    module_config /etc/gamemode.ini '# Installed by Archer Compatibility Suite
[general]
reaper_freq=5
desiredgov=performance
softrealtime=auto
renice=10
ioprio=0' || return 1
    if [[ -f /etc/gamemode.d/archer.ini ]] && declare -F read_manifest_field >/dev/null &&
       [[ " $(read_manifest_field files_created) " == *" /etc/gamemode.d/archer.ini "* ]]; then
        run_sudo rm -f /etc/gamemode.d/archer.ini || return 1
    fi
    log "GameMode installed. Use gamemoderun to launch a game."
}

module_uninstall() {
    module_remove_config /etc/gamemode.ini || return 1
    if [[ -f /etc/gamemode.d/archer.ini ]] && declare -F read_manifest_field >/dev/null &&
       [[ " $(read_manifest_field files_created) " == *" /etc/gamemode.d/archer.ini "* ]]; then
        run_sudo rm -f /etc/gamemode.d/archer.ini || return 1
    fi
}

module_verify() {
    has_cmd gamemoded && [[ -f /etc/gamemode.ini ]] || return 1
    grep -q '^desiredgov=performance$' /etc/gamemode.ini || return 1
}
