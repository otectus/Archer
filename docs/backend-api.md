# Architecture and API in 2.1

The shared inventory walks Linux bus and class interfaces. It records physical topology, numeric IDs, kernel binding, exposed attributes and duplicate identities. A generation token changes when bindings or interfaces change. Consumers must rescan a selected device after hotplug. Local identity can change when a device changes physical ports. Duplicate identities cannot safely select a writable device. The installer consumes a NUL-delimited projection from the same Python scanner. The report exports a separate allowlisted projection. [Inventory schema](../schemas/inventory-v1.json)

## System control service

The bus name and interface are `io.otectus.Archer1` and the object path is `/io/otectus/Archer1`. Replies are JSON strings with a boolean `success` and a `data` object or an `error`. Existing methods and signals remain available. Check `Ping` for `interface` and `capability_schema` before using additive methods. Legacy clients can continue using `GetAllSettings` and `GetMonitoringData`. Client timeouts and authorization failures cannot establish that the hardware changed.

| Method | Arguments | Result or effect |
| --- | --- | --- |
| Ping | none | Service version, interface major and capability schema major |
| GetCapabilities | none | Capabilities and inventory generation |
| GetPolicyStatus | none | Detected owners and policy conflicts |
| GetBatteryDevices | none | Per-device observations, units and control constraints |
| GetGraphicsPlan | mode string | Candidate packages, current owner, targets, active outputs and reasons |
| ApplyGraphicsPlan | mode string, review token | Re-evaluates the plan and rejects changes in device or boot evidence before applying. |
| SetFanDevice | fan ID, integer percent | Manual PWM through the selected native backend |
| SetFanMode | fan ID, mode | Native Auto or Turbo |
| SetNativeFanCurve | JSON with fan_id, sensor_id, points, duration_seconds and resume_after_suspend, or fan_id and enabled=false | Starts or releases a bounded curve for one native channel. |
| RestoreFirmwareAuto | none | Request the firmware cooling default |
| SetBatteryThreshold | device key, start and end integers | Request supported thresholds. Use −1 for an absent field. |
| ChargeFullOnce | device key, duration in seconds (300–172800) | A temporary charge limit with conflict-aware restoration |

A capability describes its backend, owner, availability, mutability, value type, constraints, evidence and reason. A state separates desired, persisted, applied and observed values. A successful write without reliable readback is unverified. Metrics include units, validity, source and time. Older positional telemetry fields remain for older clients. See the exact fields in the [capability schema](../schemas/capabilities-v1.json) and [state schema](../schemas/state-v1.json).

## Ownership and permissions

Read-only queries are available to system-bus clients. Mutations pass the corresponding polkit action and serialize through the daemon. The installer owns recorded files and service transitions. Package application code lives under `/usr/lib/archer/app` and script payloads under `/opt/archer`. Both use `gui/VERSION`. Session tasks use the desktop power-profile provider, logind, the selected media server or the device's own service. Workspace imports accept a fixed preset and a bounded duration. [Workspace schema](../schemas/workspace-v1.json)

The daemon coalesces kernel device events and rescans through its maintenance worker. Clients receive the `InventoryChanged(s)` generation signal and refresh their settings. Native curve leases retain their selected local device identities through an eligible resume.
