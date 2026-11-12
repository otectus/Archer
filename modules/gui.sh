#!/usr/bin/env bash
# Module: Archer GUI
# GTK4/Adwaita control panel and system daemon for hardware management

MODULE_NAME="Archer GUI (Control Panel)"
MODULE_ID="gui"
MODULE_DESCRIPTION="GTK4/Adwaita control panel and hardware daemon"

_GUI_INSTALL_DIR="/opt/archer"
_GUI_SERVICE="/etc/systemd/system/archer-daemon.service"
_GUI_DESKTOP="/usr/share/applications/io.github.archer.desktop"
_GUI_AUTOSTART="/etc/xdg/autostart/io.github.archer.desktop"
_GUI_ICON="/usr/share/icons/hicolor/scalable/apps/io.github.archer.svg"
_GUI_LAUNCHER="/usr/local/bin/archer-gui"
_GUI_SETTINGS_DIR="/etc/archer"

module_detect() {
    # Relevant if a display server is running (not headless/SSH)
    [[ -n "${DISPLAY:-}" ]] || [[ -n "${WAYLAND_DISPLAY:-}" ]]
}

module_check_installed() {
    [[ -f "$_GUI_INSTALL_DIR/archer_daemon.py" ]] && [[ -f "$_GUI_SERVICE" ]]
}

module_preflight() {
    # Verify all source files exist before any sudo write so a partial install
    # doesn't leave the system in a broken state.
    local _required=(
        "$SCRIPT_DIR/gui/archer_daemon.py"
        "$SCRIPT_DIR/gui/archer_dbus.py"
        "$SCRIPT_DIR/gui/archer_ene.py"
        "$SCRIPT_DIR/gui/archer_runtime.py"
        "$SCRIPT_DIR/gui/archer_system.py"
        "$SCRIPT_DIR/gui/archer_settings.py"
        "$SCRIPT_DIR/gui/archer_battery.py"
        "$SCRIPT_DIR/gui/archer_binary.py"
        "$SCRIPT_DIR/gui/archer_gui.py"
        "$SCRIPT_DIR/gui/io.otectus.Archer1.conf"
        "$SCRIPT_DIR/gui/io.otectus.Archer1.policy"
        "$SCRIPT_DIR/gui/archer-daemon.service"
        "$SCRIPT_DIR/gui/io.github.archer.desktop"
        "$SCRIPT_DIR/gui/assets/archer.svg"
        "$SCRIPT_DIR/gui/archer/__init__.py"
        "$SCRIPT_DIR/gui/archer/application.py"
        "$SCRIPT_DIR/gui/archer/autostart.py"
        "$SCRIPT_DIR/gui/archer/binary.py"
        "$SCRIPT_DIR/gui/archer/window.py"
        "$SCRIPT_DIR/gui/archer/client.py"
        "$SCRIPT_DIR/gui/archer/state.py"
        "$SCRIPT_DIR/gui/archer/preferences.py"
        "$SCRIPT_DIR/gui/archer/tray.py"
        "$SCRIPT_DIR/gui/archer/style.css"
        "$SCRIPT_DIR/gui/archer/pages/__init__.py"
        "$SCRIPT_DIR/gui/archer/pages/controls.py"
        "$SCRIPT_DIR/gui/archer/pages/overview.py"
        "$SCRIPT_DIR/gui/archer/widgets/__init__.py"
        "$SCRIPT_DIR/gui/archer/widgets/forms.py"
    )
    local _src
    for _src in "${_required[@]}"; do
        [[ -f "$_src" ]] || { warn "Required GUI source file missing: $_src"; return 1; }
    done
    [[ -d "$SCRIPT_DIR/gui/archer" ]] || { warn "Required GUI package directory missing: $SCRIPT_DIR/gui/archer"; return 1; }
    [[ -d "$SCRIPT_DIR/gui/assets" ]] || { warn "Required GUI assets directory missing: $SCRIPT_DIR/gui/assets"; return 1; }

}

