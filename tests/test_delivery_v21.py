"""Cross-entry contracts for 2.1 ownership transfer, release approval and session probes."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gui"))
from archer_firmware import install_reviewed, vendor_route
from archer_graphics import distro_owner, review_token
from archer_inventory import gpu_roles
from archer_peripherals import modems, orientation, readers
from archer_workspace import dock_state
from archer_update_history import record, regression
from archer_session import mic_preset
from test_expansion import load_script


class DeploymentFiles(unittest.TestCase):
    def test_script_ownership_and_rollback_across_package_transfer(self):
        helper = load_script("archer-gui-files")
        transfer = load_script("archer-package-migration")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            helper.deploy("install", root)
            manifest = root / transfer.MANIFEST
            manifest.write_text(json.dumps({"modules_installed": ["audio", "gui"], "files_created": ["/opt/archer"]}))
            launcher = root / "usr/local/bin/archer"
            original = launcher.read_bytes()
            local = root / "opt/archer/personal-file"
            local.write_text("retain me")
            launcher.write_bytes(b"local edit")
            with self.assertRaisesRegex(ValueError, "locally edited"):
                helper.deploy("remove", root)
            self.assertTrue(launcher.exists())
            launcher.write_bytes(original)
            packages = set()

            def run(*args, check=True):
                rc = int(args[0] == "pacman" and args[-1] not in packages)
                if check and rc:
                    raise subprocess.CalledProcessError(rc, args)
                return subprocess.CompletedProcess(args, rc, "inactive", "")

            with patch.object(transfer, "run", side_effect=run):
                transfer.transfer("prepare", root)
                self.assertFalse(launcher.exists())
                transfer.transfer("prepare", root)
                packages.update(("archer-core", "archer-gui", "archer-daemon"))
                transfer.transfer("finish", root)
                self.assertEqual(json.loads(manifest.read_text())["modules_installed"], ["audio"])
                packages.clear()
                transfer.transfer("restore", root)
                self.assertEqual(launcher.read_bytes(), original)
                self.assertTrue((root / helper.STATE).exists())
                helper.deploy("remove", root)
                self.assertFalse(launcher.exists())
                self.assertEqual(local.read_text(), "retain me")


class SessionContracts(unittest.TestCase):
    def test_discrete_intel_and_amd_devices_are_not_treated_as_integrated(self):
        with tempfile.TemporaryDirectory() as directory:
            sysfs = Path(directory)
            switch = sysfs / "kernel/debug/vgaswitcheroo/switch"
            switch.parent.mkdir(parents=True)
            switch.write_text("0:IGD:+:Pwr:0000:06:00.0\n1:DIS: :DynOff:0000:01:00.0\n")
            devices = [{"kind": "pci", "node": node, "driver": driver, "topology": "devices/pci0000:00/" + node,
                        "attributes": {"class": "0x030000", "vendor": vendor}}
                       for node, driver, vendor in (("0000:06:00.0", "amdgpu", "0x1002"), ("0000:01:00.0", "xe", "0x8086"),
                                                    ("0000:03:00.0", "amdgpu", "0x1002"))]
            gpu_roles(devices, sysfs)
            self.assertEqual([d["attributes"]["gpu_role"] for d in devices], ["integrated", "discrete", "unknown"])

    def test_unknown_distribution_keeps_its_graphics_owner(self):
        self.assertEqual(distro_owner({"ID": "fedora"}), "distribution")
        self.assertEqual(distro_owner({"ID": "unreviewed", "ID_LIKE": "arch"}), "distribution")

    def test_sleeping_gpu_does_not_start_management_client(self):
        from archer_daemon import HardwareManager
        manager = HardwareManager.__new__(HardwareManager)
        manager._inventory = {"devices": [{"kind": "pci", "node": "0000:01:00.0", "key": "gpu", "driver": "nvidia",
                                           "attributes": {"class": "0x030000", "vendor": "0x10de"}}]}
        with patch("archer_daemon.read_sysfs", return_value="suspended"), patch("archer_daemon.run_command") as query:
            self.assertEqual(manager._active_nvidia_sample(), (None, None))
            query.assert_not_called()

    def test_generation_change_coalesces_device_events(self):
        from archer_daemon import HardwareManager
        manager = HardwareManager.__new__(HardwareManager)
        manager._inventory = {"generation": "old"}
        manager._refresh_devices = Mock(side_effect=lambda: setattr(manager, "_inventory", {"generation": "new"}))
        with patch("archer_daemon.time.monotonic", side_effect=[1, 2, 30]), patch("archer_daemon.read_sysfs", return_value="9"):
            self.assertEqual(manager.poll_inventory(), "new")
            self.assertIsNone(manager.poll_inventory())
            self.assertIsNone(manager.poll_inventory())
        manager._refresh_devices.assert_called_once()

    def test_signed_advisory_catalog_rejects_modified_or_outdated_input(self):
        from archer_readiness import advisory_catalog
        from archer_runtime import CommandResult
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            builtin, provenance = advisory_catalog(root)
            self.assertEqual(provenance, "bundled")
            data = root / "var/lib/archer/advisories"
            data.mkdir(parents=True)
            (root / "etc/archer").mkdir(parents=True)
            (root / "etc/archer/advisory-keys.gpg").touch()
            (data / "catalog.json.sig").touch()
            (data / "catalog.json").write_text(json.dumps(builtin))
            self.assertIn("rejected", advisory_catalog(root, Mock(return_value=CommandResult(1)))[1])
            verify = Mock(return_value=CommandResult(0))
            self.assertEqual(advisory_catalog(root, verify)[1], "publisher_signature_verified")
            (data / "catalog.json").write_text(json.dumps({**builtin, "reviewed": "2000-01-01"}))
            self.assertIn("rejected", advisory_catalog(root, verify)[1])

    def test_microphone_preset_is_local_to_one_selected_source(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"XDG_CONFIG_HOME": tmp}):
            self.assertEqual(mic_preset("alsa_input.a"), "balanced")
            mic_preset("alsa_input.a", "quiet-room")
            self.assertEqual(mic_preset("alsa_input.a"), "quiet-room")
            self.assertEqual(mic_preset("alsa_input.b"), "balanced")
            with self.assertRaises(ValueError):
                mic_preset("alsa_input.a", "unrecognized")

    def test_vendor_route_requires_exact_vendor_and_model(self):
        self.assertTrue(vendor_route("Acer", "Predator PT516-52s").endswith("/Predator_PT516-52s/downloads"))
        self.assertIsNone(vendor_route("Acer", "Predator PHN16S-71"))
        self.assertIsNone(vendor_route("Other", "ANV16S-41"))

    def test_release_is_revalidated_before_deployment(self):
        device = {"device_key": "fwupd-mock", "status": "updates", "local_id": "a" * 40,
                  "name": "Fixture", "ac_required": True, "releases": [{"version": "1.2"}]}
        report = {"metadata": "fresh", "devices": [device]}
        exec_mock = Mock(return_value=0)
        with patch("builtins.print"):
            result = install_reviewed("fwupd-mock", "1.2", status=lambda: report, confirm=lambda _: "", execute=exec_mock)
            self.assertEqual(result["operation"], "cancelled")
            exec_mock.assert_not_called()
            with self.assertRaisesRegex(ValueError, "Refresh"):
                install_reviewed("fwupd-mock", "1.2", status=Mock(side_effect=[report, {**report, "metadata": "stale"}]),
                                 confirm=lambda _: "1.2", execute=exec_mock)
            exec_mock.assert_not_called()
            result = install_reviewed("fwupd-mock", "1.2", status=lambda: report, confirm=lambda _: "1.2", execute=exec_mock)
            self.assertEqual(result["exit_status"], 0)
            self.assertEqual(exec_mock.call_args.args[0][1:4], ["install", "a" * 40, "1.2"])

    def test_pacman_observations_are_bounded_and_readable_after_reboot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "var/lib/archer/update-health"
            before = {"schema_version": 1, "packages": {}, "devices": [{"key": "battery1", "driver": "battery", "interfaces": []}]}
            after = {**before, "devices": []}
            record("before", target, lambda: before)
            record("after", target, lambda: after)
            self.assertEqual(regression(root, after)["lost"], ["battery1"])
            self.assertEqual((target / "before.json").stat().st_mode & 0o777, 0o644)
            with self.assertRaises(ValueError):
                record("before", target, lambda: {"value": "." * (5 * 1024 * 1024)})
            self.assertEqual(regression(root, after)["lost"], ["battery1"])

    def test_dock_changes_invalidate_context_and_graphics_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            panel = root / "class/drm/card2-eDP-1"
            panel.mkdir(parents=True)
            (panel / "enabled").write_text("enabled")
            self.assertFalse(dock_state(root))
            panel.rename(panel.with_name("card2-DP-1"))
            self.assertTrue(dock_state(root))
        plan = {"requested_mode": "integrated", "preflight": {"active_outputs": []}}
        token = review_token(plan)
        plan["preflight"]["active_outputs"].append("card2-DP-1")
        self.assertNotEqual(token, review_token(plan))

    def test_peripheral_services_do_not_disclose_subscriber_or_fingerprint_data(self):
        interfaces = {"/redacted": {"org.freedesktop.ModemManager1.Modem": {
            "EquipmentIdentifier": "private", "State": 11, "AccessTechnologies": 8, "SignalQuality": (72, True)}}}
        data = modems(None, lambda *args: interfaces)
        self.assertNotIn("private", str(data))
        self.assertEqual(data[0]["signal_percent"], 72)
        props = {"name": "Reader", "scan-type": "press", "finger-present": True}
        data = readers(None, Mock(side_effect=[["/dev/1"], props]))
        self.assertNotIn("finger-present", data[0])
        self.assertEqual(orientation(None, lambda *args: {"HasAccelerometer": True}), {"HasAccelerometer": True})
