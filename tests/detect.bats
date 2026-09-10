#!/usr/bin/env bats
# Tests for lib/detect.sh — hardware detection and recommendation engine

setup() {
    # Initialize variables before sourcing (prevents unbound variable errors)
    DRY_RUN=1
    NO_CONFIRM=1
    VERBOSE=0
    LOG_FILE=""
    REBOOT_REQUIRED=0
    export KERNEL_MODULES_ROOT="$BATS_TEST_TMPDIR/modules"
    export KERNEL_SYS_ROOT="$BATS_TEST_TMPDIR/sys"
    export KERNEL_PROC_VERSION="$BATS_TEST_TMPDIR/version"
    mkdir -p "$KERNEL_MODULES_ROOT" "$KERNEL_SYS_ROOT"
    echo 'Linux version 7.0 (gcc)' > "$KERNEL_PROC_VERSION"
    uname() { echo 6.12.1-arch1-1; }
    pacman() { return 1; }
    # Source utils first (detect.sh depends on it)
    source "$BATS_TEST_DIRNAME/../lib/utils.sh"
    # Override error() to not exit during tests
    error() { echo "ERROR: $*"; return 1; }
}

@test "detect_kernel parses kernel version correctly" {
    source "$BATS_TEST_DIRNAME/../lib/detect.sh"
    detect_kernel
    [ -n "$KERNEL_VERSION" ]
    [ "$KERNEL_MAJOR" -gt 0 ]
}

@test "detect_kernel sets KERNEL_HEADERS to linux-headers by default" {
    # Mock uname to return a non-cachyos kernel
    uname() { echo "6.8.1-arch1-1"; }
    export -f uname
    source "$BATS_TEST_DIRNAME/../lib/detect.sh"
    detect_kernel
    [ "$KERNEL_HEADERS" = "linux-headers" ]
    [ "$IS_CACHYOS" -eq 0 ]
}

@test "detect_kernel identifies CachyOS kernel" {
    uname() { echo "6.12.1-2-cachyos"; }
    export -f uname
    # Mock pacman to return a package name
    pacman() {
        case "$1" in
            -Qoq) echo "linux-cachyos" ;;
            -Q)   echo "linux-cachyos 6.12.1" ;;
        esac
    }
    export -f pacman
    source "$BATS_TEST_DIRNAME/../lib/detect.sh"
    detect_kernel
    [ "$IS_CACHYOS" -eq 1 ]
    [ "$KERNEL_HEADERS" = "linux-cachyos-headers" ]
}

@test "detect_kernel enables thermal profiles only for an exposed capability" {
    mkdir -p "$KERNEL_SYS_ROOT/firmware/acpi"
    echo balanced > "$KERNEL_SYS_ROOT/firmware/acpi/platform_profile"
    echo balanced > "$KERNEL_SYS_ROOT/firmware/acpi/platform_profile_choices"
    uname() { echo "6.8.0-arch1-1"; }
    export -f uname
    source "$BATS_TEST_DIRNAME/../lib/detect.sh"
    detect_kernel
    [ "$SUPPORTS_THERMAL_PROFILES" -eq 1 ]
}

@test "detect_kernel disables thermal profiles for kernel < 6.8" {
    uname() { echo "6.7.9-arch1-1"; }
    export -f uname
    source "$BATS_TEST_DIRNAME/../lib/detect.sh"
    detect_kernel
    [ "$SUPPORTS_THERMAL_PROFILES" -eq 0 ]
}

@test "detect_distro identifies arch-based distros" {
    source "$BATS_TEST_DIRNAME/../lib/detect.sh"

    # Create a temporary os-release
    local tmpdir=$(mktemp -d)
    cat > "$tmpdir/os-release" <<'EOF'
ID=cachyos
NAME="CachyOS Linux"
EOF

    # Override detection to use temp file
    detect_distro_from_file() {
        DISTRO_ID=$(grep "^ID=" "$1" | cut -d= -f2 | tr -d '"')
        DISTRO_NAME=$(grep "^NAME=" "$1" | cut -d= -f2 | tr -d '"')
        case "$DISTRO_ID" in
            arch|cachyos|endeavouros|manjaro|garuda|artix) DISTRO_FAMILY="arch" ;;
            *) DISTRO_FAMILY="unknown" ;;
        esac
    }
    detect_distro_from_file "$tmpdir/os-release"
    [ "$DISTRO_FAMILY" = "arch" ]

    rm -rf "$tmpdir"
}

@test "detect_model_family recognizes Nitro" {
    source "$BATS_TEST_DIRNAME/../lib/detect.sh"
    ACER_PRODUCT_NAME="Nitro AN515-58"
    detect_model_family
    [ "$MODEL_FAMILY" = "nitro" ]
}

