"""``vuoro-ops``: read-only diagnostic operations with structured results.

Every subcommand prints one ``vuoro-diagnostic-result/v1`` JSON document to
stdout (and optionally ``--output FILE``).  Exit status: 0 ok, 1 attention,
2 error or refusal.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
import os
from pathlib import Path
import sys
from typing import Any

from vuoro_service import __version__

from .result import DiagnosticResult, default_attestation_path, default_manifest_path, parse_work_ref

_EXIT = {"ok": 0, "attention": 1, "error": 2}


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--pins", type=Path, help="composition manifest (default: VUORO_COMPOSITION_MANIFEST or the source pins)")
    parser.add_argument("--attestation", type=Path, help="installed composition attestation (default: VUORO_INSTALLED_COMPOSITION_PATH)")
    parser.add_argument("--work-ref", action="append", default=[], metavar="KIND:ID",
                        help="link the result to work, e.g. work-item:2600 (repeatable)")
    parser.add_argument("--output", type=Path, help="also write the result document here")


def _served(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", type=Path, required=True, help="vuoro-client-profile/v1 served profile")
    parser.add_argument("--repo-id", help="repository scope for owner reads")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vuoro-ops", description=__doc__.splitlines()[0])
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    uncertain = commands.add_parser("uncertain-effects", help="classify redeemed-but-unreported effect attempts")
    _common(uncertain)
    source = uncertain.add_mutually_exclusive_group(required=True)
    source.add_argument("--profile", type=Path, help="read attempts through work.effect.attempt-get-v1")
    source.add_argument("--attempts-export", type=Path, help="exported attempt-get results (JSON list or JSONL)")
    uncertain.add_argument("--repo-id")
    uncertain.add_argument("--attempt-id", action="append", default=[], help="attempt id (repeatable)")
    uncertain.add_argument("--attempt-ids-file", type=Path, help="one attempt id per line")
    uncertain.add_argument("--journal", type=Path, help="installed reconciler execution journal directory")

    outcome = commands.add_parser("provider-outcome-check", help="establish one attempt's provider outcome read-only")
    _common(outcome)
    outcome_source = outcome.add_mutually_exclusive_group(required=True)
    outcome_source.add_argument("--profile", type=Path)
    outcome_source.add_argument("--attempts-export", type=Path)
    outcome.add_argument("--repo-id")
    outcome.add_argument("--attempt-id", required=True)
    outcome.add_argument("--journal", type=Path)
    outcome.add_argument("--artifact-root", type=Path, help="protected Git artifact root (holds artifacts/<effect_key>/original.bundle)")
    outcome.add_argument("--remote", help="git remote to ls-remote (default: https://github.com/<repository>.git)")
    outcome.add_argument("--github-api", default="https://api.github.com")
    outcome.add_argument("--github-token-env", default="GITHUB_TOKEN", help="environment variable holding a read token (optional)")

    for name, help_text in (("role-probe", "handshake + identity.current + authority probes against the pins"),
                            ("catalog-probe", "served catalog against the pinned composition")):
        probe = commands.add_parser(name, help=help_text)
        _common(probe)
        _served(probe)
        if name == "catalog-probe":
            probe.add_argument("--expected-catalog-revision")
            probe.add_argument("--expected-operation-count", type=int)
            probe.add_argument("--expected-from-image", help="derive the expected catalog inside this image")

    preflight = commands.add_parser("migration-preflight", help="recovery class for a candidate image + target schema")
    _common(preflight)
    preflight.add_argument("--candidate-image", required=True, help="immutable image ref (@sha256:...)")
    preflight.add_argument("--prior-image", required=True, help="image serving now, the revert target")
    preflight.add_argument("--target-schema", type=int, required=True)
    preflight.add_argument("--domain", default="work", choices=("work", "audit"))
    preflight.add_argument("--current-schema", type=int, help="observed current schema version")
    preflight.add_argument("--profile", type=Path, help="served profile: deduce current schema from the served handshake")
    preflight.add_argument("--served-image", help="image actually serving (default: --prior-image)")
    preflight.add_argument("--run-startup-proof", action="store_true",
                           help="also run scripts/verify_pre_migration_startup.py where it applies")
    preflight.add_argument("--startup-proof-script", type=Path,
                           default=Path(__file__).resolve().parents[5] / "scripts" / "verify_pre_migration_startup.py")

    export = commands.add_parser("recovery-export", help="export one incident's RecoveryLog with the composition stamp")
    _common(export)
    export.add_argument("--incident-id", required=True)
    export.add_argument("--root", type=Path, help="recovery root (default: VUORO_RECOVERY_ROOT or ~/.vuoro/recovery)")

    restore = commands.add_parser("isolated-restore", help="restore a pg_dump -Fc into a disposable PostgreSQL and check compatibility")
    _common(restore)
    restore.add_argument("--dump", type=Path, required=True, help="plain pg_dump -Fc file (age-encrypted dumps unsupported)")
    restore.add_argument("--owner-image", help="run the compatibility check inside this image's pinned owners")
    restore.add_argument("--pg-bin", type=Path, help="PostgreSQL bin directory (default: VUORO_PG_BIN, PATH, nix PG16)")
    restore.add_argument("--role", action="append", default=[], metavar="DOMAIN=ROLE", help="runtime role per domain")
    restore.add_argument("--schema", action="append", default=[], metavar="DOMAIN=SCHEMA", help="schema per domain")
    restore.add_argument("--keep", action="store_true", help="keep the disposable cluster directory for inspection")
    return parser


def _pairs(values: list[str]) -> dict[str, str]:
    pairs = {}
    for value in values:
        key, separator, item = value.partition("=")
        if not separator:
            raise SystemExit(f"expected DOMAIN=VALUE, got {value!r}")
        pairs[key] = item
    return pairs


def _attempt_source(args: argparse.Namespace):
    from .effects import file_attempt_source, served_attempt_source

    if args.attempts_export:
        return file_attempt_source(args.attempts_export)
    from .served import _reader

    return served_attempt_source(_reader(args.profile, None, None), args.repo_id)


def dispatch(args: argparse.Namespace) -> DiagnosticResult:
    refs = [parse_work_ref(value) for value in args.work_ref]
    manifest = args.pins or default_manifest_path()
    if args.command == "uncertain-effects":
        from .effects import uncertain_effects

        ids = list(args.attempt_id)
        if args.attempt_ids_file:
            ids += [line.strip() for line in args.attempt_ids_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        return uncertain_effects(ids, _attempt_source(args), journal_dir=args.journal, work_refs=refs)
    if args.command == "provider-outcome-check":
        from .effects import provider_outcome_check

        return provider_outcome_check(
            args.attempt_id, _attempt_source(args), journal_dir=args.journal, remote=args.remote,
            artifact_root=args.artifact_root, github_api=args.github_api.rstrip("/"),
            github_token=os.environ.get(args.github_token_env) or None, work_refs=refs)
    if args.command == "role-probe":
        from .served import role_probe

        result = role_probe(args.profile, manifest_path=manifest, repo_id=args.repo_id)
    elif args.command == "catalog-probe":
        from .served import catalog_probe

        expected: dict[str, Any] = {}
        if args.expected_catalog_revision:
            expected["revision"] = args.expected_catalog_revision
        if args.expected_operation_count is not None:
            expected["total"] = args.expected_operation_count
        result = catalog_probe(args.profile, manifest_path=manifest, expected=expected or None,
                               expected_from_image=args.expected_from_image)
    elif args.command == "migration-preflight":
        from .migration import migration_preflight

        handshake = None
        if args.profile:
            from .served import _reader

            status, body = _reader(args.profile, None, None).handshake()
            handshake = body if status == 200 else None
        result = migration_preflight(
            candidate_image=args.candidate_image, prior_image=args.prior_image,
            target_schema=args.target_schema, domain=args.domain, current_schema=args.current_schema,
            served_handshake=handshake, served_image=args.served_image,
            run_startup_proof=args.run_startup_proof, startup_proof_script=args.startup_proof_script,
            work_refs=refs)
        if handshake is not None:
            result.result["served_handshake"] = {
                "environment": (handshake.get("environment") or {}).get("name"),
                "service_version": handshake.get("service_version"),
                "compatibility": handshake.get("compatibility"),
            }
        return result
    elif args.command == "recovery-export":
        from .recovery import recovery_export

        return recovery_export(args.incident_id, root=args.root, work_refs=refs)
    elif args.command == "isolated-restore":
        from .restore import isolated_restore

        return isolated_restore(args.dump, manifest_path=manifest, owner_image=args.owner_image,
                                roles=_pairs(args.role), schemas=_pairs(args.schema), bin_dir=args.pg_bin,
                                keep=args.keep, work_refs=refs)
    else:  # pragma: no cover - argparse enforces the choice
        raise SystemExit(2)
    result.work_refs = refs + result.work_refs
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = dispatch(args)
    except (OSError, ValueError) as error:
        result = DiagnosticResult(args.command, "1", "error", {"error": f"{type(error).__name__}: {error}"})
    document = result.document(manifest_path=args.pins or default_manifest_path(),
                               attestation_path=args.attestation or default_attestation_path())
    text = json.dumps(document, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    sys.stdout.write(text + "\n")
    return _EXIT[document["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
