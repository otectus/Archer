"""Focused regression tests for reviewed GUI payload ownership."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReviewedGuiOwnership(unittest.TestCase):
    def test_each_reader_accepts_exact_reviewed_versions_only(self):
        manifest = json.loads((ROOT / "scripts/gui-2.0.1-hashes.json").read_text())
        target = "opt/archer/archer/pages/controls.py"
        older = "7134bec005a72ef94688cf532eedc4f4a522d0c13740e93c8391c6d6ff46f578"
        for script in ("archer-gui-files", "archer-package-migration"):
            module = load_script(script)
            self.assertIn(older, module.accepted_hashes(manifest, target))
            self.assertNotIn("0" * 64, module.accepted_hashes(manifest, target))
            with self.assertRaisesRegex(ValueError, "Invalid reviewed hash"):
                module.accepted_hashes({target: [older, "not-a-hash"]}, target)

    def test_check_accepts_reviewed_old_hash_but_rejects_an_edit(self):
        module = load_script("archer-gui-files")
        old = b"reviewed shipped payload\n"
        target = "opt/archer/archer/pages/controls.py"
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory)
            repo = fixture / "repo"
            (repo / "scripts").mkdir(parents=True)
            (repo / "scripts/gui-2.0.1-hashes.json").write_text(json.dumps({
                target: [hashlib.sha256(b"current payload\n").hexdigest(), hashlib.sha256(old).hexdigest()]
            }))
            root = fixture / "root"
            path = root / target
            path.parent.mkdir(parents=True)
            path.write_bytes(old)
            manifest = root / "var/lib/archer/install-manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text('{"modules_installed":["gui"]}')
            with patch.object(module, "files", return_value={}):
                module.deploy("check", root, repo)
                path.write_bytes(old + b"administrator edit\n")
                with self.assertRaisesRegex(ValueError, "locally edited or unowned"):
                    module.deploy("check", root, repo)


if __name__ == "__main__":
    unittest.main()
