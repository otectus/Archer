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

@test "check_conflicts detects driver+thermal conflict" {
    MODULE_SELECTED=(1 0 0 0 0 0 0 1 0 0 0 0 0)
    ! check_conflicts
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
