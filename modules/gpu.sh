#!/usr/bin/env bash
# Module: GPU Switching
# Installs EnvyControl for NVIDIA Optimus GPU mode management

source "$(dirname "${BASH_SOURCE[0]}")/../lib/kernel.sh"

MODULE_NAME="GPU Switching"
MODULE_ID="gpu"
MODULE_DESCRIPTION="EnvyControl for NVIDIA Optimus hybrid graphics switching"
_GPU_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
_GPU_SYSTEM_HELPER="$_GPU_ROOT/gui/archer_system.py"

_gpu_plan() {
    /usr/bin/python3 "$_GPU_SYSTEM_HELPER" graphics-plan "${1:-hybrid}"
}

_gpu_plan_field() {
    local field=$1
    /usr/bin/python3 -c 'import json,sys; value=json.load(sys.stdin); value=value.get("data", value).get(sys.argv[1]); print(json.dumps(value) if isinstance(value, (list,dict)) else str(value).lower() if isinstance(value,bool) else value or "")' "$field"
}

module_detect() {
    # Relevant if NVIDIA dGPU + an integrated GPU
    if [[ "$HAS_NVIDIA" -eq 1 ]] && { [[ "$HAS_INTEL_GPU" -eq 1 ]] || [[ "$HAS_AMD_GPU" -eq 1 ]]; }; then
        return 0
    fi
    return 1
}

module_check_installed() {
    has_cmd envycontrol
}

module_install() {
    local plan owner available packages package reason
    plan=$(_gpu_plan hybrid) || return 1
    available=$(printf '%s' "$plan" | _gpu_plan_field available) || return 1
    owner=$(printf '%s' "$plan" | _gpu_plan_field owner) || return 1
    if [[ "$owner" == mhwd || "$owner" == chwd ]]; then
        warn "$owner owns this graphics stack. Use its reviewed graphics profile; Archer will not bypass it."
        return 1
    fi
    reason=$(printf '%s' "$plan" | _gpu_plan_field reason_code) || return 1
    if [[ "$available" != true && "$reason" != mode_backend_unavailable ]]; then
        warn "No safe graphics plan is available for the inventoried NVIDIA device and configured repositories."
        return 1
    fi
    packages=$(printf '%s' "$plan" | _gpu_plan_field packages) || return 1
    while read -r package; do
        [[ -n "$package" ]] || continue
        run_sudo pacman -S --needed --noconfirm "$package" || return 1
        INSTALLED_PACKAGES+=" $package"
    done < <(/usr/bin/python3 -c 'import json,sys; print("\n".join(json.loads(sys.stdin.read())))' <<<"$packages")

    if [[ "${DRY_RUN:-0}" -eq 0 ]]; then
        kernel_collect_targets "$KERNEL_MODULES_ROOT" || return 1
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
        /usr/bin/python3 "$_GPU_SYSTEM_HELPER" graphics-verify || return 1
    fi

    # A root daemon requires a system-managed executable, never a user pip install.
    if ! pacman -Q envycontrol >/dev/null 2>&1; then
        if pacman -Si envycontrol >/dev/null 2>&1; then
            run_sudo pacman -S --needed --noconfirm envycontrol || return 1
        elif [[ -n "$AUR_HELPER" ]]; then
            run "$AUR_HELPER" -S --needed --noconfirm envycontrol || return 1
        else
            warn "Install the EnvyControl system package using an AUR helper, then retry."
            return 1
        fi
    fi
    INSTALLED_PACKAGES+=" envycontrol"

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

    plan=$(_gpu_plan "$gpu_mode") || return 1
    if [[ "$(printf '%s' "$plan" | _gpu_plan_field available)" != true && "${DRY_RUN:-0}" -eq 0 ]]; then
        warn "The current graphics plan no longer permits this change."
        return 1
    fi
    if [[ "$gpu_mode" == integrated ]]; then
        log "Connected outputs and required checks: $(printf '%s' "$plan" | _gpu_plan_field preflight)"
        read -rp "Integrated mode can disconnect outputs wired to NVIDIA. Apply? [y/N] " gpu_ack
        [[ "$gpu_ack" == [yY] ]] || return 1
    fi
    log "Setting GPU mode to: $gpu_mode"
    # Installer and GUI use the same fixed-argument executor and boot adapter.
    run_sudo /usr/bin/python3 "$_GPU_SYSTEM_HELPER" display "$gpu_mode" || return 1

    INSTALLED_PACKAGES+=" envycontrol"
    mark_reboot_required
    log "GPU mode set to '$gpu_mode'. A reboot is required to apply changes."
}

module_uninstall() {
    log "Restoring Archer-owned GPU configuration..."
    # EnvyControl's broad reset action removes every path in its cleanup list, including
    # administrator and package-owned files. The system helper restores only
    # journaled entries that Archer changed and that still match its last result.
    run_sudo /usr/bin/python3 "$_GPU_SYSTEM_HELPER" display-restore-if-owned || return 1
    log "EnvyControl package retained (remove manually if desired)."
}

module_verify() {
    if [[ -x /usr/bin/envycontrol ]]; then
        local mode
        mode=$(/usr/bin/envycontrol --query) || return 1
        case "$mode" in hybrid|integrated|nvidia) ;; *) warn "Unrecognized GPU mode: $mode"; return 1 ;; esac
        log "Current GPU mode: $mode"
        return 0
    fi
    warn "EnvyControl not found"
    return 1
}

module_preflight() {
    if [[ -z "$AUR_HELPER" ]] && ! pacman -Q envycontrol >/dev/null 2>&1 && ! pacman -Si envycontrol >/dev/null 2>&1; then
        warn "GPU setup requires the system EnvyControl package or an AUR helper."
        return 1
    fi
    local plan available reason
    plan=$(_gpu_plan hybrid) || return 1
    available=$(printf '%s' "$plan" | _gpu_plan_field available) || return 1
    reason=$(printf '%s' "$plan" | _gpu_plan_field reason_code) || return 1
    if [[ "$available" != true && "$reason" != mode_backend_unavailable ]]; then
        warn "Graphics resolver blocked the plan: ${reason:-unknown_reason}."
        return 1
    fi
}
