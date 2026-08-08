"""Tesseract CLI OCR adapter — selected only when probe reports offline-ready."""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path

from halyk_agent.adapters.archive.hashing import sha256_bytes
from halyk_agent.adapters.ocr.probe import (
    _subprocess_env_without_broken_tessdata_prefix,
    discover_tessdata_dir,
    probe_tesseract_cli,
)
from halyk_agent.domain.ids import sha256_text
from halyk_agent.domain.ocr import (
    REQUIRED_OCR_LANGUAGES,
    OcrBackendAvailability,
    OcrBackendIdentity,
    OcrBackendKind,
    OcrFailureReason,
    OcrPageRequest,
    OcrPageResult,
    OcrPageStatus,
    OcrTextBlock,
    TextOrigin,
    ocr_configuration_hash,
    validate_ocr_page_text,
)


class TesseractCliOcrBackend:
    """Explicit Tesseract CLI backend. No silent engine fallback."""

    def __init__(
        self,
        *,
        languages: Sequence[str] | None = None,
        render_scale: float = 2.0,
        page_segmentation_mode: int = 6,
        timeout_seconds: float = 60.0,
        max_concurrency: int = 2,
        executable_path: str | None = None,
    ) -> None:
        self.languages = list(languages or REQUIRED_OCR_LANGUAGES)
        self.render_scale = render_scale
        self.page_segmentation_mode = page_segmentation_mode
        self.timeout_seconds = timeout_seconds
        self.max_concurrency = max(1, max_concurrency)
        self.executable_path = executable_path
        self._identity: OcrBackendIdentity | None = None

    def identity(self, availability: OcrBackendAvailability | None = None) -> OcrBackendIdentity:
        avail = availability or probe_tesseract_cli()
        cfg = ocr_configuration_hash(
            languages=self.languages,
            render_scale=self.render_scale,
            page_segmentation_mode=self.page_segmentation_mode,
            # Bump invalidates any cache written under locale/cp1251 decode.
            extra="tesseract_cli_utf8_strict_v2",
        )
        lang_id = sha256_text(
            "|".join(sorted(avail.installed_languages)) + "|" + (avail.language_data_path or "")
        )[:24]
        identity = OcrBackendIdentity(
            kind=OcrBackendKind.TESSERACT_CLI,
            backend_version=avail.version or "unknown",
            executable_or_package=avail.executable_path or self.executable_path or "tesseract",
            language_data_identity=lang_id,
            languages=list(self.languages),
            render_scale=self.render_scale,
            page_segmentation_mode=self.page_segmentation_mode,
            configuration_hash=cfg,
        )
        self._identity = identity
        return identity

    async def probe(self) -> OcrBackendAvailability:
        return probe_tesseract_cli()

    async def recognize_pages(
        self,
        requests: Sequence[OcrPageRequest],
    ) -> Sequence[OcrPageResult]:
        availability = await self.probe()
        if not availability.offline_ready:
            return [
                OcrPageResult(
                    request=req,
                    status=OcrPageStatus.OCR_BACKEND_UNAVAILABLE
                    if not availability.installed
                    else OcrPageStatus.OCR_LANGUAGE_UNAVAILABLE,
                    failure_reason=(
                        OcrFailureReason.BACKEND_UNAVAILABLE
                        if not availability.installed
                        else OcrFailureReason.LANGUAGE_UNAVAILABLE
                    ),
                    message="tesseract CLI not offline-ready",
                )
                for req in requests
            ]
        identity = self.identity(availability)
        sem = asyncio.Semaphore(self.max_concurrency)
        results: list[OcrPageResult | None] = [None] * len(requests)

        async def _one(index: int, request: OcrPageRequest) -> None:
            async with sem:
                results[index] = await asyncio.to_thread(
                    self._recognize_one, request, identity, availability
                )

        await asyncio.gather(*[_one(i, req) for i, req in enumerate(requests)])
        return [item for item in results if item is not None]

    def _recognize_one(
        self,
        request: OcrPageRequest,
        identity: OcrBackendIdentity,
        availability: OcrBackendAvailability,
    ) -> OcrPageResult:
        started = time.perf_counter()
        tmp_bytes = 0
        cleanup_ok = True
        tmp_path: Path | None = None
        try:
            png_bytes = _render_page_png(
                Path(request.source_path),
                request.page_number,
                scale=self.render_scale,
            )
            tmp_bytes = len(png_bytes)
            fd, name = tempfile.mkstemp(prefix="halyk-ocr-", suffix=".png")
            os.close(fd)
            tmp_path = Path(name)
            # Never write under the repository root by using system temp.
            tmp_path.write_bytes(png_bytes)
            text, conf = _run_tesseract(
                executable=availability.executable_path or "tesseract",
                image_path=tmp_path,
                languages=self.languages,
                psm=self.page_segmentation_mode,
                timeout=self.timeout_seconds,
            )
            status = validate_ocr_page_text(text)
            if status is not OcrPageStatus.OCR_SUCCEEDED:
                return OcrPageResult(
                    request=request,
                    status=status,
                    failure_reason=OcrFailureReason.LOW_QUALITY
                    if status is OcrPageStatus.OCR_LOW_QUALITY
                    else OcrFailureReason.EMPTY_OUTPUT,
                    message="ocr quality validation rejected output",
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    temporary_bytes_written=tmp_bytes,
                    temporary_cleanup_ok=cleanup_ok,
                )
            block = OcrTextBlock(
                text=text.strip(),
                page_number=request.page_number,
                bbox=None,
                reading_order=0,
                confidence=conf,
                origin=TextOrigin.OCR,
                backend=identity,
                source_image_identity=sha256_bytes(png_bytes)[:32],
            )
            return OcrPageResult(
                request=request,
                status=OcrPageStatus.OCR_SUCCEEDED,
                blocks=[block],
                duration_ms=int((time.perf_counter() - started) * 1000),
                temporary_bytes_written=tmp_bytes,
                temporary_cleanup_ok=cleanup_ok,
            )
        except TimeoutError:
            return OcrPageResult(
                request=request,
                status=OcrPageStatus.OCR_TIMEOUT,
                failure_reason=OcrFailureReason.TIMEOUT,
                message="tesseract timed out",
                duration_ms=int((time.perf_counter() - started) * 1000),
                temporary_bytes_written=tmp_bytes,
                temporary_cleanup_ok=cleanup_ok,
            )
        except RuntimeError as exc:
            message = str(exc)
            if message.startswith("OCR_UTF8_DECODE_FAILED"):
                return OcrPageResult(
                    request=request,
                    status=OcrPageStatus.OCR_FAILED,
                    failure_reason=OcrFailureReason.UTF8_DECODE_FAILED,
                    message=message[:300],
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    temporary_bytes_written=tmp_bytes,
                    temporary_cleanup_ok=cleanup_ok,
                )
            return OcrPageResult(
                request=request,
                status=OcrPageStatus.OCR_FAILED,
                failure_reason=OcrFailureReason.SUBPROCESS_FAILED,
                message=f"{exc.__class__.__name__}: {message[:200]}",
                duration_ms=int((time.perf_counter() - started) * 1000),
                temporary_bytes_written=tmp_bytes,
                temporary_cleanup_ok=cleanup_ok,
            )
        except Exception as exc:
            return OcrPageResult(
                request=request,
                status=OcrPageStatus.OCR_FAILED,
                failure_reason=OcrFailureReason.SUBPROCESS_FAILED,
                message=f"{exc.__class__.__name__}",
                duration_ms=int((time.perf_counter() - started) * 1000),
                temporary_bytes_written=tmp_bytes,
                temporary_cleanup_ok=cleanup_ok,
            )
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                    cleanup_ok = not tmp_path.exists()
                except OSError:
                    cleanup_ok = False


