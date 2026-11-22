"""Interactive, unprivileged probes through the desktop's hardware services."""
from pathlib import Path
import re
import subprocess

from archer_inventory import read

FPRINT = "net.reactivated.Fprint"
MANAGER = "/net/reactivated/Fprint/Manager"
MM = "org.freedesktop.ModemManager1"
SENSOR = "net.hadess.SensorProxy"
BT = "org.bluez"
NM = "org.freedesktop.NetworkManager"
NETWORKD = "org.freedesktop.network1"
IWD = "net.connman.iwd"
CONNMAN = "net.connman"
PROPERTIES = "org.freedesktop.DBus.Properties"
OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"
MAX_DEVICES = 32
MAX_OBJECTS = 128
FINGERS = tuple(f"{hand}-{part}" for hand in ("left", "right") for part in
                ("thumb", "index-finger", "middle-finger", "ring-finger", "little-finger"))


def call(bus, name, path, interface, method, signature="", arguments=(), activate=False):
    from gi.repository import Gio, GLib
    reply = bus.call_sync(name, path, interface, method, GLib.Variant(signature, arguments) if signature else None,
                          None, Gio.DBusCallFlags.NONE if activate else Gio.DBusCallFlags.NO_AUTO_START, 3500, None)
    return reply.unpack()[0]


def readers(bus, query=call):
    paths = query(bus, FPRINT, MANAGER, FPRINT + ".Manager", "GetDevices", activate=True)
    result = []
    for path in paths:
        properties = query(bus, FPRINT, path, "org.freedesktop.DBus.Properties", "GetAll", "(s)", (FPRINT + ".Device",))
        result.append({"name": properties.get("name"), "scan_type": properties.get("scan-type"),
                       "enrollment_stages": properties.get("num-enroll-stages")})
    return result


def enroll(finger, bus, query=call):
    if finger not in FINGERS:
        raise ValueError("Choose one supported finger")
    paths = query(bus, FPRINT, MANAGER, FPRINT + ".Manager", "GetDevices", activate=True)
    if len(paths) != 1:
        raise ValueError("Enrollment requires one unambiguous reader. Use your desktop's account settings to choose between readers.")
    # fprintd owns authorization, fingerprint storage and cancellation on disconnect.
    # Its foreground tool supplies scan feedback and uses the calling user's account.
    return subprocess.call(["/usr/bin/fprintd-enroll", "-f", finger])


def _integer(value, minimum=0, maximum=1_000_000):
    if isinstance(value, bool):
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if minimum <= value <= maximum else None


def _enum(value, names, default="unknown"):
    value = _integer(value, -1, 1024)
    return names.get(value, default)


MODEM_STATES = {-1: "failed", 0: "unknown", 1: "initializing", 2: "locked", 3: "disabled",
                4: "disabling", 5: "enabling", 6: "enabled", 7: "searching", 8: "registered",
                9: "disconnecting", 10: "connecting", 11: "connected"}
REGISTRATION_STATES = {0: "unknown", 1: "idle", 2: "home", 3: "searching", 4: "denied",
                       5: "roaming", 6: "home_sms_only", 7: "roaming_sms_only",
                       8: "emergency_only", 9: "home_csfb_not_preferred",
                       10: "roaming_csfb_not_preferred", 11: "attached_rlos"}
PACKET_STATES = {0: "unknown", 1: "detached", 2: "attaching", 3: "attached", 4: "detaching"}


