"""Standalone Stage 6 evaluator CLI.

The main competition solver will call the same application service directly;
this entrypoint exists for deterministic replay and evaluator-only verification.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from halyk_agent.app.evaluation import (
    EvaluationServiceError,
    evaluate_from_paths,
    print_evaluation_summary,
    report_to_json,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="halyk-evaluate")
    parser.add_argument("--covenants", required=True, type=Path)
    parser.add_argument("--transactions", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--json-output", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = evaluate_from_paths(
            covenants_dir=args.covenants,
            transactions_dir=args.transactions,
            output_dir=args.output,
            overwrite=bool(args.overwrite),
        )
    except EvaluationServiceError as exc:
        print(f"evaluation failed: {exc.message}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"evaluation failed: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1

    if args.json_output:
        print(report_to_json(report), end="")
    else:
        print_evaluation_summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
