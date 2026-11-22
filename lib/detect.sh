#!/usr/bin/env bash
# Hardware detection and recommendation engine (Bash)

source "$(dirname "${BASH_SOURCE[0]}")/kernel.sh"

# --- DMI Information ---
ACER_PRODUCT_NAME=""
ACER_BOARD_NAME=""
ACER_BIOS_VERSION=""
ACER_SYS_VENDOR=""
MODEL_FAMILY="unknown"

# --- GPU ---
HAS_NVIDIA=0
HAS_AMD_DGPU=0
HAS_INTEL_GPU=0
HAS_INTEL_IGPU=0
HAS_AMD_GPU=0
HAS_AMD_IGPU=0
NVIDIA_MODEL=""

# --- WiFi ---
WIFI_DEVICE=""
WIFI_CHIPSET="unknown"

# --- Audio ---
AUDIO_CODEC=""
SOF_ACTIVE=0
CPU_VENDOR=""

# --- Touchpad ---
HAS_I2C_TOUCHPAD=0
TOUCHPAD_ERRORS=0

# --- Battery ---
HAS_BATTERY=0
BATTERY_WMI_LOADED=0

# --- Kernel ---
KERNEL_VERSION=""
KERNEL_MAJOR=0
KERNEL_MINOR=0
SUPPORTS_THERMAL_PROFILES=0

# --- Distro ---
DISTRO_ID=""
DISTRO_NAME=""
DISTRO_FAMILY="unknown"
AUR_HELPER=""

# --- CachyOS / Clang ---
IS_CACHYOS=0
IS_CLANG_KERNEL=0
CLANG_BUILD_FLAGS=""
KERNEL_HEADERS="linux-headers"

# --- Recommendations ---
RECOMMENDED_MODULES=()
OPTIONAL_MODULES=()

detect_model_family() {
    case "$ACER_PRODUCT_NAME" in
        *Nitro*|ANV[0-9]*|AN[0-9]*)      MODEL_FAMILY="nitro" ;;
        *Predator*)   MODEL_FAMILY="predator" ;;
        *Helios*|PHN[0-9]*|PH[0-9]*)     MODEL_FAMILY="helios" ;;
        *Triton*|PT[0-9]*)     MODEL_FAMILY="triton" ;;
        *Swift*)      MODEL_FAMILY="swift" ;;
        *Aspire*)     MODEL_FAMILY="aspire" ;;
        *Spin*)       MODEL_FAMILY="spin" ;;
        *Enduro*)     MODEL_FAMILY="enduro" ;;
        *Extensa*) MODEL_FAMILY="extensa" ;;
        *ConceptD*) MODEL_FAMILY="conceptd" ;;
        *TravelMate*) MODEL_FAMILY="travelmate" ;;
        *)            MODEL_FAMILY="unknown" ;;
    esac
}

detect_audio() {
    AUDIO_CODEC=""
    if [[ -d /proc/asound ]]; then
        AUDIO_CODEC=$(cat /proc/asound/card*/codec* 2>/dev/null | grep "Codec:" | head -1 | sed 's/.*Codec: //' || echo "")
    fi
    SOF_ACTIVE=0
    read_kernel_log
    local dmesg_out="${_CACHED_DMESG:-}"
    if echo "$dmesg_out" | grep -qi "sof.*firmware\|sof-audio"; then
        SOF_ACTIVE=1
    fi
    CPU_VENDOR=$(grep -m1 "vendor_id" /proc/cpuinfo 2>/dev/null | awk '{print $3}' || echo "")
}

