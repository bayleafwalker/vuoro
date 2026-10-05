"""``vuoro-knowledge``: local-checkout CLI over the knowledge operations.

Every command takes ``--root`` one or more times; the catalog is rebuilt from
those checkouts on each call. The caller can only see what it can already
read on disk. Exit codes: 0 ok, 1 validation errors or unchanged-check
failure, 2 usage error, 3 recheck found changes needing review.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Any

from .catalog import build_catalog
from .evidence import evidence_item_draft, evidence_ref
from .resolve import recheck, resolve_context
from .retrieval import get, search
from .validate import has_errors, validate


def _emit(value: Any) -> None:
    json.dump(value, sys.stdout, indent=2, ensure_ascii=False, sort_keys=False)
    sys.stdout.write("\n")


def _roots(args: argparse.Namespace) -> list[Path]:
    return [Path(r) for r in (args.root or ["."])]


def _cmd_validate(args: argparse.Namespace) -> int:
    catalog = build_catalog(_roots(args))
    problems = validate(catalog)
    shown = [p for p in problems if p.severity == "error" or not args.errors_only]
    _emit(
        {
            "catalog_digest": catalog.digest(),
            "documents": len(catalog.documents),
            "errors": sum(p.severity == "error" for p in problems),
            "warnings": sum(p.severity == "warning" for p in problems),
            "problems": [p.to_dict() for p in shown],
        }
    )
    return 1 if has_errors(problems) else 0


def _cmd_build(args: argparse.Namespace) -> int:
    catalog = build_catalog(_roots(args))
    data = catalog.to_dict()
    data["problems"] = [p.to_dict() for p in validate(catalog)]
    if args.no_text:
        for doc in data["documents"]:
            doc.pop("text", None)
    out = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        Path(args.out).write_text(out)
        _emit({"catalog_digest": data["digest"], "documents": len(data["documents"]), "out": args.out})
    else:
        sys.stdout.write(out)
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    catalog = build_catalog(_roots(args))
    filters = {
        "purpose": args.purpose,
        "lifecycle": args.lifecycle,
        "repo": args.repo,
        "component": args.component,
        "subject": args.subject,
    }
    _emit(search(catalog, args.query, filters=filters, limit=args.limit))
    return 0


def _cmd_get(args: argparse.Namespace) -> int:
    catalog = build_catalog(_roots(args))
    try:
        _emit(get(catalog, args.doc_id, section=args.section, revision=args.revision, max_lines=args.max_lines))
    except KeyError as exc:
        print(f"error: {exc.args[0]}", file=sys.stderr)
        return 2
    return 0


def _request(args: argparse.Namespace) -> dict[str, Any]:
    request: dict[str, Any] = {}
    if args.request:
        request = json.loads(Path(args.request).read_text())
    for key in ("subjects", "components", "repos", "environments", "questions", "require"):
        values = getattr(args, key)
        if values:
            request[key] = sorted(set(request.get(key, [])) | set(values))
    if args.topic:
        request["topic"] = args.topic
    if args.as_of:
        request["as_of"] = args.as_of
    for pair in args.version or []:
        component, _, version = pair.partition("=")
        request.setdefault("versions", {})[component] = version
    for pair in args.revision or []:
        repo, _, revision = pair.partition("=")
        request.setdefault("revisions", {})[repo] = revision
    return request


def _cmd_resolve(args: argparse.Namespace) -> int:
    catalog = build_catalog(_roots(args))
    try:
        manifest = resolve_context(catalog, _request(args))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    manifest["generated_at"] = _dt.datetime.now(_dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    data = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode()
    if not args.out:
        sys.stdout.write(data.decode())
        return 0
    Path(args.out).write_bytes(data)
    summary = {
        "manifest_digest": manifest["manifest_digest"],
        "out": args.out,
        "sources": len(manifest["sources"]),
        "conflicts": [c["code"] for c in manifest["conflicts"]],
        "unresolved": [u["code"] for u in manifest["unresolved"]],
        "evidence_ref": evidence_ref(data, args.ref or args.out),
    }
    if args.item_id:
        summary["evidence_item"] = evidence_item_draft(
            manifest, data, args.ref or args.out, item_id=args.item_id, generated_at=manifest["generated_at"]
        )
    _emit(summary)
    return 0


def _cmd_recheck(args: argparse.Namespace) -> int:
    catalog = build_catalog(_roots(args))
    manifest = json.loads(Path(args.manifest).read_text())
    try:
        result = recheck(catalog, manifest)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not args.full:
        result.pop("current")
    _emit(result)
    return {"unchanged": 0, "changed": 0, "review-required": 3}[result["status"]]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vuoro-knowledge", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def command(name: str, help_text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--root", action="append", help="repository checkout to catalog (repeatable)")
        return p

    p = command("validate", "check identity, references, lifecycle and contradictions")
    p.add_argument("--errors-only", action="store_true")
    p.set_defaults(func=_cmd_validate)

    p = command("build", "write the rebuildable catalog projection")
    p.add_argument("--out")
    p.add_argument("--no-text", action="store_true", help="omit document text")
    p.set_defaults(func=_cmd_build)

    p = command("search", "knowledge.search")
    p.add_argument("query")
    p.add_argument("--purpose", action="append")
    p.add_argument("--lifecycle", action="append")
    p.add_argument("--repo", action="append")
    p.add_argument("--component", action="append")
    p.add_argument("--subject", action="append")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=_cmd_search)

    p = command("get", "knowledge.get")
    p.add_argument("doc_id")
    p.add_argument("--section")
    p.add_argument("--revision")
    p.add_argument("--max-lines", type=int, default=400)
    p.set_defaults(func=_cmd_get)

    p = command("resolve", "knowledge.resolve_context; writes a context manifest")
    p.add_argument("--request", help="JSON request file")
    p.add_argument("--topic")
    p.add_argument("--subject", dest="subjects", action="append")
    p.add_argument("--component", dest="components", action="append")
    p.add_argument("--repo", dest="repos", action="append")
    p.add_argument("--environment", dest="environments", action="append")
    p.add_argument("--question", dest="questions", action="append")
    p.add_argument("--require", action="append", help="purpose that must have applicable guidance")
    p.add_argument("--version", action="append", help="component=version")
    p.add_argument("--revision", action="append", help="repo=commit the work targets")
    p.add_argument("--as-of")
    p.add_argument("--out", help="write the manifest here and print the evidence binding")
    p.add_argument("--ref", help="artifact reference to record instead of --out")
    p.add_argument("--item-id", help="also print a work.evidence.append-v1 item draft")
    p.set_defaults(func=_cmd_resolve)

    p = command("recheck", "compare a recorded manifest with current sources")
    p.add_argument("manifest")
    p.add_argument("--full", action="store_true")
    p.set_defaults(func=_cmd_recheck)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
