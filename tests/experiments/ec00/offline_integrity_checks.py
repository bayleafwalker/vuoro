"""Falsify archive/bootstrap binding and revision provenance before namespace launch."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('offline_packages', ROOT / 'scripts/experiments/ec00/offline_packages.py')
packages = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packages)


def archive(path, entries):
    with tarfile.open(path, 'w') as stream:
        for name, raw in entries.items():
            info = tarfile.TarInfo(name); info.size = len(raw)
            stream.addfile(info, io.BytesIO(raw))
    return packages.sha(path)


class OfflineIntegrityTests(unittest.TestCase):
    def test_only_externally_verified_bundled_launcher_executes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); bundle = root / 'bundle.tar'; report = root / 'report'
            source = b'def restore_and_run(archive, expected, report):\n    report.write_text("bundled launcher executed")\n'
            expected = archive(bundle, {'payload/offline_namespace.py': source})
            packages.run_bundle(bundle, expected, report)
            self.assertEqual('bundled launcher executed', report.read_text())
            report.unlink()
            with bundle.open('r+b') as stream:
                stream.seek(-1, 2); old = stream.read(1); stream.seek(-1, 2); stream.write(bytes([old[0] ^ 1]))
            with self.assertRaisesRegex(ValueError, 'externally pinned SHA256'):
                packages.run_bundle(bundle, expected, report)
            self.assertFalse(report.exists())

    def test_internal_file_corruption_and_extra_files_refuse_before_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundled_launcher = Path(__file__).with_name('offline_namespace.py').read_bytes()
            manifest = json.dumps({'files': {
                'input': {'sha256': hashlib.sha256(b'original').hexdigest()},
                'offline_namespace.py': {'sha256': hashlib.sha256(bundled_launcher).hexdigest()},
            }}).encode()
            for name, contents, reason in (
                ('corrupt', {'payload/input': b'changed'}, 'file hash mismatch'),
                ('extra', {'payload/input': b'original', 'payload/extra': b'unlisted'}, 'unmanifested'),
            ):
                with self.subTest(name=name):
                    bundle = root / (name + '.tar')
                    expected = archive(bundle, {'payload/manifest.json': manifest, 'payload/offline_namespace.py': bundled_launcher, **contents})
                    with self.assertRaisesRegex(ValueError, reason):
                        packages.run_bundle(bundle, expected, root / 'report')
                    self.assertFalse((root / 'report').exists())

    def test_dirty_revision_inputs_refuse_before_dependency_download(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); repository = root / 'repository'; repository.mkdir()
            subprocess.run(['git', 'init', '-q', str(repository)], check=True)
            input_file = repository / 'tests/experiments/ec00/candidates/uv.lock'
            input_file.parent.mkdir(parents=True); input_file.write_text('committed lock')
            subprocess.run(['git', '-C', str(repository), 'add', '.'], check=True)
            subprocess.run(['git', '-C', str(repository), '-c', 'user.name=Offline Fixture',
                            '-c', 'user.email=offline@example.invalid', 'commit', '-qm', 'fixture'], check=True)
            input_file.write_text('uncommitted different lock')
            with patch.object(packages, 'ROOT', repository):
                with self.assertRaisesRegex(ValueError, 'commit experiment inputs'):
                    packages.prepare(root / 'destination')
            self.assertFalse((root / 'destination/payload/repository.tar').exists())


if __name__ == '__main__':
    unittest.main()
