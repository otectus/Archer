#!/usr/bin/env bash
# Archer Compatibility Suite - GUI Installer
# Installs the GTK4/Adwaita GUI application and daemon service

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/archer"
SERVICE_FILE="/etc/systemd/system/archer-daemon.service"
DESKTOP_FILE="/usr/share/applications/io.github.archer.desktop"
ICON_DIR="/usr/share/icons/hicolor/scalable/apps"

# Colors
CYAN="\033[0;36m"
GREEN="\033[0;32m"
YELLOW="\033[0;33m"
RED="\033[0;31m"
RESET="\033[0m"

log()     { echo -e "${CYAN}>>>${RESET} $*"; }
success() { echo -e "${GREEN}>>>${RESET} $*"; }
warn()    { echo -e "${YELLOW}>>>${RESET} $*"; }
error()   { echo -e "${RED}>>>${RESET} $*"; exit 1; }

# Check root
if [ "$EUID" -ne 0 ]; then
    error "This script must be run as root (sudo ./install-gui.sh)"
fi

echo ""
echo -e "${CYAN}=== Archer Compatibility Suite - GUI Installer ===${RESET}"
echo ""

# Install dependencies
log "Installing system dependencies..."
pacman -S --needed --noconfirm python-gobject gtk4 libadwaita python python-pillow 2>/dev/null || {
    warn "Some packages may already be installed."
}

# Create install directory and settings directory
log "Installing Archer GUI to $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"
mkdir -p /etc/archer

# Copy files
cp -r "$SCRIPT_DIR/archer_daemon.py" "$INSTALL_DIR/"
cp -r "$SCRIPT_DIR/archer_gui.py" "$INSTALL_DIR/"
cp -r "$SCRIPT_DIR/archer" "$INSTALL_DIR/"
cp -r "$SCRIPT_DIR/assets" "$INSTALL_DIR/"

# Set permissions
chmod 755 "$INSTALL_DIR/archer_daemon.py"
chmod 755 "$INSTALL_DIR/archer_gui.py"

# Install systemd service
log "Installing daemon service..."
cp "$SCRIPT_DIR/archer-daemon.service" "$SERVICE_FILE"
systemctl daemon-reload
systemctl enable archer-daemon.service
systemctl start archer-daemon.service || warn "Daemon may not start without Linuwu-Sense driver."

# Install desktop entry
log "Installing desktop entry..."
cp "$SCRIPT_DIR/io.github.archer.desktop" "$DESKTOP_FILE"

# Install application icon
log "Installing application icon..."
mkdir -p "$ICON_DIR"
cp "$SCRIPT_DIR/assets/archer.svg" "$ICON_DIR/io.github.archer.svg"
gtk-update-icon-cache /usr/share/icons/hicolor/ 2>/dev/null || true

# Create launcher script
log "Creating launcher script..."
cat > /usr/local/bin/archer-gui << 'EOF'
#!/bin/bash
exec python3 /opt/archer/archer_gui.py "$@"
EOF
chmod 755 /usr/local/bin/archer-gui

echo ""
success "Archer GUI installed successfully!"
echo ""
log "Start the GUI:  archer-gui"
log "Daemon status:  sudo systemctl status archer-daemon"
log "Daemon logs:    sudo journalctl -u archer-daemon -f"
echo ""
