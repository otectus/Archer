#!/usr/bin/env bash
# Compile only: never registers DKMS, signs, installs or loads host modules.
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
kernel="${1:?Usage: check-kernel-modules.sh KERNEL_RELEASE V4L2_SOURCE}"
v4l2_source="${2:?Supply the distro v4l2loopback source tree}"
staging=$(mktemp -d)
trap 'rm -rf "$staging"' EXIT
for module in linuwu-sense acer-wmi-battery; do
    local_source=()
    case "$module" in
        linuwu-sense) [[ -z "${LINUWU_TEST_REPO:-}" ]] || local_source=("$LINUWU_TEST_REPO") ;;
        acer-wmi-battery) [[ -z "${BATTERY_TEST_REPO:-}" ]] || local_source=("$BATTERY_TEST_REPO") ;;
    esac
    bash "$repo/driver/$module/prepare.sh" "$staging/$module" "${local_source[@]}"
    # Exercise the complete immutable preparation twice.
    bash "$repo/driver/$module/prepare.sh" "$staging/$module" "${local_source[@]}"
    (cd "$staging/$module" && bash archer-build.sh "$kernel")
done
cp -r "$v4l2_source" "$staging/v4l2loopback"
cp "$repo/driver/build.sh" "$staging/v4l2loopback/archer-build.sh"
cp "$repo/lib/kernel.sh" "$staging/v4l2loopback/archer-kernel.sh"
(cd "$staging/v4l2loopback" && bash archer-build.sh "$kernel")
