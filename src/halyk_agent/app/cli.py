"""CLI entrypoint for Halyk agent utilities."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from halyk_agent.adapters.archive.errors import ArchiveInspectionError
from halyk_agent.app.inspection import inspect_archive
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

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
