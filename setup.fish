#!/usr/bin/env fish
# Archer Compatibility Suite v2.0 (Fish)
# Comprehensive Acer laptop compatibility suite for Arch Linux
# Supports: Nitro, Predator, Helios, Triton, Swift, Aspire, and more

# --- Resolve script directory ---
set -g SCRIPT_DIR (realpath (dirname (status filename)))

# --- Source libraries ---
source "$SCRIPT_DIR/lib/utils.fish"
source "$SCRIPT_DIR/lib/detect.fish"
source "$SCRIPT_DIR/lib/manifest.fish"

# --- Module registry ---
set -g MODULE_IDS core-damx battery gpu touchpad audio wifi power thermal
set -g MODULE_LABELS \
    "DAMX Fan & RGB Control" \
    "Battery Charge Limit (80%)" \
    "GPU Switching (EnvyControl)" \
    "Touchpad Fix (I2C HID)" \
    "Audio Fix (SOF/ALSA)" \
    "WiFi/Bluetooth Troubleshooting" \
    "Power Management (TLP)" \
    "Kernel Thermal Profiles"
set -g MODULE_SELECTED 0 0 0 0 0 0 0 0
set -g INSTALLED_FILES
set -g INSTALLED_DKMS
set -g INSTALLED_PACKAGES

# --- CLI argument parsing ---
set -g SELECT_ALL_RECOMMENDED 0
set -g EXPLICIT_MODULES ""
set -g SHOW_HELP 0
set -g SHOW_VERSION 0
set -g VERIFY_ONLY 0

function parse_args
    set -l i 1
    while test $i -le (count $argv)
        switch $argv[$i]
            case --all
                set -g SELECT_ALL_RECOMMENDED 1
            case --modules
                set i (math $i + 1)
                set -g EXPLICIT_MODULES $argv[$i]
            case --no-confirm
                set -g NO_CONFIRM 1
            case --dry-run
                set -g DRY_RUN 1
            case --verify
                set -g VERIFY_ONLY 1
            case --help -h
                set -g SHOW_HELP 1
            case --version -v
                set -g SHOW_VERSION 1
            case '*'
                error "Unknown option: $argv[$i]. Use --help for usage."
        end
        set i (math $i + 1)
    end
end

function show_help
    echo "Archer Compatibility Suite v$INSTALLER_VERSION"
    echo ""
    echo "Usage: ./setup.fish [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --all            Install all recommended modules (non-interactive)"
    echo "  --modules LIST   Comma-separated list of modules to install"
    echo "  --verify         Check status of previously installed modules"
    echo "  --no-confirm     Skip all confirmation prompts"
    echo "  --dry-run        Show what would be done without making changes"
    echo "  --help, -h       Show this help message"
    echo "  --version, -v    Show version"
    echo ""
    echo "Available modules:"
    echo "  core-damx   Linuwu-Sense kernel driver and DAMX GUI for fan/RGB management"
    echo "  battery     Limits battery charging to 80% via acer-wmi-battery"
    echo "  gpu         EnvyControl for NVIDIA Optimus hybrid graphics switching"
    echo "  touchpad    Fix I2C HID touchpad detection (module reload, kernel params)"
    echo "  audio       SOF firmware, ALSA configuration, and audio codec fixes"
    echo "  wifi        Diagnose and fix WiFi/Bluetooth issues for various chipsets"
    echo "  power       TLP power management with Acer-optimized configuration"
    echo "  thermal     Native kernel thermal profiles via acer_wmi (kernel 6.8+)"
    echo ""
    echo "Interactive mode (default): Detects hardware and presents a menu."
end

# --- Menu display and interaction ---
function is_in_list
    set -l item $argv[1]
    set -l list $argv[2..-1]
    for entry in $list
        if test "$entry" = "$item"
            return 0
        end
    end
    return 1
end

