---
name: GUI not updating / daemon unreachable
about: Use this when the Archer GUI shows stale data, says "Offline" / "Stale", or won't open at all.
title: "[GUI] "
labels: [gui]
assignees: []
---

## What happened

Describe the steps, expected behavior and observed behavior. Include the affected destination or control.

## Environment

Give the Archer client and service versions, model, BIOS, kernel, distribution, desktop and tray host when relevant.

## Reviewed evidence

Run `archer issue` for a starting template or open **System → Hardware checks** and preview the report. Include the parts relevant to your problem after reviewing them. If Archer cannot connect, include the service state and the specific error from `journalctl -u archer-daemon -b`. Remove identifying paths and unrelated log content.

[GUI troubleshooting](https://github.com/otectus/Archer#troubleshooting)
