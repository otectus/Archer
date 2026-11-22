#!/usr/bin/env bash
# Run only in Archer's disposable, offline QEMU validation guest.
set -euo pipefail
[[ $(systemd-detect-virt --vm) == @(qemu|kvm) && " $(</proc/cmdline) " == *" archer.package-test=1 "* ]] ||
    { echo "Requires an explicitly marked disposable Archer package-test VM" >&2; exit 1; }
trap 'echo ARCHER_VM_FAILED_AT_LINE_$LINENO; journalctl -u archer-daemon -b -n30 --no-pager; systemctl poweroff --no-block' ERR
mkdir -p /mnt/packages
mount -o ro /dev/vdb /mnt/packages
service_call() { busctl call io.otectus.Archer1 /io/otectus/Archer1 io.otectus.Archer1 "$@"; }
ping_service() {
    local attempt
    for attempt in {1..20}; do
        if service_call Ping; then return 0; fi
        sleep 1
    done
    return 1
}
if [[ ! -e /var/lib/archer-guest-reboot ]]; then
    mkdir -p /var/lib/archer-guest-source /etc/archer
    tar --strip-components=1 -xf /mnt/packages/archer-*.tar.gz -C /var/lib/archer-guest-source
    scripts=/var/lib/archer-guest-source/scripts
    tar -xf /mnt/packages/legacy-source.tar.gz -C /var/lib/archer-guest-source
    (
        SCRIPT_DIR=/var/lib/archer-guest-source/legacy
        INSTALLED_PACKAGES='' INSTALLED_FILES=''
        # shellcheck disable=SC1091
        source "$SCRIPT_DIR/lib/utils.sh"
        # shellcheck disable=SC1091
        source "$SCRIPT_DIR/modules/gui.sh"
        # The archived Type=simple unit can be active before the old
        # installer's immediate Ping. Retry its unchanged verifier in that case.
        if ! module_install >/tmp/archer-legacy-install.log 2>&1; then
            cat /tmp/archer-legacy-install.log
            grep -q 'Archer D-Bus Ping failed' /tmp/archer-legacy-install.log
            ping_service
            module_verify
        fi
        [[ $INSTALLER_VERSION == 2.0.1 ]]
        python "$SCRIPT_DIR/scripts/archer-manifest.py" /var/lib/archer/install-manifest.json write \
            gui "$INSTALLED_FILES" '' "$INSTALLED_PACKAGES" "$INSTALLER_VERSION" QEMU unknown "$(uname -r)"
    )
    ping_service
    echo ARCHER_VM_LEGACY_PASS
    echo '{"schema_version":2,"lcd_override":false}' >/etc/archer/settings.json
    echo 'unrelated administrator file' >/opt/archer/guest-retained
    python "$scripts/archer-package-migration.py" plan >/tmp/archer-reviewed-migration.json
    python "$scripts/archer-package-migration.py" prepare >/dev/null
    pacman -U --noconfirm /mnt/packages/*.pkg.tar.zst
    python "$scripts/archer-package-migration.py" finish >/dev/null
    systemctl is-active archer-daemon
    test -f /opt/archer/guest-retained
    test ! -e /usr/local/bin/archer
    echo ARCHER_VM_MIGRATION_PASS
    systemctl enable --now archer-daemon
    ping_service
    service_call GetCapabilities
    runuser -u nobody -- archer status >/dev/null
    python -c 'import json;assert json.load(open("/etc/archer/settings.json"))["lcd_override"] is False'
    pacman -U --noconfirm /mnt/packages/*.pkg.tar.zst
    pacman -Qkk archer-core archer-daemon archer-gui
    touch /var/lib/archer-guest-reboot
    echo ARCHER_VM_INSTALL_PASS
    systemctl reboot --no-block
else
    systemctl is-enabled archer-daemon
    ping_service
    systemctl is-active archer-daemon
    service_call GetBatteryDevices
    python -c 'import json;assert json.load(open("/etc/archer/settings.json"))["lcd_override"] is False'
    if [[ -r /mnt/packages/v4l2loopback.ko ]]; then
        modprobe vivid n_devs=1 node_types=0x1
        insmod /mnt/packages/v4l2loopback.ko exclusive_caps=1 card_label='Archer Camera'
        udevadm settle
        chmod a+rw /dev/video*
        devices=$(runuser -u nobody -- archer-session cameras)
        source=$(python -c 'import json,sys; print(next(d["node"] for d in json.load(sys.stdin) if d["role"]=="source"))' <<<"$devices")
        output=$(python -c 'import json,sys; print(next(d["node"] for d in json.load(sys.stdin) if d["role"]=="output"))' <<<"$devices")
        runuser -u nobody -- archer-session stream "$source" "$output" --effect monochrome &
        worker=$!
        sleep 3
        kill -0 "$worker"
        timeout 15 ffmpeg -nostdin -v error -f v4l2 -i "/dev/$output" -frames:v 4 -f framemd5 /tmp/archer-frames.md5
        [[ $(grep -c '^0,' /tmp/archer-frames.md5) == 4 ]]
        kill "$worker"
        wait "$worker" || true
        echo ARCHER_VM_CAMERA_PASS
    fi
    systemctl disable --now archer-daemon
    # Exercise pacman's removal hook with a known graphics-file transition
    # inside this VM. No NVIDIA device or GPU mode change is required.
    target=/etc/X11/xorg.conf.d/90-nvidia.conf
    [[ ! -e $target ]]
    if [[ $(systemctl show --value -p LoadState nvidia-persistenced.service) == not-found ]]; then
        cat >/run/systemd/system/nvidia-persistenced.service <<'UNIT'
[Service]
ExecStart=/usr/bin/true
[Install]
WantedBy=multi-user.target
UNIT
        systemctl daemon-reload
    fi
    python - "$target" <<'PY'
from pathlib import Path
import sys
sys.path.insert(0, '/usr/lib/archer/app')
import archer_system
target = Path(sys.argv[1])
target.parent.mkdir(parents=True, exist_ok=True)
archer_system._prepare_envycontrol('hybrid')
target.write_text('# Archer VM applied\n')
archer_system._record_envycontrol('complete', 'hybrid')
PY
    echo '# administrator edit' >"$target"
    if pacman -R --noconfirm archer-gui archer-daemon archer-core; then
        echo "Package removal ignored a graphics ownership conflict" >&2
        systemctl poweroff --no-block
        exit 1
    fi
    pacman -Q archer-gui archer-daemon archer-core
    grep -Fx '# administrator edit' "$target"
    echo '# Archer VM applied' >"$target"
    pacman -R --noconfirm archer-gui archer-daemon archer-core
    test ! -e "$target"
    test ! -e /var/lib/archer/envycontrol-ownership.json
    echo ARCHER_VM_GRAPHICS_RESTORE_PASS
    pacman -U --noconfirm /mnt/packages/archer-core-*.pkg.tar.zst
    pacman -R --noconfirm archer-core
    test -f /etc/archer/settings.json
    test ! -e /usr/bin/archer
    echo ARCHER_VM_REBOOT_REMOVE_PASS
    systemctl poweroff --no-block
fi
