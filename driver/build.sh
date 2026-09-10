#!/usr/bin/env bash
# Kbuild only: DKMS owns signing, depmod and installation for its target kernel.
set -euo pipefail
helper_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$helper_dir/archer-kernel.sh" ]]; then
    source "$helper_dir/archer-kernel.sh"
else
    source "$helper_dir/../lib/kernel.sh"
fi
kernel="${1:?DKMS must supply the target kernel version}"
[[ "$kernel" =~ ^[a-zA-Z0-9._+-]+$ ]] || exit 1
kernel_validate_headers "$kernel"
build_dir="$KERNEL_MODULES_ROOT/$kernel/build"
flags=()
if grep -qs '^CONFIG_CC_IS_CLANG=y' "$build_dir/.config"; then
    flags+=(LLVM=1 CC=clang)
elif ! grep -qs '^CONFIG_CC_IS_GCC=y' "$build_dir/.config"; then
    echo "Cannot identify compiler in $build_dir/.config; install prepared matching headers." >&2
    exit 1
fi
# Probe API shape, including vendor backports, rather than assume a version.
cflags=()
if [[ -f src/linuwu_sense.c ]]; then
    if ! grep -q 'struct platform_profile_ops {' "$build_dir/include/linux/platform_profile.h"; then
        cflags+=(-DARCHER_LEGACY_PLATFORM_PROFILE)
    fi
    if grep -q 'wmi_notify_handler.*u32' "$build_dir/include/linux/acpi.h"; then
        cflags+=(-DARCHER_LEGACY_WMI_NOTIFY)
    fi
    if grep -q 'void (\*remove_new)' "$build_dir/include/linux/platform_device.h"; then
        cflags+=(-DARCHER_LEGACY_PLATFORM_REMOVE)
    fi
fi
if [[ ${#cflags[@]} -gt 0 ]]; then flags+=("KCFLAGS=${cflags[*]}"); fi
exec make -C "$build_dir" "M=$PWD" "${flags[@]}" modules
