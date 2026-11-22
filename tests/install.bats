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
    [[ "$output" == *'/scripts/archer-gui-files.py install'* ]]
}

@test "GUI install records login startup and uninstall removes it" {
    source "$BATS_TEST_DIRNAME/../modules/gui.sh"
    SCRIPT_DIR="$BATS_TEST_DIRNAME/.."
    DRY_RUN=1
    dkms() { echo 'linuwu-sense/1.0.archer2: installed'; }
    module_install > "$BATS_TEST_TMPDIR/install-output"
    [[ "$INSTALLED_FILES" == *" $_GUI_AUTOSTART "* ]]
    output="$(cat "$BATS_TEST_TMPDIR/install-output")"
    [[ "$output" == *"/scripts/archer-gui-files.py install"* ]]
    output="$(module_uninstall)"
    [[ "$output" == *"/scripts/archer-gui-files.py remove"* ]]
}

@test "GUI verification detects a missing login startup entry" {
    source "$BATS_TEST_DIRNAME/../modules/gui.sh"
    _GUI_INSTALL_DIR="$BATS_TEST_TMPDIR"
    _GUI_DESKTOP="$BATS_TEST_TMPDIR/desktop"
    _GUI_AUTOSTART="$BATS_TEST_TMPDIR/autostart"
    touch "$_GUI_INSTALL_DIR/archer_daemon.py" "$_GUI_DESKTOP"
    systemctl() { return 0; }
    busctl() { return 0; }
    output="$(module_verify)" && status=0 || status=$?
    [ "$status" -eq 1 ]
    [[ "$output" == *'GUI login startup entry not found'* ]]
    touch "$_GUI_AUTOSTART"
    module_verify
}

@test "real package modules propagate failure under the installer conditional" {
    for module in audio wifi power gamemode firmware audio-enhance; do
        source "$BATS_TEST_DIRNAME/../modules/$module.sh"
        if [[ "$module" == wifi ]]; then
            KERNEL_LOG_AVAILABLE=1
            _CACHED_DMESG='iwlwifi firmware failed'
        fi
        run_sudo() { return 37; }
        pacman() { return 1; }
        INSTALLED_FILES='' INSTALLED_PACKAGES=''
        if module_install; then
            echo "Package failure was swallowed by $module"
            return 1
        fi
        [ -z "$INSTALLED_PACKAGES" ]
    done
}

@test "GUI deployment does not continue after an essential copy failure" {
    source "$BATS_TEST_DIRNAME/../modules/gui.sh"
    run_sudo() {
        [[ "$*" != *"archer-gui-files.py install"* ]] || return 1
        [[ "$*" != *'systemctl restart'* ]] || touch "$BATS_TEST_TMPDIR/restarted"
    }
    if module_install; then return 1; fi
    [ ! -f "$BATS_TEST_TMPDIR/restarted" ]
}

@test "verification returns failure for a selected broken module" {
    SCRIPT_DIR="$BATS_TEST_TMPDIR"
    mkdir -p "$SCRIPT_DIR/modules"
    echo 'module_verify() { return 1; }' > "$SCRIPT_DIR/modules/driver.sh"
    MODULE_SELECTED=(1 0 0 0 0 0 0 0 0 0 0 0 0)
    ! verify_modules
}

@test "native GameMode installs when multilib is unavailable" {
    source "$BATS_TEST_DIRNAME/../modules/gamemode.sh"
    pacman() { return 1; }
    run_sudo() { echo "$*" >> "$BATS_TEST_TMPDIR/commands"; }
    module_config() { [[ "$1" == /etc/gamemode.ini ]]; }
    INSTALLED_PACKAGES=''
    module_install
    [[ "$INSTALLED_PACKAGES" == *gamemode* ]]
    [[ "$INSTALLED_PACKAGES" != *lib32* ]]
    ! grep -q lib32 "$BATS_TEST_TMPDIR/commands"
}

@test "a declined power transition is skipped without an installed record" {
    source "$BATS_TEST_DIRNAME/../modules/power.sh"
    pacman() { return 0; }
    confirm() { return 1; }
    systemctl() { [[ "$*" == *'is-active --quiet power-profiles-daemon.service'* ]]; }
    run_sudo() { return 99; }
    INSTALLED_FILES='' INSTALLED_PACKAGES='' MODULE_SKIPPED=0
    module_install
    [ "$MODULE_SKIPPED" -eq 1 ]
    [ -z "$INSTALLED_FILES$INSTALLED_PACKAGES" ]
}

@test "legacy manifest migration is read-only during dry run" {
    DRY_RUN=1
    MANIFEST_FILE="$BATS_TEST_TMPDIR/new/manifest.json"
    local legacy="$BATS_TEST_TMPDIR/old.json"
    echo '{"modules_installed":["driver"]}' > "$legacy"
    _LEGACY_MANIFEST_CANDIDATES=("$legacy")
    run_sudo() { touch "$BATS_TEST_TMPDIR/mutated"; }
    migrate_legacy_manifest
    [ -f "$legacy" ]
    [ ! -e "$BATS_TEST_TMPDIR/mutated" ]
}

@test "denied kernel logs are cached and reported as unknown" {
    KERNEL_LOG_AVAILABLE=unknown
    dmesg() { echo attempt >> "$BATS_TEST_TMPDIR/dmesg-calls"; return 1; }
    read_kernel_log
    read_kernel_log
    [ "$KERNEL_LOG_AVAILABLE" -eq 0 ]
    [ "$(wc -l < "$BATS_TEST_TMPDIR/dmesg-calls")" -eq 1 ]
}

@test "unsupported init fails before manifest migration or installation" {
    systemctl() { return 1; }
    migrate_legacy_manifest() { touch "$BATS_TEST_TMPDIR/migrated"; }
    install_shared_deps() { touch "$BATS_TEST_TMPDIR/packages"; }
    if main --modules audio --no-confirm; then return 1; fi
    [ ! -e "$BATS_TEST_TMPDIR/migrated" ]
    [ ! -e "$BATS_TEST_TMPDIR/packages" ]
}
