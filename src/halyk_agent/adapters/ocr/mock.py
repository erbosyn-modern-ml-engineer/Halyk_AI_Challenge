"""Deterministic mock OCR backend for tests (no subprocess, no downloads)."""

from __future__ import annotations

from collections.abc import Callable, Sequence

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
)


class MockOcrBackend:
    """In-memory OCR backend used by unit tests."""

    def __init__(
        self,
        *,
        text_for_page: Callable[[OcrPageRequest], str] | None = None,
        fail_pages: set[int] | None = None,
        offline_ready: bool = True,
        languages: Sequence[str] | None = None,
        render_scale: float = 2.0,
        page_segmentation_mode: int = 6,
        version: str = "mock-1",
    ) -> None:
        self.text_for_page = text_for_page or (
            lambda req: (
                f"Recovered alphanumeric covenant body text for page {req.page_number} "
                "with sufficient characters for trusted OCR quality validation."
            )
        )
        self.fail_pages = fail_pages or set()
        self.offline_ready = offline_ready
        self.languages = list(languages or REQUIRED_OCR_LANGUAGES)
        self.render_scale = render_scale
        self.page_segmentation_mode = page_segmentation_mode
        self.version = version
        self.invoked_pages: list[int] = []
        self.invoked_requests: list[OcrPageRequest] = []

    def identity(self) -> OcrBackendIdentity:
        return OcrBackendIdentity(
            kind=OcrBackendKind.MOCK,
            backend_version=self.version,
            executable_or_package="mock",
            language_data_identity="mock-langs",
            languages=list(self.languages),
            render_scale=self.render_scale,
            page_segmentation_mode=self.page_segmentation_mode,
            configuration_hash=ocr_configuration_hash(
                languages=self.languages,
                render_scale=self.render_scale,
                page_segmentation_mode=self.page_segmentation_mode,
                extra=self.version,
            ),
        )

    async def probe(self) -> OcrBackendAvailability:
        return OcrBackendAvailability(
            kind=OcrBackendKind.MOCK,
            installed=True,
            offline_ready=self.offline_ready,
            version=self.version,
            executable_path="mock",
            installed_languages=list(self.languages),
            missing_languages=[] if self.offline_ready else list(REQUIRED_OCR_LANGUAGES),
            missing_components=[] if self.offline_ready else ["mock_disabled"],
            network_required=False,
            may_download=False,
        )

    async def recognize_pages(
        self,
        requests: Sequence[OcrPageRequest],
    ) -> Sequence[OcrPageResult]:
        identity = self.identity()
        out: list[OcrPageResult] = []
        for request in requests:
            self.invoked_pages.append(request.page_number)
            self.invoked_requests.append(request)
            if not self.offline_ready:
                out.append(
                    OcrPageResult(
                        request=request,
                        status=OcrPageStatus.OCR_BACKEND_UNAVAILABLE,
                        failure_reason=OcrFailureReason.BACKEND_UNAVAILABLE,
                        message="mock backend unavailable",
                    )
                )
                continue
            if request.page_number in self.fail_pages:
                out.append(
                    OcrPageResult(
                        request=request,
                        status=OcrPageStatus.OCR_FAILED,
                        failure_reason=OcrFailureReason.SUBPROCESS_FAILED,
                        message="mock page failure",
                    )
                )
                continue
            text = self.text_for_page(request)
            out.append(
                OcrPageResult(
                    request=request,
                    status=OcrPageStatus.OCR_SUCCEEDED,
                    blocks=[
                        OcrTextBlock(
                            text=text,
                            page_number=request.page_number,
                            bbox=(10.0, 10.0, 200.0, 40.0),
                            reading_order=0,
                            confidence=0.91,
                            origin=TextOrigin.OCR,
                            backend=identity,
                            source_image_identity=f"mock-img-{request.page_number}",
                        )
                    ],
                    duration_ms=1,
                    temporary_bytes_written=0,
                    temporary_cleanup_ok=True,
                )
            )
        return out
