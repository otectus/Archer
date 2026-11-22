#!/usr/bin/env bash
# Archer Compatibility Suite v2.0
# Comprehensive Acer laptop compatibility suite for Arch Linux
# Supports: Nitro, Predator, Helios, Triton, Swift, Aspire, and more

set -euo pipefail

# --- Resolve script directory ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Source libraries ---
source "$SCRIPT_DIR/lib/utils.sh"
source "$SCRIPT_DIR/lib/modules.sh"
source "$SCRIPT_DIR/lib/detect.sh"
source "$SCRIPT_DIR/lib/manifest.sh"

# --- Module registry (defined in lib/modules.sh) ---
MODULE_SELECTED=()
INSTALLED_FILES=""
INSTALLED_DKMS=""
INSTALLED_PACKAGES=""

# --- CLI argument parsing ---
SELECT_ALL_RECOMMENDED=0
EXPLICIT_MODULES=""
SHOW_HELP=0
SHOW_VERSION=0
VERIFY_ONLY=0
TRANSACTION_STARTED=0
TRANSACTION_MUTATING=0
TRANSACTION_COMPLETE=0
TRANSACTION_ID=""

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --all)        SELECT_ALL_RECOMMENDED=1 ;;
            --modules)    EXPLICIT_MODULES="$2"; shift ;;
            --no-confirm) NO_CONFIRM=1 ;;
            --dry-run)    DRY_RUN=1 ;;
            --verbose)    VERBOSE=1 ;;
            --log)        LOG_FILE="$2"; shift ;;
            --verify)     VERIFY_ONLY=1 ;;
            --help|-h)    SHOW_HELP=1 ;;
            --version|-v) SHOW_VERSION=1 ;;
            *) error "Unknown option: $1. Use --help for usage." ;;
        esac
        shift
    done
}

show_help() {
    echo "Archer Compatibility Suite v${INSTALLER_VERSION}"
    echo ""
    echo "Usage: ./install.sh [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --all            Install all recommended modules (non-interactive)"
    echo "  --modules LIST   Comma-separated list of modules to install"
    echo "  --verify         Check status of previously installed modules"
    echo "  --no-confirm     Skip all confirmation prompts"
    echo "  --dry-run        Show what would be done without making changes"
    echo "  --verbose        Enable debug output for troubleshooting"
    echo "  --log FILE       Write all output to FILE (in addition to console)"
    echo "  --help, -h       Show this help message"
    echo "  --version, -v    Show version"
    echo ""
    echo "Available modules:"
    local i
    for i in "${!MODULE_IDS[@]}"; do
        printf "  %-15s %s\n" "${MODULE_IDS[$i]}" "${MODULE_LABELS[$i]}"
    done
    echo ""
    echo "Interactive mode (default): Detects hardware and presents a menu."
}

begin_install_transaction() {
    INVENTORY_GENERATION=$(python3 "$SCRIPT_DIR/gui/archer_inventory.py" generation 2>/dev/null) || INVENTORY_GENERATION="unavailable"
    [[ "$DRY_RUN" -eq 0 ]] || return 0
    local plan init_name selected="" approval="interactive"
    plan=$(mktemp) || return 1
    for i in "${!MODULE_IDS[@]}"; do
        [[ "${MODULE_SELECTED[$i]}" -eq 1 ]] && selected+="${selected:+,}${MODULE_IDS[$i]}"
    done
    init_name=$(cat /proc/1/comm 2>/dev/null || printf unknown)
    [[ "$NO_CONFIRM" -eq 0 ]] || approval="no-confirm"
    python3 - "$plan" "$selected" "$INSTALLER_VERSION" "${DISTRO_ID:-unknown}" \
        "${DISTRO_NAME:-unknown}" "$init_name" "$INVENTORY_GENERATION" "$SCRIPT_DIR" <<'PY'
import hashlib, json, subprocess, sys
from pathlib import Path
path, modules, version, distro_id, distro_name, init_name, generation, root = sys.argv[1:]
boot = subprocess.run([sys.executable, root + '/scripts/archer-boot.py', 'inspect'],
                      capture_output=True, text=True)
boot_report = json.loads(boot.stdout) if boot.returncode == 0 else {'error': boot.stderr.strip()}
resources = []
for resource in ('/etc/default/grub', '/var/lib/archer/install-manifest.json',
                 root + '/driver/linuwu-sense/source.conf',
                 root + '/driver/acer-wmi-battery/source.conf'):
    item = {'path': resource, 'sha256': None}
    try:
        item['sha256'] = hashlib.sha256(Path(resource).read_bytes()).hexdigest()
    except OSError:
        pass
    resources.append(item)
plan = {'schema': 1, 'modules': modules.split(',') if modules else [],
        'installer_version': version, 'inventory_generation': generation,
        'environment': {'distribution_id': distro_id, 'distribution_name': distro_name,
                        'init': init_name}, 'boot': boot_report,
        'operations': ['full package upgrade', 'selected module changes',
                       'boot refresh when required', 'post-change verification'],
        'resources_before': resources,
        'rollback_limits': ['package downgrades are not automatic',
                            'administrator-edited files are retained']}
with open(path, 'w') as stream:
    json.dump(plan, stream, indent=2)
    stream.write('\n')
PY
    TRANSACTION_ID=$(run_sudo python3 "$SCRIPT_DIR/scripts/archer-transaction.py" begin "$plan" --approval "$approval" --kind install --resume) || {
        rm -f "$plan"; return 1;
    }
    rm -f "$plan"
    log "Transaction journal: /var/lib/archer/transactions/${TRANSACTION_ID}.json"
    TRANSACTION_STARTED=1
}

