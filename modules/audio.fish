#!/usr/bin/env fish
# Module: Audio Fix
# Fixes microphone and speaker detection issues

set -g MODULE_NAME "Audio Fix"
set -g MODULE_ID "audio"
set -g MODULE_DESCRIPTION "SOF firmware, ALSA configuration, and audio codec fixes"

set -g _AUDIO_AMD_CONF "/etc/modprobe.d/acer-audio-amd.conf"

function module_detect
    return 1
end

function module_check_installed
    test -f "$_AUDIO_AMD_CONF"; or pacman -Qi sof-firmware >/dev/null 2>&1
end

function module_install
    log "Installing audio firmware and utilities..."
    run_sudo pacman -S --needed --noconfirm sof-firmware alsa-ucm-conf alsa-utils

    set -l rebuild_initramfs 0

    if test "$CPU_VENDOR" = "AuthenticAMD"
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
        set -ga INSTALLED_FILES $_AUDIO_AMD_CONF
        set rebuild_initramfs 1
    else
        log "Intel platform detected. Checking SOF driver status..."
        if dmesg 2>/dev/null | grep -qi "sof.*firmware.*missing|sof.*fw.*not found"
            warn "SOF firmware may not be loaded in initramfs."
            set rebuild_initramfs 1
        else
            log "SOF firmware appears to be loading correctly."
        end
    end

    if test $rebuild_initramfs -eq 1
        rebuild_initramfs
    end

    echo ""
    log "Audio system status:"
    if has_cmd wpctl
        log "  PipeWire/WirePlumber detected"
        wpctl status 2>/dev/null | head -20; or true
    else if has_cmd pactl
        log "  PulseAudio detected"
        pactl list sources short 2>/dev/null; or true
    end

    echo ""
    log "If microphone issues persist after reboot:"
    log "  1. Run: alsamixer (press F6 to select card, F4 for capture, unmute channels)"
    log "  2. Run: pavucontrol (set profile to 'Analog Duplex')"
    log "  3. Check: wpctl status (for PipeWire users)"

    set -ga INSTALLED_PACKAGES sof-firmware alsa-ucm-conf alsa-utils
    mark_reboot_required
end

function module_uninstall
    log "Removing audio configuration..."
    if test -f "$_AUDIO_AMD_CONF"
        sudo rm -f "$_AUDIO_AMD_CONF"
        rebuild_initramfs
    end
    log "Audio packages retained as they are standard system components."
end

function module_verify
    if arecord -l 2>/dev/null | grep -q "card"
        return 0
    end
    if aplay -l 2>/dev/null | grep -q "card"
        warn "Playback devices found but no capture devices (mic may need reboot)"
        return 0
    end
    warn "No audio devices detected"
    return 1
end
