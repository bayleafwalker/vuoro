"""CLI entry point for workflow subscriptions and their owning-thread inbox."""

import argparse
import json
import re
import sys
import time
from pathlib import Path

from . import store
from .forgejo import Unavailable, observe

REPO = r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
ORIGIN = r"https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?"
SHA = r"[0-9a-f]{40}"
DIGEST = r"sha256:[0-9a-f]{64}"
LABEL = r"[A-Za-z0-9_.@/-]{1,200}"
THREAD = r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}"


def matching(pattern):
    def parse(value):
        if not re.fullmatch(pattern, value) or any(
            segment in {".", ".."} for segment in value.split("/")
        ):
            raise argparse.ArgumentTypeError("invalid identifier")
        return value
    return parse


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--state", type=Path, default=store.default_path())
    root.add_argument("--json", action="store_true", help="emit JSON")
    commands = root.add_subparsers(required=True, dest="command")
    watch = commands.add_parser("watch", help="register a bounded exact-run subscription")
    watch.add_argument("--kind", choices=["ci", "promotion"], default="ci")
    watch.add_argument("--origin", required=True, type=matching(ORIGIN))
    watch.add_argument("--repository", required=True, type=matching(REPO))
    watch.add_argument("--head", required=True, type=matching(SHA))
    watch.add_argument("--run", required=True, type=positive)
    watch.add_argument("--workflow", required=True, type=matching(LABEL))
    watch.add_argument("--event", required=True, type=matching(LABEL))
    target = watch.add_mutually_exclusive_group(required=True)
    target.add_argument("--pr", type=positive)
    target.add_argument("--ref", type=matching(LABEL))
    watch.add_argument("--base", type=matching(LABEL))
    watch.add_argument("--required", required=True, action="append", type=matching(LABEL))
    watch.add_argument("--thread", required=True, type=matching(THREAD))
    watch.add_argument("--ttl", default=86400, type=positive, help="expire after this many seconds")
    watch.add_argument("--environment", type=matching(LABEL))
    watch.add_argument("--digest", type=matching(DIGEST))
    watch.add_argument("--digest-input", default="image_digest", type=matching(LABEL))
    commands.add_parser("status", help="list watched runs and recorded observations")
    reconcile = commands.add_parser("reconcile", help="refresh active watches")
    reconcile.add_argument("--interval", type=positive, help="poll until stopped")
    inbox = commands.add_parser("inbox", help="read pending events for an owning thread")
    inbox.add_argument("--thread", required=True, type=matching(THREAD))
    ack = commands.add_parser("ack", help="acknowledge a received event")
    ack.add_argument("--thread", required=True, type=matching(THREAD))
    ack.add_argument("event", type=matching(r"[0-9a-f]{64}"))
    stop = commands.add_parser("stop", help="stop future observations of a watch")
    stop.add_argument("watch", type=matching(r"[0-9a-f]{64}"))
    return root


def register(args, cli, db):
    if args.pr and not args.base:
        cli.error("--pr requires --base")
    if args.ref and args.base:
        cli.error("--base applies to --pr only")
    if args.kind == "promotion":
        if not (args.ref and args.environment and args.digest):
            cli.error("promotion requires --ref, --environment and --digest")
    elif args.environment or args.digest:
        cli.error("--environment and --digest apply to promotion only")
    spec = {field: getattr(args, field) for field in (
        "kind", "origin", "repository", "head", "run", "workflow", "event",
        "pr", "ref", "base", "thread", "environment", "digest", "digest_input",
    )}
    spec["required"] = sorted(set(args.required))
    return {"watch": store.register(db, spec, args.ttl)}


def emit(value, as_json):
    if as_json:
        print(json.dumps(value, sort_keys=True), flush=True)
    elif isinstance(value, list):
        for row in value:
            spec = row.get("spec", row)
            observation = row.get("observation") or row
            print(
                f"{row['id']} {spec['repository']} run {spec['run']} "
                f"{spec['head']} {observation.get('state', 'unobserved')}",
                flush=True,
            )
    else:
        for key, item in value.items():
            print(f"{key} {item}", flush=True)


def main(argv=None):
    values = sys.argv[1:] if argv is None else argv
    if values == ["_forge-observe"]:
        try:
            observation = observe(json.load(sys.stdin))
        except (ValueError, Unavailable) as error:
            observation = {"state": "unavailable", "reason": str(error)}
        print(json.dumps(observation))
        return 0
    cli = parser()
    args = cli.parse_args(values)
    with store.connect(args.state) as db:
        if args.command == "watch":
            result = register(args, cli, db)
        elif args.command == "status":
            result = store.status(db)
        elif args.command == "inbox":
            result = store.inbox(db, args.thread)
        elif args.command == "ack":
            result = {"acknowledged": store.acknowledge(db, args.thread, args.event)}
        elif args.command == "stop":
            result = {"stopped": store.stop(db, args.watch)}
        else:
            while True:
                store.reconcile(db)
                result = store.status(db)
                if args.interval is None:
                    break
                emit(result, args.json)
                time.sleep(args.interval)
        emit(result, args.json)
    return int(False in result.values()) if isinstance(result, dict) else 0


if __name__ == "__main__":
    raise SystemExit(main())
