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
    log "Installing GameMode..."
    run_sudo pacman -S --needed --noconfirm gamemode lib32-gamemode

    log "Creating GameMode configuration..."
    run_sudo mkdir -p /etc/gamemode.d
    run_sudo tee /etc/gamemode.d/archer.ini > /dev/null <<'GAMEMODE_EOF'
[general]
reaper_freq=5
desiredgov=performance
softrealtime=auto
renice=10
ioprio=0

[gpu]
apply_gpu_optimisations=accept-responsibility
nv_powermizer_mode=1

[custom]
start=notify-send "Game Mode" "Performance mode activated"
end=notify-send "Game Mode" "Performance mode deactivated"
GAMEMODE_EOF

    log "GameMode installed. Use 'gamemoderun <command>' to launch games."

    INSTALLED_FILES+=" /etc/gamemode.d/archer.ini"
    INSTALLED_PACKAGES+=" gamemode lib32-gamemode"
}

module_uninstall() {
    log "Removing GameMode configuration..."
    run_sudo rm -f /etc/gamemode.d/archer.ini
    run_sudo rmdir /etc/gamemode.d 2>/dev/null || true
}

module_verify() {
    if has_cmd gamemoded; then
        return 0
    fi
    warn "gamemoded not found"
    return 1
}
