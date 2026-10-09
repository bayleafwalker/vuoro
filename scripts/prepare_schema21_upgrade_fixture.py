"""Prepare the actual hash-pinned old service closure for the upgrade gate."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from urllib.request import urlopen


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--artifact-cache", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    fixtures = root / "packages/vuoro-service/tests/fixtures"
    manifest = fixtures / "schema21-adapter-pins.json"
    service = json.loads((fixtures / "schema21-service.json").read_text())
    pins = json.loads(manifest.read_text())["release_locks"]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    wheels = output / "wheels"
    wheels.mkdir()
    for pin in [*pins, service]:
        filename = pin["artifact_url"].rsplit("/", 1)[-1]
        cached = args.artifact_cache / filename if args.artifact_cache else None
        if cached and cached.is_file():
            data = cached.read_bytes()
        else:
            with urlopen(pin["artifact_url"], timeout=60) as response:
                data = response.read()
        if hashlib.sha256(data).hexdigest() != pin["artifact_sha256"]:
            raise SystemExit(f"old closure artifact checksum mismatch: {filename}")
        (wheels / filename).write_bytes(data)
    interpreter = output / "venv/bin/python"
    subprocess.run(["uv", "venv", "--python", "3.12", str(output / "venv")], check=True)
    subprocess.run(["uv", "pip", "install", "--python", str(interpreter),
                    str(wheels / service["artifact_url"].rsplit("/", 1)[-1]),
                    "httpx>=0.27,<1", "psycopg[binary]>=3.1,<4", "click>=8.1"], check=True)
    subprocess.run(["uv", "pip", "install", "--python", str(interpreter), "--no-deps",
                    *(str(wheels / p["artifact_url"].rsplit("/", 1)[-1]) for p in pins)], check=True)
    subprocess.run(["uv", "pip", "check", "--python", str(interpreter)], check=True)
    subprocess.run([str(interpreter), "-I", str(root / "scripts/attest_installed_composition.py"),
                    str(manifest), str(wheels), str(output / "installed.json")], check=True)


if __name__ == "__main__":
    main()
