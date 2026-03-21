#!/usr/bin/env bash
# Legacy wrapper — delegates to the module system
# Use './install.sh --modules gui' instead
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo "Note: Use './install.sh --modules gui' instead. This script will be removed in a future version."
exec "$SCRIPT_DIR/install.sh" --modules gui --no-confirm