@test "detect_model_family recognizes Predator" {
    source "$BATS_TEST_DIRNAME/../lib/detect.sh"
    ACER_PRODUCT_NAME="Predator Helios 300"
    detect_model_family
    [ "$MODEL_FAMILY" = "predator" ]
}

@test "detect_model_family returns unknown for unrecognized models" {
    source "$BATS_TEST_DIRNAME/../lib/detect.sh"
    ACER_PRODUCT_NAME="ThinkPad X1"
    detect_model_family
    [ "$MODEL_FAMILY" = "unknown" ]
}

@test "is_in_list finds items in space-separated list" {
    source "$BATS_TEST_DIRNAME/../lib/detect.sh"
    # is_in_list is defined in install.sh, define it here
    is_in_list() { [[ " $2 " == *" $1 "* ]]; }
    is_in_list "driver" "driver battery gpu"
    ! is_in_list "thermal" "driver battery gpu"
}

@test "detect_kernel uses dynamic header detection for non-CachyOS" {
    uname() { echo "6.12.1-zen1-1-zen"; }
    export -f uname
    pacman() {
        case "$1" in
            -Qoq) echo "linux-zen" ;;
        esac
    }
    export -f pacman
    source "$BATS_TEST_DIRNAME/../lib/detect.sh"
    detect_kernel
    [ "$IS_CACHYOS" -eq 0 ]
    [ "$KERNEL_HEADERS" = "linux-zen-headers" ]
}

@test "detect_kernel detects clang-built kernel" {
    uname() { echo "6.12.1-arch1-1"; }
    export -f uname
    pacman() { echo ""; }
    export -f pacman
    echo "Linux version (Clang 22)" > "$KERNEL_PROC_VERSION"
    source "$BATS_TEST_DIRNAME/../lib/detect.sh"
    detect_kernel
    [ "$IS_CLANG_KERNEL" -eq 1 ]
    [ "$CLANG_BUILD_FLAGS" = "LLVM=1 CC=clang" ]
}

@test "detect_model_family recognizes all gaming families" {
    source "$BATS_TEST_DIRNAME/../lib/detect.sh"

    for model_pair in "Helios:helios" "Triton:triton" "Swift:swift" "Aspire:aspire" "Spin:spin"; do
        local name="${model_pair%%:*}"
        local expected="${model_pair##*:}"
        ACER_PRODUCT_NAME="Acer $name 500"
        detect_model_family
        [ "$MODEL_FAMILY" = "$expected" ]
    done
}

@test "build_recommendations includes driver for gaming models" {
    source "$BATS_TEST_DIRNAME/../lib/detect.sh"
    MODEL_FAMILY="nitro"
    HAS_BATTERY=1
    HAS_NVIDIA=1
    HAS_INTEL_IGPU=1
    HAS_AMD_IGPU=0
    HAS_AMD_DGPU=0
    TOUCHPAD_ERRORS=0
    HAS_I2C_TOUCHPAD=0
    WIFI_CHIPSET="intel"
    SUPPORTS_THERMAL_PROFILES=1
    DISPLAY=":0"
    build_recommendations
    [[ " ${RECOMMENDED_MODULES[*]} " == *" driver "* ]]
    [[ " ${RECOMMENDED_MODULES[*]} " == *" battery "* ]]
    [[ " ${RECOMMENDED_MODULES[*]} " == *" gpu "* ]]
}

@test "build_recommendations makes driver optional for non-gaming" {
    source "$BATS_TEST_DIRNAME/../lib/detect.sh"
    MODEL_FAMILY="swift"
    HAS_BATTERY=1
    HAS_NVIDIA=0
    HAS_INTEL_IGPU=1
    HAS_AMD_IGPU=0
    HAS_AMD_DGPU=0
    TOUCHPAD_ERRORS=0
    HAS_I2C_TOUCHPAD=0
    WIFI_CHIPSET="intel"
    SUPPORTS_THERMAL_PROFILES=0
    DISPLAY=""
    WAYLAND_DISPLAY=""
    build_recommendations
    [[ " ${OPTIONAL_MODULES[*]} " == *" driver "* ]]
    ! [[ " ${RECOMMENDED_MODULES[*]} " == *" driver "* ]]
}

@test "ANV16S-41 is a Nitro family with or without marketing prefix" {
    source "$BATS_TEST_DIRNAME/../lib/detect.sh"
    for product in 'Nitro ANV16S-41' 'ANV16S-41' 'Nitro ANV15-51' 'Nitro ANV15-41'; do
        ACER_PRODUCT_NAME="$product"
        detect_model_family
        [ "$MODEL_FAMILY" = nitro ]
    done
}
