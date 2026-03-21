#!/usr/bin/env bash
# Module: Core DAMX - Linuwu-Sense kernel driver + DAMX GUI daemon
# This is the original installer functionality extracted into the module interface.

MODULE_NAME="DAMX Fan & RGB Control"
MODULE_ID="core-damx"
MODULE_DESCRIPTION="Linuwu-Sense kernel driver and DAMX GUI for fan/RGB management"

REPO_DRIVER="https://github.com/0x7375646F/Linuwu-Sense.git"
REPO_APP="PXDiv/Div-Acer-Manager-Max"
DRIVER_MODULE="linuwu_sense"
DKMS_NAME="linuwu-sense"
DKMS_VERSION="1.0"
_DAMX_INSTALL_DIR="$HOME/.local/share/damx"

module_detect() {
    # Relevant for gaming Acer models; usable on others with warning
    case "$MODEL_FAMILY" in
        nitro|predator|helios|triton) return 0 ;;
        *) return 1 ;;
    esac
}

module_check_installed() {
    # Check if Linuwu-Sense DKMS module is installed and daemon is running
    if dkms status 2>/dev/null | grep -q "$DKMS_NAME"; then
        return 0
    fi
    return 1
}

module_install() {
    local src_dir="/usr/src/${DKMS_NAME}-${DKMS_VERSION}"

    # --- Kernel Module (Linuwu-Sense) via DKMS ---
    log "Setting up $DRIVER_MODULE via DKMS..."
    run_sudo rm -rf "$src_dir"
    run_sudo git clone "$REPO_DRIVER" "$src_dir"

    # Use centralized Clang detection from detect_kernel()
    local make_flags="$CLANG_BUILD_FLAGS"
    if [ "$IS_CLANG_KERNEL" -eq 1 ]; then
        log "Clang-built kernel detected. Using LLVM build flags."
    fi

    # Create DKMS config
    run_sudo tee "$src_dir/dkms.conf" > /dev/null <<DKMS_EOF
PACKAGE_NAME="$DKMS_NAME"
PACKAGE_VERSION="$DKMS_VERSION"
CLEAN="make clean"
MAKE[0]="make KVERSION=\\\$kernelver $make_flags"
BUILT_MODULE_NAME[0]="$DRIVER_MODULE"
BUILT_MODULE_LOCATION[0]="src/"
DEST_MODULE_LOCATION[0]="/kernel/drivers/platform/x86"
AUTOINSTALL="yes"
DKMS_EOF

    run_sudo dkms add -m "$DKMS_NAME" -v "$DKMS_VERSION" || true
    log "Building DKMS module (this may take a few minutes)..."
    run_sudo dkms install -m "$DKMS_NAME" -v "$DKMS_VERSION"

    # Blacklist acer_wmi
    log "Blacklisting acer_wmi..."
    echo "blacklist acer_wmi" | run_sudo tee /etc/modprobe.d/blacklist-acer-wmi.conf > /dev/null

    # --- DAMX GUI & Daemon Setup ---
    log "Fetching latest DAMX..."
    mkdir -p "$_DAMX_INSTALL_DIR"

    local latest_release
    latest_release=$(curl -s --connect-timeout 10 --max-time 30 "https://api.github.com/repos/$REPO_APP/releases/latest" | python3 -c "import sys, json; print(json.load(sys.stdin)['tag_name'])")
    log "Latest version found: $latest_release"

    local v_num
    v_num=$(echo "$latest_release" | sed 's/v//')
    local dl_url="https://github.com/$REPO_APP/releases/download/$latest_release/DAMX-$v_num.tar.xz"

    curl -L --connect-timeout 10 --max-time 300 --progress-bar "$dl_url" -o "$_DAMX_INSTALL_DIR/damx.tar.xz"
    tar -xf "$_DAMX_INSTALL_DIR/damx.tar.xz" --strip-components=1 -C "$_DAMX_INSTALL_DIR"
    rm -f "$_DAMX_INSTALL_DIR/damx.tar.xz"

    # Install Python dependencies in a venv if possible
    local python_exec="$_DAMX_INSTALL_DIR/DAMX.py"
    if [ -f "$_DAMX_INSTALL_DIR/requirements.txt" ]; then
        if python3 -m venv "$_DAMX_INSTALL_DIR/.venv" 2>/dev/null; then
            log "Installing Python dependencies in virtual environment..."
            "$_DAMX_INSTALL_DIR/.venv/bin/pip" install -r "$_DAMX_INSTALL_DIR/requirements.txt" > /dev/null 2>&1 || warn "pip install encountered issues."
            python_exec="$_DAMX_INSTALL_DIR/.venv/bin/python $_DAMX_INSTALL_DIR/DAMX.py"
        else
            warn "Could not create venv. Falling back to --break-system-packages."
            pip install -r "$_DAMX_INSTALL_DIR/requirements.txt" --break-system-packages > /dev/null 2>&1 || warn "pip install encountered issues."
        fi
    fi

    # --- Systemd Service Setup ---
    log "Configuring systemd units..."
    local service_file="$HOME/.config/systemd/user/damx-daemon.service"
    mkdir -p "$(dirname "$service_file")"

    tee "$service_file" > /dev/null <<SERVICE_EOF
[Unit]
Description=DAMX Daemon - Fan & RGB Manager
After=graphical-session.target

[Service]
ExecStart=$python_exec --daemon
Restart=on-failure
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
SERVICE_EOF

    run systemctl --user daemon-reload
    run systemctl --user enable --now damx-daemon.service

    mark_reboot_required

    INSTALLED_FILES+=" /etc/modprobe.d/blacklist-acer-wmi.conf $service_file"
    INSTALLED_DKMS+=" ${DKMS_NAME}/${DKMS_VERSION}"
}

module_uninstall() {
    log "Stopping and disabling DAMX Daemon..."
    systemctl --user disable --now damx-daemon.service 2>/dev/null || true
    rm -f "$HOME/.config/systemd/user/damx-daemon.service"
    systemctl --user daemon-reload

    log "Removing Linuwu-Sense DKMS module..."
    sudo dkms remove -m "$DKMS_NAME" -v "$DKMS_VERSION" --all 2>/dev/null || true
    sudo rm -rf "/usr/src/${DKMS_NAME}-${DKMS_VERSION}"

    log "Restoring acer_wmi (removing blacklist)..."
    sudo rm -f /etc/modprobe.d/blacklist-acer-wmi.conf

    log "Removing DAMX files..."
    rm -rf "$_DAMX_INSTALL_DIR"
}

module_verify() {
    local failures=0

    if ! dkms status 2>/dev/null | grep -q "$DKMS_NAME.*installed"; then
        warn "DKMS module $DKMS_NAME not showing as installed"
        failures=$((failures + 1))
    fi

    if ! lsmod 2>/dev/null | grep -q "$DRIVER_MODULE"; then
        warn "$DRIVER_MODULE not loaded (may require reboot)"
    fi

    if ! systemctl --user is-active --quiet damx-daemon.service 2>/dev/null; then
        warn "damx-daemon.service not active (may require reboot)"
    fi

    return $(( failures > 0 ? 1 : 0 ))
}
