#!/usr/bin/env bash
# Shared, target-specific kernel detection. Roots are overridable for tests.
KERNEL_MODULES_ROOT="${KERNEL_MODULES_ROOT:-/usr/lib/modules}"
KERNEL_SYS_ROOT="${KERNEL_SYS_ROOT:-/sys}"
KERNEL_PROC_VERSION="${KERNEL_PROC_VERSION:-/proc/version}"

kernel_at_least() {
    local release="$1" major="$2" minor="$3" patch="${4:-0}"
    [[ "$release" =~ ^([0-9]+)\.([0-9]+)(\.([0-9]+))? ]] || return 1
    local a=$((10#${BASH_REMATCH[1]})) b=$((10#${BASH_REMATCH[2]})) c=$((10#${BASH_REMATCH[4]:-0}))
    (( a > major || (a == major && (b > minor || (b == minor && c >= patch))) ))
}

kernel_header_package() {
    local release="$1" root="${2:-$KERNEL_MODULES_ROOT}" package=""
    # pkgbase is supplied by Arch-family kernels, including Manjaro and custom builds.
    if [[ -r "$root/$release/pkgbase" ]]; then
        read -r package < "$root/$release/pkgbase"
    else
        package=$(pacman -Qoq "$root/$release/vmlinuz" 2>/dev/null) || package=""
    fi
    if [[ "$package" =~ ^[a-zA-Z0-9][a-zA-Z0-9@._+-]*$ ]]; then
        printf '%s-headers\n' "$package"
        return 0
    fi
    # Suffixes are hints for diagnostics, never proof of a matching header tree.
    case "$release" in
        *cachyos*) return 1 ;; # Variant cannot safely be inferred from a removed tree.
        *-lts*) echo linux-lts-headers ;;
        *-zen*) echo linux-zen-headers ;;
        *-hardened*) echo linux-hardened-headers ;;
        *-arch*) echo linux-headers ;;
        *) return 1 ;;
    esac
}

kernel_validate_headers() {
    local release="$1" root="${2:-$KERNEL_MODULES_ROOT}" actual="" expected
    local build="$root/$release/build"
    if [[ -r "$build/include/config/kernel.release" ]]; then
        read -r actual < "$build/include/config/kernel.release" || [[ -n "$actual" ]]
    fi
    if [[ ! -f "$build/Makefile" || ! -f "$build/.config" || "$actual" != "$release" ]]; then
        expected=$(kernel_header_package "$release" "$root") || expected="the matching custom kernel headers"
        echo "Kernel $release: expected $build with release $release; found ${actual:-missing/unprepared headers}." >&2
        echo "Install $expected alongside its kernel. If upgraded, reboot into the installed kernel and retry; never link another kernel's headers here." >&2
        return 1
    fi
}

