#!/usr/bin/env bash
# Developer convenience entry point; prepared trees contain the shared helper.
set -euo pipefail
exec bash "$(dirname "${BASH_SOURCE[0]}")/../build.sh" "$@"
