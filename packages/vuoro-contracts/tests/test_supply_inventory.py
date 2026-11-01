"""Q3 locked-source inventory; fixtures do not qualify a compiler upgrade."""

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/contracts/supply_inventory.py"
spec = importlib.util.spec_from_file_location("supply_inventory", SCRIPT)
supply = importlib.util.module_from_spec(spec)
spec.loader.exec_module(supply)


class SupplyInventoryTests(unittest.TestCase):
    def setUp(self):
        self.sources, _ = supply.qualified_sources(ROOT)
        self.npm_project = json.loads(self.sources["tools/contracts/package.json"])
        self.npm_lock = json.loads(self.sources["tools/contracts/package-lock.json"])
        self.python_project = tomllib.loads(
            self.sources["tools/contracts/pyproject.toml"].decode()
        )
        self.python_lock = tomllib.loads(
            self.sources["tools/contracts/uv.lock"].decode()
        )

    def source_copy(self, destination):
        for relative, raw in self.sources.items():
            path = destination / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        path = destination / "scripts/contracts/generate.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / "scripts/contracts/generate.py").read_bytes())
        shutil.copytree(ROOT / "contracts", destination / "contracts")

    def test_real_locks_are_complete_deterministic_and_do_not_overclaim(self):
        observed = supply.inventory(ROOT)
        self.assertEqual(observed, supply.inventory(ROOT))
        self.assertEqual(175, len(observed["npm"]))
        self.assertEqual(29, len(observed["python"]))
        self.assertEqual(5, len(observed["localContractSources"]))
        self.assertEqual("locked-source-metadata-only", observed["scope"])
        self.assertIn(
            "generation-chain-upgrade-and-rollback", observed["unestablished"]
        )
        for entry in observed["python"]:
            self.assertIsNone(entry["licence"]["declared"])
            self.assertEqual("unknown", entry["support"])
        for entry in observed["npm"]:
            self.assertEqual("unknown", entry["licence"]["assessment"])

    def test_changed_source_is_refused_and_exact_rollback_recovers_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.source_copy(root)
            original = supply.inventory(root)
            lock = root / "tools/contracts/package-lock.json"
            before = lock.read_bytes()
            changed = json.loads(before)
            changed["packages"]["node_modules/@typespec/compiler"]["version"] = "99.0.0"
            lock.write_text(json.dumps(changed))
            with self.assertRaisesRegex(supply.SupplyError, "qualified-input-drift"):
                supply.inventory(root)
            failed = subprocess.run(
                [sys.executable, str(SCRIPT), "--root", str(root)],
                capture_output=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(2, failed.returncode)
            self.assertEqual(b"", failed.stdout)
            self.assertEqual(
                b"generation source supply inventory refused\n", failed.stderr
            )
            lock.write_bytes(before)
            self.assertEqual(original, supply.inventory(root))

    def test_missing_source_and_input_symlink_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.source_copy(root)
            lock = root / "tools/contracts/uv.lock"
            before = lock.read_bytes()
            lock.unlink()
            with self.assertRaisesRegex(supply.SupplyError, "source-file"):
                supply.inventory(root)
            elsewhere = root / "same-lock"
            elsewhere.write_bytes(before)
            lock.symlink_to(elsewhere)
            with self.assertRaisesRegex(supply.SupplyError, "source-symlink"):
                supply.inventory(root)

    def test_local_symlink_directory_cannot_disappear_from_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.source_copy(root)
            (root / "contracts/hidden").symlink_to(
                ROOT / "contracts/http", target_is_directory=True
            )
            with self.assertRaisesRegex(supply.SupplyError, "source-symlink"):
                supply.inventory(root)

    def test_local_dependency_source_version_must_match_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.source_copy(root)
            project = root / "contracts/package.json"
            changed = json.loads(project.read_bytes())
            changed["version"] = "99.0.0"
            project.write_text(json.dumps(changed))
            with self.assertRaisesRegex(supply.SupplyError, "local-source-version"):
                supply.inventory(root)

    def test_exact_check_rejects_missing_or_changed_source_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            expected = Path(directory) / "inventory.json"
            good = subprocess.run(
                [sys.executable, str(SCRIPT)],
                capture_output=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(0, good.returncode, good.stderr)
            expected.write_bytes(good.stdout)
            command = [sys.executable, str(SCRIPT), "--check", str(expected)]
            self.assertEqual(
                0,
                subprocess.run(
                    command, capture_output=True, timeout=10, check=False
                ).returncode,
            )
            expected.write_bytes(good.stdout + b" ")
            failed = subprocess.run(
                command, capture_output=True, timeout=10, check=False
            )
            self.assertEqual(2, failed.returncode)
            self.assertEqual(b"", failed.stdout)
            expected.unlink()
            self.assertEqual(
                2,
                subprocess.run(
                    command, capture_output=True, timeout=10, check=False
                ).returncode,
            )

    def test_npm_missing_direct_package_or_mismatched_version_refuses(self):
        for mutation in ("missing", "version"):
            with self.subTest(mutation=mutation):
                lock = deepcopy(self.npm_lock)
                if mutation == "missing":
                    del lock["packages"]["node_modules/@typespec/compiler"]
                else:
                    lock["packages"]["node_modules/@typespec/compiler"]["version"] = (
                        "99.0.0"
                    )
                with self.assertRaisesRegex(supply.SupplyError, "npm-direct-version"):
                    supply.npm_packages(self.npm_project, lock)

    def test_npm_root_project_declaration_must_match_lock(self):
        lock = deepcopy(self.npm_lock)
        lock["packages"][""]["engines"]["node"] = "99.0.0"
        with self.assertRaisesRegex(supply.SupplyError, "npm-project-lock"):
            supply.npm_packages(self.npm_project, lock)

    def test_npm_missing_or_invalid_integrity_and_non_https_source_refuse(self):
        for field, value, code in (
            ("integrity", None, "npm-artifact-integrity"),
            ("integrity", "sha512-YQ==", "npm-artifact-integrity"),
            ("resolved", "http://registry.invalid/tool.tgz", "npm-artifact-source"),
        ):
            with self.subTest(field=field, value=value):
                lock = deepcopy(self.npm_lock)
                lock["packages"]["node_modules/@typespec/compiler"][field] = value
                with self.assertRaisesRegex(supply.SupplyError, code):
                    supply.npm_packages(self.npm_project, lock)

    def test_python_missing_hash_artifact_duplicate_and_direct_version_refuse(self):
        for mutation, code in (
            ("hash", "python-artifact-integrity"),
            ("artifact", "python-artifact-missing"),
            ("duplicate", "python-package-duplicate"),
            ("version", "python-direct-version"),
        ):
            with self.subTest(mutation=mutation):
                lock = deepcopy(self.python_lock)
                target = next(p for p in lock["package"] if p["name"] == "httpx")
                if mutation == "hash":
                    target["sdist"]["hash"] = "sha256:bad"
                elif mutation == "artifact":
                    target.pop("sdist")
                    target["wheels"] = []
                elif mutation == "duplicate":
                    lock["package"].append(deepcopy(target))
                else:
                    target["version"] = "99.0.0"
                with self.assertRaisesRegex(supply.SupplyError, code):
                    supply.python_packages(self.python_project, lock)

    def test_python_lock_interpreter_mismatch_refuses(self):
        lock = deepcopy(self.python_lock)
        lock["requires-python"] = ">=3.11"
        with self.assertRaisesRegex(supply.SupplyError, "python-lock-version"):
            supply.python_packages(self.python_project, lock)

    def test_declared_licence_absence_is_unknown_not_an_approval(self):
        lock = deepcopy(self.npm_lock)
        lock["packages"]["node_modules/@typespec/compiler"].pop("license")
        entry = next(
            p
            for p in supply.npm_packages(self.npm_project, lock)
            if p["location"] == "node_modules/@typespec/compiler"
        )
        self.assertIsNone(entry["licence"]["declared"])
        self.assertEqual("unknown", entry["licence"]["assessment"])

    def test_duplicate_json_keys_refuse(self):
        with self.assertRaisesRegex(supply.SupplyError, "duplicate-json-key"):
            supply._json(b'{"version":1,"version":2}')
