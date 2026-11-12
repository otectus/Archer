# Archer battery fallback package

`source.conf` pins upstream `9f90d75cc9237aeed7964622d10dbdf4d2c7b518` as
`acer-wmi-battery/0.1.0.archer2`. The upstream 6.12 unaligned-header fix is
included. Patch `0001` validates ACPI object type, pointer and length before
buffer access; patch `0002` reports failed setters, failed queries and mismatched
readback as sysfs errors without falsely updating the cache. Preparation and per-target Kbuild use the same
helpers as Linuwu-Sense, avoiding upstream's running-kernel-only Makefile and
providing the DKMS metadata missing upstream.

Use `bash prepare.sh DIRECTORY [LOCAL_GIT_REPO]`; an identical prepared tree is
accepted on repeated runs, while modified sources fail validation. Increment
the Archer version for any source, patch or shared build-helper change. The
installer prefers an existing equivalent interface and removes only its own
DKMS namespace or manifest-owned legacy sources. See the
[kernel audit and validation](../../docs/kernel-compatibility.md).

The battery-only service and GUI share `/etc/archer/battery.json`. The default is an 80% limit, but an existing valid GUI choice is migrated. Boot and power-supply events apply that saved choice. Calibration is detected independently of Linuwu-Sense and can start only with verified external power.
