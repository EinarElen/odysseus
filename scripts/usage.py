#!/usr/bin/env python3
"""Inspect and maintain Odysseus' persistent usage ledger."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.usage_observability import usage_store  # noqa: E402


def _time(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _print(value) -> None:
    print(json.dumps(value, indent=2, default=str, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", default="local", help="ledger owner (default: local)")
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("summary", "runs"):
        command = sub.add_parser(name)
        command.add_argument("--from", dest="start")
        command.add_argument("--to", dest="end")
    sub.choices["runs"].add_argument("--limit", type=int, default=100)

    export = sub.add_parser("export")
    export.add_argument("--format", choices=("jsonl", "csv"), default="jsonl")
    export.add_argument("--from", dest="start")
    export.add_argument("--to", dest="end")
    export.add_argument("--output", type=Path)

    sub.add_parser("backfill")
    sub.add_parser("rebuild-rollups")
    retention = sub.add_parser("apply-retention")
    retention.add_argument("days", type=int)
    delete = sub.add_parser("delete")
    delete_group = delete.add_mutually_exclusive_group(required=True)
    delete_group.add_argument("--run-id")
    delete_group.add_argument("--before")
    delete_group.add_argument("--all", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "summary":
        _print(usage_store.query_summary(owner=args.owner, start=_time(args.start), end=_time(args.end)))
    elif args.command == "runs":
        _print(usage_store.list_runs(owner=args.owner, start=_time(args.start), end=_time(args.end), limit=args.limit))
    elif args.command == "export":
        body = usage_store.export(owner=args.owner, format=args.format, start=_time(args.start), end=_time(args.end))
        if args.output:
            args.output.write_text(body, encoding="utf-8")
        else:
            sys.stdout.write(body)
    elif args.command == "backfill":
        _print(usage_store.backfill_legacy_messages())
    elif args.command == "rebuild-rollups":
        _print({"rebuilt": usage_store.rebuild_daily_rollups(owner=args.owner)})
    elif args.command == "apply-retention":
        _print({"deleted": usage_store.apply_retention(days=args.days)})
    elif args.command == "delete":
        _print({"deleted": usage_store.delete_usage(owner=args.owner, run_id=args.run_id, before=_time(args.before), all_usage=args.all)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
