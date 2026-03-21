#!/usr/bin/env bash
# Install manifest tracking (Bash)
# Tracks what modules were installed for clean uninstallation

MANIFEST_DIR="$HOME/.local/share/damx"
MANIFEST_FILE="$MANIFEST_DIR/install-manifest.json"

# Write the install manifest after installation
# Usage: write_manifest "core-damx battery gpu" "file1 file2" "linuwu-sense/1.0" "envycontrol tlp"
write_manifest() {
    local modules_str="$1"
    local files_str="$2"
    local dkms_str="$3"
    local packages_str="$4"

    mkdir -p -m 700 "$MANIFEST_DIR"

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
        DAMX_VERSION="$INSTALLER_VERSION" \
        DAMX_MODEL="$ACER_PRODUCT_NAME" \
        DAMX_FAMILY="$MODEL_FAMILY" \
        DAMX_KERNEL="$KERNEL_VERSION" \
        DAMX_MODULES="$modules_str" \
        DAMX_FILES="$files_str" \
        DAMX_DKMS="$dkms_str" \
        DAMX_PKGS="$packages_str" \
        DAMX_MANIFEST="$MANIFEST_FILE" \
        python3 -c "
import json, datetime, os
manifest = {
    'install_date': datetime.datetime.now().isoformat(),
    'installer_version': os.environ['DAMX_VERSION'],
    'acer_model': os.environ['DAMX_MODEL'],
    'model_family': os.environ['DAMX_FAMILY'],
    'kernel_version': os.environ['DAMX_KERNEL'],
    'modules_installed': os.environ['DAMX_MODULES'].split(),
    'files_created': os.environ['DAMX_FILES'].split(),
    'dkms_modules': os.environ['DAMX_DKMS'].split(),
    'packages_installed': os.environ['DAMX_PKGS'].split()
}
with open(os.environ['DAMX_MANIFEST'], 'w') as f:
    json.dump(manifest, f, indent=2)
"
    fi
    log "Install manifest written to $MANIFEST_FILE"
}

# Read installed modules from manifest
# Returns space-separated list of module IDs
read_manifest_modules() {
    if [ ! -f "$MANIFEST_FILE" ]; then
        echo ""
        return 1
    fi
    if has_cmd jq; then
        jq -r '.modules_installed // [] | join(" ")' "$MANIFEST_FILE"
    else
        DAMX_MANIFEST="$MANIFEST_FILE" python3 -c "
import json, os
with open(os.environ['DAMX_MANIFEST']) as f:
    data = json.load(f)
print(' '.join(data.get('modules_installed', [])))
"
    fi
}

# Read a specific field from manifest
read_manifest_field() {
    local field="$1"
    if [ ! -f "$MANIFEST_FILE" ]; then
        echo ""
        return 1
    fi
    if has_cmd jq; then
        jq -r --arg f "$field" '.[$f] // "" | if type == "array" then join(" ") else tostring end' "$MANIFEST_FILE"
    else
        DAMX_MANIFEST="$MANIFEST_FILE" DAMX_FIELD="$field" python3 -c "
import json, os
with open(os.environ['DAMX_MANIFEST']) as f:
    data = json.load(f)
val = data.get(os.environ['DAMX_FIELD'], [])
if isinstance(val, list):
    print(' '.join(val))
else:
    print(val)
"
    fi
}

# Check if manifest exists
has_manifest() {
    [ -f "$MANIFEST_FILE" ]
}

# Remove the manifest file
remove_manifest() {
    rm -f "$MANIFEST_FILE"
}
