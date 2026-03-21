#!/usr/bin/env bash
# Module: Audio Fix
# Fixes microphone and speaker detection issues

MODULE_NAME="Audio Fix"
MODULE_ID="audio"
MODULE_DESCRIPTION="SOF firmware, ALSA configuration, and audio codec fixes"

_AUDIO_AMD_CONF="/etc/modprobe.d/acer-audio-amd.conf"

module_detect() {
    # Always optional - user knows if they have audio issues
    return 1
}

module_check_installed() {
    [ -f "$_AUDIO_AMD_CONF" ] || pacman -Qi sof-firmware &>/dev/null
}

module_install() {
    # Install essential audio firmware and tools
    log "Installing audio firmware and utilities..."
    run_sudo pacman -S --needed --noconfirm sof-firmware alsa-ucm-conf alsa-utils

    local rebuild_initramfs=0

    if [ "$CPU_VENDOR" = "AuthenticAMD" ]; then
        log "AMD platform detected. Configuring SOF audio driver..."
        run_sudo tee "$_AUDIO_AMD_CONF" > /dev/null <<'EOF'
# AMD audio configuration for Acer laptops
# Installed by Archer Compatibility Suite

# Enable SOF driver for AMD ACP
options snd_pci_acp3x enable=1
# Disable legacy ACP PDM drivers to avoid conflicts
options snd_acp3x_pdm_dma enable=0
options snd_acp6x_pdm_dma enable=0
EOF
        INSTALLED_FILES+=" $_AUDIO_AMD_CONF"
        rebuild_initramfs=1
    else
        log "Intel platform detected. Checking SOF driver status..."
        if dmesg 2>/dev/null | grep -qi "sof.*firmware.*missing\|sof.*fw.*not found"; then
            warn "SOF firmware may not be loaded in initramfs."
            rebuild_initramfs=1
        else
            log "SOF firmware appears to be loading correctly."
        fi
    fi

    if [ "$rebuild_initramfs" -eq 1 ]; then
        rebuild_initramfs
    fi

    # Display current audio status
    echo ""
    log "Audio system status:"
    if has_cmd wpctl; then
        log "  PipeWire/WirePlumber detected"
        wpctl status 2>/dev/null | head -20 || true
    elif has_cmd pactl; then
        log "  PulseAudio detected"
        pactl list sources short 2>/dev/null || true
    fi

    echo ""
    log "If microphone issues persist after reboot:"
    log "  1. Run: alsamixer (press F6 to select card, F4 for capture, unmute channels)"
    log "  2. Run: pavucontrol (set profile to 'Analog Duplex')"
    log "  3. Check: wpctl status (for PipeWire users)"

    INSTALLED_PACKAGES+=" sof-firmware alsa-ucm-conf alsa-utils"
    mark_reboot_required
}

module_uninstall() {
    log "Removing audio configuration..."
    if [ -f "$_AUDIO_AMD_CONF" ]; then
        sudo rm -f "$_AUDIO_AMD_CONF"
        rebuild_initramfs
    fi
    log "Audio packages retained as they are standard system components."
}

module_verify() {
    # Check for audio playback devices
    if arecord -l 2>/dev/null | grep -q "card"; then
        return 0
    fi
    # Check if at least playback works
    if aplay -l 2>/dev/null | grep -q "card"; then
        warn "Playback devices found but no capture devices (mic may need reboot)"
        return 0
    fi
    warn "No audio devices detected"
    return 1
}
