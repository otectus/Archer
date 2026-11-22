#!/usr/bin/env bash
# Run inside a clean Arch build container. Export its root and boot with QEMU.
set -euo pipefail
workspace=${1:?Pass a package-test work directory}
mkdir -p "$workspace/files"
workspace=$(cd "$workspace" && pwd)
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
if [[ "$workspace" != /archer-vm || ! -f /.dockerenv ]]; then
    echo "This guest-image builder requires an Arch Docker container and /archer-vm" >&2
    exit 1
fi
sed -i '/^NoExtract .*usr\/share\/doc\//d' /etc/pacman.conf
pacman -Syu --needed --noconfirm linux linux-headers mkinitcpio base-devel python-gobject python-cairo python-dbus gtk4 libadwaita polkit ffmpeg e2fsprogs v4l2loopback-dkms
install -Dm755 "$repo/tests/package_guest.sh" /usr/local/bin/archer-package-guest
cat > /etc/systemd/system/archer-package-guest.service <<'UNIT'
[Unit]
Description=Verify Archer packages in a test guest
After=dbus.service systemd-user-sessions.service
Wants=dbus.service
[Service]
Type=simple
StandardOutput=journal+console
StandardError=journal+console
ExecStart=/usr/local/bin/archer-package-guest
[Install]
WantedBy=multi-user.target
UNIT
systemctl enable archer-package-guest
releases=(/usr/lib/modules/*/build/include/config/kernel.release)
[[ ${#releases[@]} -eq 1 ]]
kernel=$(cat "${releases[0]}")
cp "/usr/lib/modules/$kernel/vmlinuz" "$workspace/vmlinuz"
cat > "$workspace/guest.conf" <<'CONF'
MODULES=(virtio_pci virtio_blk ext4)
BINARIES=()
FILES=()
HOOKS=(base udev block filesystems)
COMPRESSION="zstd"
CONF
mkinitcpio -c "$workspace/guest.conf" -k "$kernel" -g "$workspace/initrd"
module=$(modinfo -k "$kernel" -F filename v4l2loopback)
case "$module" in
    *.zst) zstd -dc "$module" > "$workspace/files/v4l2loopback.ko" ;;
    *.xz) xz -dc "$module" > "$workspace/files/v4l2loopback.ko" ;;
    *.ko) cp "$module" "$workspace/files/v4l2loopback.ko" ;;
    *) echo "Unknown loopback module format" >&2; exit 1 ;;
esac
truncate -s 24M "$workspace/packages.ext4"
mkfs.ext4 -q -F -d "$workspace/files" "$workspace/packages.ext4"
mkdir -p "$workspace/guest"
# Exclude pseudo filesystems, transient container state and the output mount.
tar --one-file-system --exclude=./archer-vm --exclude=./repo --exclude=./sys --exclude=./proc --exclude=./dev \
    --exclude=./run --exclude=./.dockerenv --exclude=./var/cache/pacman/pkg --exclude=./tmp -C / -cf - . |
    tar -C "$workspace/guest" -xpf -
mkdir -p "$workspace/guest"/{dev,proc,run,sys,tmp}
chmod 1777 "$workspace/guest/tmp"
truncate -s 6G "$workspace/root.ext4"
mkfs.ext4 -q -F -d "$workspace/guest" "$workspace/root.ext4"
