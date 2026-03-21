#!/usr/bin/env fish
# Shared utility functions for Archer Compatibility Suite (Fish)

set -g INSTALLER_VERSION "2.0.0"
set -q DRY_RUN; or set -g DRY_RUN 0
set -q NO_CONFIRM; or set -g NO_CONFIRM 0
set -g REBOOT_REQUIRED 0

function log
    echo (set_color cyan)">>> "(set_color normal)$argv
end

function warn
    echo (set_color yellow)"\u26a0  "(set_color normal)$argv
end

function error
    echo (set_color red)"\u274c "(set_color normal)$argv
    exit 1
end

function success
    echo (set_color green)"\u2705 "(set_color normal)$argv
end

# Execute a command, or print it in dry-run mode
function run
    if test "$DRY_RUN" = 1
        echo (set_color yellow)"[DRY RUN]"(set_color normal) $argv
        return 0
    end
    $argv
end

# Execute a command with sudo, or print it in dry-run mode
function run_sudo
    if test "$DRY_RUN" = 1
        echo (set_color yellow)"[DRY RUN]"(set_color normal) sudo $argv
        return 0
    end
    sudo $argv
end

# Execute a command with sudo and a timeout
# Usage: run_sudo_timeout <seconds> <command...>
function run_sudo_timeout
    set -l timeout_secs $argv[1]
    set -l cmd $argv[2..-1]
    if test "$DRY_RUN" = 1
        echo (set_color yellow)"[DRY RUN]"(set_color normal) "sudo (timeout {$timeout_secs}s)" $cmd
        return 0
    end
    timeout --signal=TERM --kill-after=30 $timeout_secs sudo $cmd
end

# Rebuild initramfs for the current kernel only (with timeout)
# Falls back to mkinitcpio -P if preset detection fails
function rebuild_initramfs
    set -l kernel_version (uname -r)
    set -l preset_base (string replace -r '\.[^.]*$' '' $kernel_version)
    set -l preset "/etc/mkinitcpio.d/$preset_base.preset"

    # Try common preset naming patterns
    if not test -f "$preset"
        set preset "/etc/mkinitcpio.d/$kernel_version.preset"
    end
    if not test -f "$preset"
        # Try matching by package ownership
        set -l found_preset ""
        for p in /etc/mkinitcpio.d/*.preset
            test -f "$p"; or continue
            set -l pname (basename "$p" .preset)
            if pacman -Qo "/usr/lib/modules/$kernel_version" 2>/dev/null | grep -qF "$pname"
                set found_preset "$p"
                break
            end
        end
        if test -n "$found_preset"
            set preset "$found_preset"
        end
    end

    # Use /usr/bin/mkinitcpio directly to avoid any wrapper scripts (e.g.
    # CachyOS/Limine wrapper at /usr/local/bin/mkinitcpio that prompts
    # interactively and hangs non-interactive subprocess calls).
    set -l mkinitcpio_bin /usr/bin/mkinitcpio

    if test -f "$preset"
        set -l preset_name (basename "$preset" .preset)
        log "Regenerating initramfs for preset '$preset_name' (timeout 5min)..."
        if not run_sudo_timeout 300 $mkinitcpio_bin -p "$preset_name"
            warn "Initramfs rebuild failed or timed out for '$preset_name'."
            warn "You may need to run 'sudo mkinitcpio -P' manually after reboot."
        end
    else
        log "Could not detect kernel preset. Regenerating all initramfs (timeout 5min)..."
        if not run_sudo_timeout 300 $mkinitcpio_bin -P
            warn "Initramfs rebuild failed or timed out."
            warn "You may need to run 'sudo mkinitcpio -P' manually after reboot."
        end
    end

    # If limine is in use, update its boot entries too
    if has_cmd limine-mkinitcpio
        log "Limine bootloader detected. Updating boot entries..."
        run_sudo_timeout 120 limine-mkinitcpio; or warn "limine-mkinitcpio failed. Run it manually if needed."
    end
end

# Prompt for confirmation (respects NO_CONFIRM)
function confirm
    set -l prompt $argv[1]
    test -z "$prompt"; and set prompt "Continue?"
    if test "$NO_CONFIRM" = 1
        return 0
    end
    read -P "$prompt [y/N]: " answer
    string match -rqi '^y' "$answer"
end

# Mark that a reboot is needed
function mark_reboot_required
    set -g REBOOT_REQUIRED 1
end

# Detect the script's own directory
function detect_script_dir
    set -l script_path (status filename)
    dirname (realpath "$script_path")/..
end

# Print a section header
function section
    echo ""
    echo (set_color --bold)"=== $argv ==="(set_color normal)
    echo ""
end

# Check if a command exists
function has_cmd
    command -v $argv[1] >/dev/null 2>&1
end

# Safely add kernel parameters to GRUB config
# Usage: add_grub_params "param1 param2"
function add_grub_params
    set -l params $argv[1]
    if test -f /etc/default/grub
        set -l current (grep "^GRUB_CMDLINE_LINUX_DEFAULT=" /etc/default/grub | sed 's/^GRUB_CMDLINE_LINUX_DEFAULT="//;s/"$//')
        set -l new_params ""
        for p in (string split " " $params)
            if not string match -q "*$p*" "$current"
                set new_params "$new_params $p"
            end
        end
        if test -n (string trim "$new_params")
            log "Adding kernel parameters to GRUB:$new_params"
            set -l updated "$new_params $current"
            set -l tmpfile (mktemp)
            run_sudo awk -v val="$updated" '/^GRUB_CMDLINE_LINUX_DEFAULT=/{print "GRUB_CMDLINE_LINUX_DEFAULT=\"" val "\""; next} {print}' \
                /etc/default/grub > $tmpfile; and run_sudo mv $tmpfile /etc/default/grub
            run_sudo grub-mkconfig -o /boot/grub/grub.cfg
        end
    else if test -d /boot/loader/entries
        log "systemd-boot detected. Please manually add these kernel parameters:"
        log "  $params"
        log "  Edit files in /boot/loader/entries/ and add to the 'options' line."
    end
end

# Safely remove kernel parameters from GRUB config
# Usage: remove_grub_params "param1 param2"
function remove_grub_params
    set -l params $argv[1]
    if test -f /etc/default/grub
        set -l needs_update 0
        for p in (string split " " $params)
            if grep -qF "$p" /etc/default/grub
                set needs_update 1
                break
            end
        end
        if test $needs_update -eq 1
            log "Removing kernel parameters from GRUB: $params"
            set -l current (grep "^GRUB_CMDLINE_LINUX_DEFAULT=" /etc/default/grub | sed 's/^GRUB_CMDLINE_LINUX_DEFAULT="//;s/"$//')
            set -l updated "$current"
            for p in (string split " " $params)
                set updated (string replace -a "$p" "" "$updated" | string trim)
            end
            set -l tmpfile (mktemp)
            run_sudo awk -v val="$updated" '/^GRUB_CMDLINE_LINUX_DEFAULT=/{print "GRUB_CMDLINE_LINUX_DEFAULT=\"" val "\""; next} {print}' \
                /etc/default/grub > $tmpfile; and run_sudo mv $tmpfile /etc/default/grub
            run_sudo grub-mkconfig -o /boot/grub/grub.cfg
        end
    end
end
