"""Actual local Connect generator rejects drift and competing protobuf output."""
import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / 'scripts/experiments/ec00/generate_connect.py'


def generator():
    spec = importlib.util.spec_from_file_location('connect_generator_under_test', SCRIPT)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


class ConnectGenerationTests(unittest.TestCase):
    def test_output_drift_is_refused(self):
        module = generator()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'ec00_connect'
            shutil.copytree(module.CONNECT_OUTPUT, target)
            file = target / 'oa_v1_connect.py'; file.write_bytes(file.read_bytes() + b'\n# deliberate drift\n')
            with patch.object(module, 'CONNECT_OUTPUT', target), patch.object(sys, 'argv', [str(SCRIPT)]):
                with self.assertRaisesRegex(SystemExit, 'Connect output drift'):
                    module.main()

    def test_canonical_protobuf_disagreement_is_refused(self):
        module = generator()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'wrong_pb2.py'; target.write_bytes(module.PROTOBUF_PYTHON.read_bytes() + b'\n# drift\n')
            with patch.object(module, 'PROTOBUF_PYTHON', target), patch.object(sys, 'argv', [str(SCRIPT)]):
                with self.assertRaisesRegex(SystemExit, 'changed the canonical Python protobuf bytes'):
                    module.main()


if __name__ == '__main__':
    unittest.main()
