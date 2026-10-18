"""Falsify isolated v2 generation guards using real pinned compilers/generators."""
import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SCRIPT = ROOT / "scripts/experiments/ec00/generate_versioned_counters.py"


def generator():
    spec = importlib.util.spec_from_file_location("versioned_generator_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VersionedGenerationTests(unittest.TestCase):
    def test_generated_v2_drift_is_refused_without_touching_repo_outputs(self):
        module = generator()
        with tempfile.TemporaryDirectory(prefix="ec00-v2-drift-") as directory:
            destination = Path(directory)
            shutil.copytree(module.GENERATED / "typespec-v2", destination / "typespec-v2",
                            ignore=shutil.ignore_patterns("__pycache__", ".ruff_cache"))
            changed = destination / "typespec-v2/clients/typescript/types.gen.ts"
            changed.write_bytes(changed.read_bytes() + b"\n// deliberate output drift\n")
            with patch.object(module, "GENERATED", destination), patch.object(sys, "argv", [str(SCRIPT)]):
                with self.assertRaisesRegex(SystemExit, "versioned-counter output drift: typespec-v2/clients/typescript"):
                    module.main()

    def test_versioned_source_cannot_change_v1_wire_schema(self):
        module = generator()
        # Keep the authored import relative to the real v1 source; this temporary
        # entry point is never committed or written into a generated subtree.
        with tempfile.NamedTemporaryFile(prefix="v2-v1-collision-", suffix=".tsp",
                dir=module.VERSIONED_TYPESPEC.parent, delete=False) as stream:
            path = Path(stream.name)
            stream.write(module.VERSIONED_TYPESPEC.read_bytes())
            stream.write(b"\n@@encode(Ec00.Oa.V1.MutationResult.revision, string);\n")
        try:
            with patch.object(module, "VERSIONED_TYPESPEC", path), patch.object(sys, "argv", [str(SCRIPT)]):
                with self.assertRaisesRegex(SystemExit, "versioned source changed the committed v1 OpenAPI document bytes"):
                    module.main()
        finally:
            path.unlink()


if __name__ == "__main__":
    unittest.main()
