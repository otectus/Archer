#!/usr/bin/env bash
# Archer Compatibility Suite - Unified Uninstaller v2.0
# Supports manifest-based selective uninstall with legacy fallback

# Note: -e intentionally omitted so uninstall continues if individual steps fail
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source utilities
source "$SCRIPT_DIR/lib/utils.sh"
source "$SCRIPT_DIR/lib/modules.sh"
source "$SCRIPT_DIR/lib/detect.sh"
source "$SCRIPT_DIR/lib/manifest.sh"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)    DRY_RUN=1 ;;
        --no-confirm) NO_CONFIRM=1 ;;
        --help|-h)
            echo "Archer Compatibility Suite - Uninstaller v${INSTALLER_VERSION}"
            echo ""
            echo "Usage: ./uninstall.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --dry-run        Show what would be removed without making changes"
            echo "  --no-confirm     Skip confirmation prompts"
            echo "  --help, -h       Show this help message"
            exit 0
            ;;
        *) warn "Unknown option: $1" ;;
    esac
    shift
done

if [[ "$DRY_RUN" -eq 1 ]]; then
    log "DRY RUN mode enabled. No changes will be made."
fi

migrate_legacy_manifest || exit 1

section "Archer Compatibility Suite - Uninstaller"

if has_manifest; then
    log "Install manifest found. Performing targeted uninstall."
    echo ""

    INSTALLED_MODS=$(read_manifest_modules) || exit 1
    remaining_mods="$INSTALLED_MODS"
    log "Installed modules: $INSTALLED_MODS"
    echo ""

    uninstall_failed=0
    for mod in $INSTALLED_MODS; do
        # Allowlist gate: reject path-traversal or arbitrary names from a
        # tampered manifest before sourcing. is_known_module enforces both
        # the safe-name regex and the canonical MODULE_IDS list.
        if ! is_known_module "$mod"; then
            warn "Refusing to source unknown module name '$mod' from manifest (skipping)."
            echo ""
            uninstall_failed=1
            continue
        fi
        local_mod_file="$SCRIPT_DIR/modules/${mod}.sh"
        if [[ -f "$local_mod_file" ]]; then
            log "Uninstalling: $mod"
            if ! source "$local_mod_file"; then
                uninstall_failed=1
                continue
            fi
            if module_uninstall; then
                next_remaining=""
                for remaining in $remaining_mods; do
                    [[ "$remaining" == "$mod" ]] || next_remaining+=" $remaining"
                done
                retain_manifest_modules "$next_remaining" || exit 1
                remaining_mods="$next_remaining"
                success "Removed: $mod"
            else
                warn "Cleanup failed for $mod; retaining the manifest for retry."
                uninstall_failed=1
            fi
        else
            warn "Module file not found: $local_mod_file (retaining cleanup record)"
            uninstall_failed=1
        fi
        echo ""
    done

    if [[ "$uninstall_failed" -ne 0 ]]; then
        exit 1
    fi
    # Remove manifest
    remove_manifest || exit 1
    log "Install manifest removed."

else
    warn "No install manifest found. Performing legacy uninstall."
    warn "This will attempt to remove all known components."
    echo ""

    # Without a manifest, use each module's ownership-aware cleanup. Do not reset
    # independently installed GPU tools or disable unrelated pre-existing services.
    legacy_failed=0
    for mod in gui battery driver thermal touchpad audio power gamemode audio-enhance camera-enhance wifi firmware; do
        source "$SCRIPT_DIR/modules/$mod.sh" || { legacy_failed=1; continue; }
        module_uninstall || legacy_failed=1
    done
    [[ "$legacy_failed" -eq 0 ]] || exit 1

fi

section "Cleanup Complete"
success "All installed components have been removed."
echo ""
warn "You may need to reboot to fully restore default driver behavior."
