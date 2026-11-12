#!/usr/bin/env bats
# Exercise the real uninstaller and manifest writer in an isolated fake install.
setup() {
    root="$BATS_TEST_TMPDIR/checkout"
    mkdir -p "$root/lib" "$root/scripts" "$root/modules"
    cp "$BATS_TEST_DIRNAME/../uninstall.sh" "$root/"
    cp "$BATS_TEST_DIRNAME/../lib/"*.sh "$root/lib/"
    cp "$BATS_TEST_DIRNAME/../scripts/"*.py "$root/scripts/"
    export TEST_MANIFEST="$BATS_TEST_TMPDIR/manifest.json"
    cat >> "$root/lib/manifest.sh" <<'FIXTURE'
MANIFEST_FILE="$TEST_MANIFEST"
MANIFEST_DIR="${TEST_MANIFEST%/*}"
_LEGACY_MANIFEST_CANDIDATES=()
FIXTURE
    cat >> "$root/lib/utils.sh" <<'FIXTURE'
run_sudo() {
    [[ "$DRY_RUN" -eq 0 ]] || return 0
    case "$1" in
        python3) "$@" ;;
        rm) [[ "$3" == "$TEST_MANIFEST" ]] && "$@" ;;
        *) echo "Unexpected privileged command: $*"; return 99 ;;
    esac
}
FIXTURE
    echo 'module_uninstall() { return 0; }' > "$root/modules/audio.sh"
    echo 'module_uninstall() { return 1; }' > "$root/modules/battery.sh"
}

@test "cleanup failures retain a retryable manifest after successful modules are removed" {
    echo '{"modules_installed":["audio","battery"]}' > "$TEST_MANIFEST"
    run bash "$root/uninstall.sh" --no-confirm
    [ "$status" -eq 1 ]
    [ "$(python3 "$root/scripts/archer-manifest.py" "$TEST_MANIFEST" read modules_installed)" = battery ]
    echo 'module_uninstall() { return 0; }' > "$root/modules/battery.sh"
    run bash "$root/uninstall.sh" --no-confirm
    [ "$status" -eq 0 ]
    [ ! -e "$TEST_MANIFEST" ]
}

@test "unknown and missing modules keep their cleanup records" {
    echo '{"modules_installed":["unknown","thermal","audio"]}' > "$TEST_MANIFEST"
    run bash "$root/uninstall.sh" --no-confirm
    [ "$status" -eq 1 ]
    [ "$(python3 "$root/scripts/archer-manifest.py" "$TEST_MANIFEST" read modules_installed)" = 'unknown thermal' ]
}

@test "malformed manifests never fall through to legacy cleanup" {
    echo '{"modules_installed":"audio"}' > "$TEST_MANIFEST"
    cp "$TEST_MANIFEST" "$TEST_MANIFEST.before"
    run bash "$root/uninstall.sh" --no-confirm
    [ "$status" -eq 1 ]
    cmp "$TEST_MANIFEST" "$TEST_MANIFEST.before"
    [[ "$output" != *'legacy uninstall'* ]]
}

@test "uninstaller dry run and help preserve manifest bytes" {
    echo '{"modules_installed":["audio"]}' > "$TEST_MANIFEST"
    cp "$TEST_MANIFEST" "$TEST_MANIFEST.before"
    run bash "$root/uninstall.sh" --dry-run --no-confirm
    [ "$status" -eq 0 ]
    cmp "$TEST_MANIFEST" "$TEST_MANIFEST.before"
    run bash "$root/uninstall.sh" --help
    [ "$status" -eq 0 ]
    cmp "$TEST_MANIFEST" "$TEST_MANIFEST.before"
}
