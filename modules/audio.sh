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
    [[ -f "$_AUDIO_AMD_CONF" ]] || pacman -Qi sof-firmware &>/dev/null
}

module_install() {
    # Install essential audio firmware and tools
    log "Installing audio firmware and utilities..."
    run_sudo pacman -S --needed --noconfirm sof-firmware alsa-ucm-conf alsa-utils || return 1

    INSTALLED_PACKAGES+=" sof-firmware alsa-ucm-conf alsa-utils"
    local rebuild_initramfs=0

    if [[ "$CPU_VENDOR" = "AuthenticAMD" ]]; then
        log "Using kernel-selected AMD audio drivers; no blanket ACP override is applied."
        if [[ -f "$_AUDIO_AMD_CONF" ]] && grep -q 'Installed by Archer Compatibility Suite' "$_AUDIO_AMD_CONF"; then
            module_remove_config "$_AUDIO_AMD_CONF" || return 1
            rebuild_initramfs=1
        fi
    else
        read_kernel_log
        if [[ "${KERNEL_LOG_AVAILABLE:-0}" == 1 ]]; then
            if echo "$_CACHED_DMESG" | grep -qi 'sof.*firmware.*missing\|sof.*fw.*not found'; then
                warn "SOF firmware may not be loaded in initramfs."
                rebuild_initramfs=1
            fi
        fi
    fi
    if [[ "$rebuild_initramfs" -eq 1 ]]; then
        rebuild_initramfs || return 1
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

    mark_reboot_required
}

module_uninstall() {
    log "Removing audio configuration..."
    if [[ -f "$_AUDIO_AMD_CONF" ]] && grep -q "Installed by Archer Compatibility Suite" "$_AUDIO_AMD_CONF"; then
        module_remove_config "$_AUDIO_AMD_CONF" || return 1
        rebuild_initramfs || return 1
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
