#!/usr/bin/env bash
# Generate standalone makepkg inputs; the output directory can be moved to a clean chroot.
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
out=${1:?Pass an empty output directory}
mkdir -p "$out"
out=$(cd "$out" && pwd)
if [[ -n "$(ls -A "$out")" ]]; then
    echo "Package input directory must be empty" >&2
    exit 1
fi
cd "$repo"
version=$(cat gui/VERSION)
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]
export SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH:-$(git log -1 --format=%ct)}
# Explicit source paths keep local files, screenshots from tests and bytecode out of the release.
git ls-files -co --exclude-standard -z -- .github .gitignore gui docs driver lib modules scripts schemas packaging tests install.sh uninstall.sh LICENSE README.md CHANGELOG.md CONTRIBUTING.md |
    while IFS= read -r -d '' file; do
        if [[ -f "$file" ]]; then printf '%s\0' "$file"; fi
    done | LC_ALL=C sort -zu | tar --no-recursion --null --files-from=- --mtime="@$SOURCE_DATE_EPOCH" \
    --mode='u+rwX,go+rX,go-w' --owner=0 --group=0 --numeric-owner --transform="s,^,archer-$version/," -cf - | gzip -n > "$out/archer-$version.tar.gz"
digest=$(sha256sum "$out/archer-$version.tar.gz" | cut -d' ' -f1)
sed -e "s/@VERSION@/$version/g" -e "s/@SHA256@/$digest/g" packaging/PKGBUILD.in > "$out/PKGBUILD"
printf '%s\n' "$SOURCE_DATE_EPOCH" > "$out/SOURCE_DATE_EPOCH"
(cd "$out" && makepkg --printsrcinfo > .SRCINFO)
