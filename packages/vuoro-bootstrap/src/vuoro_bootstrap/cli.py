"""CLI for the release-gated, human-approved bootstrap flow."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from vuoro_bootstrap import __version__ as bootstrap_version
from vuoro_bootstrap.api import BootstrapApi, BootstrapError
from vuoro_bootstrap.files import (
    apply_plan,
    plan_files,
    read_private_file,
    write_private_file,
)
from vuoro_client import __version__ as client_version


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vuoro")
    commands = parser.add_subparsers(dest="command")
    bootstrap = commands.add_parser("bootstrap")
    bootstrap.add_argument("endpoint")
    bootstrap.add_argument("--repo-id", required=True)
    bootstrap.add_argument("--device-code")
    bootstrap.add_argument("--root", type=Path, default=Path.cwd())
    bootstrap.add_argument("--credential-dir", type=Path, default=Path.home() / ".config/vuoro/credentials")
    bootstrap.add_argument("--session-file", type=Path)
    bootstrap.add_argument("--install-mode", choices=("print", "tool", "none"), default="tool")
    bootstrap.add_argument("--output", choices=("text", "json"), default="text")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "bootstrap":
        build_parser().print_help()
        return 2
    try:
        with BootstrapApi(args.endpoint) as api:
            discovery = api.discovery()
            manifest = api.manifest(discovery)
            if manifest.packages.vuoro_bootstrap != bootstrap_version:
                raise BootstrapError("bootstrap version is not in the advertised compatibility set")
            if manifest.packages.vuoro_client != client_version:
                raise BootstrapError("client version is not in the advertised compatibility set")
            if args.install_mode == "print":
                result = {
                    "status": "print-only",
                    "manifest": manifest.model_dump(mode="json", by_alias=True),
                    "planned_paths": [
                        str(args.root / ".vuoro" / "project.json"),
                        str(args.root / ".sprintctl" / "backend.json"),
                        str(args.root / ".vuoro" / "profile.json"),
                    ],
                }
                if args.output == "json":
                    print(json.dumps(result, indent=2, sort_keys=True))
                else:
                    print(json.dumps(result, indent=2, sort_keys=True))
                return 0
            session_file = args.session_file or args.root / ".vuoro" / "bootstrap-session.json"
            for path in (session_file, *session_file.parents):
                if path.is_symlink():
                    raise BootstrapError(f"refusing to use symlinked session path: {path}")
            created_session = False
            if session_file.is_file():
                saved = json.loads(read_private_file(session_file).decode())
                if (
                    saved.get("repo_id") != args.repo_id
                    or saved.get("api_endpoint") != discovery.api_endpoint
                    or saved.get("environment_id") != discovery.environment_id
                    or not isinstance(saved.get("session"), dict)
                ):
                    raise BootstrapError("saved bootstrap session belongs to another repository")
                session = saved["session"]
                device_code = args.device_code or session.get("device_code")
                if not isinstance(device_code, str) or not device_code:
                    raise BootstrapError("saved bootstrap session has no device code")
            else:
                if args.device_code:
                    raise BootstrapError("no saved bootstrap session exists for --device-code")
                session = api.create_session(discovery, repository_hint={"repo_id": args.repo_id})
                write_private_file(
                    session_file,
                    (json.dumps(
                        {
                            "repo_id": args.repo_id,
                            "api_endpoint": discovery.api_endpoint,
                            "environment_id": discovery.environment_id,
                            "session": session,
                        },
                        sort_keys=True,
                    ) + "\n").encode(),
                )
                created_session = True
                device_code = None
            if created_session:
                result = {
                    "status": "awaiting-approval",
                    "session_id": session["session_id"],
                    "user_code": session["user_code"],
                    "verification_uri": session["verification_uri"],
                    "expires_in": session["expires_in"],
                    "manifest": manifest.model_dump(mode="json", by_alias=True),
                }
            else:
                exchange = api.exchange(
                    discovery,
                    session_id=session["session_id"],
                    device_code=device_code,
                )
                plan = plan_files(
                    exchange,
                    root=args.root,
                    credential_dir=args.credential_dir,
                    repo_id=args.repo_id,
                    expected_environment_id=discovery.environment_id,
                    expected_endpoint=discovery.api_endpoint,
                    sprintctl_version=manifest.packages.sprintctl,
                    install_mode=args.install_mode,
                )
                if args.install_mode != "print":
                    apply_plan(plan)
                session_file.unlink(missing_ok=True)
                result = {
                    "status": "planned" if args.install_mode == "print" else "applied",
                    "files": [str(path) for path in plan.files],
                    "commands": list(plan.commands),
                    "installation": (
                        "not-run; execute the listed command"
                        if plan.commands
                        else "not-requested"
                    ),
                }
    except (BootstrapError, ValueError) as error:
        print(json.dumps({"status": "error", "error": str(error)}))
        return 2
    if args.output == "json":
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
