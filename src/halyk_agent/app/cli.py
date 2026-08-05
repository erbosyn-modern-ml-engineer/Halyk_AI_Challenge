"""CLI entrypoint for Halyk agent utilities."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from halyk_agent.adapters.archive.errors import ArchiveInspectionError
from halyk_agent.adapters.parsing.errors import (
    DocumentParsingError,
    ParserDependencyMissingError,
)
from halyk_agent.app.indexing import IndexingError, index_parsed_directory
from halyk_agent.app.inspection import inspect_archive
from halyk_agent.app.models_prewarm import prewarm_components
from halyk_agent.app.parsing import parse_inspection_directory
from halyk_agent.app.retrieval import RetrievalServiceError, result_to_json, search_index
from halyk_agent.config import get_settings


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level CLI parser."""
    parser = argparse.ArgumentParser(prog="halyk-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser(
        "inspect",
        help="Safely inspect a competition ZIP and emit manifest/schema outputs",
    )
    inspect_parser.add_argument("--input", required=True, type=Path)
    inspect_parser.add_argument("--output", required=True, type=Path)
    inspect_parser.add_argument("--overwrite", action="store_true")

    parse_parser = subparsers.add_parser(
        "parse",
        help="Parse document artifacts from a Stage 2 inspection directory",
    )
    parse_parser.add_argument("--inspection", required=True, type=Path)
    parse_parser.add_argument("--output", required=True, type=Path)
    parse_parser.add_argument("--profile", required=True, choices=["fast", "full"])
    parse_parser.add_argument("--overwrite", action="store_true")
    parse_parser.add_argument("--force-docling", action="store_true")

    index_parser = subparsers.add_parser(
        "index",
        help="Chunk, embed, and index Stage 3 parse outputs",
    )
    index_parser.add_argument("--parsed", required=True, type=Path)
    index_parser.add_argument("--output", required=True, type=Path)
    index_parser.add_argument("--profile", required=True, choices=["fast", "full"])
    index_parser.add_argument("--overwrite", action="store_true")
    index_parser.add_argument("--include-partial", action="store_true")
    index_parser.add_argument("--embedding-model", default=None)
    index_parser.add_argument("--batch-size", type=int, default=16)

    search_parser = subparsers.add_parser(
        "search",
        help="Hybrid search against a FAST or FULL retrieval index",
    )
    search_parser.add_argument("--index", type=Path, default=None)
    search_parser.add_argument("--query", required=True)
    search_parser.add_argument("--top-k", type=int, default=10)
    search_parser.add_argument("--profile", required=True, choices=["fast", "full"])
    search_parser.add_argument("--document-id", action="append", default=[])
    search_parser.add_argument("--document-version-id", action="append", default=[])
    search_parser.add_argument("--source-file", action="append", default=[])
    search_parser.add_argument("--page", action="append", type=int, default=[])
    search_parser.add_argument("--chunk-kind", action="append", default=[])
    search_parser.add_argument("--include-parent-context", action="store_true")
    search_parser.add_argument("--rerank", action="store_true")
    search_parser.add_argument("--lexical-only", action="store_true")
    search_parser.add_argument("--json-output", action="store_true")

    models_parser = subparsers.add_parser("models", help="Model utilities")
    models_sub = models_parser.add_subparsers(dest="models_command", required=True)
    prewarm = models_sub.add_parser("prewarm", help="Explicitly download/load models")
    prewarm.add_argument("--profile", required=True, choices=["fast", "full"])
    prewarm.add_argument(
        "--components",
        required=True,
        help="Comma-separated: embeddings,reranker,parser",
    )
    prewarm.add_argument(
        "--approve-large-models",
        action="store_true",
        help=(
            "Required to download optional large models (BGE-M3 / BGE reranker). "
            "Default competition path uses multilingual-e5-small only."
        ),
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
        except (ArchiveInspectionError, OSError) as exc:
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
        except (ParserDependencyMissingError, DocumentParsingError) as exc:
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

    if args.command == "index":
        try:
            index_report = asyncio.run(
                index_parsed_directory(
                    args.parsed,
                    args.output,
                    profile=args.profile,
                    overwrite=bool(args.overwrite),
                    include_partial=bool(args.include_partial),
                    embedding_model=args.embedding_model,
                    batch_size=int(args.batch_size),
                    settings=get_settings(),
                )
            )
        except IndexingError as exc:
            print(f"index failed: {exc.message}", file=sys.stderr)
            return 1
        except Exception as exc:
            print(f"index failed: {exc.__class__.__name__}: {exc}", file=sys.stderr)
            return 1
        print("index complete")
        print(f"profile={index_report.profile}")
        print(f"chunks={index_report.chunk_count}")
        print(f"vectors={index_report.indexed_vectors}")
        return 0

    if args.command == "search":
        try:
            search_result = asyncio.run(
                search_index(
                    index_dir=args.index,
                    query_text=args.query,
                    profile=args.profile,
                    top_k=int(args.top_k),
                    document_ids=list(args.document_id),
                    document_version_ids=list(args.document_version_id),
                    source_files=list(args.source_file),
                    page_numbers=list(args.page),
                    chunk_kinds=list(args.chunk_kind),
                    include_parent_context=bool(args.include_parent_context),
                    rerank=bool(args.rerank),
                    lexical_only=bool(args.lexical_only),
                )
            )
        except RetrievalServiceError as exc:
            print(f"search failed: {exc.message}", file=sys.stderr)
            return 1
        except Exception as exc:
            print(f"search failed: {exc.__class__.__name__}: {exc}", file=sys.stderr)
            return 1
        if args.json_output:
            print(result_to_json(search_result), end="")
        else:
            print(f"hits={len(search_result.hits)}")
            for hit in search_result.hits:
                print(
                    f"{hit.final_rank}\t{hit.matched_by.value}\t"
                    f"{hit.chunk.id[:12]}\t{hit.chunk.raw_text[:80]!r}"
                )
        return 0

    if args.command == "models" and args.models_command == "prewarm":
        components = [part.strip() for part in str(args.components).split(",") if part.strip()]
        try:
            lines = asyncio.run(
                prewarm_components(
                    profile=args.profile,
                    components=components,
                    approve_large_models=bool(args.approve_large_models),
                )
            )
        except Exception as exc:
            print(f"prewarm failed: {exc.__class__.__name__}: {exc}", file=sys.stderr)
            return 1
        for line in lines:
            print(line)
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
