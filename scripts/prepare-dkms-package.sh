#!/usr/bin/env bash
# Materialize a pinned, patched source archive for an offline makepkg build.
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
module=${1:?Select linuwu-sense or acer-wmi-battery}
case "$module" in linuwu-sense|acer-wmi-battery) ;; *) exit 2 ;; esac
out=${2:?Pass an empty output directory}
mkdir -p "$out"
out=$(cd "$out" && pwd)
[[ -z "$(ls -A "$out")" ]] || { echo "Package input directory must be empty" >&2; exit 1; }
source "$repo/driver/$module/source.conf"
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
name="$DKMS_NAME-$DKMS_VERSION"
local_source=()
[[ -z "${3:-}" ]] || local_source=("$3")
bash "$repo/driver/$module/prepare.sh" "$stage/$name" "${local_source[@]}"
# Distribute only Kbuild inputs, Archer helpers and the original upstream notices.
mkdir -p "$stage/package/$name/$(dirname "$SOURCE_FILE")"
for file in "$SOURCE_FILE" LICENSE dkms.conf archer-build.sh archer-kernel.sh archer-source; do
    cp "$stage/$name/$file" "$stage/package/$name/$file"
done
cp "$repo/LICENSE" "$stage/package/$name/Archer-LICENSE"
printf 'obj-m := %s.o\n' "${SOURCE_FILE%.c}" > "$stage/package/$name/Makefile"
epoch=${SOURCE_DATE_EPOCH:-$(git -C "$repo" log -1 --format=%ct)}
tar --sort=name --mtime="@$epoch" --mode='u+rwX,go+rX,go-w' --owner=0 --group=0 --numeric-owner -C "$stage/package" -cf - "$name" | gzip -n > "$out/$name.tar.gz"
digest=$(sha256sum "$out/$name.tar.gz" | cut -d' ' -f1)
cat > "$out/PKGBUILD" <<EOF
pkgname=archer-$DKMS_NAME-dkms
pkgver=$DKMS_VERSION
pkgrel=1
pkgdesc='Archer patched $DKMS_NAME external kernel module'
arch=(x86_64)
license=('GPL-2.0-or-later' 'MIT')
url='$REPO_DRIVER'
depends=(dkms)
conflicts=('$DKMS_NAME-dkms')
source=('$name.tar.gz')
sha256sums=('$digest')
package() {
    install -d "\$pkgdir/usr/src"
    cp -R "\$srcdir/$name" "\$pkgdir/usr/src/"
}
EOF
printf '%s\n' "$epoch" > "$out/SOURCE_DATE_EPOCH"
(cd "$out" && makepkg --printsrcinfo > .SRCINFO)
