# Archer 2.1.0 implementation and qualification

Working-tree update based on `62e96c4d397e438c65dc5f3704e0f47ee152c649`. Validation date November 21, 2026. The hardware and distribution release gate remains open. The table records software and qualification separately so a successful fixture does not silently expand the list of tested laptops.

| Work | Implemented changes and evidence | Outstanding qualification or implementation limits |
| --- | --- | --- |
| W01 | Reproduced the audited baseline in a separate checkout, including 107 Python tests with the pinned upstream fixtures. Retained the binary-state, C, GUI and D-Bus suites. | Historical reports retain their original scope. |
| W02 | Common multi-bus sysfs scanner, stable local keys, generation, shell projection and offline report. Renumber/rebind, multiple battery/GPU and privacy fixtures. | Indistinguishable devices require manual disambiguation. |
| W03 | Additive D-Bus version handshake, typed capabilities, leases, measurements and per-device controls. JSON schemas and legacy client paths. | Some legacy controls still use their original payloads. |
| W04 | Native Acer PWM and labelled temperature backend, per-fan modes, source/owner checks and Auto restoration. Separate native and Linuwu tests. | Verify physical RPM, sensors and transitions by board and firmware. |
| W05 | Evidence-matched AMD/PS2 repairs, current network health, preservation of network manager and modified historical workaround files. | No generally qualified disruptive Wi-Fi rebind or PCI reset. |
| W06 | Exact GPU resolver, distro ownership, installed-kernel preflight, shared fixed-argument mode helper and review token, Switcheroo desktop launch. | Physical render-offload, dGPU runtime suspension and external connector wiring need validation. |
| W07 | Reviewed transaction journal, explicit recovery, boot-source inspection and verified rebuilds. Failed state and interruption fixtures. | Verify each supported loader/generator/signing combination on a bootable installation. [Adapters](platform-adapters.md) |
| W08 | Detect external policy providers, request expiring session holds through the PPD API, and conditionally restore prior owned state. | Run complete TLP/tlp-pd, tuned and desktop transitions on real sessions. |
| W09 | Pinned Linuwu forward-port with original input, firmware and fan safety properties, gated upstream-only hardware paths and kernel API shims. | New upstream transport/model writes remain experimental pending evidence. |
| W10 | Per-battery readings and validity, constraints, policy ownership, charge-full expiry and restoration. Multi-pack fixtures. | Physical charge hysteresis and calibration need measured evidence. |
| W11 | Native fan curves select a stable fan and sensor, expire, and restore owned channels on errors, suspend and shutdown. Crash recovery matches the journaled identity. | Verify the sensor mapping, Auto recovery and safe temperatures on each supported board. |
| W12 | Selected microphone, meter, per-microphone presets, rnnoise and WebRTC echo cancellation with explicit microphone and speaker routes. | Private-server restart and reconnect verified. A running request waits up to 30 seconds for its source. Listening tests for echo reduction and real Bluetooth/headset hotplug remain open. |
| W13 | Capability-probed physical source and loopback destination, preview, color, monochrome, framing and keyed backdrop blur. Generated FFmpeg frames verified. | Physical UVC and MIPI/libcamera pipelines require device-specific testing. Learned background segmentation is outside the implemented effects. |
| W14 | Before/after battery change, observed logind sleep/wake signals, wake IRQ, swap/resume/signing evidence, logind brightness, Type-C/DRM inventory and AC/dock presets. | Full hibernate, dock hotplug and adaptive display policy require hardware tests. |
| W15 | Existing ENE protocol narrowed by ownership, generic keyboard backlight uses logind, binary state remains explicit. | Additional controllers need dated protocol evidence. |
| W16 | fwupd status per device/release, metadata freshness, prerequisite errors and explicit interactive fixed-release installation. Bundled and optionally publisher-signed, data-only advisories. A changed signed catalog was rejected in an offline test. | Three reviewed Acer model-support routes are available. Live flashing and publisher signing require separate qualification or authority. |
| W17 | Six destinations with hardware checks, a resumable first-run goal workflow, pen/pointer tests, session presets, per-device controls, media and a user-initiated sleep observer. | 49 GTK checks passed on native Wayland with headless Sway. KWin passed 47 interaction checks. GNOME Shell started its compositor but could not run the client without desktop services. Screen-reader and full desktop acceptance remain open. |
| W18 | Editable allowlisted support report, issue template, capped session history, before/after comparisons and explicit session functional checks. | The core package additionally takes pre/post package snapshots. The signed offline bundle builder needs a matching target transaction, cached artifacts and trusted signer keyring. |
| W19 | Three application packages, two optional pinned DKMS recipes, unified version, checksum and file-journal adoption, config-safe removal and restore. | Publisher signing and repository distribution require maintainer-owned keys and infrastructure. |
| W20 | Arch and derivative inventory/ownership detection, restricted OpenRC modules with reversible service operations, alternate init read-only behavior. | A signed Artix OpenRC guest passed a two-boot audio-module lifecycle. Other derivatives, inits and real devices need qualification. |
| W21 | fprintd readers/enrollment, BlueZ, ModemManager, GNOME touchpad, iio-sensor-proxy, storage/reader health and optional local NPU graph. | Real devices, desktop rotation and pen mapping, CPU fallback rejection on hardware, carrier and storage health coverage need tests. |
| W22 | Scheduled current and historical kernel checks, clean packaging, private buses/media and GTK captures. | Hardware cohorts and distribution/boot combinations listed in the support matrix remain unqualified. |

