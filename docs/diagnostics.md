# Diagnostics and support reports

Use `archer` after installing the core package or `python3 gui/archer_cli.py` in the checkout. These commands need no daemon. A mounted target must have its sysfs tree available. An offline root cannot establish its running kernel or desktop session.

```sh
archer --root /mnt status
archer inventory
archer readiness
archer snapshot > before.json
archer compare before.json
archer sleep-sample > before-sleep.json
archer sleep-compare before-sleep.json
archer report > reviewed-report.json
archer issue
```

Compare commands read the current machine and a saved local sample. Sleep deltas report each battery's native units and elapsed time. Save the initial sample immediately before sleeping. A change identifies a difference to investigate. Actual functional checks require the relevant device.

## What a report contains

The report includes the Archer version, DMI product/board and BIOS fields, distribution, architecture, kernel, selected package versions, numeric device and subsystem IDs, driver names, interface names, sleep capabilities, readiness and grouped health outcomes. The CLI adds a local source digest. Exported device numbers apply only to that report. The full inventory contains local paths, interface names, topology and modaliases. Keep it local unless you have reviewed it.

The support projection omits serial numbers, UUIDs, network addresses, wireless names, fingerprints, modem subscriber details, usernames, home paths, raw journals, kernel command lines, arbitrary configuration, microphone samples and camera frames. Model and version strings still describe your hardware. Edit them if needed before sharing. **System → Hardware checks** provides an editable preview and copies only the text you select to share. All exports are local. [Report schema](../schemas/support-report-v1.json)

## Session checks

Run `archer-session peripheral network` for carrier, Ethernet speed/duplex, radio blocks, manager-reported authentication/addressing and resolver configuration, or choose `bluetooth`, `wwan`, `orientation`, `storage` or `fingerprint`. Running services supply their own read-only results. A manager-reported connected state or configured DNS server still requires a functional test to establish reachability. Use your network manager to reconnect or activate mobile service. Enrollment runs in the foreground through fprintd with its normal authorization flow. `archer-session npu-test` requires NumPy, OpenVINO and an available NPU execution device. It compiles a tiny local ReLU graph and verifies the execution device and output.

For a lighting, firmware or battery issue, record the exact model, BIOS, kernel, backend, request, observed state and recovery. For a GUI issue, include the daemon and client versions plus the part of the report you reviewed. [Issue template](../.github/ISSUE_TEMPLATE/gui-not-updating.md)

The initial scan is passive. Each explicit GUI scan records a new snapshot and retains up to 20 snapshots in `$XDG_STATE_HOME/archer/health-history.json`, defaulting to `~/.local/state/archer`. The history is private to the login user and is separate from the support export. The Overview graph retains about two minutes of readings in memory.

The core package installs pre- and post-transaction pacman hooks for Archer, kernel, graphics, firmware, media and power packages. They keep the latest two system snapshots under `/var/lib/archer/update-health`. Use `archer update-compare` after restarting to inspect changes since the pre-update snapshot. Hardware checks also show devices that disappeared. Package versions and timing can help locate a regression. A comparison cannot establish its cause.

Readiness reports identify the advisory source. To use an independently signed catalog, install the authorized publisher keyring as `/etc/archer/advisory-keys.gpg` and the reviewed `catalog.json` and detached `catalog.json.sig` in `/var/lib/archer/advisories/`. Keep these paths administrator-owned. Archer checks the signature, schema and minimum catalog date before evaluating the fixed data rules. Rejected catalogs fall back to the bundled rules with an explicit status.
