#!/usr/bin/env bash
# Install manifest tracking (Bash)
# Tracks what modules were installed for clean uninstallation

MANIFEST_DIR="$HOME/.local/share/archer"
MANIFEST_FILE="$MANIFEST_DIR/install-manifest.json"

# Migrate legacy DAMX manifest location
_LEGACY_MANIFEST="$HOME/.local/share/damx/install-manifest.json"
if [[ ! -f "$MANIFEST_FILE" ]] && [[ -f "$_LEGACY_MANIFEST" ]]; then
    mkdir -p "$MANIFEST_DIR"
    cp "$_LEGACY_MANIFEST" "$MANIFEST_FILE"
    if has_cmd sed; then
        sed -i 's/"core-damx"/"driver"/g' "$MANIFEST_FILE"
    fi
fi

# Write the install manifest after installation
# Usage: write_manifest "driver battery gpu" "file1 file2" "linuwu-sense/1.0" "envycontrol tlp"
write_manifest() {
    local modules_str="$1"
    local files_str="$2"
    local dkms_str="$3"
    local packages_str="$4"

    mkdir -p "$MANIFEST_DIR"
    chmod 700 "$MANIFEST_DIR"

    if has_cmd jq; then
        jq -n \
            --arg date "$(date -Iseconds)" \
            --arg version "$INSTALLER_VERSION" \
            --arg model "$ACER_PRODUCT_NAME" \
            --arg family "$MODEL_FAMILY" \
            --arg kernel "$KERNEL_VERSION" \
            --arg modules "$modules_str" \
            --arg files "$files_str" \
            --arg dkms "$dkms_str" \
            --arg packages "$packages_str" \
            '{
                install_date: $date,
                installer_version: $version,
                acer_model: $model,
                model_family: $family,
                kernel_version: $kernel,
                modules_installed: ($modules | split(" ") | map(select(. != ""))),
                files_created: ($files | split(" ") | map(select(. != ""))),
                dkms_modules: ($dkms | split(" ") | map(select(. != ""))),
                packages_installed: ($packages | split(" ") | map(select(. != "")))
            }' > "$MANIFEST_FILE"
    else
        # Python fallback: pass values via environment to prevent injection
        ARCHER_VERSION="$INSTALLER_VERSION" \
        ARCHER_MODEL="$ACER_PRODUCT_NAME" \
        ARCHER_FAMILY="$MODEL_FAMILY" \
        ARCHER_KERNEL="$KERNEL_VERSION" \
        ARCHER_MODULES="$modules_str" \
        ARCHER_FILES="$files_str" \
        ARCHER_DKMS="$dkms_str" \
        ARCHER_PKGS="$packages_str" \
        ARCHER_MANIFEST="$MANIFEST_FILE" \
        python3 -c "
import json, datetime, os
manifest = {
    'install_date': datetime.datetime.now().isoformat(),
    'installer_version': os.environ['ARCHER_VERSION'],
    'acer_model': os.environ['ARCHER_MODEL'],
    'model_family': os.environ['ARCHER_FAMILY'],
    'kernel_version': os.environ['ARCHER_KERNEL'],
    'modules_installed': os.environ['ARCHER_MODULES'].split(),
    'files_created': os.environ['ARCHER_FILES'].split(),
    'dkms_modules': os.environ['ARCHER_DKMS'].split(),
    'packages_installed': os.environ['ARCHER_PKGS'].split()
}
with open(os.environ['ARCHER_MANIFEST'], 'w') as f:
    json.dump(manifest, f, indent=2)
"
    fi
    log "Install manifest written to $MANIFEST_FILE"
}

# Read installed modules from manifest
# Returns space-separated list of module IDs
read_manifest_modules() {
    if [[ ! -f "$MANIFEST_FILE" ]]; then
        echo ""
        return 1
    fi
    if has_cmd jq; then
        jq -r '.modules_installed // [] | join(" ")' "$MANIFEST_FILE"
    else
        ARCHER_MANIFEST="$MANIFEST_FILE" python3 -c "
import json, os
with open(os.environ['ARCHER_MANIFEST']) as f:
    data = json.load(f)
print(' '.join(data.get('modules_installed', [])))
"
    fi
}

# Read a specific field from manifest
read_manifest_field() {
    local field="$1"
    if [[ ! -f "$MANIFEST_FILE" ]]; then
        echo ""
        return 1
    fi
    if has_cmd jq; then
        jq -r --arg f "$field" '.[$f] // "" | if type == "array" then join(" ") else tostring end' "$MANIFEST_FILE"
    else
        ARCHER_MANIFEST="$MANIFEST_FILE" ARCHER_FIELD="$field" python3 -c "
import json, os
with open(os.environ['ARCHER_MANIFEST']) as f:
    data = json.load(f)
val = data.get(os.environ['ARCHER_FIELD'], [])
if isinstance(val, list):
    print(' '.join(val))
else:
    print(val)
"
    fi
}

# Check if manifest exists
has_manifest() {
    [[ -f "$MANIFEST_FILE" ]]
}

# Remove the manifest file
remove_manifest() {
    rm -f "$MANIFEST_FILE"
}
