#!/usr/bin/env fish
# Install manifest tracking (Fish)
# Tracks what modules were installed for clean uninstallation

set -g MANIFEST_DIR "$HOME/.local/share/damx"
set -g MANIFEST_FILE "$MANIFEST_DIR/install-manifest.json"

# Write the install manifest after installation
# Usage: write_manifest "core-damx battery gpu" "file1 file2" "linuwu-sense/1.0" "envycontrol tlp"
function write_manifest
    set -l modules_str $argv[1]
    set -l files_str $argv[2]
    set -l dkms_str $argv[3]
    set -l packages_str $argv[4]

    mkdir -p -m 700 "$MANIFEST_DIR"

    if has_cmd jq
        jq -n \
            --arg date (date -Iseconds) \
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
        set -lx DAMX_VERSION "$INSTALLER_VERSION"
        set -lx DAMX_MODEL "$ACER_PRODUCT_NAME"
        set -lx DAMX_FAMILY "$MODEL_FAMILY"
        set -lx DAMX_KERNEL "$KERNEL_VERSION"
        set -lx DAMX_MODULES "$modules_str"
        set -lx DAMX_FILES "$files_str"
        set -lx DAMX_DKMS "$dkms_str"
        set -lx DAMX_PKGS "$packages_str"
        set -lx DAMX_MANIFEST "$MANIFEST_FILE"
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
    end
    log "Install manifest written to $MANIFEST_FILE"
end

# Read installed modules from manifest
function read_manifest_modules
    if not test -f "$MANIFEST_FILE"
        echo ""
        return 1
    end
    if has_cmd jq
        jq -r '.modules_installed // [] | join(" ")' "$MANIFEST_FILE"
    else
        set -lx DAMX_MANIFEST "$MANIFEST_FILE"
        python3 -c "
import json, os
with open(os.environ['DAMX_MANIFEST']) as f:
    data = json.load(f)
print(' '.join(data.get('modules_installed', [])))
"
    end
end

# Read a specific field from manifest
function read_manifest_field
    set -l field $argv[1]
    if not test -f "$MANIFEST_FILE"
        echo ""
        return 1
    end
    if has_cmd jq
        jq -r --arg f "$field" '.[$f] // "" | if type == "array" then join(" ") else tostring end' "$MANIFEST_FILE"
    else
        set -lx DAMX_MANIFEST "$MANIFEST_FILE"
        set -lx DAMX_FIELD "$field"
        python3 -c "
import json, os
with open(os.environ['DAMX_MANIFEST']) as f:
    data = json.load(f)
val = data.get(os.environ['DAMX_FIELD'], [])
if isinstance(val, list):
    print(' '.join(val))
else:
    print(val)
"
    end
end

# Check if manifest exists
function has_manifest
    test -f "$MANIFEST_FILE"
end

# Remove the manifest file
function remove_manifest
    rm -f "$MANIFEST_FILE"
end
