"""Check the optional image's exact closure; run through stdin in that image."""
from __future__ import annotations

import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

MANIFEST = Path('/opt/vuoro/composition/adapter-pins.json')
ATTESTATION = Path('/opt/vuoro/composition/installed-composition.json')


def validate(raw, expected_hash, attestation, fresh, installed, python_version):
    if not re.fullmatch(r'[0-9a-f]{64}', expected_hash):
        raise ValueError('invalid expected manifest SHA-256')
    if hashlib.sha256(raw).hexdigest() != expected_hash:
        raise ValueError('selected manifest hash differs')
    if tuple(python_version[:2]) < (3, 13):
        raise ValueError('lane image requires Python >=3.13')
    manifest = json.loads(raw)
    domains = [item['domain'] for item in manifest['runtime_descriptors']]
    if sorted(domains) != ['audit', 'knowledge', 'lane', 'work']:
        raise ValueError('optional four-owner composition required')
    locks = manifest['release_locks']
    pins = {item['distribution']: item for item in locks}
    if len(pins) != len(locks) or 'lanectl' not in pins:
        raise ValueError('duplicate or missing lane distribution pin')
    if (attestation.get('schema_version') != 'vuoro-installed-composition/v1'
            or attestation.get('verified') is not True
            or attestation.get('manifest_sha256') != expected_hash):
        raise ValueError('invalid installed-composition attestation')
    records = attestation.get('distributions', [])
    names = [item['distribution'] for item in records]
    if len(set(names)) != len(names) or set(names) != set(pins):
        raise ValueError('attested pin set differs')
    for record in records:
        pin = pins[record['distribution']]
        expected = {k: v for k, v in pin.items() if k != 'distribution_version'}
        expected['expected_version'] = pin['distribution_version']
        if any(record.get(k) != v for k, v in expected.items()):
            raise ValueError('attested source/artifact pin differs')
        if (record.get('installed_version') != pin['distribution_version']
                or installed.get(record['distribution']) != pin['distribution_version']
                or record.get('wheel_present') is not True
                or not re.fullmatch(r'[0-9a-f]{64}', record.get('installed_files_sha256', ''))
                or type(record.get('installed_files_count')) is not int
                or record['installed_files_count'] <= 0):
            raise ValueError('installed distribution differs')
    if fresh != attestation:
        raise ValueError('recomputed installed file/wheel attestation differs')
    return {'python': list(python_version[:3]), 'manifest_sha256': expected_hash,
            'installed_versions': installed, 'verified': True}


def main(argv):
    if len(argv) != 1:
        raise ValueError('usage: validate_lane_service_image.py MANIFEST_SHA256')
    raw = MANIFEST.read_bytes()
    manifest = json.loads(raw)
    attestation = json.loads(ATTESTATION.read_text())
    with tempfile.TemporaryDirectory(prefix='vuoro-lane-attest-') as directory:
        target = Path(directory) / 'attestation.json'
        subprocess.run([sys.executable, '/usr/local/bin/attest-installed-composition',
                        str(MANIFEST), '/opt/vuoro/adapters', str(target)],
                       check=True, capture_output=True, text=True, timeout=120)
        fresh = json.loads(target.read_text())
    installed = {item['distribution']: version(item['distribution'])
                 for item in manifest['release_locks']}
    print(json.dumps(validate(raw, argv[0], attestation, fresh, installed, sys.version_info)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
