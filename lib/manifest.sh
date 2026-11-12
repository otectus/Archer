#!/usr/bin/env bash
# Install manifest tracking (Bash)
# Tracks what modules were installed for clean uninstallation.
#
# The manifest lives at /var/lib/archer/install-manifest.json (root-owned, 0644)
# so that install (run as user with sudo) and uninstall (often run via sudo,
# where $HOME=/root) read and write the same path. It also stops local users
# tampering with manifest entries that uninstall.sh later sources.

MANIFEST_DIR="/var/lib/archer"
MANIFEST_FILE="$MANIFEST_DIR/install-manifest.json"

# Legacy locations migrated on first install. Both real-user $HOME and /root
# are checked because earlier versions wrote under whichever HOME was set when
# install.sh ran (sometimes via sudo). DAMX is the project's prior name.
_LEGACY_MANIFEST_CANDIDATES=(
    "${HOME:-}/.local/share/archer/install-manifest.json"
    "${HOME:-}/.local/share/damx/install-manifest.json"
    "/root/.local/share/archer/install-manifest.json"
    "/root/.local/share/damx/install-manifest.json"
)

# Move any legacy manifest into the new location. Idempotent. Run once near
# the start of install.sh / uninstall.sh.
migrate_legacy_manifest() {
    [[ -f "$MANIFEST_FILE" ]] && return 0
    [[ "${DRY_RUN:-0}" -eq 1 ]] && return 0

    local src
    for src in "${_LEGACY_MANIFEST_CANDIDATES[@]}"; do
        [[ -z "$src" ]] && continue
        [[ -f "$src" ]] || continue

        python3 "$(detect_script_dir)/scripts/archer-manifest.py" "$src" read modules_installed >/dev/null || return 1
        log "Migrating legacy manifest $src -> $MANIFEST_FILE"
        run_sudo mkdir -p "$MANIFEST_DIR" || return 1
        run_sudo chmod 755 "$MANIFEST_DIR" || return 1
        run_sudo cp "$src" "$MANIFEST_FILE" || return 1
        run_sudo chmod 644 "$MANIFEST_FILE" || return 1

        # Older DAMX manifests used the module ID "core-damx" for what is now "driver".
        if has_cmd sed; then
            run_sudo sed -i 's/"core-damx"/"driver"/g' "$MANIFEST_FILE" || return 1
        fi

        python3 "$(detect_script_dir)/scripts/archer-manifest.py" "$MANIFEST_FILE" read modules_installed >/dev/null || return 1

        # Best-effort cleanup of the legacy file (only if writable by us).
        if [[ -w "$src" ]]; then
            run rm -f "$src" || return 1
        fi
        return 0
    done
    return 0
}

# Write the install manifest after installation.
# Usage: write_manifest "driver battery gpu" "file1 file2" "linuwu-sense/1.0" "envycontrol tlp"
write_manifest() {
    local modules_str="$1"
    local files_str="$2"
    local dkms_str="$3"
    local packages_str="$4"

    run_sudo python3 "$(detect_script_dir)/scripts/archer-manifest.py" "$MANIFEST_FILE" write \
        "$modules_str" "$files_str" "$dkms_str" "$packages_str" \
        "$INSTALLER_VERSION" "${ACER_PRODUCT_NAME:-Unknown}" "${MODEL_FAMILY:-unknown}" "${KERNEL_VERSION:-unknown}" || return 1
    log "Install manifest written to $MANIFEST_FILE"
}

read_manifest_modules() { read_manifest_field modules_installed; }
read_manifest_field() {
    python3 "$(detect_script_dir)/scripts/archer-manifest.py" "$MANIFEST_FILE" read "$1"
}
retain_manifest_modules() {
    run_sudo python3 "$(detect_script_dir)/scripts/archer-manifest.py" "$MANIFEST_FILE" retain "$1" || return 1
}

# Check if manifest exists.
has_manifest() {
    [[ -f "$MANIFEST_FILE" ]]
}

# Remove the manifest file.
remove_manifest() {
    run_sudo rm -f "$MANIFEST_FILE" || return 1
}
