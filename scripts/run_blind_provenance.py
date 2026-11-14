"""CLI runner for SEAL stage 4 blind provenance collection.

This is the production entry point.  It wraps ``src.seal_collection`` and
exposes the frozen protocol/dry-run/collect/freeze-study/compare commands.
No inference happens unless ``collect`` is invoked with the explicit parent
authorization token and a verified account-evidence snapshot; mock/offline runs
inject a client through the Python API instead of this CLI.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import seal_collection as sc


def _print(payload: dict) -> None:
    print(json.dumps(payload, indent=2, default=str))


def cmd_freeze_protocol(args: argparse.Namespace) -> int:
    result = sc.freeze_protocol_files(
        args.study_dir,
        fixture_indices=tuple(args.fixture_indices) if args.fixture_indices else sc.FIXTURE_INDICES,
        worker_corpus_path=args.worker_corpus,
    )
    _print(result)
    return 0


def cmd_dry_run(args: argparse.Namespace) -> int:
    result = sc.dry_run(
        args.study_dir,
        budget_config_path=args.budget_config,
        worker_corpus_path=args.worker_corpus,
        fixture_indices=tuple(args.fixture_indices) if args.fixture_indices else sc.FIXTURE_INDICES,
    )
    _print(result)
    return 0


def cmd_collect(args: argparse.Namespace) -> int:
    authorize = args.authorize
    result = sc.collect(
        args.study_dir,
        budget_config_path=args.budget_config,
        worker_corpus_path=args.worker_corpus,
        authorize=authorize,
        now=None,
        fixture_indices=tuple(args.fixture_indices) if args.fixture_indices else None,
    )
    _print(result)
    return 0


def cmd_freeze_study(args: argparse.Namespace) -> int:
    result = sc.freeze_study(args.study_dir)
    _print(result)
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    result = sc.build_comparison(
        study_dir=args.study_dir,
        original_dir=args.original,
        output_path=args.output,
    )
    _print(result)
    return 0


def cmd_smoke(args: argparse.Namespace) -> int:
    # Offline-only: reconstruct the schedule and print counts.  No sends.
    entries = sc.build_schedule()
    print(json.dumps(sc.schedule_counts(entries), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("freeze-protocol", help="Write (or verify) protocol.json + schedule.json")
    p.add_argument("--study-dir", type=Path, required=True)
    p.add_argument("--worker-corpus", type=Path, default=sc.WORKER_CORPUS_PATH)
    p.add_argument("--fixture-indices", type=int, nargs="*", default=None)
    p.set_defaults(func=cmd_freeze_protocol)

    p = sub.add_parser("dry-run", help="Offline reservation estimate under blinding")
    p.add_argument("--study-dir", type=Path, required=True)
    p.add_argument("--budget-config", type=Path, required=True)
    p.add_argument("--worker-corpus", type=Path, default=sc.WORKER_CORPUS_PATH)
    p.add_argument("--fixture-indices", type=int, nargs="*", default=None)
    p.set_defaults(func=cmd_dry_run)

    p = sub.add_parser("collect", help="Collect the frozen schedule (real or injected)")
    p.add_argument("--study-dir", type=Path, required=True)
    p.add_argument("--budget-config", type=Path, required=True)
    p.add_argument("--worker-corpus", type=Path, default=sc.WORKER_CORPUS_PATH)
    p.add_argument("--authorize", default=None, help="Explicit parent authorization token")
    p.add_argument("--fixture-indices", type=int, nargs="*", default=None)
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("freeze-study", help="Reconcile, hash, and freeze a completed study")
    p.add_argument("--study-dir", type=Path, required=True)
    p.set_defaults(func=cmd_freeze_study)

    p = sub.add_parser("compare", help="Generate provenance_comparison.csv")
    p.add_argument("--study-dir", type=Path, required=True)
    p.add_argument("--original", type=Path, default=sc.ORIGINAL_MAIN_DIR)
    p.add_argument("--output", type=Path, default=sc.COMPARISON_OUTPUT_PATH)
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("smoke", help="Offline schedule smoke (no sends)")
    p.set_defaults(func=cmd_smoke)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