kernel_collect_targets() {
    local root="${1:-$KERNEL_MODULES_ROOT}" path release running
    KERNEL_TARGETS=()
    running=$(uname -r)
    for path in "$root"/*; do
        [[ -d "$path" ]] || continue
        [[ -e "$path/vmlinuz" || -d "$path/kernel" || -e "$path/build/Makefile" ]] || continue
        release=${path##*/}
        kernel_validate_headers "$release" "$root" || return 1
        KERNEL_TARGETS+=("$release")
    done
    if [[ ${#KERNEL_TARGETS[@]} -eq 0 ]]; then
        kernel_validate_headers "$running" "$root" || return 1
        echo "No installed kernel targets found under $root" >&2
        return 1
    fi
    if [[ ! " ${KERNEL_TARGETS[*]} " == *" $running "* ]]; then
        echo "Running kernel $running is no longer installed; building only installed targets: ${KERNEL_TARGETS[*]}. Reboot before loading modules." >&2
    fi
}

kernel_profile_path() {
    local root="${1:-$KERNEL_SYS_ROOT}" path
    if [[ -r "$root/firmware/acpi/platform_profile" && -r "$root/firmware/acpi/platform_profile_choices" ]]; then
        echo "$root/firmware/acpi/platform_profile"
        return 0
    fi
    local candidates=()
    for path in "$root"/class/platform-profile/*/profile; do
        [[ -r "$path" && -r "${path%/profile}/choices" ]] && candidates+=("$path")
    done
    # Do not choose an arbitrary provider on multi-provider systems.
    [[ ${#candidates[@]} -eq 1 ]] || return 1
    echo "${candidates[0]}"
}

kernel_dkms_diagnostics() {
    local name="$1" version="$2" kernel="$3" path
    echo "DKMS $name/$version failed for $kernel. Build logs:" >&2
    for path in /var/lib/dkms/"$name"/"$version"/{build,"$kernel"/*}/log/make.log /var/lib/dkms/"$name"/"$version"/build/make.log; do
        [[ -f "$path" ]] || continue
        echo "$path" >&2
        tail -n 40 "$path" >&2
    done
    echo "Find logs: sudo find /var/lib/dkms/$name/$version -name make.log -print" >&2
    echo "A compiler error is separate from Secure Boot: 'Key was rejected' on modprobe requires an enrolled DKMS signing key." >&2
}

kernel_refresh_initramfs() {
    local etc="${1:-/etc}" mkinitcpio="${2:-/usr/bin/mkinitcpio}" dracut="${3:-/usr/bin/dracut}"
    # Early boot images can contain both the old module and modprobe config.
    # Rebuild all installed presets; bypass interactive /usr/local wrappers.
    if [[ -x "$mkinitcpio" ]] && compgen -G "$etc/mkinitcpio.d/*.preset" >/dev/null; then
        run_sudo_timeout 300 "$mkinitcpio" -P || return 1
        if has_cmd limine-mkinitcpio; then
            run_sudo_timeout 120 limine-mkinitcpio || return 1
        fi
    elif [[ -x "$dracut" ]]; then
        run_sudo_timeout 300 "$dracut" --regenerate-all --force || return 1
    else
        log "No mkinitcpio presets or dracut found. Rebuild custom early-boot images if they include Acer modules."
    fi
}

archer_config_owned() {
    local path="$1"
    grep -qs 'Archer Compatibility Suite' "$path" && return 0
    if declare -F read_manifest_field >/dev/null; then
        [[ " $(read_manifest_field files_created) " == *" $path "* ]] && return 0
    fi
    return 1
}

archer_write_config() {
    local path="$1" content="$2"
    if [[ -e "$path" ]] && ! archer_config_owned "$path"; then
        warn "Refusing to overwrite user-managed $path. Review it alongside Archer's configuration before retrying."
        return 1
    fi
    printf '# Installed by Archer Compatibility Suite\n%s\n' "$content" |
        run_sudo tee "$path" >/dev/null || return 1
    INSTALLED_FILES+=" $path"
}

archer_remove_config() {
    local path
    for path in "$@"; do
        [[ -e "$path" ]] || continue
        if archer_config_owned "$path"; then
            run_sudo rm -f "$path" || return 1
        else
            log "Retaining user-managed $path"
        fi
    done
}

kernel_battery_limit_path() {
    local path health="${1:-$KERNEL_SYS_ROOT/bus/wmi/drivers/acer-wmi-battery/health_mode}"
    for path in "$KERNEL_SYS_ROOT"/class/power_supply/*/charge_control_end_threshold; do
        [[ -r "$path" ]] || continue
        [[ "$(cat "${path%/*}/type" 2>/dev/null)" == Battery ]] || continue
        echo "$path"; return 0
    done
    for path in "$KERNEL_SYS_ROOT"/devices/platform/acer-wmi/{predator_sense,nitro_sense}/battery_limiter "$health"; do
        [[ -r "$path" ]] || continue
        echo "$path"; return 0
    done
    return 1
}
