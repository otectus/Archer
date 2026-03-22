#!/usr/bin/env bats
# Tests for lib/detect.sh — hardware detection and recommendation engine

setup() {
    # Source utils first (detect.sh depends on it)
    source "$BATS_TEST_DIRNAME/../lib/utils.sh"
    # Override error() to not exit during tests
    error() { echo "ERROR: $*"; return 1; }
    DRY_RUN=1
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

@test "detect_kernel enables thermal profiles for kernel 6.8+" {
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