detect_kernel() {
    KERNEL_VERSION=$(uname -r)
    if [[ "$KERNEL_VERSION" =~ ^([0-9]+)\.([0-9]+) ]]; then
        KERNEL_MAJOR=$((10#${BASH_REMATCH[1]}))
        KERNEL_MINOR=$((10#${BASH_REMATCH[2]}))
    else
        warn "Cannot parse kernel release: $KERNEL_VERSION"
        return 1
    fi
    SUPPORTS_THERMAL_PROFILES=0
    if kernel_profile_path "$KERNEL_SYS_ROOT" >/dev/null; then SUPPORTS_THERMAL_PROFILES=1; fi
    IS_CACHYOS=0
    [[ "$KERNEL_VERSION" == *cachyos* ]] && IS_CACHYOS=1
    KERNEL_HEADERS=$(kernel_header_package "$KERNEL_VERSION") || KERNEL_HEADERS=""
    IS_CLANG_KERNEL=0
    CLANG_BUILD_FLAGS=""
    if grep -qi clang "$KERNEL_PROC_VERSION" 2>/dev/null; then
        IS_CLANG_KERNEL=1
        # Informational only. DKMS must select tools from each target's config.
        CLANG_BUILD_FLAGS="LLVM=1 CC=clang"
    fi
}

# Read data as fields. Firmware and os-release text must never become shell code.
detect_all() {
    local values=() inventory
    inventory="$(dirname "${BASH_SOURCE[0]}")/../gui/archer_inventory.py"
    mapfile -d '' -t values < <(python3 "$inventory" installer)
    if (( ${#values[@]} != 15 )); then
        warn "Could not read hardware inventory. Check Python and the sysfs mount."
        return 1
    fi
    ACER_PRODUCT_NAME=${values[0]}
    ACER_BOARD_NAME=${values[1]}
    ACER_BIOS_VERSION=${values[2]}
    ACER_SYS_VENDOR=${values[3]}
    HAS_NVIDIA=${values[4]}
    NVIDIA_MODEL=${values[5]}
    HAS_INTEL_GPU=${values[6]}
    HAS_AMD_GPU=${values[7]}
    WIFI_DEVICE=${values[8]}
    WIFI_CHIPSET=${values[9]:-unknown}
    HAS_BATTERY=${values[10]}
    HAS_I2C_INPUT=${values[11]}
    DISTRO_ID=${values[12]}
    DISTRO_NAME=${values[13]}
    DISTRO_FAMILY=${values[14]}
    BATTERY_WMI_LOADED=0
    [[ ! -d /sys/module/acer_wmi_battery ]] || BATTERY_WMI_LOADED=1
    detect_model_family
    detect_audio
    detect_kernel
    if command -v paru &>/dev/null; then
        AUR_HELPER=paru
    elif command -v yay &>/dev/null; then
        AUR_HELPER=yay
    fi
}

# Print a summary of detected hardware
print_hw_summary() {
    local gpu_info=""
    if [[ "$HAS_NVIDIA" -eq 1 ]]; then
        gpu_info="$NVIDIA_MODEL"
    fi
    if [[ "$HAS_INTEL_GPU" -eq 1 ]]; then
        [[ -n "$gpu_info" ]] && gpu_info="$gpu_info + "
        gpu_info="${gpu_info}Intel GPU"
    fi
    if [[ "$HAS_AMD_GPU" -eq 1 ]]; then
        [[ -n "$gpu_info" ]] && gpu_info="$gpu_info + "
        gpu_info="${gpu_info}AMD GPU"
    fi
    if [[ "$HAS_AMD_DGPU" -eq 1 ]]; then
        [[ -n "$gpu_info" ]] && gpu_info="$gpu_info + "
        gpu_info="${gpu_info}AMD dGPU"
    fi
    [[ -z "$gpu_info" ]] && gpu_info="Unknown"

    local wifi_info="$WIFI_CHIPSET"
    [[ -n "$WIFI_DEVICE" ]] && wifi_info="$WIFI_DEVICE"

    local kernel_extra=""
    if [[ "$IS_CLANG_KERNEL" -eq 1 ]]; then
        kernel_extra=" [Clang]"
    fi

    echo -e "${_BOLD}Detected:${_RESET} $ACER_PRODUCT_NAME ($ACER_SYS_VENDOR)"
    echo -e "${_BOLD}Kernel:${_RESET}   ${KERNEL_VERSION}${kernel_extra} | ${_BOLD}GPU:${_RESET} $gpu_info"
    echo -e "${_BOLD}WiFi:${_RESET}     $wifi_info"
    echo -e "${_BOLD}Battery:${_RESET}  $([[ "$HAS_BATTERY" -eq 1 ]] && echo "Present" || echo "Not detected")"
    echo -e "${_BOLD}Distro:${_RESET}   ${DISTRO_NAME:-Unknown} ($DISTRO_FAMILY)$([[ -n "$AUR_HELPER" ]] && echo " | AUR: $AUR_HELPER")"
}

# Build recommended and optional module lists based on hardware
build_recommendations() {
    RECOMMENDED_MODULES=()
    OPTIONAL_MODULES=(driver touchpad audio wifi power gamemode audio-enhance camera-enhance firmware)
    if (( HAS_BATTERY )); then OPTIONAL_MODULES+=(battery); fi
    if (( HAS_NVIDIA )); then OPTIONAL_MODULES+=(gpu); fi
    if (( SUPPORTS_THERMAL_PROFILES )); then OPTIONAL_MODULES+=(thermal); fi
    if [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]; then
        RECOMMENDED_MODULES+=(gui)
    else
        OPTIONAL_MODULES+=(gui)
    fi
}
