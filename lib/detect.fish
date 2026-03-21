#!/usr/bin/env fish
# Hardware detection and recommendation engine (Fish)

# --- DMI Information ---
set -g ACER_PRODUCT_NAME ""
set -g ACER_BOARD_NAME ""
set -g ACER_BIOS_VERSION ""
set -g ACER_SYS_VENDOR ""
set -g MODEL_FAMILY "unknown"

# --- GPU ---
set -g HAS_NVIDIA 0
set -g HAS_AMD_DGPU 0
set -g HAS_INTEL_IGPU 0
set -g HAS_AMD_IGPU 0
set -g NVIDIA_MODEL ""

# --- WiFi ---
set -g WIFI_DEVICE ""
set -g WIFI_CHIPSET "unknown"

# --- Audio ---
set -g AUDIO_CODEC ""
set -g SOF_ACTIVE 0
set -g CPU_VENDOR ""

# --- Touchpad ---
set -g HAS_I2C_TOUCHPAD 0
set -g TOUCHPAD_ERRORS 0

# --- Battery ---
set -g HAS_BATTERY 0
set -g BATTERY_WMI_LOADED 0

# --- Kernel ---
set -g KERNEL_VERSION ""
set -g KERNEL_MAJOR 0
set -g KERNEL_MINOR 0
set -g SUPPORTS_THERMAL_PROFILES 0

# --- Distro ---
set -g DISTRO_ID ""
set -g DISTRO_NAME ""
set -g DISTRO_FAMILY "unknown"
set -g AUR_HELPER ""

# --- CachyOS / Clang ---
set -g IS_CACHYOS 0
set -g IS_CLANG_KERNEL 0
set -g CLANG_BUILD_FLAGS ""
set -g KERNEL_HEADERS "linux-headers"

# --- Recommendations ---
set -g RECOMMENDED_MODULES
set -g OPTIONAL_MODULES

function detect_dmi
    set -g ACER_PRODUCT_NAME (cat /sys/devices/virtual/dmi/id/product_name 2>/dev/null; or echo "Unknown")
    set -g ACER_BOARD_NAME (cat /sys/devices/virtual/dmi/id/board_name 2>/dev/null; or echo "Unknown")
    set -g ACER_BIOS_VERSION (cat /sys/devices/virtual/dmi/id/bios_version 2>/dev/null; or echo "Unknown")
    set -g ACER_SYS_VENDOR (cat /sys/devices/virtual/dmi/id/sys_vendor 2>/dev/null; or echo "Unknown")
end

function detect_model_family
    if string match -q '*Nitro*' "$ACER_PRODUCT_NAME"
        set -g MODEL_FAMILY "nitro"
    else if string match -q '*Predator*' "$ACER_PRODUCT_NAME"
        set -g MODEL_FAMILY "predator"
    else if string match -q '*Helios*' "$ACER_PRODUCT_NAME"
        set -g MODEL_FAMILY "helios"
    else if string match -q '*Triton*' "$ACER_PRODUCT_NAME"
        set -g MODEL_FAMILY "triton"
    else if string match -q '*Swift*' "$ACER_PRODUCT_NAME"
        set -g MODEL_FAMILY "swift"
    else if string match -q '*Aspire*' "$ACER_PRODUCT_NAME"
        set -g MODEL_FAMILY "aspire"
    else if string match -q '*Spin*' "$ACER_PRODUCT_NAME"
        set -g MODEL_FAMILY "spin"
    else if string match -q '*Enduro*' "$ACER_PRODUCT_NAME"
        set -g MODEL_FAMILY "enduro"
    else if string match -q '*TravelMate*' "$ACER_PRODUCT_NAME"
        set -g MODEL_FAMILY "travelmate"
    else
        set -g MODEL_FAMILY "unknown"
    end
end

function detect_gpu
    set -l lspci_out "$_CACHED_LSPCI"
    test -z "$lspci_out"; and set lspci_out (lspci 2>/dev/null; or echo "")

    if echo "$lspci_out" | grep -qi "nvidia"
        set -g HAS_NVIDIA 1
        set -g NVIDIA_MODEL (echo "$lspci_out" | grep -i "nvidia" | grep -iE "vga|3d" | head -1 | sed 's/.*: //')
    end
    if echo "$lspci_out" | grep -qiE "AMD.*(Navi|RDNA|Radeon RX)"
        set -g HAS_AMD_DGPU 1
    end
    if echo "$lspci_out" | grep -qiE "Intel.*(Graphics|UHD|Iris)"
        set -g HAS_INTEL_IGPU 1
    end
    if echo "$lspci_out" | grep -qiE "AMD.*(Renoir|Cezanne|Barcelo|Phoenix|Raphael|Rembrandt|Lucienne|Mendocino)"
        set -g HAS_AMD_IGPU 1
    end
    if test "$HAS_AMD_IGPU" = 0; and echo "$lspci_out" | grep -qiE "AMD.*VGA.*Radeon"
        set -g HAS_AMD_IGPU 1
    end
