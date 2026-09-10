#!/usr/bin/env bats
# Tests for install.sh — CLI argument parsing and module selection logic

setup() {
    DRY_RUN=0
    NO_CONFIRM=0
    VERBOSE=0
    LOG_FILE=""
    REBOOT_REQUIRED=0
    source "$BATS_TEST_DIRNAME/../lib/utils.sh"
    # Override error() to not exit
    error() { echo "ERROR: $*"; return 1; }
    # Source install.sh (main is guarded, won't execute)
    source "$BATS_TEST_DIRNAME/../install.sh"
}

@test "parse_args sets --all flag" {
    SELECT_ALL_RECOMMENDED=0
    parse_args --all
    [ "$SELECT_ALL_RECOMMENDED" -eq 1 ]
}

@test "parse_args sets --dry-run flag" {
    DRY_RUN=0
    parse_args --dry-run
    [ "$DRY_RUN" -eq 1 ]
}

@test "parse_args sets --verbose flag" {
    VERBOSE=0
    parse_args --verbose
    [ "$VERBOSE" -eq 1 ]
}

@test "parse_args sets --log flag with file" {
    LOG_FILE=""
    parse_args --log /tmp/test.log
    [ "$LOG_FILE" = "/tmp/test.log" ]
}

@test "driver and thermal may share an existing profile provider" {
    MODULE_SELECTED=(1 0 0 0 0 0 0 1 0 0 0 0 0)
    check_conflicts
}

@test "check_conflicts passes when no conflict" {
    MODULE_SELECTED=(1 1 0 0 0 0 0 0 0 0 0 0 0)
    check_conflicts
}

@test "MODULE_IDS contains all 13 modules" {
    [ "${#MODULE_IDS[@]}" -eq 13 ]
}

@test "MODULE_IDS and MODULE_LABELS have same length" {
    [ "${#MODULE_IDS[@]}" -eq "${#MODULE_LABELS[@]}" ]
}

@test "is_known_module accepts every canonical module ID" {
    local id
    for id in "${MODULE_IDS[@]}"; do
        is_known_module "$id" || { echo "rejected canonical id: $id"; return 1; }
    done
}

@test "is_known_module rejects path-traversal attempts" {
    ! is_known_module "../../tmp/evil"
    ! is_known_module "../etc/x"
    ! is_known_module "/etc/passwd"
    ! is_known_module ".hidden"
    ! is_known_module ""
}

@test "is_known_module rejects shell metacharacters" {
    ! is_known_module "driver;rm -rf /"
    ! is_known_module 'driver$(whoami)'
    ! is_known_module "driver|cat"
    ! is_known_module "driver\`id\`"
}

@test "is_known_module rejects unknown but well-formed IDs" {
    ! is_known_module "unknown"
    ! is_known_module "fakemod"
}

@test "driver update retains other module manifest records and replaces old DKMS version" {
    SCRIPT_DIR="$BATS_TEST_TMPDIR"
    mkdir -p "$SCRIPT_DIR/modules"
    cat > "$SCRIPT_DIR/modules/driver.sh" <<'DRIVER'
module_install() {
    DKMS_NAME=linuwu-sense DKMS_VERSION=1.0.archer1
    INSTALLED_DKMS+=' linuwu-sense/1.0.archer1'
}
DRIVER
    MODULE_SELECTED=(1 0 0 0 0 0 0 0 0 0 0 0 0)
    has_manifest() { return 0; }
    read_manifest_modules() { echo 'driver gui gpu'; }
    read_manifest_field() {
        case "$1" in
            files_created) echo /opt/archer ;;
            dkms_modules) echo 'linuwu-sense/1.0 another/2.0' ;;
            packages_installed) echo envycontrol ;;
        esac
    }
    write_manifest() { saved_modules="$1" saved_files="$2" saved_dkms="$3" saved_packages="$4"; }
    run_selected_modules
    [ "$saved_modules" = 'driver gui gpu' ]
    [ "$saved_files" = /opt/archer ]
    [ "$saved_packages" = envycontrol ]
    [[ "$saved_dkms" == *another/2.0* ]]
    [[ "$saved_dkms" == *linuwu-sense/1.0.archer1* ]]
    [[ " $saved_dkms " != *' linuwu-sense/1.0 '* ]]
}

@test "failed installation returns failure instead of reporting success" {
    SCRIPT_DIR="$BATS_TEST_TMPDIR"
    mkdir -p "$SCRIPT_DIR/modules"
    echo 'module_install() { return 1; }' > "$SCRIPT_DIR/modules/driver.sh"
    MODULE_SELECTED=(1 0 0 0 0 0 0 0 0 0 0 0 0)
    has_manifest() { return 1; }
    INSTALLED_FILES='' INSTALLED_DKMS='' INSTALLED_PACKAGES=''
    ! run_selected_modules
}

@test "failed DKMS build retains cleanup manifest for its staged source" {
    SCRIPT_DIR="$BATS_TEST_TMPDIR"
    mkdir -p "$SCRIPT_DIR/modules"
    cat > "$SCRIPT_DIR/modules/driver.sh" <<'DRIVER'
module_install() {
    INSTALLED_DKMS+=' linuwu-sense/1.0.archer2'
    return 1
}
DRIVER
    MODULE_SELECTED=(1 0 0 0 0 0 0 0 0 0 0 0 0)
    has_manifest() { return 1; }
    INSTALLED_FILES='' INSTALLED_DKMS='' INSTALLED_PACKAGES=''
    write_manifest() { saved_modules="$1" saved_dkms="$3"; }
    ! run_selected_modules
    [ "$saved_modules" = driver ]
    [[ "$saved_dkms" == *linuwu-sense/1.0.archer2* ]]
}

@test "GUI preflight rejects a missing tray module before system writes" {
    source "$BATS_TEST_DIRNAME/../modules/gui.sh"
    cp -r "$BATS_TEST_DIRNAME/../gui" "$BATS_TEST_TMPDIR/gui"
    SCRIPT_DIR="$BATS_TEST_TMPDIR"
    rm "$SCRIPT_DIR/gui/archer/tray.py"
    error() { echo "$*"; exit 1; }
    run_sudo() { touch "$BATS_TEST_TMPDIR/system-write"; }
    output="$(module_install 2>&1)" && status=0 || status=$?
    [ "$status" -eq 1 ]
    [[ "$output" == *'Required GUI source file missing:'*'/archer/tray.py'* ]]
    [ ! -e "$BATS_TEST_TMPDIR/system-write" ]
}

@test "GUI deployment restarts the daemon to load upgraded code" {
    source "$BATS_TEST_DIRNAME/../modules/gui.sh"
    SCRIPT_DIR="$BATS_TEST_DIRNAME/.."
    DRY_RUN=1
    dkms() { echo 'linuwu-sense/1.0.archer2: installed'; }
    output="$(module_install)"
    [[ "$output" == *'systemctl restart archer-daemon.service'* ]]
    [[ "$output" == *'/gui/archer /opt/archer/'* ]]
}