## Disposition of reviewed findings

| Finding | Disposition and test focus |
| --- | --- |
| ARC-N01 | Numeric bus IDs and physical topology replace marketing-string control selection. Inventory fixture covers PCI display and audio on the same vendor. |
| ARC-N02 | Shell, service and report consume the common inventory. Multiple supplies and unknown/init-only environments are covered. |
| ARC-N03 | Preserve native interfaces and expose per-fan native operations with binding checks. Validate blacklist impact physically before recommending an extension. |
| ARC-N04 | Branch and distro ownership are part of the GPU plan. Legacy/unknown/unsupported packages stay gated. Blanket RTD3 is removed. |
| ARC-N05 | Installer and GUI mode changes enter the same privileged helper. The GUI revalidates its reviewed plan and readback. The shared helper journals the pre-change files and service state, and removal checks for later changes before restoring them. |
| ARC-N06 | Input repairs require matching transport and symptom. Administrator-edited files survive retirement. |
| ARC-N07 | The wireless module preserves the owner, evaluates current state and retires identified legacy config. No historical-log-triggered PCI reset. |
| ARC-N08 | Provider and lease fixtures exercise contention and conditional restoration. PPD/tlp-pd session workflows defer to the current owner. |
| ARC-N09 | Installer and privileged helper call the same boot adapter. Missing generators, command failures and stale or missing boot images are errors. Repeated reproducible image builds are accepted when the generator replaces the target. |
| ARC-N10 | Per-device units, bounds, missing attributes and override expiry have regression coverage. |
| ARC-N11 | Native Acer mappings and Linuwu monitoring use separate sensor semantics and explicit validity. |
| ARC-N12 | Arbitrary enumerated native channels pair with individually labelled sensors and an expiring curve. Failure and rebind tests check restoration and ownership. |
| ARC-N13 | Known ENE capability stays narrow. No new HID protocol has sufficient physical evidence for a stable profile. |
| ARC-N14 | fwupd result fixtures distinguish current, unsupported, stale, offline, blocked and applicable updates. |
| ARC-N15 | Installer catalog and session UI describe the real camera pipeline, with chroma-key blur identified explicitly. |
| ARC-N16 | PipeWire audio plugins are explicit dependencies. A synthetic source delivers signal through rnnoise in the private-session test. |
| ARC-N17 | CI includes rolling kernel, derivative build, dated baselines, package lifecycle and GUI/media session checks. Full cross-distro boot qualification remains open. |


