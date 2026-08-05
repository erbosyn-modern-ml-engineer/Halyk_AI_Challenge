"""CLI entrypoint for Halyk agent utilities."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from halyk_agent.adapters.archive.errors import ArchiveInspectionError
from halyk_agent.adapters.parsing.errors import (
    DocumentParsingError,
    ParserDependencyMissingError,
)
from halyk_agent.app.inspection import inspect_archive
from halyk_agent.app.parsing import parse_inspection_directory
from halyk_agent.config import get_settings


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level CLI parser."""
    parser = argparse.ArgumentParser(prog="halyk-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser(
        "inspect",
        help="Safely inspect a competition ZIP and emit manifest/schema outputs",
    )
    inspect_parser.add_argument("--input", required=True, type=Path, help="Input ZIP path")
    inspect_parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Output directory for extracted files and reports",
    )
    inspect_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow writing into a non-empty output directory",
    )

    parse_parser = subparsers.add_parser(
        "parse",
        help="Parse document artifacts from a Stage 2 inspection directory",
    )
    parse_parser.add_argument(
        "--inspection",
        required=True,
        type=Path,
        help="Stage 2 inspection output directory",
    )
    parse_parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Parse output directory",
    )
    parse_parser.add_argument(
        "--profile",
        required=True,
        choices=["fast", "full"],
        help="Parser profile",
    )
    parse_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow writing into a non-empty output directory",
    )
    parse_parser.add_argument(
        "--force-docling",
        action="store_true",
        help="FULL only: always run Docling (bypass FAST accept)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI main. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "inspect":
        try:
            result = inspect_archive(
                args.input,
                args.output,
                overwrite=bool(args.overwrite),
                settings=get_settings(),
            )
        except ArchiveInspectionError as exc:
            print(f"inspection failed: {exc}", file=sys.stderr)
            return 1
        except OSError as exc:
            print(f"inspection failed: {exc}", file=sys.stderr)
            return 1

        print("inspection complete")
        print(f"archive_sha256={result.manifest.archive_sha256}")
        print(f"files={result.manifest.total_files}")
        print(f"tables={len(result.schema_profile.tables)}")
        print(f"summary={result.summary_path}")
        return 0

    if args.command == "parse":
        if args.force_docling and args.profile != "full":
            print(
                "parse failed: --force-docling is only valid with --profile full",
                file=sys.stderr,
            )
            return 1
        try:
            report = parse_inspection_directory(
                args.inspection,
                args.output,
                profile=args.profile,
                overwrite=bool(args.overwrite),
                force_docling=bool(args.force_docling),
                settings=get_settings(),
            )
        except ParserDependencyMissingError as exc:
            print(f"parse failed: {exc.message}", file=sys.stderr)
            return 1
        except DocumentParsingError as exc:
            print(f"parse failed: {exc.message}", file=sys.stderr)
            return 1
        except OSError as exc:
            print(f"parse failed: {exc}", file=sys.stderr)
            return 1

        print("parse complete")
        print(f"profile={report.profile}")
        print(f"candidates={report.total_candidates}")
        print(f"successful={report.successful}")
        print(f"partial={report.partial}")
        print(f"failed={report.failed}")
        print(f"unsupported={report.unsupported}")
        print(f"cache_hits={report.cache_hits}")
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
