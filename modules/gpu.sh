#!/usr/bin/env bash
# Module: GPU Switching
# Installs EnvyControl for NVIDIA Optimus GPU mode management

source "$(dirname "${BASH_SOURCE[0]}")/../lib/kernel.sh"

MODULE_NAME="GPU Switching"
MODULE_ID="gpu"
MODULE_DESCRIPTION="EnvyControl for NVIDIA Optimus hybrid graphics switching"

module_detect() {
    # Relevant if NVIDIA dGPU + an integrated GPU
    if [[ "$HAS_NVIDIA" -eq 1 ]] && { [[ "$HAS_INTEL_IGPU" -eq 1 ]] || [[ "$HAS_AMD_IGPU" -eq 1 ]]; }; then
        return 0
    fi
    return 1
}

module_check_installed() {
    has_cmd envycontrol
}

module_install() {
    # Ensure NVIDIA driver is installed
    if ! modinfo nvidia &>/dev/null && [[ ! -d /sys/module/nvidia ]]; then
        log "NVIDIA driver not installed. Installing nvidia-dkms..."
        run_sudo pacman -S --needed --noconfirm nvidia-dkms nvidia-utils || return 1
        INSTALLED_PACKAGES+=" nvidia-dkms nvidia-utils"
    fi

    if [[ "${DRY_RUN:-0}" -eq 0 ]]; then
        kernel_collect_targets || return 1
        local kernel
        for kernel in "${KERNEL_TARGETS[@]}"; do
            # Catch pacman hooks that warned but let the package transaction succeed.
            if ! run_sudo dkms autoinstall -k "$kernel"; then
                warn "DKMS rebuild failed for $kernel; inspect /var/lib/dkms/*/*/build/make.log before changing GPU mode."
                return 1
            fi
            if ! modinfo -k "$kernel" nvidia &>/dev/null; then
                warn "No NVIDIA module for $kernel. Install the distro's NVIDIA package matching your GPU and kernel."
                return 1
            fi
        done
    fi

    # Install EnvyControl
    if [[ -n "$AUR_HELPER" ]]; then
        log "Installing EnvyControl via $AUR_HELPER..."
        run $AUR_HELPER -S --needed --noconfirm envycontrol
    else
        log "No AUR helper found. Installing EnvyControl via pip..."
        run pip install envycontrol --break-system-packages 2>/dev/null || warn "pip install encountered issues."
    fi

    # GPU mode selection
    log "GPU Switching Modes:"
    log "  1) hybrid      - iGPU by default, NVIDIA on demand (recommended)"
    log "  2) nvidia       - Always use NVIDIA GPU (best performance)"
    log "  3) integrated   - Disable NVIDIA entirely (best battery life)"

    local gpu_mode="hybrid"
    if [[ "$NO_CONFIRM" -eq 0 ]]; then
        read -rp "Select mode [1]: " gpu_choice
        case "${gpu_choice:-1}" in
            2) gpu_mode="nvidia" ;;
            3) gpu_mode="integrated" ;;
            *) gpu_mode="hybrid" ;;
        esac
    fi

    log "Setting GPU mode to: $gpu_mode"
    # Resolve the executable before limiting PATH to distro tools. This bypasses
    # interactive /usr/local wrappers without replacing a system executable.
    local envycontrol_path
    envycontrol_path=$(command -v envycontrol) || return 1
    if [[ "$gpu_mode" = "hybrid" ]]; then
        run_sudo env PATH=/usr/bin:/bin "$envycontrol_path" -s hybrid --rtd3 2 || return 1
    else
        run_sudo env PATH=/usr/bin:/bin "$envycontrol_path" -s "$gpu_mode" || return 1
    fi

    # Rebuild every installed kernel, including LTS and vendor variants.
    rebuild_initramfs || return 1

    INSTALLED_PACKAGES+=" envycontrol"
    mark_reboot_required
    log "GPU mode set to '$gpu_mode'. A reboot is required to apply changes."
}

module_uninstall() {
    log "Resetting GPU configuration..."
    if has_cmd envycontrol; then
        local envycontrol_path
        envycontrol_path=$(command -v envycontrol) || return 1
        run_sudo env PATH=/usr/bin:/bin "$envycontrol_path" --reset || return 1
        rebuild_initramfs || return 1
    fi
    log "EnvyControl package retained (remove manually if desired)."
}

module_verify() {
    if has_cmd envycontrol; then
        local mode
        mode=$(envycontrol --query 2>/dev/null || echo "unknown")
        log "Current GPU mode: $mode"
        return 0
    fi
    warn "EnvyControl not found"
    return 1
}
