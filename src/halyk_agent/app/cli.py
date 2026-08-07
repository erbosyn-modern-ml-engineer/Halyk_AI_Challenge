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

    dataset_parser = subparsers.add_parser(
        "dataset",
        help="Dataset preflight / quarantine utilities",
    )
    dataset_sub = dataset_parser.add_subparsers(dest="dataset_command", required=True)
    preflight_parser = dataset_sub.add_parser(
        "preflight",
        help="Sanitize raw dataset; quarantine answer-key candidates",
    )
    preflight_parser.add_argument("--input", required=True, type=Path)
    preflight_parser.add_argument("--output", required=True, type=Path)

    solve_parser = subparsers.add_parser(
        "solve",
        help="Competition baseline solve from sanitized manifest",
    )
    solve_parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Path to sanitized_manifest.json (preferred)",
    )
    solve_parser.add_argument(
        "--dataset",
        type=Path,
        default=None,
        help="Raw dataset root (composition: preflight then solve; solver never walks root)",
    )
    solve_parser.add_argument("--output", required=True, type=Path)
    solve_parser.add_argument("--team", default=None)
    solve_parser.add_argument("--email", default=None)
    solve_parser.add_argument("--model", default=None)

    train_parser = subparsers.add_parser(
        "train-score",
        help="Training-only scorer (requires HALYK_MODE=training)",
    )
    train_parser.add_argument("--dataset", required=True, type=Path)
    train_parser.add_argument("--submission", required=True, type=Path)
    train_parser.add_argument("--output", required=True, type=Path)

    ocr_parser = subparsers.add_parser(
        "ocr-diagnose",
        help="Bounded OCR quality diagnostic over PDF documents",
    )
    ocr_parser.add_argument("--documents", required=True, type=Path)
    ocr_parser.add_argument("--output", required=True, type=Path)

    ocr_cmd = subparsers.add_parser(
        "ocr",
        help="Selective provenance-safe OCR (Stage 5A.4)",
    )
    ocr_sub = ocr_cmd.add_subparsers(dest="ocr_command", required=True)
    ocr_probe = ocr_sub.add_parser("probe", help="Read-only OCR backend probe (no downloads)")
    ocr_probe.add_argument("--json-output", action="store_true")
    ocr_run = ocr_sub.add_parser("run", help="Selectively OCR blocking pages from parse output")
    ocr_run.add_argument("--parsed", required=True, type=Path)
    ocr_run.add_argument("--output", required=True, type=Path)
    ocr_run.add_argument("--overwrite", action="store_true")
    ocr_run.add_argument("--backend", default=None, help="Explicit backend (tesseract_cli)")
    ocr_run.add_argument("--languages", default="eng+rus+kaz")
    ocr_run.add_argument("--only-required", action="store_true", default=True)
    ocr_run.add_argument("--max-pages", type=int, default=32)
    ocr_run.add_argument("--timeout", type=float, default=60.0)
    ocr_run.add_argument("--scale", type=float, default=2.0)
    ocr_run.add_argument("--psm", type=int, default=6)
    ocr_run.add_argument(
        "--source-root",
        action="append",
        default=[],
        type=Path,
        help="Directory containing original PDF files (repeatable)",
    )

    route_parser = subparsers.add_parser(
        "route",
        help="Deterministic scenario/entity routing (Stage 5B)",
    )
    route_parser.add_argument(
        "--dataset-manifest",
        required=True,
        type=Path,
        help="Path to sanitized_manifest.json",
    )
    route_parser.add_argument(
        "--parsed",
        required=True,
        type=Path,
        help="OCR-enriched or Stage 3/5A parse output directory",
    )
    route_parser.add_argument("--output", required=True, type=Path)
    route_parser.add_argument("--overwrite", action="store_true")
    route_parser.add_argument("--json-output", action="store_true")
    route_parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail on structural conflicts that make a scenario unusable",
    )

    authority_parser = subparsers.add_parser(
        "authority",
        help="Document taxonomy and authority resolution (Stage 5C)",
    )
    authority_parser.add_argument(
        "--routing",
        required=True,
        type=Path,
        help="Stage 5B routing output directory",
    )
    authority_parser.add_argument(
        "--parsed",
        required=True,
        type=Path,
        help="OCR-enriched or Stage 5A parse output directory",
    )
    authority_parser.add_argument("--output", required=True, type=Path)
    authority_parser.add_argument("--overwrite", action="store_true")
    authority_parser.add_argument("--json-output", action="store_true")
    authority_parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail when a required authority domain has an unresolved conflict",
    )

    covenant_parser = subparsers.add_parser(
        "covenant",
        help="Compile typed covenant definitions from authority (Stage 5D)",
    )
    covenant_sub = covenant_parser.add_subparsers(dest="covenant_command", required=True)
    covenant_compile = covenant_sub.add_parser(
        "compile",
        help="Compile CovenantDefinitions for all template covenant cells",
    )
    covenant_compile.add_argument(
        "--authority",
        required=True,
        type=Path,
        help="Stage 5C authority output directory",
    )
    covenant_compile.add_argument(
        "--parsed",
        required=True,
        type=Path,
        help="OCR-enriched or Stage 5A parse output directory",
    )
    covenant_compile.add_argument(
        "--template",
        required=True,
        type=Path,
        help="Submission template JSON (answers universe)",
    )
    covenant_compile.add_argument("--output", required=True, type=Path)
    covenant_compile.add_argument("--overwrite", action="store_true")
    covenant_compile.add_argument("--json-output", action="store_true")

    facts_parser = subparsers.add_parser(
        "facts",
        help="Structured fact extraction (Stage 5E)",
    )
    facts_sub = facts_parser.add_subparsers(dest="facts_command", required=True)
    facts_extract = facts_sub.add_parser(
        "extract",
        help="Extract structured facts from authoritative documents",
    )
    facts_extract.add_argument(
        "--authority",
        required=True,
        type=Path,
        help="Stage 5C authority output directory",
    )
    facts_extract.add_argument(
        "--covenants",
        required=True,
        type=Path,
        help="Stage 5D covenant output directory (or covenant_definitions.jsonl)",
    )
    facts_extract.add_argument(
        "--parsed",
        required=True,
        type=Path,
        help="OCR-enriched or Stage 5A parse output directory",
    )
    facts_extract.add_argument(
        "--ledger",
        type=Path,
        default=None,
        help="Optional ledger CSV for TXN id semantic checks",
    )
    facts_extract.add_argument("--output", required=True, type=Path)
    facts_extract.add_argument("--overwrite", action="store_true")
    facts_extract.add_argument(
        "--allow-network-models",
        action="store_true",
        help="Enable model gateway HTTP (default: deterministic-only, fail-closed)",
    )
    facts_extract.add_argument("--json-output", action="store_true")

    transactions_parser = subparsers.add_parser(
        "transactions",
        help="Deterministic transaction taxonomy & calculation inputs (Stage 5F)",
    )
    transactions_sub = transactions_parser.add_subparsers(
        dest="transactions_command", required=True
    )
    transactions_prepare = transactions_sub.add_parser(
        "prepare",
        help="Classify ledger rows and apply Stage 5E adjustments for Stage 6",
    )
    transactions_prepare.add_argument(
        "--routing",
        required=True,
        type=Path,
        help="Stage 5B routing output directory",
    )
    transactions_prepare.add_argument(
        "--covenants",
        required=True,
        type=Path,
        help="Stage 5D covenant output directory",
    )
    transactions_prepare.add_argument(
        "--facts",
        required=True,
        type=Path,
        help="Stage 5E facts output directory",
    )
    transactions_prepare.add_argument(
        "--ledger",
        required=True,
        type=Path,
        help="Master ledger CSV (must match routing ledger SHA)",
    )
    transactions_prepare.add_argument("--output", required=True, type=Path)
    transactions_prepare.add_argument("--overwrite", action="store_true")
    transactions_prepare.add_argument("--json-output", action="store_true")

    models_probe = models_sub.add_parser(
        "probe",
        help="Show configured LLM providers (never HTTP unless --allow-network)",
    )
    models_probe.add_argument(
        "--allow-network",
        action="store_true",
        help="Acknowledge network mode; probe still does not call providers",
    )
    models_probe.add_argument("--json-output", action="store_true")

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

    if args.command == "models" and args.models_command == "probe":
        from halyk_agent.app.facts import probe_models

        probe = probe_models(
            allow_network=bool(args.allow_network),
            settings=get_settings(),
        )
        if args.json_output:
            import json

            print(json.dumps(probe, indent=2, sort_keys=True) + "\n", end="")
        else:
            for key, value in sorted(probe.items()):
                print(f"{key}={value}")
        return 0

    if args.command == "dataset" and args.dataset_command == "preflight":
        from halyk_agent.app.preflight import run_dataset_preflight

        try:
            manifest = run_dataset_preflight(args.input, args.output)
        except Exception as exc:
            print(f"preflight failed: {exc.__class__.__name__}: {exc}", file=sys.stderr)
            return 1
        print("preflight complete")
        print(f"quarantined={len(manifest.quarantined)}")
        print(f"sanitized_manifest={args.output / 'sanitized_manifest.json'}")
        return 0

    if args.command == "solve":
        from halyk_agent.app.solve import run_solve, run_solve_from_manifest

        if args.manifest is None and args.dataset is None:
            print("solve failed: provide --manifest or --dataset", file=sys.stderr)
            return 1
        if args.manifest is not None and args.dataset is not None:
            print("solve failed: use either --manifest or --dataset, not both", file=sys.stderr)
            return 1
        try:
            if args.manifest is not None:
                solve_result = run_solve_from_manifest(
                    args.manifest,
                    args.output,
                    team=args.team,
                    contact_email=args.email,
                    model_name=args.model,
                )
            else:
                solve_result = run_solve(
                    args.dataset,
                    args.output,
                    team=args.team,
                    contact_email=args.email,
                    model_name=args.model,
                )
        except Exception as exc:
            print(f"solve failed: {exc.__class__.__name__}: {exc}", file=sys.stderr)
            return 1
        print("solve complete")
        print(f"run_id={solve_result['run_id']}")
        print(f"submission={solve_result['submission']}")
        return 0

    if args.command == "train-score":
        # Lazy import keeps training out of competition import graphs.
        from halyk_agent.app.train_score import run_train_score

        try:
            score_report = run_train_score(args.dataset, args.submission, args.output)
        except Exception as exc:
            print(f"train-score failed: {exc.__class__.__name__}: {exc}", file=sys.stderr)
            return 1
        print("train-score complete")
        print(f"uniform_total={score_report.uniform_total}")
        print(f"cells={score_report.cell_count}")
        return 0

    if args.command == "ocr-diagnose":
        from halyk_agent.app.ocr_diagnose import run_ocr_diagnose

        try:
            ocr_report = run_ocr_diagnose(args.documents, args.output)
        except Exception as exc:
            print(f"ocr-diagnose failed: {exc.__class__.__name__}: {exc}", file=sys.stderr)
            return 1
        print("ocr-diagnose complete")
        print(f"pdfs={ocr_report['pdf_count']}")
        print(f"pages={ocr_report['page_count']}")
        print(f"ocr_required_pages={ocr_report['ocr_required_page_count']}")
        backend = ocr_report.get("backend")
        available = backend.get("available") if isinstance(backend, dict) else None
        print(f"backend_available={available}")
        return 0

    if args.command == "ocr" and args.ocr_command == "probe":
        from halyk_agent.app.ocr import run_ocr_probe

        _report, text = run_ocr_probe(json_output=bool(args.json_output))
        print(text, end="")
        return 0 if _report.offline_ready_backend else 2

    if args.command == "ocr" and args.ocr_command == "run":
        from halyk_agent.app.ocr import SelectiveOcrError, run_selective_ocr

        languages = [part for part in str(args.languages).replace(",", "+").split("+") if part]
        try:
            run_report = asyncio.run(
                run_selective_ocr(
                    args.parsed,
                    args.output,
                    overwrite=bool(args.overwrite),
                    backend_name=args.backend,
                    languages=languages,
                    only_required=bool(args.only_required),
                    max_pages=int(args.max_pages),
                    timeout=float(args.timeout),
                    scale=float(args.scale),
                    psm=int(args.psm),
                    source_roots=list(args.source_root),
                )
            )
        except SelectiveOcrError as exc:
            print(f"ocr run failed: {exc.message}", file=sys.stderr)
            return 1
        except Exception as exc:
            print(f"ocr run failed: {exc.__class__.__name__}: {exc}", file=sys.stderr)
            return 1
        print("ocr run complete")
        print(f"selected={run_report.selected_pages}")
        print(f"attempted={run_report.attempted_pages}")
        print(f"succeeded={run_report.succeeded_pages}")
        print(f"remaining_blocking={run_report.remaining_blocking_pages}")
        return 0

    if args.command == "route":
        from halyk_agent.app.routing import (
            RoutingServiceError,
            print_route_summary,
            report_to_json,
            route_from_paths,
        )

        try:
            routing_report = route_from_paths(
                dataset_manifest=args.dataset_manifest,
                parsed_dir=args.parsed,
                output_dir=args.output,
                overwrite=bool(args.overwrite),
                strict=bool(args.strict),
            )
        except RoutingServiceError as exc:
            print(f"route failed: {exc.message}", file=sys.stderr)
            return 1
        except Exception as exc:
            print(f"route failed: {exc.__class__.__name__}: {exc}", file=sys.stderr)
            return 1
        if args.json_output:
            print(report_to_json(routing_report), end="")
        else:
            print_route_summary(routing_report)
        return 0

    if args.command == "authority":
        from halyk_agent.app.authority import (
            AuthorityServiceError,
            authority_from_paths,
            print_authority_summary,
        )
        from halyk_agent.app.authority import report_to_json as authority_report_to_json

        try:
            authority_report = authority_from_paths(
                routing_dir=args.routing,
                parsed_dir=args.parsed,
                output_dir=args.output,
                overwrite=bool(args.overwrite),
                strict=bool(args.strict),
            )
        except AuthorityServiceError as exc:
            print(f"authority failed: {exc.message}", file=sys.stderr)
            return 1
        except Exception as exc:
            print(f"authority failed: {exc.__class__.__name__}: {exc}", file=sys.stderr)
            return 1
        if args.json_output:
            print(authority_report_to_json(authority_report), end="")
        else:
            print_authority_summary(authority_report)
        return 0

    if args.command == "covenant":
        if args.covenant_command == "compile":
            from halyk_agent.app.covenant import (
                CovenantServiceError,
                covenant_from_paths,
                print_covenant_summary,
            )
            from halyk_agent.app.covenant import (
                report_to_json as covenant_report_to_json,
            )

            try:
                covenant_report = covenant_from_paths(
                    authority_dir=args.authority,
                    parsed_dir=args.parsed,
                    template_path=args.template,
                    output_dir=args.output,
                    overwrite=bool(args.overwrite),
                )
            except CovenantServiceError as exc:
                print(f"covenant failed: {exc.message}", file=sys.stderr)
                return 1
            except Exception as exc:
                print(f"covenant failed: {exc.__class__.__name__}: {exc}", file=sys.stderr)
                return 1
            if args.json_output:
                print(covenant_report_to_json(covenant_report), end="")
            else:
                print_covenant_summary(covenant_report)
            return 0
        parser.error(f"unknown covenant command: {args.covenant_command}")
        return 2

    if args.command == "facts":
        if args.facts_command == "extract":
            from halyk_agent.app.facts import (
                FactServiceError,
                facts_from_paths,
                print_facts_summary,
            )
            from halyk_agent.app.facts import report_to_json as facts_report_to_json

            try:
                facts_report = facts_from_paths(
                    authority_dir=args.authority,
                    covenants_dir=args.covenants,
                    parsed_dir=args.parsed,
                    output_dir=args.output,
                    ledger_path=args.ledger,
                    overwrite=bool(args.overwrite),
                    allow_network_models=bool(args.allow_network_models),
                    settings=get_settings(),
                )
            except FactServiceError as exc:
                print(f"facts extract failed: {exc.message}", file=sys.stderr)
                return 1
            except Exception as exc:
                print(
                    f"facts extract failed: {exc.__class__.__name__}: {exc}",
                    file=sys.stderr,
                )
                return 1
            if args.json_output:
                print(facts_report_to_json(facts_report), end="")
            else:
                print_facts_summary(facts_report)
            return 0
        parser.error(f"unknown facts command: {args.facts_command}")
        return 2

    if args.command == "transactions":
        if args.transactions_command == "prepare":
            from halyk_agent.app.transactions import (
                TransactionServiceError,
                print_taxonomy_summary,
                transactions_from_paths,
            )
            from halyk_agent.app.transactions import (
                report_to_json as taxonomy_report_to_json,
            )

            try:
                taxonomy_report = transactions_from_paths(
                    routing_dir=args.routing,
                    covenants_dir=args.covenants,
                    facts_dir=args.facts,
                    ledger_path=args.ledger,
                    output_dir=args.output,
                    overwrite=bool(args.overwrite),
                )
            except TransactionServiceError as exc:
                print(f"transactions prepare failed: {exc.message}", file=sys.stderr)
                return 1
            except Exception as exc:
                print(
                    f"transactions prepare failed: {exc.__class__.__name__}: {exc}",
                    file=sys.stderr,
                )
                return 1
            if args.json_output:
                print(taxonomy_report_to_json(taxonomy_report), end="")
            else:
                print_taxonomy_summary(taxonomy_report)
            return 0
        parser.error(f"unknown transactions command: {args.transactions_command}")
        return 2

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