transaction_transition() {
    [[ "$TRANSACTION_STARTED" -eq 1 ]] || return 0
    run_sudo python3 "$SCRIPT_DIR/scripts/archer-transaction.py" transition "$1" --message "${2:-}"
}

transaction_step() {
    [[ "$TRANSACTION_STARTED" -eq 1 ]] || return 0
    local resource="${4:-}"
    if [[ -n "$resource" ]]; then
        run_sudo python3 "$SCRIPT_DIR/scripts/archer-transaction.py" step "$1" "$2" --message "${3:-}" --resource "$resource"
    else
        run_sudo python3 "$SCRIPT_DIR/scripts/archer-transaction.py" step "$1" "$2" --message "${3:-}"
    fi
}

transaction_abort() {
    local status="$1"
    [[ "$status" -ne 0 && "$TRANSACTION_STARTED" -eq 1 && "$TRANSACTION_COMPLETE" -eq 0 ]] || return 0
    if [[ "$TRANSACTION_MUTATING" -eq 1 ]]; then
        transaction_transition recovery-required "Installation stopped after mutations began. Inspect the journal, manifest, package state, DKMS state, and boot artifacts before retrying." || true
    else
        transaction_transition failed "Installation stopped before mutation; correct preflight failures and retry." || true
    fi
}

# --- Menu display and interaction ---
display_menu() {
    echo ""
    echo -e "${_BOLD}=== Archer Compatibility Suite v${INSTALLER_VERSION} ===${_RESET}"
    echo ""
    print_hw_summary
    echo ""
    echo -e "${_BOLD}Select modules to install:${_RESET}"

    for i in "${!MODULE_IDS[@]}"; do
        local id="${MODULE_IDS[$i]}"
        local label="${MODULE_LABELS[$i]}"
        local num=$((i + 1))
        local marker=" "
        local tag=""

        # Determine tag
        if is_in_list "$id" "${RECOMMENDED_MODULES[*]}"; then
            tag="${_GREEN}[RECOMMENDED]${_RESET}"
        elif is_in_list "$id" "${OPTIONAL_MODULES[*]}"; then
            tag="[OPTIONAL]"
        fi

        # GUI dependency hint
        if [[ "$id" = "gui" ]] && [[ "${MODULE_SELECTED[0]}" -eq 0 ]]; then
            tag="${tag} ${_YELLOW}(needs #1 Linuwu-Sense)${_RESET}"
        fi

        # Selection marker
        if [[ "${MODULE_SELECTED[$i]}" -eq 1 ]]; then
            marker="${_GREEN}*${_RESET}"
        fi

        printf "  [%b] %d. %-40s %b\n" "$marker" "$num" "$label" "$tag"
    done

    echo ""
    echo "Toggle: Enter number (1-${#MODULE_IDS[@]}) | a=all recommended | n=none | c=confirm"
}

is_in_list() {
    local item="$1"
    local list="$2"
    [[ " $list " == *" $item "* ]]
}

init_selections() {
    MODULE_SELECTED=()
    for i in "${!MODULE_IDS[@]}"; do
        local id="${MODULE_IDS[$i]}"
        if is_in_list "$id" "${RECOMMENDED_MODULES[*]}"; then
            MODULE_SELECTED+=(1)
        else
            MODULE_SELECTED+=(0)
        fi
    done
}