end

function detect_wifi
    set -l lspci_out "$_CACHED_LSPCI"
    test -z "$lspci_out"; and set lspci_out (lspci 2>/dev/null; or echo "")
    set -g WIFI_DEVICE (echo "$lspci_out" | grep -iE "network|wireless" | head -1; or echo "")

    if string match -rqi 'MT7921|MT7902|MT7925|MT7922|MediaTek' "$WIFI_DEVICE"
        set -g WIFI_CHIPSET "mediatek"
    else if string match -rqi 'AX200|AX201|AX210|AX211|BE200|Intel.*Wi-Fi|Wireless.*8265' "$WIFI_DEVICE"
        set -g WIFI_CHIPSET "intel"
    else if string match -rqi 'RTL|Realtek' "$WIFI_DEVICE"
        set -g WIFI_CHIPSET "realtek"
    else if string match -rqi 'QCA|Qualcomm|Atheros' "$WIFI_DEVICE"
        set -g WIFI_CHIPSET "qualcomm"
    else
        set -g WIFI_CHIPSET "unknown"
    end
end

function detect_audio
    set -g AUDIO_CODEC ""
    if test -d /proc/asound
        set -g AUDIO_CODEC (cat /proc/asound/card*/codec* 2>/dev/null | grep "Codec:" | head -1 | sed 's/.*Codec: //'; or echo "")
    end
    set -g SOF_ACTIVE 0
    set -l dmesg_out "$_CACHED_DMESG"
    test -z "$dmesg_out"; and set dmesg_out (dmesg 2>/dev/null; or echo "")
    if echo "$dmesg_out" | grep -qi "sof.*firmware|sof-audio"
        set -g SOF_ACTIVE 1
    end
    set -g CPU_VENDOR (grep -m1 "vendor_id" /proc/cpuinfo 2>/dev/null | awk '{print $3}'; or echo "")
end

function detect_touchpad
    set -g HAS_I2C_TOUCHPAD 0
    set -g TOUCHPAD_ERRORS 0

    set -l dmesg_out "$_CACHED_DMESG"
    test -z "$dmesg_out"; and set dmesg_out (dmesg 2>/dev/null; or echo "")
    if echo "$dmesg_out" | grep -qi "i2c_hid_acpi.*ELAN|i2c_hid_acpi.*SYN|i2c_hid_acpi.*MSFT|i2c.*hid.*touchpad"
        set -g HAS_I2C_TOUCHPAD 1
    end
    if echo "$dmesg_out" | grep -qi "i2c_hid_acpi.*failed|i2c_hid_acpi.*error|i2c_hid.*timeout|i2c_hid.*weird size"
        set -g TOUCHPAD_ERRORS 1
    end
end

function detect_battery
    set -g HAS_BATTERY 0
    if test -d /sys/class/power_supply/BAT0; or test -d /sys/class/power_supply/BAT1
        set -g HAS_BATTERY 1
    end
    set -g BATTERY_WMI_LOADED 0
    if lsmod 2>/dev/null | grep -q "acer_wmi_battery"
        set -g BATTERY_WMI_LOADED 1
    end
end

function detect_kernel
    set -g KERNEL_VERSION (uname -r)
    set -g KERNEL_MAJOR (echo "$KERNEL_VERSION" | cut -d. -f1)
    set -g KERNEL_MINOR (echo "$KERNEL_VERSION" | cut -d. -f2)
    set -g SUPPORTS_THERMAL_PROFILES 0
    if test "$KERNEL_MAJOR" -gt 6; or begin; test "$KERNEL_MAJOR" -eq 6; and test "$KERNEL_MINOR" -ge 8; end
        set -g SUPPORTS_THERMAL_PROFILES 1
    end

    set -g IS_CACHYOS 0
    set -g KERNEL_HEADERS "linux-headers"
    if string match -q "*cachyos*" "$KERNEL_VERSION"
        set -g IS_CACHYOS 1
        set -g KERNEL_HEADERS "linux-cachyos-headers"
    end

    # Detect Clang-built kernel (CachyOS and other distros may build with Clang/LLVM)
    set -g IS_CLANG_KERNEL 0
    set -g CLANG_BUILD_FLAGS ""
    if grep -q "clang" /proc/version 2>/dev/null
        set -g IS_CLANG_KERNEL 1
        set -g CLANG_BUILD_FLAGS "LLVM=1 CC=clang"
    end
end

