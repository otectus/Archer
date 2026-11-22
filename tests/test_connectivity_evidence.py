"""Privacy and read-only contracts for session connectivity evidence."""
from pathlib import Path
import json
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gui"))

from archer_peripherals import CONNMAN, IWD, MM, NETWORKD, NM, bluetooth, links, modems, network


def write(path, value=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(value))


class ConnectivityEvidence(unittest.TestCase):
    def test_sysfs_link_evidence_has_speed_but_no_interface_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ethernet = root / "class/net/enx001122334455"
            write(ethernet / "device/present", "1")
            write(ethernet / "operstate", "up")
            write(ethernet / "carrier", "1")
            write(ethernet / "speed", "2500")
            write(ethernet / "duplex", "full")
            wireless = root / "class/net/wlan-private"
            write(wireless / "device/present", "1")
            (wireless / "wireless").mkdir()
            write(wireless / "operstate", "dormant")
            write(wireless / "carrier", "0")

            data = links(root)

        self.assertEqual(data["interfaces"], [
            {"kind": "ethernet", "operstate": "up", "carrier": True,
             "speed_mbps": 2500, "duplex": "full"},
            {"kind": "wireless", "operstate": "dormant", "carrier": False},
        ])
        self.assertNotIn("001122334455", json.dumps(data))
        self.assertNotIn("wlan-private", json.dumps(data))

    def test_networkmanager_separates_auth_address_and_dns_evidence(self):
        calls = []

        def query(_bus, name, path, interface, method, *args, **kwargs):
            calls.append((name, path, interface, method))
            if name == NM and path == "/org/freedesktop/NetworkManager" and method == "GetAll":
                return {"State": 70, "Connectivity": 4}
            if name == NM and method == "GetDevices":
                return ["/device/ethernet", "/device/wifi"]
            requested = args[1][0] if len(args) > 1 else ""
            if path == "/device/ethernet" and requested.endswith(".Device.Wired"):
                return {"Carrier": True, "Speed": 2500}
            if path == "/device/ethernet":
                return {"DeviceType": 1, "State": 100, "Managed": True,
                        "Interface": "enx-secret-mac"}
            if path == "/device/wifi":
                return {"DeviceType": 2, "State": 60, "Managed": True,
                        "Interface": "wlan-secret"}
            if name == IWD:
                return {}
            raise AssertionError((name, path, interface, method, args, kwargs))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write(root / "class/net/enx-secret-mac/duplex", "full")
            write(root / "class/net/wlan-secret/carrier", "0")
            write(root / "class/net/wlan-secret/operstate", "dormant")
            resolver = root / "resolv.conf"
            write(resolver, "nameserver 192.0.2.53\n")
            data = network(None, root, query, resolver)

        manager = data["manager"]
        self.assertEqual(manager["name"], "NetworkManager")
        self.assertEqual(manager["connectivity"], "full")
        self.assertEqual(manager["dns"], {"configured": True, "connectivity": "full"})
        self.assertEqual(manager["interfaces"][0]["authentication"], "complete")
        self.assertEqual(manager["interfaces"][0]["dhcp_or_address"], "configured")
        self.assertEqual(manager["interfaces"][0]["speed_mbps"], 2500)
        self.assertEqual(manager["interfaces"][0]["duplex"], "full")
        self.assertEqual(manager["interfaces"][1]["authentication"], "required")
        self.assertEqual(manager["interfaces"][1]["address_configuration"], "not_started")
        self.assertFalse(manager["interfaces"][1]["carrier"])
        self.assertEqual(manager["interfaces"][1]["operstate"], "dormant")
        encoded = json.dumps(data)
        self.assertNotIn("192.0.2.53", encoded)
        self.assertNotIn("enx-secret", encoded)
        self.assertNotIn("wlan-secret", encoded)
        self.assertTrue(all(method in {"GetAll", "GetDevices", "GetManagedObjects"}
                            for _name, _path, _interface, method in calls))

    def test_networkd_and_iwd_fallback_remain_observational(self):
        methods = []

        def query(_bus, name, path, _interface, method, *args, **_kwargs):
            methods.append(method)
            if name == NM:
                raise RuntimeError("not installed")
            if name == NETWORKD and method == "ListLinks":
                return [(2, "private-interface", "/org/freedesktop/network1/link/_32")]
            if name == NETWORKD:
                return {"OperationalState": "routable", "CarrierState": "carrier",
                        "AddressState": "routable", "Addresses": ["192.0.2.9"]}
            if name == IWD:
                return {"/private/station": {IWD + ".Station": {
                    "State": "connected", "Scanning": False, "ConnectedNetwork": "/private/ssid"}}}
            raise AssertionError((name, path, method, args))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write(root / "class/net/private-interface/device/present", "1")
            write(root / "class/net/private-interface/speed", "1000")
            write(root / "class/net/private-interface/duplex", "full")
            data = network(None, root, query)

        self.assertEqual(data["manager"], {"name": "systemd-networkd", "interfaces": [{
            "kind": "ethernet", "operational": "routable", "carrier": "carrier",
            "dhcp_or_address": "routable", "address_configuration": "routable",
            "authentication": "not_exposed", "speed_mbps": 1000, "duplex": "full"}]})
        self.assertEqual(data["wifi_authentication"], [{
            "state": "connected", "scanning": False, "authentication": "complete"}])
        self.assertNotIn("private", json.dumps(data))
        self.assertNotIn("192.0.2.9", json.dumps(data))
        self.assertTrue(set(methods) <= {"GetAll", "GetManagedObjects", "ListLinks"})

    def test_connman_fallback_filters_service_identity_and_configuration(self):
        methods = []

        def query(_bus, name, _path, _interface, method, *_args, **_kwargs):
            methods.append(method)
            if name in (NM, NETWORKD):
                raise RuntimeError("not active")
            if name == CONNMAN:
                props = {"Type": "wifi", "State": "online", "Name": "private-ssid",
                         "Security": ["psk"], "Nameservers": ["192.0.2.53"],
                         "IPv4": {"Address": "192.0.2.9"}}
                return [("/private/service", props)]
            if name == IWD:
                return {}
            raise AssertionError(name)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resolver = root / "resolv.conf"
            write(resolver, "nameserver 192.0.2.53\n")
            data = network(None, root, query, resolver)

        self.assertEqual(data["manager"]["name"], "ConnMan")
        self.assertEqual(data["manager"]["connectivity"], "full")
        self.assertEqual(data["manager"]["interfaces"][0]["authentication"], "complete")
        self.assertEqual(data["manager"]["interfaces"][0]["dhcp_or_address"], "configured")
        self.assertEqual(data["dns"], {"configured": True, "connectivity": "full"})
        encoded = json.dumps(data)
        self.assertNotIn("private-ssid", encoded)
        self.assertNotIn("192.0.2", encoded)
        allowed = {"GetAll", "GetDevices", "GetManagedObjects", "GetServices", "ListLinks"}
        self.assertTrue(set(methods) <= allowed)

    def test_bluez_reports_audio_roles_without_peer_identity(self):
        objects = {
            "/adapter/private": {"org.bluez.Adapter1": {
                "Powered": True, "Discovering": False, "Address": "00:11:22:33:44:55"}},
            "/device/private": {"org.bluez.Device1": {
                "Connected": True, "Paired": True, "Trusted": False,
                "Name": "Private Headset", "Address": "66:77:88:99:AA:BB",
                "UUIDs": ["0000110B-0000-1000-8000-00805F9B34FB",
                          "0000111E-0000-1000-8000-00805F9B34FB"]}},
            "/transport/private": {"org.bluez.MediaTransport1": {
                "State": "active", "Device": "/device/private", "Volume": 80}},
        }

        data = bluetooth(None, lambda *args, **kwargs: objects)

        self.assertEqual(data["adapters"], [{"powered": True, "discovering": False}])
        self.assertEqual(data["connected_devices"][0]["audio_roles"], ["audio_sink", "hands_free"])
        self.assertEqual(data["audio_transports"], [{"state": "active"}])
        encoded = json.dumps(data)
        self.assertNotIn("Private Headset", encoded)
        self.assertNotIn("00:11", encoded)
        self.assertNotIn("/device/", encoded)

    def test_wwan_reports_registration_and_packet_state_without_identifiers(self):
        modem = {"State": 11, "AccessTechnologies": 8, "SignalQuality": (72, True),
                 "Bearers": ["/bearer/private"], "EquipmentIdentifier": "private-imei",
                 "PrimaryPort": "private-port"}
        registration = {"RegistrationState": 5, "PacketServiceState": 3,
                        "OperatorName": "private-operator"}
        objects = {
            "/modem/private": {
                MM + ".Modem": modem,
                MM + ".Modem.Modem3gpp": registration},
            "/bearer/private": {MM + ".Bearer": {
                "Connected": True, "Suspended": False, "Interface": "private-interface",
                "Properties": {"apn": "private-apn"}, "Ip4Config": {"address": "192.0.2.7"}}},
        }

        data = modems(None, lambda *args, **kwargs: objects)

        self.assertEqual(data[0]["state"], "connected")
        self.assertTrue(data[0]["registered"])
        self.assertEqual(data[0]["registration"], "roaming")
        self.assertEqual(data[0]["packet_service"], "attached")
        self.assertEqual(data[0]["connected_bearers"], 1)
        self.assertEqual(data[0]["signal_percent"], 72)
        self.assertNotIn("private", json.dumps(data))
        self.assertNotIn("192.0.2.7", json.dumps(data))

    def test_bluez_results_are_capped(self):
        objects = {f"/device/{index}": {"org.bluez.Device1": {
            "Connected": True, "Paired": False, "Trusted": False, "UUIDs": []}}
            for index in range(40)}
        self.assertEqual(len(bluetooth(None, lambda *args, **kwargs: objects)["connected_devices"]), 32)


if __name__ == "__main__":
    unittest.main()
