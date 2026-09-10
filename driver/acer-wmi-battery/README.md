# Archer battery fallback package

`source.conf` pins upstream `9f90d75cc9237aeed7964622d10dbdf4d2c7b518` as
`acer-wmi-battery/0.1.0.archer1`. The upstream 6.12 unaligned-header fix is
included; no additional C patch is required. `patches/series` is intentionally
empty apart from its comment. Preparation and per-target Kbuild use the same
helpers as Linuwu-Sense, avoiding upstream's running-kernel-only Makefile and
providing the DKMS metadata missing upstream.

Use `bash prepare.sh DIRECTORY [LOCAL_GIT_REPO]`; an identical prepared tree is
accepted on repeated runs, while modified sources fail validation. Increment
the Archer version for any source, patch or shared build-helper change. The
installer prefers an existing equivalent interface and removes only its own
DKMS namespace or manifest-owned legacy sources. See the
[kernel audit and validation](../../docs/kernel-compatibility.md).
