"""Install every and only hash-verified wheel from an immutable composition."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import sys


def fetch_helpers():
    sibling = Path(__file__).with_name('fetch_pinned_adapters.py')
    if not sibling.exists():
        sibling = Path(__file__).with_name('fetch-pinned-adapters')
    return runpy.run_path(str(sibling))


def install(manifest_path, wheel_dir, *, run=subprocess.run):
    helpers = fetch_helpers()
    manifest = json.loads(Path(manifest_path).read_text())
    helpers['validate_interpreter'](manifest)
    wheels = []
    for label, pin in helpers['artifact_pins'](manifest):
        filename = helpers['_release_wheel_filename'](pin['source_repository'], pin['artifact_url'])
        path = Path(wheel_dir) / filename
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != pin['artifact_sha256']:
            raise SystemExit(f'wheel missing or checksum mismatch: {label}')
        wheels.append(str(path))
    # No glob expansion, no index lookup and no dependency resolution.
    run([sys.executable, '-m', 'pip', 'install', '--no-cache-dir', '--no-deps', '--no-index', *wheels], check=True)
    return 0


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit('usage: install_pinned_adapters.py MANIFEST WHEEL_DIR')
    raise SystemExit(install(*sys.argv[1:]))