function display_menu
    echo ""
    echo (set_color --bold)"=== Archer Compatibility Suite v$INSTALLER_VERSION ==="(set_color normal)
    echo ""
    print_hw_summary
    echo ""
    echo (set_color --bold)"Select modules to install:"(set_color normal)

    for i in (seq (count $MODULE_IDS))
        set -l id $MODULE_IDS[$i]
        set -l label $MODULE_LABELS[$i]
        set -l marker " "
        set -l tag ""

        # Determine tag
        if is_in_list "$id" $RECOMMENDED_MODULES
            set tag (set_color green)"[RECOMMENDED]"(set_color normal)
        else if is_in_list "$id" $OPTIONAL_MODULES
            set tag "[OPTIONAL]"
        end

        # Check conflicts
        if test "$id" = "thermal"; and test "$MODULE_SELECTED[1]" = 1
            set tag (set_color red)"[CONFLICTS WITH #1]"(set_color normal)
        end
        if test "$id" = "core-damx"; and test "$MODULE_SELECTED[8]" = 1
            set tag (set_color red)"[CONFLICTS WITH #8]"(set_color normal)
        end

        # Selection marker
        if test "$MODULE_SELECTED[$i]" = 1
            set marker (set_color green)"*"(set_color normal)
        end

        printf "  [%s] %d. %-40s %s\n" "$marker" $i "$label" "$tag"
    end

    echo ""
    echo "Toggle: Enter number (1-"(count $MODULE_IDS)") | a=all recommended | n=none | c=confirm"
end

function init_selections
    set -g MODULE_SELECTED
    for i in (seq (count $MODULE_IDS))
        set -l id $MODULE_IDS[$i]
        if is_in_list "$id" $RECOMMENDED_MODULES
            set -a MODULE_SELECTED 1
        else
            set -a MODULE_SELECTED 0
        end
    end
end

function check_conflicts
    # core-damx (#1) and thermal (#8) conflict (fish is 1-indexed)
    if test "$MODULE_SELECTED[1]" = 1; and test "$MODULE_SELECTED[8]" = 1
        warn "DAMX (Linuwu-Sense) and Kernel Thermal Profiles both selected."
        warn "These conflict: Linuwu-Sense blacklists acer_wmi, which thermal profiles require."
        warn "Please deselect one of them."
        return 1
    end
    return 0
end

function run_menu
    while true
        display_menu
        read -P "> " choice

        switch "$choice"
            case 1 2 3 4 5 6 7 8
                set -l idx $choice
                if test "$MODULE_SELECTED[$idx]" = 1
                    set MODULE_SELECTED[$idx] 0
                else
                    set MODULE_SELECTED[$idx] 1
                end
            case a A
                init_selections
            case n N
                for i in (seq (count $MODULE_SELECTED))
                    set MODULE_SELECTED[$i] 0
                end
            case c C
                if check_conflicts
                    break
                end
            case '*'
                warn "Invalid input. Enter 1-"(count $MODULE_IDS)", a, n, or c."
        end
    end
end

# --- Module execution ---
function install_shared_deps
    log "Installing shared system dependencies..."
    set -l deps base-devel dkms git curl $KERNEL_HEADERS python-pip

    # Clang-built kernels require clang/llvm toolchain for DKMS module compilation
    if test "$IS_CLANG_KERNEL" = 1
        log "Clang-built kernel detected — including LLVM toolchain for DKMS builds."
        set -a deps clang llvm
    end

    run_sudo pacman -Syu --needed --noconfirm $deps
end

function run_selected_modules
    set -l selected_names

    for i in (seq (count $MODULE_IDS))
        if test "$MODULE_SELECTED[$i]" = 1
            set -l id $MODULE_IDS[$i]
            set -l label $MODULE_LABELS[$i]
            set -a selected_names $id

            section "Installing: $label"
            source "$SCRIPT_DIR/modules/$id.fish"
            if not module_install
                warn "Module $id failed to install. Continuing..."
                continue
            end
        end
    end

    # Write manifest
    set -l modules_joined (string join " " $selected_names)
    set -l files_joined (string join " " $INSTALLED_FILES)
    set -l dkms_joined (string join " " $INSTALLED_DKMS)
    set -l pkgs_joined (string join " " $INSTALLED_PACKAGES)
    write_manifest "$modules_joined" "$files_joined" "$dkms_joined" "$pkgs_joined"
end

