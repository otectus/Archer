#!/usr/bin/env bash
# Module: Audio Enhancement (Noise Suppression)
# Sets up PipeWire noise suppression via rnnoise

MODULE_NAME="Audio Enhancement (Noise Suppression)"
MODULE_ID="audio-enhance"
MODULE_DESCRIPTION="Noise suppression via PipeWire/rnnoise"

_FILTER_CONF="/etc/pipewire/pipewire.conf.d/archer-noise-suppress.conf"

module_detect() {
    pacman -Qi pipewire &>/dev/null
}

module_check_installed() {
    [[ -f "$_FILTER_CONF" || -f "$_FILTER_CONF.disabled" ]]
}

module_install() {
    log "Installing audio enhancement dependencies..."
    run_sudo pacman -S --needed --noconfirm pipewire wireplumber || return 1

    INSTALLED_PACKAGES+=" pipewire wireplumber"
    if ! pacman -Q noise-suppression-for-voice >/dev/null 2>&1; then
        if pacman -Si noise-suppression-for-voice >/dev/null 2>&1; then
            run_sudo pacman -S --needed --noconfirm noise-suppression-for-voice || return 1
        elif has_cmd paru; then
            run paru -S --needed --noconfirm noise-suppression-for-voice || return 1
        elif has_cmd yay; then
            run yay -S --needed --noconfirm noise-suppression-for-voice || return 1
        else
            warn "Install the noise-suppression-for-voice system package, then retry."
            return 1
        fi
    fi
    INSTALLED_PACKAGES+=" noise-suppression-for-voice"
    if [[ "${DRY_RUN:-0}" -eq 0 && ! -f /usr/lib/ladspa/librnnoise_ladspa.so ]]; then
        warn "rnnoise LADSPA plugin is missing; audio enhancement was not configured."
        return 1
    fi
    local old=/etc/pipewire/filter-chain.conf.d/archer-noise-suppress.conf disabled="" config
    [[ -f "$old.disabled" || -f "$_FILTER_CONF.disabled" ]] && disabled=.disabled
    log "Creating PipeWire noise suppression filter..."
    run_sudo mkdir -p "$(dirname "$_FILTER_CONF")" || return 1
    config=$(cat <<'FILTER_EOF'
# Installed by Archer Compatibility Suite
context.modules = [
    { name = libpipewire-module-filter-chain
        args = {
            node.description = "Archer Noise Suppression"
            media.name = "Archer Noise Suppression"
            filter.graph = {
                nodes = [
                    {
                        type = ladspa
                        name = rnnoise
                        plugin = librnnoise_ladspa
                        label = noise_suppressor_mono
                        control = { "VAD Threshold (%)" = 50.0 }
                    }
                ]
            }
            capture.props = {
                node.name = "archer_noise_capture"
                node.passive = true
                audio.rate = 48000
            }
            playback.props = {
                node.name = "archer_noise_suppressed"
                media.class = "Audio/Source"
                audio.rate = 48000
            }
        }
    }
]
FILTER_EOF
)
    module_config "$_FILTER_CONF$disabled" "$config" || return 1
    if [[ -n "$disabled" ]]; then
        [[ ! -f "$_FILTER_CONF" ]] || module_remove_config "$_FILTER_CONF" || return 1
    fi
    for old in "$old" "$old.disabled"; do
        if [[ -f "$old" ]] && grep -q 'archer_noise_capture' "$old"; then
            module_remove_config "$old" || return 1
        fi
    done

    log "Audio enhancement installed."
    log "Select 'Archer Noise Suppression' as your mic in application settings."
    log "Restart PipeWire to activate: systemctl --user restart pipewire.service"

    INSTALLED_FILES+=" $_FILTER_CONF"
    INSTALLED_PACKAGES+=" pipewire wireplumber"
}

module_uninstall() {
    local path
    for path in "$_FILTER_CONF" "$_FILTER_CONF.disabled" /etc/pipewire/filter-chain.conf.d/archer-noise-suppress.conf /etc/pipewire/filter-chain.conf.d/archer-noise-suppress.conf.disabled; do
        if [[ -f "$path" ]] && grep -q 'archer_noise_capture' "$path"; then
            module_remove_config "$path" || return 1
        fi
    done
    log "Filter config removed. Restart PipeWire to deactivate."
}

module_verify() {
    [[ -f "$_FILTER_CONF" || -f "$_FILTER_CONF.disabled" ]] && [[ -f /usr/lib/ladspa/librnnoise_ladspa.so ]] && return 0
    warn "Noise suppression config not found at $_FILTER_CONF"
    return 1
}

module_preflight() {
    if ! pacman -Q noise-suppression-for-voice >/dev/null 2>&1 && ! pacman -Si noise-suppression-for-voice >/dev/null 2>&1 && ! has_cmd paru && ! has_cmd yay; then
        warn "Audio enhancement requires the system noise-suppression-for-voice package or an AUR helper."
        return 1
    fi
}
