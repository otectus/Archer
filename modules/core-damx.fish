#!/usr/bin/env fish
# Module: Core DAMX - Linuwu-Sense kernel driver + DAMX GUI daemon
# This is the original installer functionality extracted into the module interface.

set -g MODULE_NAME "DAMX Fan & RGB Control"
set -g MODULE_ID "core-damx"
set -g MODULE_DESCRIPTION "Linuwu-Sense kernel driver and DAMX GUI for fan/RGB management"

set -g REPO_DRIVER "https://github.com/0x7375646F/Linuwu-Sense.git"
set -g REPO_APP "PXDiv/Div-Acer-Manager-Max"
set -g DRIVER_MODULE "linuwu_sense"
set -g DKMS_NAME "linuwu-sense"
set -g DKMS_VERSION "1.0"
set -g _DAMX_INSTALL_DIR "$HOME/.local/share/damx"

function module_detect
    switch "$MODEL_FAMILY"
        case nitro predator helios triton
            return 0
        case '*'
            return 1
    end
end

function module_check_installed
    if dkms status 2>/dev/null | grep -q "$DKMS_NAME"
        return 0
    end
    return 1
end

function module_install
    set -l src_dir "/usr/src/$DKMS_NAME-$DKMS_VERSION"

    # --- Kernel Module (Linuwu-Sense) via DKMS ---
    log "Setting up $DRIVER_MODULE via DKMS..."
    run_sudo rm -rf $src_dir
    run_sudo git clone $REPO_DRIVER $src_dir

    # Use centralized Clang detection from detect_kernel()
    set -l make_flags "$CLANG_BUILD_FLAGS"
    if test "$IS_CLANG_KERNEL" = 1
        log "Clang-built kernel detected. Using LLVM build flags."
    end

    # Create DKMS config
    run_sudo tee $src_dir/dkms.conf > /dev/null <<DKMS_EOF
PACKAGE_NAME="$DKMS_NAME"
PACKAGE_VERSION="$DKMS_VERSION"
CLEAN="make clean"
MAKE[0]="make KVERSION=\$kernelver $make_flags"
BUILT_MODULE_NAME[0]="$DRIVER_MODULE"
BUILT_MODULE_LOCATION[0]="src/"
DEST_MODULE_LOCATION[0]="/kernel/drivers/platform/x86"
AUTOINSTALL="yes"
DKMS_EOF

    run_sudo dkms add -m $DKMS_NAME -v $DKMS_VERSION 2>/dev/null; or true
    log "Building DKMS module (this may take a few minutes)..."
    run_sudo dkms install -m $DKMS_NAME -v $DKMS_VERSION

    # Blacklist acer_wmi
    log "Blacklisting acer_wmi..."
    echo "blacklist acer_wmi" | run_sudo tee /etc/modprobe.d/blacklist-acer-wmi.conf > /dev/null

    # --- DAMX GUI & Daemon Setup ---
    log "Fetching latest DAMX..."
    mkdir -p $_DAMX_INSTALL_DIR

    set -l latest_release (curl -s --connect-timeout 10 --max-time 30 "https://api.github.com/repos/$REPO_APP/releases/latest" | python3 -c "import sys, json; print(json.load(sys.stdin)['tag_name'])")
    log "Latest version found: $latest_release"

    set -l v_num (string replace "v" "" $latest_release)
    set -l dl_url "https://github.com/$REPO_APP/releases/download/$latest_release/DAMX-$v_num.tar.xz"

    curl -L --connect-timeout 10 --max-time 300 --progress-bar $dl_url -o $_DAMX_INSTALL_DIR/damx.tar.xz
    tar -xf $_DAMX_INSTALL_DIR/damx.tar.xz --strip-components=1 -C $_DAMX_INSTALL_DIR
    rm -f $_DAMX_INSTALL_DIR/damx.tar.xz

    # Install Python dependencies in a venv if possible
    set -l python_exec "$_DAMX_INSTALL_DIR/DAMX.py"
    if test -f $_DAMX_INSTALL_DIR/requirements.txt
        if python3 -m venv "$_DAMX_INSTALL_DIR/.venv" 2>/dev/null
            log "Installing Python dependencies in virtual environment..."
            "$_DAMX_INSTALL_DIR/.venv/bin/pip" install -r $_DAMX_INSTALL_DIR/requirements.txt > /dev/null 2>&1; or warn "pip install encountered issues."
            set python_exec "$_DAMX_INSTALL_DIR/.venv/bin/python $_DAMX_INSTALL_DIR/DAMX.py"
        else
            warn "Could not create venv. Falling back to --break-system-packages."
            pip install -r $_DAMX_INSTALL_DIR/requirements.txt --break-system-packages > /dev/null 2>&1; or warn "pip install encountered issues."
        end
    end

    # --- Systemd Service Setup ---
    log "Configuring systemd units..."
    set -l service_file "$HOME/.config/systemd/user/damx-daemon.service"
    mkdir -p (dirname $service_file)

    tee $service_file > /dev/null <<SERVICE_EOF
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

    set -ga INSTALLED_FILES /etc/modprobe.d/blacklist-acer-wmi.conf $service_file
    set -ga INSTALLED_DKMS "$DKMS_NAME/$DKMS_VERSION"
end

function module_uninstall
    log "Stopping and disabling DAMX Daemon..."
    systemctl --user disable --now damx-daemon.service 2>/dev/null; or true
    rm -f $HOME/.config/systemd/user/damx-daemon.service
    systemctl --user daemon-reload

    log "Removing Linuwu-Sense DKMS module..."
    sudo dkms remove -m $DKMS_NAME -v $DKMS_VERSION --all 2>/dev/null; or true
    sudo rm -rf /usr/src/$DKMS_NAME-$DKMS_VERSION

    log "Restoring acer_wmi (removing blacklist)..."
    sudo rm -f /etc/modprobe.d/blacklist-acer-wmi.conf

    log "Removing DAMX files..."
    rm -rf $_DAMX_INSTALL_DIR
end

function module_verify
    set -l failures 0

    if not dkms status 2>/dev/null | grep -q "$DKMS_NAME.*installed"
        warn "DKMS module $DKMS_NAME not showing as installed"
        set failures (math $failures + 1)
    end

    if not lsmod 2>/dev/null | grep -q "$DRIVER_MODULE"
        warn "$DRIVER_MODULE not loaded (may require reboot)"
    end

    if not systemctl --user is-active --quiet damx-daemon.service 2>/dev/null
        warn "damx-daemon.service not active (may require reboot)"
    end

    test $failures -eq 0
end
