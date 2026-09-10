#!/usr/bin/env bash
# Reproducible external module preparation. No upstream install commands run.
set -euo pipefail
package_dir="${1:?Usage: prepare.sh PACKAGE DESTINATION [LOCAL_REPOSITORY]}"
destination="${2:?Missing destination}"
source "$package_dir/source.conf"
[[ "$DRIVER_REVISION" =~ ^[0-9a-f]{40}$ ]] || { echo 'Expected a full upstream commit' >&2; exit 1; }
staging=$(mktemp -d)
trap 'rm -rf "$staging"' EXIT
trap 'echo "Source/patch validation failed for $DKMS_NAME at $DRIVER_REVISION. Retry with an unmodified Archer checkout; report the failed patch. Existing sources were retained." >&2' ERR
git clone --quiet --no-checkout "${3:-$REPO_DRIVER}" "$staging/source"
git -C "$staging/source" checkout --quiet --detach "$DRIVER_REVISION"
[[ "$(git -C "$staging/source" rev-parse HEAD)" == "$DRIVER_REVISION" ]]
# Explicit series order. Fail if a named patch is absent or no longer applies.
while IFS= read -r patch_file || [[ -n "$patch_file" ]]; do
    [[ -n "$patch_file" && "$patch_file" != \#* ]] || continue
    [[ "$patch_file" =~ ^[a-zA-Z0-9._-]+\.patch$ ]]
    git -C "$staging/source" apply --check "$package_dir/patches/$patch_file"
    git -C "$staging/source" apply "$package_dir/patches/$patch_file"
done < "$package_dir/patches/series"
printf '\nMODULE_VERSION("%s");\n' "$DKMS_VERSION" >> "$staging/source/$SOURCE_FILE"
cp "$package_dir/../build.sh" "$staging/source/archer-build.sh"
cp "$package_dir/../../lib/kernel.sh" "$staging/source/archer-kernel.sh"
cat > "$staging/source/dkms.conf" <<DKMS
PACKAGE_NAME="$DKMS_NAME"
PACKAGE_VERSION="$DKMS_VERSION"
MAKE[0]="bash archer-build.sh \$kernelver"
CLEAN="true"
BUILT_MODULE_NAME[0]="$DRIVER_MODULE"
BUILT_MODULE_LOCATION[0]="$MODULE_LOCATION"
DEST_MODULE_LOCATION[0]="/updates/dkms"
AUTOINSTALL="yes"
DKMS
printf '%s\n' "$DRIVER_REVISION" "$DKMS_VERSION" > "$staging/source/archer-source"
rm -rf "$staging/source/.git"
if [[ -e "$destination" ]]; then
    # Repeated preparation is a no-op only for the complete identical tree.
    diff -qr "$staging/source" "$destination" || { echo "Refusing to replace modified source: $destination" >&2; exit 1; }
else
    mv "$staging/source" "$destination"
fi
echo "Prepared $DKMS_NAME/$DKMS_VERSION from $DRIVER_REVISION"