check_conflicts() {
    # The thermal module reuses native/Linuwu profiles without loading acer_wmi.

    return 0
}

run_menu() {
    while true; do
        display_menu
        read -rp "> " choice

        case "$choice" in
            [1-9]|[1-9][0-9])
                if [[ "$choice" -le "${#MODULE_IDS[@]}" ]] 2>/dev/null; then
                    local idx=$((choice - 1))
                    if [[ "${MODULE_SELECTED[$idx]}" -eq 1 ]]; then
                        MODULE_SELECTED[idx]=0
                    else
                        MODULE_SELECTED[idx]=1
                    fi
                else
                    warn "Invalid number. Enter 1-${#MODULE_IDS[@]}, a, n, or c."
                fi
                ;;
            a|A)
                init_selections
                ;;
            n|N)
                for i in "${!MODULE_SELECTED[@]}"; do
                    MODULE_SELECTED[i]=0
                done
                ;;
            c|C)
                if check_conflicts; then
                    break
                fi
                ;;
            *)
                warn "Invalid input. Enter 1-${#MODULE_IDS[@]}, a, n, or c."
                ;;
        esac
    done
}

# --- Module execution ---
# kernel_battery_limit_path and kernel_collect_targets both take an optional
# first argument used only to point them at a mock root from the tests. Calling
# them bare here is correct, but shellcheck reads the bare call as a mistake
# (SC2119) once -x pulls in lib/kernel.sh and it sees the ${1:-...} default.
# shellcheck disable=SC2119
install_shared_deps() {
    log "Installing shared system dependencies..."
    local deps=(base-devel dkms git curl python-pip) path package
    local need_kernels=0
    for i in "${!MODULE_IDS[@]}"; do
        if [[ "${MODULE_SELECTED[$i]}" -eq 1 ]]; then
            case "${MODULE_IDS[$i]}" in
                driver|camera-enhance|gpu) need_kernels=1 ;;
                battery) kernel_battery_limit_path >/dev/null || need_kernels=1 ;;
            esac
        fi
    done
    if [[ "$need_kernels" -eq 1 ]]; then
        for path in "$KERNEL_MODULES_ROOT"/*; do
            [[ -f "$path/vmlinuz" || -d "$path/kernel" || -f "$path/build/Makefile" ]] || continue
            if package=$(kernel_header_package "${path##*/}"); then
                if pacman -Si "$package" &>/dev/null || pacman -Qi "$package" &>/dev/null; then
                    [[ " ${deps[*]} " == *" $package "* ]] || deps+=("$package")
                else
                    warn "Header package $package is unavailable; exact prepared headers are required at $path/build."
                fi
            fi
        done
        # A package upgrade can change the compiler; include both toolchains.
        deps+=(clang llvm)
    fi
    run_sudo pacman -Syu --needed --noconfirm "${deps[@]}" || return 1
    if [[ "$need_kernels" -eq 1 && "${DRY_RUN:-0}" -eq 0 ]]; then
        kernel_collect_targets || return 1
    fi
}

