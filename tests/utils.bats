#!/usr/bin/env bats
# Tests for lib/utils.sh — shared utility functions
#
# NOTE: lib/utils.sh defines a run() function that overwrites Bats' built-in
# run command. We capture output/status manually instead of relying on Bats' run.

setup() {
    DRY_RUN=0
    NO_CONFIRM=0
    VERBOSE=0
    LOG_FILE=""
    REBOOT_REQUIRED=0
    source "$BATS_TEST_DIRNAME/../lib/utils.sh"
}

# Helper: capture output and status from a function call without Bats' run
capture() {
    output="$("$@" 2>&1)"
    status=$?
}

@test "INSTALLER_VERSION is set" {
    [ -n "$INSTALLER_VERSION" ]
}

@test "log outputs with prefix" {
    capture log "test message"
    [ "$status" -eq 0 ]
    [[ "$output" == *"test message"* ]]
}

@test "warn outputs with warning indicator" {
    capture warn "test warning"
    [ "$status" -eq 0 ]
    [[ "$output" == *"test warning"* ]]
}

@test "has_cmd finds bash" {
    has_cmd bash
}

@test "has_cmd returns false for nonexistent command" {
    ! has_cmd this_command_does_not_exist_12345
}

@test "run executes command normally" {
    capture run echo "hello"
    [ "$status" -eq 0 ]
    [[ "$output" == *"hello"* ]]
}

@test "run skips execution in dry-run mode" {
    DRY_RUN=1
    capture run echo "should not run"
    [ "$status" -eq 0 ]
    [[ "$output" == *"DRY RUN"* ]]
}

@test "run_sudo skips execution in dry-run mode" {
    DRY_RUN=1
    capture run_sudo echo "should not run"
    [ "$status" -eq 0 ]
    [[ "$output" == *"DRY RUN"* ]]
}

@test "confirm auto-accepts with NO_CONFIRM=1" {
    NO_CONFIRM=1
    confirm "test?"
}

@test "section outputs header" {
    capture section "Test Section"
    [ "$status" -eq 0 ]
    [[ "$output" == *"Test Section"* ]]
}

@test "mark_reboot_required sets flag" {
    REBOOT_REQUIRED=0
    mark_reboot_required
    [ "$REBOOT_REQUIRED" -eq 1 ]
}

@test "debug outputs nothing when VERBOSE=0" {
    VERBOSE=0
    capture debug "hidden message"
    [ "$status" -eq 0 ]
    [[ "$output" == "" ]]
}

@test "debug outputs when VERBOSE=1" {
    VERBOSE=1
    capture debug "visible message"
    [ "$status" -eq 0 ]
    [[ "$output" == *"visible message"* ]]
}

@test "log writes to LOG_FILE when set" {
    local tmplog
    tmplog=$(mktemp)
    LOG_FILE="$tmplog"
    log "log file test"
    [ -f "$tmplog" ]
    grep -q "log file test" "$tmplog"
    rm -f "$tmplog"
}

@test "LOG_FILE strips ANSI color codes" {
    local tmplog
    tmplog=$(mktemp)
    LOG_FILE="$tmplog"
    log "color test"
    # Should not contain ANSI escape sequences
    ! grep -qP '\x1b\[' "$tmplog"
    rm -f "$tmplog"
}

@test "run_sudo_timeout skips in dry-run" {
    DRY_RUN=1
    capture run_sudo_timeout 10 echo "test"
    [ "$status" -eq 0 ]
    [[ "$output" == *"DRY RUN"* ]]
}
