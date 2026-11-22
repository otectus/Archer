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

TRANSACTION_STARTED=0
TRANSACTION_MUTATING=0
TRANSACTION_COMPLETE=0

begin_uninstall_transaction() {
    [[ "$DRY_RUN" -eq 0 ]] || return 0
    local plan approval="interactive"
    plan=$(mktemp) || return 1
    [[ "$NO_CONFIRM" -eq 0 ]] || approval="no-confirm"
    python3 - "$plan" "$1" "$INSTALLER_VERSION" <<'PY'
import json, sys
path, modules, version = sys.argv[1:]
with open(path, 'w') as stream:
    json.dump({'schema': 1, 'modules': modules.split(), 'installer_version': version,
               'operations': ['ownership-aware module cleanup', 'service restoration',
                              'boot refresh when required', 'manifest removal'],
               'rollback_limits': ['removed package versions are not automatically restored',
                                   'administrator-edited files are retained']}, stream, indent=2)
    stream.write('\n')
PY
    run_sudo python3 "$SCRIPT_DIR/scripts/archer-transaction.py" begin "$plan" \
        --approval "$approval" --kind uninstall --resume --supersede || { rm -f "$plan"; return 1; }
    rm -f "$plan"
    TRANSACTION_STARTED=1
    run_sudo python3 "$SCRIPT_DIR/scripts/archer-transaction.py" transition preflight-complete \
        --message "Manifest and module allowlist preflight passed." || return 1
    run_sudo python3 "$SCRIPT_DIR/scripts/archer-transaction.py" transition applying \
        --message "Beginning ownership-aware removal." || return 1
    TRANSACTION_MUTATING=1
}

uninstall_transaction_finish() {
    local status="$1"
    [[ "$TRANSACTION_STARTED" -eq 1 ]] || return "$status"
    if [[ "$status" -eq 0 ]]; then
        run_sudo python3 "$SCRIPT_DIR/scripts/archer-transaction.py" transition verifying --message "Removal stages completed." || return 1
        run_sudo python3 "$SCRIPT_DIR/scripts/archer-transaction.py" transition committed --message "Uninstall completed." || return 1
        TRANSACTION_COMPLETE=1
    else
        run_sudo python3 "$SCRIPT_DIR/scripts/archer-transaction.py" transition recovery-required \
            --message "Uninstall stopped after mutations. Retry cleanup; ownership records and the manifest were retained." || true
        TRANSACTION_COMPLETE=1
    fi
    return "$status"
}

trap 'status=$?; if [[ "$TRANSACTION_STARTED" -eq 1 && "$TRANSACTION_COMPLETE" -eq 0 ]]; then uninstall_transaction_finish "$status"; fi' EXIT

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
    confirm "Remove these manifest-owned Archer modules?" || exit 0
    begin_uninstall_transaction "$INSTALLED_MODS" || exit 1

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
        uninstall_transaction_finish 1
        exit 1
    fi
    # Remove manifest
    remove_manifest || exit 1
    log "Install manifest removed."
    uninstall_transaction_finish 0 || exit 1

else
    warn "No install manifest found. Broad legacy cleanup is disabled because installed packages and services are not proof of Archer ownership."
    echo ""

    legacy_modules=""
    module_state_root="${ARCHER_MODULE_STATE_ROOT:-/var/lib/archer/module-state}"
    for state_file in "$module_state_root"/*.json; do
        [[ -f "$state_file" ]] || continue
        state_module=${state_file##*/}
        state_module=${state_module%.json}
        is_known_module "$state_module" || continue
        legacy_modules+="${legacy_modules:+ }$state_module"
    done
    if [[ -z "$legacy_modules" ]]; then
        warn "No trusted Archer module-state records were found. Restore the install manifest from backup or review and remove individual resources manually."
        exit 1
    fi
    log "Recoverable owned modules: $legacy_modules"
    confirm "Remove only these module-state-owned Archer resources?" || exit 0
    begin_uninstall_transaction "$legacy_modules" || exit 1

    # Without a manifest, use each module's ownership-aware cleanup. Do not reset
    # independently installed GPU tools or disable unrelated pre-existing services.
    legacy_failed=0
    for mod in $legacy_modules; do
        source "$SCRIPT_DIR/modules/$mod.sh" || { legacy_failed=1; continue; }
        module_uninstall || legacy_failed=1
    done
    [[ "$legacy_failed" -eq 0 ]] || exit 1
    uninstall_transaction_finish 0 || exit 1

fi

section "Cleanup Complete"
success "All installed components have been removed."
echo ""
warn "You may need to reboot to fully restore default driver behavior."
