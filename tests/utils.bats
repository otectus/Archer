#!/usr/bin/env bats
# Tests for lib/utils.sh — shared utility functions

setup() {
    DRY_RUN=0
    NO_CONFIRM=0
    source "$BATS_TEST_DIRNAME/../lib/utils.sh"
}

@test "INSTALLER_VERSION is set" {
    [ -n "$INSTALLER_VERSION" ]
}

@test "log outputs with prefix" {
    run log "test message"
    [ "$status" -eq 0 ]
    [[ "$output" == *"test message"* ]]
}

@test "warn outputs with warning indicator" {
    run warn "test warning"
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
    run run echo "hello"
    [ "$status" -eq 0 ]
    [[ "$output" == *"hello"* ]]
}

@test "run skips execution in dry-run mode" {
    DRY_RUN=1
    run run echo "should not run"
    [ "$status" -eq 0 ]
    [[ "$output" == *"DRY RUN"* ]]
}

@test "run_sudo skips execution in dry-run mode" {
    DRY_RUN=1
    run run_sudo echo "should not run"
    [ "$status" -eq 0 ]
    [[ "$output" == *"DRY RUN"* ]]
}

@test "confirm auto-accepts with NO_CONFIRM=1" {
    NO_CONFIRM=1
    confirm "test?"
}

@test "section outputs header" {
    run section "Test Section"
    [ "$status" -eq 0 ]
    [[ "$output" == *"Test Section"* ]]
}

@test "mark_reboot_required sets flag" {
    REBOOT_REQUIRED=0
    mark_reboot_required
    [ "$REBOOT_REQUIRED" -eq 1 ]
}
