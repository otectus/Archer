#!/usr/bin/env bash
source "$(dirname "${BASH_SOURCE[0]}")/kernel.sh"

external_versions() {
    # Only Archer's versions, including the historical 1.0 installation.
    local status
    status=$(dkms status -m "$DKMS_NAME" 2>/dev/null) || return 1
    local line version
    while IFS= read -r line; do
        version=${line#*/}; version=${version%%[,:]*}
        if external_owned_version "$version"; then echo "$version"; fi
    done <<< "$status" | sort -u
}

external_owned_version() {
    local prefix="${DKMS_VERSION_BASE}.archer"
    [[ "$1" == "$prefix"* && "${1#"$prefix"}" =~ ^[0-9]+$ ]] && return 0
    [[ "$1" == "$DKMS_VERSION_BASE" ]] || return 1
    # Plain upstream versions are not evidence of ownership.
    if declare -F read_manifest_field >/dev/null; then
        [[ " $(read_manifest_field dkms_modules) " == *" $DKMS_NAME/$1 "* ]] && return 0
    fi
    return 1
}

external_restore_versions() {
    local line version kernel
    while IFS= read -r line; do
        [[ "$line" == *': installed'* ]] || continue
        version=${line%%,*}; version=${version#*/}
        kernel=${line#*, }; kernel=${kernel%%,*}
        run_sudo dkms install -m "$DKMS_NAME" -v "$version" -k "$kernel" --force ||
            warn "Rollback failed for $version on $kernel; retain its /usr/src tree and rebuild before reboot."
    done <<< "$1"
}

external_install_dkms() {
    local src_dir="$EXTERNAL_SRC_ROOT/${DKMS_NAME}-${DKMS_VERSION}"
    local temp_dir kernel path version old_status="" old_versions=() kernels=()
    local versions status
    log "$DKMS_NAME revision $DRIVER_REVISION, Archer DKMS $DKMS_VERSION"
    if [[ "${DRY_RUN:-0}" -eq 1 ]]; then
        log "Would validate pinned source and patches, build for installed kernels, migrate DKMS, and configure boot loading."
        return 0
    fi
    kernel_collect_targets "$KERNEL_MODULES_ROOT" || return 1
    kernels=("${KERNEL_TARGETS[@]}")
    temp_dir=$(mktemp -d) || return 1
    if ! bash "$EXTERNAL_PACKAGE/prepare.sh" "$temp_dir/source"; then
        warn "Driver source/patch validation failed; existing installation was not changed."
        rm -rf "$temp_dir"
        return 1
    fi
    if [[ -d "$src_dir" ]]; then
        for path in "$SOURCE_FILE" Makefile archer-build.sh archer-kernel.sh archer-source dkms.conf; do
            if ! cmp -s "$temp_dir/source/$path" "$src_dir/$path"; then
                warn "Existing $src_dir differs from the pinned source. Refusing to overwrite registered DKMS sources."
                rm -rf "$temp_dir"
                return 1
            fi
        done
    else
        if ! run_sudo cp -r "$temp_dir/source" "$src_dir" ||
           ! run_sudo chown -R root:root "$src_dir"; then
            rm -rf "$temp_dir"
            return 1
        fi
    fi
    rm -rf "$temp_dir"
    INSTALLED_DKMS+=" $DKMS_NAME/$DKMS_VERSION"
    status=$(dkms status -m "$DKMS_NAME" -v "$DKMS_VERSION") || return 1
    if [[ -z "$status" ]]; then
        run_sudo dkms add -m "$DKMS_NAME" -v "$DKMS_VERSION" || return 1
    fi
    for kernel in "${kernels[@]}"; do
        if ! run_sudo dkms build -m "$DKMS_NAME" -v "$DKMS_VERSION" -k "$kernel"; then
            kernel_dkms_diagnostics "$DKMS_NAME" "$DKMS_VERSION" "$kernel"
            warn "Build failed for $kernel; previous driver and boot configuration retained."
            return 1
        fi
    done
    # Retain the old source and cached builds until every new build succeeds.
    versions=$(external_versions) || return 1
    while IFS= read -r version; do
        [[ -n "$version" && "$version" != "$DKMS_VERSION" ]] || continue
        old_versions+=("$version")
        status=$(dkms status -m "$DKMS_NAME" -v "$version") || return 1
        old_status+="$status"$'\n'
    done <<< "$versions"
    for version in "${old_versions[@]}"; do
        if ! run_sudo dkms remove -m "$DKMS_NAME" -v "$version" --all; then
            external_restore_versions "$old_status"
            return 1
        fi
    done
    for kernel in "${kernels[@]}"; do
        if ! run_sudo dkms install -m "$DKMS_NAME" -v "$DKMS_VERSION" -k "$kernel"; then
            run_sudo dkms remove -m "$DKMS_NAME" -v "$DKMS_VERSION" --all || warn "Rollback cleanup failed for $DKMS_NAME/$DKMS_VERSION"
            external_restore_versions "$old_status"
            warn "Installation failed; attempted to restore previous DKMS versions."
            return 1
        fi
    done
    for version in "${old_versions[@]}"; do
        run_sudo rm -rf "$EXTERNAL_SRC_ROOT/${DKMS_NAME}-${version}" || return 1
    done
    # Also remove orphaned sources from previous Archer versions, never other packages.
    for path in "$EXTERNAL_SRC_ROOT"/"$DKMS_NAME"-*; do
        version=${path##*/"$DKMS_NAME"-}
        if external_owned_version "$version" && [[ "$version" != "$DKMS_VERSION" ]]; then
            run_sudo rm -rf "$path" || return 1
        fi
    done
}

external_uninstall_dkms() {
    local versions version path
    versions=$(external_versions) || return 1
    while IFS= read -r version; do
        [[ -n "$version" ]] || continue
        run_sudo dkms remove -m "$DKMS_NAME" -v "$version" --all || return 1
    done <<< "$versions"
    for path in "$EXTERNAL_SRC_ROOT"/"$DKMS_NAME"-*; do
        version=${path##*/"$DKMS_NAME"-}
        external_owned_version "$version" || continue
        run_sudo rm -rf "$path" || return 1
    done
}