def _render_page_png(source: Path, page_number: int, *, scale: float) -> bytes:
    """Render one PDF page via pypdfium2 into PNG bytes (no repo writes)."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(source))
    try:
        if page_number < 1 or page_number > len(pdf):
            raise ValueError(f"page {page_number} out of range")
        page = pdf[page_number - 1]
        bitmap = page.render(scale=scale)
        pil = bitmap.to_pil()
        import io

        buffer = io.BytesIO()
        pil.save(buffer, format="PNG")
        return buffer.getvalue()
    finally:
        pdf.close()


def _run_tesseract(
    *,
    executable: str,
    image_path: Path,
    languages: Sequence[str],
    psm: int,
    timeout: float,
) -> tuple[str, float | None]:
    import subprocess

    lang = "+".join(languages)
    cmd = [
        executable,
        str(image_path),
        "stdout",
        "-l",
        lang,
        "--psm",
        str(psm),
    ]
    tessdata = discover_tessdata_dir(executable)
    if tessdata is not None:
        cmd[1:1] = ["--tessdata-dir", str(tessdata)]
    try:
        # Tesseract CLI emits UTF-8 bytes. Decode explicitly — never locale/cp1251.
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=False,
            timeout=timeout,
            env=_subprocess_env_without_broken_tessdata_prefix(),
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(str(exc)) from exc
    stderr = (proc.stderr or b"")[:2000].decode("utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"tesseract exit {proc.returncode}: {stderr[:200]}")
    try:
        stdout = (proc.stdout or b"").decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise RuntimeError(
            f"OCR_UTF8_DECODE_FAILED: tesseract stdout is not valid UTF-8: {exc}"
        ) from exc
    stdout = stdout[:MAX_SUBPROCESS_CHARS]
    return stdout, None


MAX_SUBPROCESS_CHARS = 500_000