module_install() {
    module_preflight || return 1
    # Install dependencies
    log "Installing GUI dependencies..."
    run_sudo pacman -S --needed --noconfirm python-gobject gtk4 libadwaita python python-cairo python-dbus polkit || return 1

    if [[ "${DRY_RUN:-0}" -eq 0 ]]; then
        if ! python3 -c 'import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1"); from gi.repository import Gtk, Adw; raise SystemExit(not ((Gtk.get_major_version(), Gtk.get_minor_version()) >= (4, 12) and (Adw.get_major_version(), Adw.get_minor_version()) >= (1, 6)))'; then
            warn "Archer requires GTK 4.12 and libadwaita 1.6 or newer. Update your system packages."
            return 1
        fi
    fi

    INSTALLED_PACKAGES+=" python-gobject gtk4 libadwaita python python-cairo python-dbus polkit"

    # Create directories
    run_sudo install -d -o root -g root -m 0755 "$_GUI_INSTALL_DIR" /etc/dbus-1/system.d /usr/share/polkit-1/actions /usr/share/applications /usr/local/bin || return 1
    INSTALLED_FILES+=" $_GUI_INSTALL_DIR"
    run_sudo mkdir -p "$_GUI_SETTINGS_DIR" || return 1
    INSTALLED_FILES+=" $_GUI_SETTINGS_DIR"

    # Copy application files
    log "Installing Archer GUI to $_GUI_INSTALL_DIR..."
    # Includes the daemon, D-Bus service, validators, ENE and system helpers.
    run_sudo cp "$SCRIPT_DIR"/gui/archer_*.py "$_GUI_INSTALL_DIR/" || return 1
    run_sudo cp "$SCRIPT_DIR/gui/io.github.archer.desktop" "$_GUI_INSTALL_DIR/" || return 1
    run_sudo cp -r "$SCRIPT_DIR/gui/archer" "$_GUI_INSTALL_DIR/" || return 1
    run_sudo cp -r "$SCRIPT_DIR/gui/assets" "$_GUI_INSTALL_DIR/" || return 1

    # Install D-Bus service configuration
    log "Installing D-Bus and polkit configuration..."
    run_sudo cp "$SCRIPT_DIR/gui/io.otectus.Archer1.conf" /etc/dbus-1/system.d/ || return 1
    INSTALLED_FILES+=" /etc/dbus-1/system.d/io.otectus.Archer1.conf"
    run_sudo cp "$SCRIPT_DIR/gui/io.otectus.Archer1.policy" /usr/share/polkit-1/actions/ || return 1
    INSTALLED_FILES+=" /usr/share/polkit-1/actions/io.otectus.Archer1.policy"

    # Reload dbus-daemon so the new policy file takes effect immediately.
    # Without this, the daemon can't claim io.otectus.Archer1 until reboot
    # (which is the symptom reported in issue #4).
    log "Reloading dbus-daemon to pick up new policy..."
    if ! run_sudo systemctl reload dbus.service 2>/dev/null; then
        # Fallback for systems where dbus.service has no reload action.
        local dbus_pid
        dbus_pid="$(pidof dbus-daemon 2>/dev/null | awk '{print $1}')"
        if [[ -n "$dbus_pid" ]]; then
            run_sudo kill -HUP "$dbus_pid" || warn "Could not signal dbus-daemon (PID $dbus_pid)."
        else
            warn "dbus-daemon not found; the new policy may require a reboot to take effect."
        fi
    fi

    # Set permissions
    run_sudo chmod 755 "$_GUI_INSTALL_DIR/archer_daemon.py" || return 1
    run_sudo chmod 755 "$_GUI_INSTALL_DIR/archer_gui.py" || return 1

    # Install systemd service
    log "Installing daemon service..."
    run_sudo cp "$SCRIPT_DIR/gui/archer-daemon.service" "$_GUI_SERVICE" || return 1
    INSTALLED_FILES+=" $_GUI_SERVICE"
    run_sudo systemctl daemon-reload || return 1
    run_sudo systemctl enable archer-daemon.service || return 1

    # Install desktop entry
    log "Installing desktop entry..."
    run_sudo cp "$SCRIPT_DIR/gui/io.github.archer.desktop" "$_GUI_DESKTOP" || return 1
    INSTALLED_FILES+=" $_GUI_DESKTOP"

    # Install icon
    run_sudo mkdir -p "$(dirname "$_GUI_ICON")" || return 1
    run_sudo cp "$SCRIPT_DIR/gui/assets/archer.svg" "$_GUI_ICON" || return 1
    INSTALLED_FILES+=" $_GUI_ICON"
    run_sudo gtk-update-icon-cache /usr/share/icons/hicolor/ 2>/dev/null || true

    # Create launcher script
    log "Creating launcher script..."
    run_sudo tee "$_GUI_LAUNCHER" > /dev/null <<'LAUNCHER_EOF' || return 1
#!/bin/bash
exec python3 /opt/archer/archer_gui.py "$@"
LAUNCHER_EOF
    INSTALLED_FILES+=" $_GUI_LAUNCHER"
    run_sudo chmod 755 "$_GUI_LAUNCHER" || return 1

    # A user Hidden=true override takes precedence and survives upgrades.
    log "Enabling GUI and system tray startup at desktop login..."
    run_sudo mkdir -p "$(dirname "$_GUI_AUTOSTART")" || return 1
    run_sudo cp "$SCRIPT_DIR/gui/io.github.archer.desktop" "$_GUI_AUTOSTART" || return 1
    INSTALLED_FILES+=" $_GUI_AUTOSTART"

    # Check for Linuwu-Sense driver
    if ! dkms status 2>/dev/null | grep -q "linuwu-sense"; then
        warn "Linuwu-Sense driver not detected. The daemon will have limited functionality."
        warn "Install the 'driver' module for full hardware control."
    fi

    if ! run_sudo systemctl restart archer-daemon.service; then
        warn "Archer daemon failed to start. Inspect journalctl -u archer-daemon; limited operation does not require Linuwu."
        return 1
    fi
    if [[ "${DRY_RUN:-0}" -eq 0 ]]; then
        module_verify || return 1
    fi
    log "Archer GUI installed."
    log "Starts at desktop login. Disable in Preferences > Start Archer at login."
    log "Start the GUI:  archer-gui"
    log "Daemon status:  sudo systemctl status archer-daemon"

    INSTALLED_FILES+=" $_GUI_INSTALL_DIR $_GUI_SERVICE $_GUI_DESKTOP $_GUI_AUTOSTART $_GUI_ICON $_GUI_LAUNCHER $_GUI_SETTINGS_DIR /etc/dbus-1/system.d/io.otectus.Archer1.conf /usr/share/polkit-1/actions/io.otectus.Archer1.policy"
    INSTALLED_PACKAGES+=" python-gobject gtk4 libadwaita python-cairo python-dbus"
}

