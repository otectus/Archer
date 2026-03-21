#!/usr/bin/env fish
# Archer Compatibility Suite - Unified Uninstaller v2.0 (Fish)
# Supports manifest-based selective uninstall with legacy fallback

set -g SCRIPT_DIR (realpath (dirname (status filename)))

# Source utilities
source "$SCRIPT_DIR/lib/utils.fish"
source "$SCRIPT_DIR/lib/detect.fish"
source "$SCRIPT_DIR/lib/manifest.fish"

# Parse arguments
set -l i 1
while test $i -le (count $argv)
    switch $argv[$i]
        case --dry-run
            set -g DRY_RUN 1
        case --no-confirm
            set -g NO_CONFIRM 1
        case --help -h
            echo "Archer Compatibility Suite - Uninstaller v$INSTALLER_VERSION"
            echo ""
            echo "Usage: ./uninstall.fish [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --dry-run        Show what would be removed without making changes"
            echo "  --no-confirm     Skip confirmation prompts"
            echo "  --help, -h       Show this help message"
            exit 0
        case '*'
            warn "Unknown option: $argv[$i]"
    end
    set i (math $i + 1)
end

if test "$DRY_RUN" = 1
    log "DRY RUN mode enabled. No changes will be made."
end

section "Archer Compatibility Suite - Uninstaller"

if has_manifest
    log "Install manifest found. Performing targeted uninstall."
    echo ""

    set -l installed_mods (read_manifest_modules)
    log "Installed modules: $installed_mods"
    echo ""

    for mod in (string split " " "$installed_mods")
        set -l local_mod_file "$SCRIPT_DIR/modules/$mod.fish"
        if test -f "$local_mod_file"
            log "Uninstalling: $mod"
            source "$local_mod_file"
            module_uninstall
            success "Removed: $mod"
        else
            warn "Module file not found: $local_mod_file (skipping)"
        end
        echo ""
    end

    # Remove manifest
    run rm -f "$MANIFEST_FILE"
    log "Install manifest removed."

else
    warn "No install manifest found. Performing legacy uninstall."
    warn "This will attempt to remove all known components."
    echo ""

    # Legacy uninstall: same as original v1 behavior
    # 1. Disable Services
    log "Stopping and disabling DAMX Daemon..."
    run systemctl --user disable --now damx-daemon.service 2>/dev/null; or true
    run rm -f $HOME/.config/systemd/user/damx-daemon.service
    run systemctl --user daemon-reload

    # 2. Remove Driver (DKMS)
    log "Removing Linuwu-Sense DKMS module..."
    run_sudo dkms remove -m linuwu-sense -v 1.0 --all 2>/dev/null; or true
    run_sudo rm -rf /usr/src/linuwu-sense-1.0

    # 3. Remove Blacklist
    log "Restoring acer_wmi (removing blacklist)..."
    run_sudo rm -f /etc/modprobe.d/blacklist-acer-wmi.conf

    # 4. Remove other possible configs from v2 modules
    run_sudo rm -f /etc/modprobe.d/touchpad-amd-fix.conf
    run_sudo rm -f /etc/modprobe.d/acer-audio-amd.conf
    run_sudo rm -f /etc/modprobe.d/acer-thermal-profiles.conf
    run_sudo rm -f /etc/udev/rules.d/99-acer-battery-health.rules
    run_sudo rm -f /etc/tlp.d/01-acer-optimize.conf

    # Remove touchpad service if present
    if test -f /etc/systemd/system/touchpad-fix.service
        run_sudo systemctl disable touchpad-fix.service 2>/dev/null; or true
        run_sudo rm -f /etc/systemd/system/touchpad-fix.service
        run_sudo systemctl daemon-reload
    end

    # Remove acer-wmi-battery DKMS if present
    run_sudo dkms remove -m acer-wmi-battery -v 0.1.0 --all 2>/dev/null; or true
    run_sudo rm -rf /usr/src/acer-wmi-battery-0.1.0

    # 5. Clean application files
    log "Removing DAMX files..."
    run rm -rf $HOME/.local/share/damx
end

section "Cleanup Complete"
success "All installed components have been removed."
echo ""
warn "You may need to reboot to fully restore default driver behavior."