run_selected_modules() {
    local installed_names=() failed=0 previous previous_modules
    SKIPPED_MODULES=""
    # A driver-only update must not discard cleanup records for other modules.
    if has_manifest; then
        previous_modules=$(read_manifest_modules) || return 1
        for previous in $previous_modules; do
            installed_names+=("$previous")
        done
        INSTALLED_FILES="$(read_manifest_field files_created)" || return 1
        INSTALLED_DKMS="$(read_manifest_field dkms_modules)" || return 1
        INSTALLED_PACKAGES="$(read_manifest_field packages_installed)" || return 1
    fi

    for i in "${!MODULE_IDS[@]}"; do
        if [[ "${MODULE_SELECTED[$i]}" -eq 1 ]]; then
            local id="${MODULE_IDS[$i]}"
            local label="${MODULE_LABELS[$i]}"

            # GUI dependency warning (driver-or-already-installed check)
            if [[ "$id" = "gui" ]]; then
                if ! is_in_list "driver" "${installed_names[*]}" && \
                   ! dkms status 2>/dev/null | grep -q "linuwu-sense"; then
                    warn "Linuwu-Sense driver not installed. The daemon will have limited functionality."
                    warn "Consider installing the 'driver' module for full hardware control."
                fi
            fi

            section "Installing: $label"
            # Retain cleanup records for completed steps even if a later step fails.
            local _files_pre="$INSTALLED_FILES"
            local _dkms_pre="$INSTALLED_DKMS"
            local _pkgs_pre="$INSTALLED_PACKAGES"

            source "$SCRIPT_DIR/modules/${id}.sh" || { failed=1; continue; }
            MODULE_SKIPPED=0
            if ! module_install; then
                warn "Module $id failed to install. Retaining cleanup records for completed steps; continuing with remaining modules."
                if [[ "$INSTALLED_FILES" != "$_files_pre" || "$INSTALLED_DKMS" != "$_dkms_pre" || "$INSTALLED_PACKAGES" != "$_pkgs_pre" ]]; then
                    is_in_list "$id" "${installed_names[*]}" || installed_names+=("$id")
                fi
                failed=1
                continue
            fi
            if [[ "$MODULE_SKIPPED" -eq 1 ]]; then
                SKIPPED_MODULES+=" $id"
                continue
            fi
            if ! is_in_list "$id" "${installed_names[*]}"; then
                installed_names+=("$id")
            fi
            if [[ "$id" == driver ]]; then
                local entry retained=""
                for entry in $INSTALLED_DKMS; do
                    if [[ "$entry" != linuwu-sense/* || "$entry" == "$DKMS_NAME/$DKMS_VERSION" ]]; then
                        retained+=" $entry"
                    fi
                done
                INSTALLED_DKMS="$retained"
            fi
        fi
    done

    # Preserve both successful modules and partial changes needing cleanup.
    local modules_joined="${installed_names[*]}"
    if [[ -z "$modules_joined" ]]; then
        warn "No modules installed successfully — skipping manifest write."
        return "$failed"
    fi
    write_manifest "$modules_joined" "$INSTALLED_FILES" "$INSTALLED_DKMS" "$INSTALLED_PACKAGES" || return 1
    return "$failed"
}

verify_modules() {
    section "Verification"
    local total=0
    local passed=0

    for i in "${!MODULE_IDS[@]}"; do
        if [[ "${MODULE_SELECTED[$i]}" -eq 1 ]]; then
            local id="${MODULE_IDS[$i]}"
            local label="${MODULE_LABELS[$i]}"
            if is_in_list "$id" "${SKIPPED_MODULES:-}"; then
                log "$label was skipped."
                continue
            fi
            total=$((total + 1))

            # Re-source to restore this module's function definitions (each module
            # overrides the same names: module_verify, module_install, etc.)
            source "$SCRIPT_DIR/modules/${id}.sh" || continue
            if module_verify; then
                success "$label"
                passed=$((passed + 1))
            else
                warn "$label (check warnings above)"
            fi
        fi
    done

    echo ""
    log "Verification: $passed/$total modules passed"
    [[ "$passed" -eq "$total" ]]
}

# --- Main entry point ---
main() {
    parse_args "$@"

    if [[ "$SHOW_VERSION" -eq 1 ]]; then
        echo "Archer Compatibility Suite v${INSTALLER_VERSION}"
        exit 0
    fi
    if [[ "$SHOW_HELP" -eq 1 ]]; then
        show_help
        exit 0
    fi

    if [[ "$DRY_RUN" -eq 0 && "$VERIFY_ONLY" -eq 0 ]]; then
        local early_init
        early_init=$(detect_init_adapter)
        if [[ "$early_init" == unsupported ]]; then
            warn "No supported init adapter is active (systemd and OpenRC are implemented)."
            return 1
        fi
        if [[ "$early_init" != systemd && -z "$EXPLICIT_MODULES" ]]; then
            warn "$early_init installations require an explicit --modules list so unsupported service integrations cannot be selected."
            return 1
        fi
        if [[ "$early_init" != systemd ]]; then
            local early_module
            IFS=',' read -ra early_modules <<< "$EXPLICIT_MODULES"
            for early_module in "${early_modules[@]}"; do
                if ! init_module_supported "$early_init" "$early_module"; then
                    warn "Module '$early_module' has no complete $early_init lifecycle adapter. No changes were made."
                    return 1
                fi
            done
        fi
    fi

    # Move any legacy manifest into the new system path before any read.
    migrate_legacy_manifest || return 1

    # Standalone verify mode: check installed modules from manifest
    if [[ "$VERIFY_ONLY" -eq 1 ]]; then
        if ! has_manifest; then
            error "No install manifest found. Nothing to verify."
        fi
        section "Verifying Installed Modules"
        local journal_state=""
        if [[ "$DRY_RUN" -eq 0 ]]; then
            journal_state=$(run_sudo python3 "$SCRIPT_DIR/scripts/archer-transaction.py" show 2>/dev/null |
                python3 -c 'import json,sys; print(json.load(sys.stdin).get("state", ""))' 2>/dev/null) || journal_state=""
            if [[ "$journal_state" == pending-restart ]]; then
                TRANSACTION_STARTED=1
                TRANSACTION_MUTATING=1
                transaction_transition verifying "Post-reboot verification started." || return 1
            fi
        fi
        local mods
        mods=$(read_manifest_modules) || return 1
        local total=0
        local passed=0
        for id in $mods; do
            total=$((total + 1))
            if ! is_known_module "$id"; then
                warn "Manifest references unknown module '$id' — skipping (refusing to source untrusted name)."
                continue
            fi
            local mod_file="$SCRIPT_DIR/modules/${id}.sh"
            if [[ -f "$mod_file" ]]; then
                source "$mod_file"
                if module_verify; then
                    success "$MODULE_NAME"
                    passed=$((passed + 1))
                else
                    warn "$MODULE_NAME (check warnings above)"
                fi
            else
                warn "Module file not found: $mod_file"
            fi
        done
        echo ""
        log "Verification: $passed/$total modules passed"
        if [[ "$passed" -eq "$total" ]]; then
            if [[ "$TRANSACTION_STARTED" -eq 1 ]]; then
                transaction_transition committed "Post-reboot verification passed." || return 1
                TRANSACTION_COMPLETE=1
            fi
            return 0
        fi
        return 1
    fi

    if [[ "$DRY_RUN" -eq 1 ]]; then
        log "DRY RUN mode enabled. No changes will be made."
    fi

    # Run hardware detection
    log "Detecting hardware..."
    detect_all || return 1
    build_recommendations

    # Vendor check
    if [[ "$ACER_SYS_VENDOR" != *"Acer"* ]] && [[ "$ACER_PRODUCT_NAME" == "Unknown" ]]; then
        warn "This does not appear to be an Acer system."
        warn "Some modules may not function correctly."
    fi

    # Distro check
    if [[ "$DISTRO_FAMILY" != "arch" ]]; then
        warn "This installer is designed for Arch-based distributions."
        warn "Detected: ${DISTRO_NAME:-Unknown} (family: $DISTRO_FAMILY)"
        confirm "Continue anyway?" || exit 0
    fi

    # Initialize selections
    init_selections

    # Handle non-interactive modes
    if [[ -n "$EXPLICIT_MODULES" ]]; then
        # Reset all, then select explicit modules
        for i in "${!MODULE_SELECTED[@]}"; do
            MODULE_SELECTED[i]=0
        done
        IFS=',' read -ra explicit_list <<< "$EXPLICIT_MODULES"
        for mod in "${explicit_list[@]}"; do
            if ! is_known_module "$mod"; then
                error "Unknown or invalid module: '$mod' (available: ${MODULE_IDS[*]})"
            fi
            for i in "${!MODULE_IDS[@]}"; do
                if [[ "${MODULE_IDS[$i]}" = "$mod" ]]; then
                    MODULE_SELECTED[i]=1
                fi
            done
        done
        if ! check_conflicts; then
            error "Module conflict detected. Aborting."
        fi
    elif [[ "$SELECT_ALL_RECOMMENDED" -eq 1 ]]; then
        # Already initialized with recommendations
        :
    else
        # Interactive menu
        run_menu
    fi

    # Count selected modules
    local count=0
    for sel in "${MODULE_SELECTED[@]}"; do
        count=$((count + sel))
    done
    if [[ "$count" -eq 0 ]]; then
        log "No modules selected. Nothing to install."
        exit 0
    fi

    # Display selected modules
    section "Installation Plan"
    for i in "${!MODULE_IDS[@]}"; do
        if [[ "${MODULE_SELECTED[$i]}" -eq 1 ]]; then
            echo "  - ${MODULE_LABELS[$i]}"
        fi
    done
    echo ""

    if [[ "$NO_CONFIRM" -eq 0 ]] && [[ "$SELECT_ALL_RECOMMENDED" -eq 0 ]] && [[ -z "$EXPLICIT_MODULES" ]]; then
        confirm "Proceed with installation?" || exit 0
    fi

    begin_install_transaction || return 1

    # Validate selected source/preconditions and the runtime before any packages change.
    if [[ "$DRY_RUN" -eq 0 ]]; then
        local active_init
        active_init=$(detect_init_adapter)
        for i in "${!MODULE_IDS[@]}"; do
            [[ "${MODULE_SELECTED[$i]}" -eq 1 ]] || continue
            if ! init_module_supported "$active_init" "${MODULE_IDS[$i]}"; then
                warn "Module '${MODULE_IDS[$i]}' has no complete $active_init lifecycle adapter."
                return 1
            fi
        done
    fi
    if has_manifest; then
        read_manifest_modules >/dev/null || return 1
    fi
    for i in "${!MODULE_IDS[@]}"; do
        [[ "${MODULE_SELECTED[$i]}" -eq 1 ]] || continue
        unset -f module_preflight
        source "$SCRIPT_DIR/modules/${MODULE_IDS[$i]}.sh" || return 1
        if declare -F module_preflight >/dev/null; then
            module_preflight || return 1
        fi
    done
    transaction_transition preflight-complete "Inventory, manifest, runtime and selected module preflights passed." || return 1

    # Install shared dependencies
    transaction_transition applying "Beginning package and module mutations." || return 1
    TRANSACTION_MUTATING=1
    check_grub_ownership || return 1
    install_shared_deps || return 1
    transaction_step shared-dependencies ok "Full-upgrade dependency transaction completed." \
        "inventory-generation=$INVENTORY_GENERATION" || return 1

    # A full upgrade can change drivers and exposed interfaces. Re-scan before
    # any feature mutation and rerun selected module preflights against it.
    local post_upgrade_generation
    detect_all || return 1
    post_upgrade_generation=$(python3 "$SCRIPT_DIR/gui/archer_inventory.py" generation 2>/dev/null) || return 1
    if [[ "$post_upgrade_generation" != "$INVENTORY_GENERATION" ]]; then
        transaction_step inventory-rescan ok "Inventory generation changed after the full upgrade; selected module preflights were re-resolved." || return 1
        INVENTORY_GENERATION="$post_upgrade_generation"
    else
        transaction_step inventory-rescan ok "Inventory generation remained stable after the full upgrade." || return 1
    fi
    for i in "${!MODULE_IDS[@]}"; do
        [[ "${MODULE_SELECTED[$i]}" -eq 1 ]] || continue
        unset -f module_preflight
        source "$SCRIPT_DIR/modules/${MODULE_IDS[$i]}.sh" || return 1
        if declare -F module_preflight >/dev/null; then
            module_preflight || return 1
        fi
    done

    # Run selected modules
    local installation_failed=0
    run_selected_modules || installation_failed=1
    local manifest_hash="absent"
    [[ ! -r "$MANIFEST_FILE" ]] || manifest_hash=$(sha256sum "$MANIFEST_FILE" | awk '{print $1}')
    transaction_step selected-modules "$([[ "$installation_failed" -eq 0 ]] && echo ok || echo failed)" \
        "Selected module stage completed; consult the manifest for retained partial changes." \
        "$MANIFEST_FILE=$manifest_hash" || return 1

    # Verify
    if [[ "$DRY_RUN" -eq 0 && "$REBOOT_REQUIRED" -eq 0 ]]; then
        transaction_transition verifying "Running current-session module verification." || return 1
        verify_modules || installation_failed=1
    fi

    # Final summary
    section "Installation Complete"
    if [[ "$installation_failed" -eq 1 ]]; then
        warn "Some modules failed installation or verification; see errors above. Existing manifest records were retained."
        return 1
    fi
    if [[ "$REBOOT_REQUIRED" -eq 1 ]]; then
        transaction_transition pending-restart "Changes require reboot. Run ./install.sh --verify after reboot to commit this transaction." || return 1
    else
        transaction_transition committed "Current-session installation and verification completed." || return 1
    fi
    TRANSACTION_COMPLETE=1
    success "All selected modules have been installed."

    if [[ "$REBOOT_REQUIRED" -eq 1 ]]; then
        echo ""
        warn "A reboot is required for some changes to take effect."
    fi

    echo ""
    log "Manifest saved to: $MANIFEST_FILE"
    log "To uninstall, run: ./uninstall.sh"
}

# Only run main when executed directly (not when sourced for testing)
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    trap 'transaction_abort $?' EXIT
    main "$@"
fi