module_uninstall() {
    log "Stopping Archer daemon..."
    if [[ -f "$_GUI_SERVICE" ]]; then
        run_sudo systemctl disable --now archer-daemon.service || return 1
    fi
    run_sudo rm -f "$_GUI_SERVICE" || return 1
    run_sudo systemctl daemon-reload || return 1

    log "Removing Archer GUI files..."
    run_sudo rm -rf "$_GUI_INSTALL_DIR" || return 1
    run_sudo rm -f "$_GUI_DESKTOP" || return 1
    run_sudo rm -f "$_GUI_AUTOSTART" || return 1
    run_sudo rm -f "$_GUI_ICON" || return 1
    run_sudo rm -f "$_GUI_LAUNCHER" || return 1
    log "Saved hardware preferences retained in $_GUI_SETTINGS_DIR."

    log "Removing D-Bus and polkit configuration..."
    run_sudo rm -f /etc/dbus-1/system.d/io.otectus.Archer1.conf || return 1
    run_sudo rm -f /usr/share/polkit-1/actions/io.otectus.Archer1.policy || return 1

    # Reload dbus-daemon so the removed policy stops being active immediately.
    if ! run_sudo systemctl reload dbus.service 2>/dev/null; then
        local dbus_pid
        dbus_pid="$(pidof dbus-daemon 2>/dev/null | awk '{print $1}')"
        if [[ -n "$dbus_pid" ]]; then
            run_sudo kill -HUP "$dbus_pid" || true
        fi
    fi

    # Clean up legacy socket/PID locations if lingering (pre-RuntimeDirectory).
    run_sudo rm -f /var/run/archer.sock 2>/dev/null || true
    run_sudo rm -f /var/run/archer-daemon.pid 2>/dev/null || true

    log "Archer GUI removed. Packages retained (remove manually with: sudo pacman -Rns python-gobject gtk4 libadwaita python-cairo)"
}

module_verify() {
    local ok=0

    if [[ ! -f "$_GUI_INSTALL_DIR/archer_daemon.py" ]]; then
        warn "Daemon not found at $_GUI_INSTALL_DIR"
        ok=1
    fi

    if ! systemctl is-enabled --quiet archer-daemon.service 2>/dev/null; then
        warn "Daemon service not enabled"
        ok=1
    fi

    if [[ ! -f "$_GUI_DESKTOP" ]]; then
        warn "Desktop entry not found"
        ok=1
    fi

    if [[ ! -f "$_GUI_AUTOSTART" ]]; then
        warn "GUI login startup entry not found"
        ok=1
    fi

    if ! systemctl is-active --quiet archer-daemon.service; then
        warn "Archer daemon is not running; inspect journalctl -u archer-daemon."
        ok=1
    fi
    if ! busctl --system --timeout=5 call io.otectus.Archer1 /io/otectus/Archer1 io.otectus.Archer1 Ping >/dev/null; then
        warn "Archer D-Bus Ping failed"
        ok=1
    fi
    if ! busctl --system --timeout=5 get-property org.freedesktop.PolicyKit1 /org/freedesktop/PolicyKit1/Authority org.freedesktop.PolicyKit1.Authority BackendName >/dev/null; then
        warn "Polkit authority is unavailable. Hardware changes require polkit and a session authentication agent."
        ok=1
    fi
    return $ok
}
