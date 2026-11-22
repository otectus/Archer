# Feature and platform compatibility

Evidence reviewed on November 21, 2026. **Discovered** means the operating system exposes an interface. **Automated** denotes a repeatable fixture, build or isolated session. Physical support requires the exact feature, model, BIOS, kernel and observed outcome. Unknown or unavailable values remain visible in diagnostics. No whole laptop family receives a blanket support rating.

| Feature | Backend and evidence | Limits and next qualification |
| --- | --- | --- |
| Temperatures, fans and profiles | Native `acer_wmi` hwmon and platform-profile, or qualified Linuwu WMI. Readback and fail-safe fixtures exercise each ABI independently. | Verify RPM, sensor identity, resume and return to Auto on each board. Unsupported layouts remain telemetry-only. |
| Battery | Enumerated power supplies expose native units, capacity, wear and cycle count when present. The daemon separates bounded variable thresholds from the Acer fixed charge mode. | Missing bounds or another policy owner block threshold writes. Test 300–172800-second override expiry, replug and actual charge behavior per pack. |
| Graphics | Numeric PCI identities, installed NVIDIA support data, kernel target modules, signatures, renderer/offload and connected-output evidence. SwitcherooControl can launch a selected desktop application. | Package ownership stays with chwd/mhwd or the distribution when applicable. Unknown and mixed generation plans can be blocked. A changed configured mode needs reboot and physical display validation. |
| Panel response, startup sound and keyboard timeout | Existing binary controller and issue #10 tests maintain observed, requested and saved values independently. | PT516-52S requires visible panel confirmation with unreadable firmware readback. |
| Lighting | ENE K5130 has previous PHN16S-71 operator observations. Other qualified Linuwu WMI effects and standard keyboard LED brightness have separate paths. | No newly qualified HID protocol. A successful firmware reply alone cannot verify the color on the keyboard. [ENE protocol](ENE_PROTOCOL.md) |
| Input and networking | Transport and current-symptom gates, legacy config ownership checks, GNOME touchpad settings, network-manager stages, resolver configuration, Ethernet link parameters and BlueZ/ModemManager read-only status. | Other compositors retain their own controls. Driver rebind, radio coexistence, pairing, pen mapping and docking require device-specific qualification. |
| Audio | Private PipeWire/WirePlumber test with generated sound, real rnnoise, WebRTC echo cancellation, server restart, automatic reconnection and virtual-source cleanup. Session meter selects a physical source. | Requires a working microphone and the correct sound server/plugins. Internal arrays, HDMI, SOF/UCM, headset routing and real hotplug need separate tests. |
| Camera | Capture/output capability probing, actual FFmpeg worker, preview and four distinct generated-frame transforms. | A booted Arch guest delivered frames from vivid to v4l2loopback. Physical UVC verification needs a camera and loaded loopback. MIPI/ISP requires its complete libcamera pipeline. Chroma-key blur requires a green background. |
| Sleep, display and docks | Sleep sampling with per-battery units, hibernate preflight, normal logind backlight writes and debounced session power/dock plans. | No generic hibernate enablement, Thunderbolt authorization, monitor routing or measured battery-life claim. Validate encrypted swap and resume physically. |
| Firmware | Local fwupd devices, releases, prerequisites, metadata freshness, explicit per-device workflow. | Vendor-only devices need exact official downloads and recovery guidance. A supported device can have unknown metadata. |
| Storage, readers and security | NVMe controller state and firmware, removable media presence, TPM/Secure Boot evidence, fprintd reader and user-selected enrollment. | A raw USB ID cannot establish biometric functionality. Test with the installed libfprint version. Never include enrollment data in reports. [fprintd device API](https://fprint.freedesktop.org/fprintd-dev/Device.html) |
| Sensors, mobile broadband and inference | iio-sensor-proxy orientation, ModemManager radio state, accelerator inventory, user-initiated OpenVINO inference with execution-target check. | Verify physical rotation, carrier activation, precise runtime/firmware/device pairing and power use on an available unit. No model downloads. |

## Distribution and desktop scope

| Environment | Implemented behavior | Qualification |
| --- | --- | --- |
| Arch x86-64, systemd | Native split packages, script transactions, system bus and polkit, configured boot adapters | Package lifecycle and module-build checks are recorded in the implementation report. |
| CachyOS, systemd | Inventory, native owner detection, target kernel metadata and toolchains. chwd manages distribution-owned GPU configuration. | Current stable and LTS header-build targets. The local machine supplied passive discovery evidence only. |
| Manjaro, systemd | Detect distribution and preserve mhwd graphics ownership | Resolver fixtures. Boot, MHWD migration and hardware qualification remain open. |
| Garuda and EndeavourOS, systemd | Arch package discovery and explicit boot-stack inspection. Garuda retains graphics ownership. | Configuration fixtures. No end-to-end installed-desktop qualification. |
| Artix, OpenRC | Explicit subset of installer modules and service enable/active restoration using rc-service and rc-update | Two-boot QEMU audio-module install, verify and uninstall. Real OpenRC service restoration and external-change detection. See the platform adapter matrix. |
| runit, s6, dinit and other distributions | Python inventory and diagnostics where Linux interfaces are present | No system-service installation claim. |
| GNOME, KDE and compositors | Native GTK/Adwaita interface. GNOME settings only when the active session exposes them. Tray needs a StatusNotifier host. | Xvfb and headless Sway with captures, 47 interaction checks under native KWin 6.8.3, private D-Bus and simulated tray checks. GNOME Shell 51.1 created a compositor socket but could not launch the client without its system services. Physical display scanout, AT-SPI screen readers, tray-host recovery and complete desktop policy sessions need verification. |

See [boot adapters](platform-adapters.md) for supported mutations and [the implementation report](implementation-2.1.md) for the exact release evidence.

## Physical qualification

The PHN16-71 with BIOS V1.20 and kernel 6.18.60-2-cachyos-lts supplied a read-only discovery sample on November 21. No new cold-boot, suspend, charge, fan, lighting or media observation on that laptop entered the 2.1 support matrix. Existing historical ENE observations describe a different machine, the PHN16S-71. The ANV16S-41 WMI implementation retains upstream-reported support. [ANV16S-41 record](anv16s-41.md)

For PT516-52S, ANV16S-41, ANV15-41, a nongaming model, a convertible, older NVIDIA, docked hybrid and single-fan/multiple-provider cohorts, record a baseline, the approved change, physical result, recovery and restored ownership. Check persistent settings across three cold boots and ten suspend/resume cycles across AC and battery where practical. Include the exact BIOS, kernel, backend and date. Keep media local. Compare cooling or energy consumption only with a controlled load, duration and environmental conditions.