function verify_modules
    section "Verification"
    set -l total 0
    set -l passed 0

    for i in (seq (count $MODULE_IDS))
        if test "$MODULE_SELECTED[$i]" = 1
            set -l id $MODULE_IDS[$i]
            set -l label $MODULE_LABELS[$i]
            set total (math $total + 1)

            # Re-source to restore this module's function definitions (each module
            # overrides the same names: module_verify, module_install, etc.)
            source "$SCRIPT_DIR/modules/$id.fish"
            if module_verify
                success "$label"
                set passed (math $passed + 1)
            else
                warn "$label (check warnings above)"
            end
        end
    end

    echo ""
    log "Verification: $passed/$total modules passed"
end

# --- Main entry point ---
function main
    parse_args $argv

    if test "$SHOW_VERSION" = 1
        echo "Archer Compatibility Suite v$INSTALLER_VERSION"
        exit 0
    end
    if test "$SHOW_HELP" = 1
        show_help
        exit 0
    end

    # Standalone verify mode: check installed modules from manifest
    if test "$VERIFY_ONLY" = 1
        if not has_manifest
            error "No install manifest found. Nothing to verify."
        end
        section "Verifying Installed Modules"
        set -l mods (read_manifest_modules)
        set -l total 0
        set -l passed 0
        for id in (string split " " "$mods")
            set -l mod_file "$SCRIPT_DIR/modules/$id.fish"
            if test -f "$mod_file"
                set total (math $total + 1)
                source "$mod_file"
                if module_verify
                    success "$MODULE_NAME"
                    set passed (math $passed + 1)
                else
                    warn "$MODULE_NAME (check warnings above)"
                end
            else
                warn "Module file not found: $mod_file"
            end
        end
        echo ""
        log "Verification: $passed/$total modules passed"
        exit 0
    end

    if test "$DRY_RUN" = 1
        log "DRY RUN mode enabled. No changes will be made."
    end

    # Run hardware detection
    log "Detecting hardware..."
    detect_all
    build_recommendations

    # Vendor check
    if not string match -q '*Acer*' "$ACER_SYS_VENDOR"; and test "$ACER_PRODUCT_NAME" = "Unknown"
        warn "This does not appear to be an Acer system."
        warn "Some modules may not function correctly."
    end

    # Distro check
    if test "$DISTRO_FAMILY" != "arch"
        warn "This installer is designed for Arch-based distributions."
        warn "Detected: $DISTRO_NAME (family: $DISTRO_FAMILY)"
        confirm "Continue anyway?"; or exit 0
    end

    # Initialize selections
    init_selections

    # Handle non-interactive modes
    if test -n "$EXPLICIT_MODULES"
        # Reset all, then select explicit modules
        for i in (seq (count $MODULE_SELECTED))
            set MODULE_SELECTED[$i] 0
        end
        for mod in (string split "," "$EXPLICIT_MODULES")
            set -l found 0
            for i in (seq (count $MODULE_IDS))
                if test "$MODULE_IDS[$i]" = "$mod"
                    set MODULE_SELECTED[$i] 1
                    set found 1
                end
            end
            if test $found -eq 0
                warn "Unknown module: '$mod' (available: $MODULE_IDS)"
            end
        end
        if not check_conflicts
            error "Module conflict detected. Aborting."
        end
    else if test "$SELECT_ALL_RECOMMENDED" = 1
        # Already initialized with recommendations
    else
        # Interactive menu
        run_menu
    end

    # Count selected modules
    set -l count 0
    for sel in $MODULE_SELECTED
        set count (math $count + $sel)
    end
    if test $count -eq 0
        log "No modules selected. Nothing to install."
        exit 0
    end

    # Display selected modules
    section "Installation Plan"
    for i in (seq (count $MODULE_IDS))
        if test "$MODULE_SELECTED[$i]" = 1
            echo "  - $MODULE_LABELS[$i]"
        end
    end
    echo ""

    if test "$NO_CONFIRM" = 0; and test "$SELECT_ALL_RECOMMENDED" = 0; and test -z "$EXPLICIT_MODULES"
        confirm "Proceed with installation?"; or exit 0
    end

    # Install shared dependencies
    install_shared_deps

    # Run selected modules
    run_selected_modules

    # Verify
    verify_modules

    # Final summary
    section "Installation Complete"
    success "All selected modules have been installed."

    if test "$REBOOT_REQUIRED" = 1
        echo ""
        warn "A reboot is required for some changes to take effect."
    end

    echo ""
    log "Manifest saved to: $MANIFEST_FILE"
    log "To uninstall, run: ./uninstall.fish"
end

main $argv
