"""Export the service image's third-party runtime requirements from uv.lock.

The image installs every third-party runtime distribution from this file
with ``pip install --require-hashes --no-deps``. The exact versions and
hashes therefore come from ``uv.lock``, never from a range resolved against
an index at build time. The workspace's own packages are installed
separately, ``--no-deps``:
- local source trees: vuoro-service, vuoro-evidence, vuoro-mcp-edge;
- hash-verified release wheels from the composition lock: the owners and the
  shared adapter-kit and schema-runtime.

The export covers:
- vuoro-mcp-edge, which pulls in vuoro-service, vuoro-evidence and
  operator-projection;
- vuoro-workspace with its ``s3`` extra (psycopg[binary], boto3);
- click, through uvicorn, for the Sprintctl owner CLI.

uv.lock resolves vuoro-client to the workspace member. The image instead takes
the released, hash-pinned client that operator-projection requires, and this
script appends it.

Usage:
    python scripts/export_image_requirements.py            # rewrite the file
    python scripts/export_image_requirements.py --check    # fail if stale
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "deploy" / "image" / "requirements.txt"
EXPORT = [
    "uv", "export", "--frozen", "--no-dev", "--no-emit-workspace", "--no-header", "--no-annotate",
    "--format", "requirements-txt",
    "--package", "vuoro-mcp-edge", "--package", "vuoro-workspace", "--extra", "s3",
]
CLIENT_URL = (
    "https://github.com/bayleafwalker/vuoro/releases/download/"
    "vuoro-client-v0.1.2/vuoro_client-0.1.2-py3-none-any.whl"
)
CLIENT_SHA256 = "7a4f32d4e2531a1abc99f5dc017fb8f6f22c4a1ef191079b98808be897081660"
HEADER = (
    "# Generated from uv.lock by scripts/export_image_requirements.py; do not edit.\n"
    "# Installed in the service image with: pip install --require-hashes --no-deps -r\n"
)


def _client_requirement() -> str:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    projection = [p for p in lock["package"] if p["name"] == "operator-projection"]
    if len(projection) != 1:
        raise SystemExit("uv.lock must contain exactly one operator-projection")
    required = [r for r in projection[0]["metadata"]["requires-dist"] if r["name"] == "vuoro-client"]
    if [r.get("url") for r in required] != [CLIENT_URL]:
        raise SystemExit(
            "operator-projection no longer requires the pinned vuoro-client release; "
            "update CLIENT_URL and CLIENT_SHA256"
        )
    return f"vuoro-client @ {CLIENT_URL}#sha256={CLIENT_SHA256} \\\n    --hash=sha256:{CLIENT_SHA256}\n"


def render() -> str:
    if shutil.which("uv") is None:
        raise SystemExit("uv is required to export the image requirements")
    exported = subprocess.run(EXPORT, cwd=ROOT, check=True, capture_output=True, text=True).stdout
    lines = exported.rstrip("\n") + "\n"
    if "vuoro-client" in lines:
        raise SystemExit("the export unexpectedly contains vuoro-client")
    return HEADER + lines + _client_requirement()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail when the checked-in file is stale")
    args = parser.parse_args(argv)
    expected = render()
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.is_file() else ""
        if current != expected:
            print(f"{OUTPUT.relative_to(ROOT)} is stale; run scripts/export_image_requirements.py", file=sys.stderr)
            return 1
        print("image requirements match uv.lock")
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(expected, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
