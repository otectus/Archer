"""Local health-history and guided-workflow contracts without host hardware."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gui"))

from archer_health import HealthHistory, compare, guided_plan, health


def sample(*, device="gpu", package="1-1", capability=True, config="a", boot="boot-a"):
    return {
        "schema_version": 1,
        "archer_version": "test",
        "observed_at": "2026-01-01T00:00:00+00:00",
        "environment": {},
        "packages": {"linux": package},
        "devices": [{"key": device, "driver": "driver", "interfaces": ["drm"]}],
        "capabilities": ([{"id": "fan.cpu.duty", "availability": "available"}]
                         if capability else []),
        "config_hashes": {"etc/archer/settings.json": config},
        "boot_targets": {"configuration_hashes": {"boot/grub/grub.cfg": boot}},
        "readiness": {},
        "health": {},
    }


class HealthOutcomeTests(unittest.TestCase):
    def test_outcomes_have_structured_evidence_without_a_score(self):
        inventory = {
            "devices": [{"key": "display", "kind": "drm", "driver": "i915",
                         "identity_unique": True, "interfaces": [], "attributes": {}}],
            "sleep": {"state": "freeze mem disk"},
        }
        groups = health(inventory, {"pacman_locked": False, "kernels": []},
                        {"status": "metadata_stale"})
        for required in ("Graphics and displays", "Connectivity", "Input", "Sound", "Camera",
                         "Battery", "Cooling", "Sleep", "Firmware", "Update readiness"):
            self.assertIn(required, groups)
            finding = groups[required]["findings"][0]
            for field in ("symptom", "device", "observed", "observed_at", "confidence",
                          "proposed_action", "changes", "interruption", "verification", "recovery"):
                self.assertIn(field, finding)
        self.assertEqual(groups["Camera"]["status"], "optional_unavailable")
        self.assertNotIn("score", json.dumps(groups).lower())

    def test_guided_plan_is_review_only(self):
        groups = {"Camera": {"status": "optional_unavailable"}}
        plan = guided_plan("conferencing", groups)
        self.assertTrue(plan["read_only_scan"])
        self.assertFalse(plan["firmware_updates_included"])
        self.assertIn("driver replacement", plan["notice"])
        self.assertEqual(plan["unavailable_outcomes"], ["Camera"])
        with self.assertRaises(ValueError):
            guided_plan("run-anything", groups)


class HistoryTests(unittest.TestCase):
    def test_history_is_local_bounded_private_and_compares(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private" / "history.json"
            history = HealthHistory(path, limit=2)
            self.assertIsNone(history.record(sample()))
            changed = history.record(sample(package="2-1", capability=False, config="b", boot="boot-b"))
            history.record(sample(device="new", package="3-1", capability=False, config="b"))
            self.assertEqual(len(history.load()), 2)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(changed["package_changes"], {"linux": ["1-1", "2-1"]})
            self.assertEqual(changed["lost_capabilities"], ["fan.cpu.duty"])
            self.assertEqual(changed["config_changes"], ["etc/archer/settings.json"])
            self.assertTrue(changed["boot_targets_changed"])
            self.assertIn("does not prove causation", changed["correlation_notice"])

    def test_symlink_history_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "target"
            target.write_text('{}')
            link = Path(directory) / "history"
            link.symlink_to(target)
            with self.assertRaises(ValueError):
                HealthHistory(link).load()

    def test_compare_rejects_unknown_schema(self):
        with self.assertRaises(ValueError):
            compare({"schema_version": 2, "devices": []}, sample())


if __name__ == "__main__":
    unittest.main()