function detect_distro
    set -g DISTRO_ID ""
    set -g DISTRO_NAME ""
    if test -f /etc/os-release
        set -g DISTRO_ID (grep "^ID=" /etc/os-release | cut -d= -f2 | tr -d '"')
        set -g DISTRO_NAME (grep "^NAME=" /etc/os-release | cut -d= -f2 | tr -d '"')
    end

    switch "$DISTRO_ID"
        case arch
            set -g DISTRO_FAMILY "arch"
        case cachyos
            set -g DISTRO_FAMILY "arch"
        case endeavouros
            set -g DISTRO_FAMILY "arch"
        case manjaro
            set -g DISTRO_FAMILY "arch"
        case garuda
            set -g DISTRO_FAMILY "arch"
        case artix
            set -g DISTRO_FAMILY "arch"
        case '*'
            set -g DISTRO_FAMILY "unknown"
    end

    set -g AUR_HELPER ""
    if command -v paru >/dev/null 2>&1
        set -g AUR_HELPER "paru"
    else if command -v yay >/dev/null 2>&1
        set -g AUR_HELPER "yay"
    end
end

# Run all detection functions
function detect_all
    # Cache expensive command outputs for reuse across detection functions
    set -g _CACHED_DMESG (dmesg 2>/dev/null; or echo "")
    set -g _CACHED_LSPCI (lspci 2>/dev/null; or echo "")

    detect_dmi
    detect_model_family
    detect_gpu
    detect_wifi
    detect_audio
    detect_touchpad
    detect_battery
    detect_kernel
    detect_distro
end

# Print a summary of detected hardware
function print_hw_summary
    set -l gpu_info ""
    if test "$HAS_NVIDIA" = 1
        set gpu_info "$NVIDIA_MODEL"
    end
    if test "$HAS_INTEL_IGPU" = 1
        test -n "$gpu_info"; and set gpu_info "$gpu_info + "
        set gpu_info "$gpu_info""Intel iGPU"
    end
    if test "$HAS_AMD_IGPU" = 1
        test -n "$gpu_info"; and set gpu_info "$gpu_info + "
        set gpu_info "$gpu_info""AMD iGPU"
    end
    if test "$HAS_AMD_DGPU" = 1
        test -n "$gpu_info"; and set gpu_info "$gpu_info + "
        set gpu_info "$gpu_info""AMD dGPU"
    end
    test -z "$gpu_info"; and set gpu_info "Unknown"

    set -l wifi_info "$WIFI_CHIPSET"
    test -n "$WIFI_DEVICE"; and set wifi_info "$WIFI_DEVICE"

    set -l bat_status "Not detected"
    test "$HAS_BATTERY" = 1; and set bat_status "Present"

    set -l kernel_extra ""
    if test "$IS_CLANG_KERNEL" = 1
        set kernel_extra " [Clang]"
    end

    echo (set_color --bold)"Detected:"(set_color normal)" $ACER_PRODUCT_NAME ($ACER_SYS_VENDOR)"
    echo (set_color --bold)"Kernel:"(set_color normal)"   $KERNEL_VERSION$kernel_extra | "(set_color --bold)"GPU:"(set_color normal)" $gpu_info"
    echo (set_color --bold)"WiFi:"(set_color normal)"     $wifi_info"
    echo (set_color --bold)"Battery:"(set_color normal)"  $bat_status"

    set -l distro_extra ""
    test -n "$AUR_HELPER"; and set distro_extra " | AUR: $AUR_HELPER"
    echo (set_color --bold)"Distro:"(set_color normal)"   $DISTRO_NAME ($DISTRO_FAMILY)$distro_extra"
end

# Build recommended and optional module lists based on hardware
function build_recommendations
    set -g RECOMMENDED_MODULES
    set -g OPTIONAL_MODULES

    # Core DAMX: recommended for gaming models
    switch "$MODEL_FAMILY"
        case nitro predator helios triton
            set -a RECOMMENDED_MODULES "core-damx"
        case '*'
            set -a OPTIONAL_MODULES "core-damx"
    end

    # Battery: recommended if battery present
    if test "$HAS_BATTERY" = 1
        set -a RECOMMENDED_MODULES "battery"
    end

    # GPU: recommended if hybrid graphics
    if test "$HAS_NVIDIA" = 1
        if test "$HAS_INTEL_IGPU" = 1; or test "$HAS_AMD_IGPU" = 1
            set -a RECOMMENDED_MODULES "gpu"
        else
            set -a OPTIONAL_MODULES "gpu"
        end
    end

    # Touchpad: recommended if errors detected
    if test "$TOUCHPAD_ERRORS" = 1
        set -a RECOMMENDED_MODULES "touchpad"
    else
        set -a OPTIONAL_MODULES "touchpad"
    end

    # Audio: always optional
    set -a OPTIONAL_MODULES "audio"

    # WiFi: recommended for MediaTek
    if test "$WIFI_CHIPSET" = "mediatek"
        set -a RECOMMENDED_MODULES "wifi"
    else
        set -a OPTIONAL_MODULES "wifi"
    end

    # Power: always optional
    set -a OPTIONAL_MODULES "power"

    # Thermal: optional for gaming models on 6.8+
    if test "$SUPPORTS_THERMAL_PROFILES" = 1
        switch "$MODEL_FAMILY"
            case nitro predator helios triton
                set -a OPTIONAL_MODULES "thermal"
        end
    end
end
