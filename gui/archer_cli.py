#!/usr/bin/env python3
"""Archer hardware status and rescue diagnostics."""
import argparse
import json
from pathlib import Path
import sys

from archer_health import compare, health, package_versions, report, sleep_comparison, sleep_sample, snapshot
from archer_inventory import collect_inventory
from archer_readiness import readiness
from archer_version import VERSION


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=VERSION)
    parser.add_argument("--root", type=Path, default=Path("/"), help="Mounted target with a sysfs tree")
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("inventory", "status", "report", "issue", "snapshot", "readiness", "sleep-sample", "update-compare"):
        sub.add_parser(name)
    sub.add_parser("compare").add_argument("before", type=Path)
    sub.add_parser("sleep-compare").add_argument("before", type=Path)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    inventory = collect_inventory(root)
    if args.action == "inventory":
        result = inventory
    elif args.action == "status":
        ready = readiness(root, inventory, package_versions(root))
        result = {"schema_version": 1, "generation": inventory["generation"], "health": health(inventory, ready),
                  "environment": inventory["environment"], "model": inventory["dmi"]["product_name"]}
    elif args.action == "readiness":
        result = readiness(root, inventory, package_versions(root))
    elif args.action == "update-compare":
        from archer_update_history import regression
        result = regression(root, snapshot(inventory, root)) or {"status": "no_pre_update_snapshot"}
    elif args.action == "issue":
        data = report(inventory, root)
        print(f"## Environment\n\nArcher {VERSION}, {data['model'].get('product_name') or 'unknown model'}, "
              f"{data['environment']['distribution']['id']}, kernel {data['environment']['kernel'] or 'unknown'}.\n"
              "\n## Affected device or capability\n\n\n## Steps to reproduce\n\n1. "
              "\n\n## Expected behavior\n\n\n## Observed behavior\n\n\n## Reviewed evidence\n")
        return 0
    elif args.action == "report":
        result = report(inventory, root)
    elif args.action.startswith("sleep-"):
        result = sleep_sample(root / "sys")
        if args.action == "sleep-compare":
            before = json.loads(args.before.read_text())
            result = sleep_comparison(before, result)
    else:
        result = snapshot(inventory, root)
        if args.action == "compare":
            result = compare(json.loads(args.before.read_text()), result)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, TypeError, KeyError, ValueError) as error:
        sys.exit(f"Archer scan failed: {error}")