## Executed qualification

The baseline was reproduced before the forward-port. The final runs passed 198 Python regression tests with the required upstream fixtures, 118 Bats checks and 14 GUI contract checks. The 2.1 regression lanes exercised compiled C fixtures, Python and shell tests, D-Bus/polkit contracts on a private bus, GTK interaction and capture at four widths in light, dark, high-contrast and large-text modes, and actual PipeWire/FFmpeg pipelines. Artificial sources make signal delivery and resource cleanup reproducible. A native headless Sway run also passed 49 GTK checks and produced 148 captures. On KWin 6.8.3 the GTK capture returned an empty render node. GNOME Shell 51.1 and Mutter created a virtual monitor, but their session required system-bus services absent in the test environment. A listener must confirm a physical speaker, and physical camera focus, exposure, or unplug still need device tests.

| Target | Checks completed on November 21, 2026 |
| --- | --- |
| Arch kernels | Linuwu-Sense, acer-wmi-battery and v4l2loopback built on stable 7.3.5-arch1-1, LTS 6.18.64-1-lts, Zen 7.3.5-zen1-1-zen and hardened 7.2.15-hardened1-3-hardened. Hardened used Arch’s matching GCC for its compiler plugins. |
| Historical Arch and CachyOS kernels | All three modules built on signed historical Arch 6.8.9-arch1-2 and 6.12.73-1-lts headers and on 7.3.4-1-cachyos and 6.18.60-2-cachyos-lts. The scheduled CI target also includes the distinct archived 6.12.75 build. |
| Arch x86-64 VM | Offline QEMU/KVM, two real systemd boots, the archived 2.0.1 GUI module and its manifest migrated to 2.1 pacman ownership, service state restored, packages reinstalled and verified, unprivileged CLI and live D-Bus calls, configuration and unrelated files retained. FFmpeg delivered four frames from a kernel vivid device through v4l2loopback. Pacman stopped removal on a modified graphics file. A retry restored its journaled state and rebuilt the boot image. Removing a subsequent install with no journal preserved external configuration. |
| Artix x86-64 VM | Signed November 2 OpenRC image, real installer dry-run, audio module with emulated HDA, verify after reboot, committed uninstall and orderly shutdown. A separate OpenRC service test verified enable/start, conditional restoration and external-owner contention. |
| Offline supply chain | Independent clean-root makepkg builds, package metadata and source archives. A disposable test signer exercised package and advisory verification. Tampering was rejected. Distribution requires the maintainer’s trusted signing key. |

Local build products are in `dist/2.1.0/`. `SHA256SUMS` covers the split packages, recipes, exact source, build epoch and metadata. The clean Arch build root used the x86-64 base-devel image with manifest digest `sha256:97119fd9e21219b02b3c14ba256c9d3c0d031d07fc26c836c41ed793beeb5b74`. Build that environment with the same package versions and paths from the embedded `.BUILDINFO`. The test logs and visual evidence are retained in the local validation directory recorded in the delivery summary.

A three-run read-only inventory sample on the PHN16-71 enumerated 704 records in a median 0.112 seconds wall time and 0.111 seconds CPU time, with a process peak of 21,280 KiB. This measures the scanner on that system. Battery energy, acoustic output and workload performance still require controlled physical tests.

No new Acer model or firmware combination earned physical verification in this update. The remaining release work includes additional exact vendor support routes, unsupported compositor and device-specific features, additional boot and distro combinations, and the hardware evidence requested in the handoff. The physical gate includes fan RPM, battery charging, runtime GPU suspend, dock routing, cold boots and repeated sleep/wake checks. [Compatibility matrix](compatibility-2.1.md)