def modems(bus, query=call):
    objects = query(bus, MM, "/org/freedesktop/ModemManager1", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects")
    result = []
    bounded_objects = list(objects.items())[:MAX_OBJECTS]
    for path, obj in bounded_objects:
        props = obj.get(MM + ".Modem")
        if not isinstance(props, dict):
            continue
        state_code = _integer(props.get("State"), -1, 11)
        quality = props.get("SignalQuality")
        signal = _integer(quality[0], 0, 100) if isinstance(quality, (tuple, list)) and quality else None
        registration = obj.get(MM + ".Modem.Modem3gpp", {}).get("RegistrationState")
        packet = obj.get(MM + ".Modem.Modem3gpp", {}).get("PacketServiceState")
        registration_name = _enum(registration, REGISTRATION_STATES)
        bearers = set(props.get("Bearers") or [])
        connected = suspended = 0
        for bearer_path, bearer in bounded_objects:
            bearer_props = bearer.get(MM + ".Bearer", {})
            if bearer_path in bearers and isinstance(bearer_props, dict):
                connected += int(bearer_props.get("Connected") is True)
                suspended += int(bearer_props.get("Suspended") is True)
        result.append({
            # Retain the old numeric fields for machine-readable compatibility.
            "state_code": state_code,
            "radio_technologies": _integer(props.get("AccessTechnologies"), 0, 2 ** 31 - 1),
            "state": _enum(state_code, MODEM_STATES),
            "registered": None if registration_name == "unknown" else registration_name in
            ("home", "roaming", "home_sms_only", "roaming_sms_only", "home_csfb_not_preferred",
             "roaming_csfb_not_preferred", "attached_rlos"),
            "registration": registration_name,
            "packet_service": _enum(packet, PACKET_STATES),
            "connected_bearers": min(connected, MAX_DEVICES),
            "suspended_bearers": min(suspended, MAX_DEVICES),
            "signal_percent": signal,
        })
        if len(result) >= MAX_DEVICES:
            break
    return result


def bluetooth(bus, query=call):
    objects = query(bus, BT, "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects")
    bounded_objects = list(objects.values())[:MAX_OBJECTS]
    adapters = [{"powered": obj["org.bluez.Adapter1"].get("Powered") is True,
                 "discovering": obj["org.bluez.Adapter1"].get("Discovering") is True}
                for obj in bounded_objects if "org.bluez.Adapter1" in obj]
    uuid_roles = {
        "0000110a-0000-1000-8000-00805f9b34fb": "audio_source",
        "0000110b-0000-1000-8000-00805f9b34fb": "audio_sink",
        "00001108-0000-1000-8000-00805f9b34fb": "headset",
        "00001112-0000-1000-8000-00805f9b34fb": "headset_gateway",
        "0000111e-0000-1000-8000-00805f9b34fb": "hands_free",
        "0000111f-0000-1000-8000-00805f9b34fb": "hands_free_gateway",
    }
    devices = []
    transports = []
    for obj in bounded_objects:
        device = obj.get("org.bluez.Device1")
        if isinstance(device, dict) and device.get("Connected") is True:
            roles = sorted({uuid_roles[uuid.lower()] for uuid in device.get("UUIDs", [])
                            if isinstance(uuid, str) and uuid.lower() in uuid_roles})
            devices.append({"connected": True, "paired": device.get("Paired") is True,
                            "trusted": device.get("Trusted") is True, "audio_roles": roles})
        transport = obj.get("org.bluez.MediaTransport1")
        if isinstance(transport, dict):
            state = transport.get("State")
            transports.append({"state": state if state in ("idle", "pending", "active") else "unknown"})
    return {"adapters": adapters[:MAX_DEVICES], "connected_devices": devices[:MAX_DEVICES],
            "audio_transports": transports[:MAX_DEVICES]}


def orientation(bus, query=call):
    props = query(bus, SENSOR, "/net/hadess/SensorProxy", "org.freedesktop.DBus.Properties", "GetAll", "(s)", (SENSOR,))
    return {k: props[k] for k in ("HasAccelerometer", "AccelerometerOrientation", "HasAmbientLight",
                                  "HasProximity", "HasCompass") if k in props}


def links(sysfs):
    links = []
    for node in sorted((sysfs / "class/net").glob("*")):
        if not (node / "device").exists():
            continue
        # Inspect carrier and interface state without scanning, resetting or exporting addresses.
        wireless = (node / "wireless").is_dir()
        operstate = read(node / "operstate")
        carrier = read(node / "carrier")
        row = {"kind": "wireless" if wireless else "ethernet",
               "operstate": operstate if operstate in ("up", "down", "dormant", "lowerlayerdown", "unknown") else "unknown",
               "carrier": True if carrier == "1" else False if carrier == "0" else None}
        if not wireless:
            row["speed_mbps"] = _integer(read(node / "speed"), 0, 1_000_000)
            duplex = (read(node / "duplex") or "").lower()
            row["duplex"] = duplex if duplex in ("full", "half") else "unknown"
        links.append(row)
        if len(links) >= MAX_DEVICES:
            break
    blocks = [{"type": read(p / "type"), "hardware": read(p / "hard"),
               "software": read(p / "soft")}
              for p in sorted((sysfs / "class/rfkill").glob("*"))[:MAX_DEVICES]]
    return {"interfaces": links, "radio_blocks": blocks}


NM_STATES = {0: "unknown", 10: "asleep", 20: "disconnected", 30: "disconnecting",
             40: "connecting", 50: "connected_local", 60: "connected_site", 70: "connected_global"}
NM_CONNECTIVITY = {0: "unknown", 1: "none", 2: "portal", 3: "limited", 4: "full"}
NM_DEVICE_TYPES = {1: "ethernet", 2: "wireless", 5: "bluetooth", 8: "wwan"}
NM_DEVICE_STATES = {0: "unknown", 10: "unmanaged", 20: "unavailable", 30: "disconnected",
                    40: "prepare", 50: "config", 60: "need_auth", 70: "ip_config",
                    80: "ip_check", 90: "secondaries", 100: "activated",
                    110: "deactivating", 120: "failed"}


def _nm_phase(state):
    state = _integer(state, 0, 120)
    authentication = ("failed" if state == 120 else "required" if state == 60
                      else "complete" if state is not None and state >= 70 else "not_started")
    address = ("configuring" if state == 70 else "configured" if state is not None and 80 <= state <= 100
               else "failed" if state == 120 else "not_started")
    return authentication, address


def _dns_evidence(resolv_conf, connectivity="not_exposed"):
    resolver = read(resolv_conf) or ""
    configured = any(line.lstrip().startswith("nameserver ") for line in resolver.splitlines()[:64])
    return {"configured": configured, "connectivity": connectivity if configured else "not_configured"}


def _interface_node(sysfs, interface):
    valid = (isinstance(interface, str)
             and interface not in (".", "..")
             and re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", interface))
    if valid:
        return sysfs / "class/net" / interface
    return None


def network_manager(bus, sysfs, query=call, resolv_conf=Path("/etc/resolv.conf")):
    root = query(bus, NM, "/org/freedesktop/NetworkManager", PROPERTIES, "GetAll", "(s)", (NM,))
    paths = query(bus, NM, "/org/freedesktop/NetworkManager", NM, "GetDevices")
    interfaces = []
    for path in list(paths)[:MAX_DEVICES]:
        props = query(bus, NM, path, PROPERTIES, "GetAll", "(s)", (NM + ".Device",))
        state = _integer(props.get("State"), 0, 120)
        authentication, address = _nm_phase(state)
        interface_node = _interface_node(sysfs, props.get("Interface"))
        carrier = read(interface_node / "carrier") if interface_node else None
        operstate = read(interface_node / "operstate") if interface_node else None
        row = {"kind": NM_DEVICE_TYPES.get(_integer(props.get("DeviceType"), 0, 32), "other"),
               "managed": props.get("Managed") is True, "state_code": state,
               "state": _enum(state, NM_DEVICE_STATES),
               "authentication": authentication, "dhcp_or_address": address,
               "address_configuration": address,
               "carrier": True if carrier == "1" else False if carrier == "0" else None,
               "operstate": operstate if operstate in
               ("up", "down", "dormant", "lowerlayerdown", "unknown") else "unknown"}
        if row["kind"] == "ethernet":
            wired = query(bus, NM, path, PROPERTIES, "GetAll", "(s)", (NM + ".Device.Wired",))
            if isinstance(wired.get("Carrier"), bool):
                row["carrier"] = wired["Carrier"]
            row["speed_mbps"] = _integer(wired.get("Speed"), 0, 1_000_000)
            if interface_node:
                duplex = (read(interface_node / "duplex") or "").lower()
                row["duplex"] = duplex if duplex in ("full", "half") else "unknown"
        interfaces.append(row)
    connectivity = _enum(root.get("Connectivity"), NM_CONNECTIVITY)
    return {"name": "NetworkManager", "state": _enum(root.get("State"), NM_STATES),
            "connectivity": connectivity, "interfaces": interfaces,
            "dns": _dns_evidence(resolv_conf, connectivity)}


def networkd(bus, sysfs=Path("/sys"), query=call):
    values = query(bus, NETWORKD, "/org/freedesktop/network1", NETWORKD + ".Manager", "ListLinks")
    rows = []
    for value in list(values)[:MAX_DEVICES]:
        interface = value[1] if isinstance(value, (tuple, list)) and len(value) >= 2 else None
        path = value[2] if isinstance(value, (tuple, list)) and len(value) >= 3 else None
        if not isinstance(path, str):
            continue
        props = query(bus, NETWORKD, path, PROPERTIES, "GetAll", "(s)", (NETWORKD + ".Link",))
        interface_node = _interface_node(sysfs, interface)
        operational = props.get("OperationalState")
        carrier = props.get("CarrierState")
        address = props.get("AddressState")
        wireless = bool(interface_node and (interface_node / "wireless").is_dir())
        ethernet = bool(interface_node and not wireless and (interface_node / "device").exists())
        row = {"kind": "wireless" if wireless else "ethernet" if ethernet else "other",
               "operational": operational if operational in
               ("off", "no-carrier", "dormant", "degraded-carrier", "degraded", "routable") else "unknown",
               "carrier": carrier if carrier in ("off", "no-carrier", "carrier", "enslaved") else "unknown",
               "dhcp_or_address": address if address in ("off", "degraded", "routable") else "unknown",
               "address_configuration": address if address in ("off", "degraded", "routable") else "unknown",
               "authentication": "not_exposed"}
        if ethernet:
            row["speed_mbps"] = _integer(read(interface_node / "speed"), 0, 1_000_000)
            duplex = (read(interface_node / "duplex") or "").lower()
            row["duplex"] = duplex if duplex in ("full", "half") else "unknown"
        rows.append(row)
    return {"name": "systemd-networkd", "interfaces": rows}


def connman(bus, sysfs=Path("/sys"), query=call, resolv_conf=Path("/etc/resolv.conf")):
    services = query(bus, CONNMAN, "/", CONNMAN + ".Manager", "GetServices")
    rows = []
    overall = "none"
    for service in list(services)[:MAX_DEVICES]:
        props = service[1] if isinstance(service, (tuple, list)) and len(service) >= 2 else None
        if not isinstance(props, dict):
            continue
        state = props.get("State")
        state = state if state in ("idle", "association", "configuration", "ready", "disconnect",
                                   "online", "failure") else "unknown"
        error = props.get("Error")
        authentication = ("failed" if state == "failure" and error in
                          ("invalid-key", "blocked", "auth-failed") else
                          "in_progress" if state == "association" else
                          "complete" if state in ("configuration", "ready", "online") else "not_started")
        address = ("configuring" if state == "configuration" else
                   "configured" if state in ("ready", "online") else
                   "failed" if state == "failure" else "not_started")
        ethernet = props.get("Ethernet") if isinstance(props.get("Ethernet"), dict) else {}
        interface_node = _interface_node(sysfs, ethernet.get("Interface"))
        carrier = read(interface_node / "carrier") if interface_node else None
        kind = props.get("Type")
        row = {"kind": kind if kind in ("ethernet", "wifi", "cellular", "bluetooth") else "other",
               "state": state, "carrier": True if carrier == "1" else False if carrier == "0" else None,
               "authentication": authentication, "dhcp_or_address": address,
               "address_configuration": address}
        if kind == "ethernet":
            row["speed_mbps"] = _integer(ethernet.get("Speed"), 0, 1_000_000)
            duplex = str(ethernet.get("Duplex", "")).lower()
            row["duplex"] = duplex if duplex in ("full", "half") else "unknown"
        rows.append(row)
        overall = "full" if state == "online" else "local" if state == "ready" and overall != "full" else overall
    return {"name": "ConnMan", "connectivity": overall, "interfaces": rows,
            "dns": _dns_evidence(resolv_conf, overall)}


def iwd(bus, query=call):
    objects = query(bus, IWD, "/", OBJECT_MANAGER, "GetManagedObjects")
    rows = []
    for obj in list(objects.values())[:MAX_DEVICES]:
        station = obj.get(IWD + ".Station")
        if not isinstance(station, dict):
            continue
        state = station.get("State")
        states = ("disconnected", "connecting", "connected", "disconnecting", "roaming")
        rows.append({"state": state if state in states else "unknown",
                     "scanning": station.get("Scanning") is True,
                     "authentication": "complete" if state in ("connected", "roaming") else
                     "in_progress" if state == "connecting" else "not_started"})
    return rows


def network(bus, sysfs=Path("/sys"), query=call, resolv_conf=Path("/etc/resolv.conf")):
    result = links(sysfs)
    result["manager"] = None
    result["dns"] = _dns_evidence(resolv_conf)
    result["wifi_authentication"] = []
    try:
        result["manager"] = network_manager(bus, sysfs, query, resolv_conf)
    except Exception:
        try:
            result["manager"] = networkd(bus, sysfs, query)
        except Exception:
            try:
                result["manager"] = connman(bus, sysfs, query, resolv_conf)
            except Exception:
                result["manager"] = {"name": "unavailable", "interfaces": []}
    if "dns" in result["manager"]:
        result["dns"] = result["manager"]["dns"]
    try:
        result["wifi_authentication"] = iwd(bus, query)
    except Exception:
        pass
    if result["manager"]["name"] == "unavailable" and result["wifi_authentication"]:
        interfaces = []
        for row in result["wifi_authentication"]:
            interfaces.append(dict(row, dhcp_or_address="not_exposed",
                                   address_configuration="not_exposed"))
        result["manager"] = {"name": "iwd", "interfaces": interfaces}
    return result


def storage(sysfs):
    return {"controllers": [{"state": read(p / "state"), "firmware": read(p / "firmware_rev"),
                             "runtime_power": read(p / "device/power/runtime_status")}
                            for p in sorted((sysfs / "class/nvme").glob("*")) if re.fullmatch(r"nvme\d+", p.name)],
            "removable_media": [{"sectors": read(p / "size"), "read_only": read(p / "ro")}
                                for p in sorted((sysfs / "class/block").glob("*")) if read(p / "removable") == "1"]}


def probe(name, sysfs=Path("/sys")):
    if name == "storage":
        return storage(sysfs)
    from gi.repository import Gio, GLib
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        operation = {"fingerprint": readers, "wwan": modems, "bluetooth": bluetooth,
                     "orientation": orientation, "network": lambda value: network(value, sysfs)}[name]
        return {"result": operation(bus)}
    except GLib.Error as error:
        return {"result": None, "reason": Gio.DBusError.get_remote_error(error) or "service_unavailable"}
