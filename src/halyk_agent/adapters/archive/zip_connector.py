"""Secure ZIP archive connector implementing SourceConnector."""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from halyk_agent.adapters.archive.errors import (
    ArchiveLimitExceededError,
    CorruptArchiveError,
    UnsafeArchiveEntryError,
)
from halyk_agent.adapters.archive.format_id import detect_format, guess_mime
from halyk_agent.adapters.archive.hashing import artifact_id_for, sha256_file
from halyk_agent.adapters.archive.safety import (
    ensure_within_root,
    is_special_unix_file,
    is_symlink_zipinfo,
    normalize_member_path,
    register_normalized_path,
    validate_declared_sizes,
)
from halyk_agent.config import Settings, get_settings
from halyk_agent.contracts.connectors import (
    ConnectorBatch,
    ConnectorCheckpoint,
    ConnectorItem,
)
from halyk_agent.domain.datasets import ArtifactFormat


@dataclass(frozen=True)
class ExtractedMember:
    """One safely extracted archive member."""

    relative_path: str
    normalized_path: str
    absolute_path: Path
    size_bytes: int
    compressed_size_bytes: int
    sha256: str
    format: ArtifactFormat
    mime_type: str | None
    warnings: list[str]
    is_directory: bool = False


@dataclass(frozen=True)
class ExtractionResult:
    """Result of validating and extracting a ZIP archive."""

    archive_path: Path
    archive_name: str
    archive_sha256: str
    workspace: Path
    members: list[ExtractedMember]
    warnings: list[str]


class ArchiveZipConnector:
    """LoadConnector-style ZIP inspector with failure isolation per member."""

    def __init__(
        self,
        archive_path: Path,
        workspace: Path,
        *,
        settings: Settings | None = None,
    ) -> None:
        self._archive_path = archive_path
        self._workspace = workspace
        self._settings = settings or get_settings()
        self._extraction: ExtractionResult | None = None

    def extract(self) -> ExtractionResult:
        """Validate, extract, hash, and identify all safe archive members."""
        if self._extraction is not None:
            return self._extraction
        if not self._archive_path.is_file():
            raise CorruptArchiveError(f"archive not found: {self._archive_path}")

        archive_sha = sha256_file(self._archive_path)
        self._workspace.mkdir(parents=True, exist_ok=True)
        extracted_root = self._workspace / "extracted"
        extracted_root.mkdir(parents=True, exist_ok=True)

        warnings: list[str] = []
        members: list[ExtractedMember] = []
        seen: dict[str, str] = {}

        try:
            zf = zipfile.ZipFile(self._archive_path)
        except zipfile.BadZipFile as exc:
            raise CorruptArchiveError(f"corrupt ZIP archive: {exc}") from exc

        with zf:
            infos = zf.infolist()
            if len(infos) > self._settings.max_archive_files:
                raise ArchiveLimitExceededError(
                    f"archive file count {len(infos)} exceeds "
                    f"max_archive_files={self._settings.max_archive_files}"
                )

            total_declared = sum(max(info.file_size, 0) for info in infos if not info.is_dir())
            if total_declared > self._settings.max_total_uncompressed_bytes:
                raise ArchiveLimitExceededError(
                    f"declared total uncompressed size {total_declared} exceeds "
                    f"max_total_uncompressed_bytes="
                    f"{self._settings.max_total_uncompressed_bytes}"
                )

            file_count = 0
            for info in infos:
                name = info.filename
                normalized = normalize_member_path(
                    name,
                    max_path_length=self._settings.max_path_length,
                )
                if is_symlink_zipinfo(info.external_attr, info.create_system):
                    raise UnsafeArchiveEntryError(f"symlink entry rejected: {name!r}")
                if is_special_unix_file(info.external_attr, info.create_system):
                    raise UnsafeArchiveEntryError(f"special file entry rejected: {name!r}")

                if info.is_dir() or name.endswith("/"):
                    register_normalized_path(seen, normalized)
                    target_dir = ensure_within_root(extracted_root, extracted_root / normalized)
                    target_dir.mkdir(parents=True, exist_ok=True)
                    continue

                file_count += 1
                validate_declared_sizes(
                    file_count=file_count,
                    file_name=name,
                    file_size=info.file_size,
                    compress_size=info.compress_size,
                    total_uncompressed=total_declared,
                    settings=self._settings,
                )
                register_normalized_path(seen, normalized)
                members.append(self._extract_member(zf, info, normalized, extracted_root))

        result = ExtractionResult(
            archive_path=self._archive_path,
            archive_name=self._archive_path.name,
            archive_sha256=archive_sha,
            workspace=self._workspace,
            members=sorted(members, key=lambda item: item.normalized_path),
            warnings=warnings,
        )
        self._extraction = result
        return result

    def _extract_member(
        self,
        zf: zipfile.ZipFile,
        info: zipfile.ZipInfo,
        normalized: str,
        extracted_root: Path,
    ) -> ExtractedMember:
        target = ensure_within_root(extracted_root, extracted_root / normalized)
        target.parent.mkdir(parents=True, exist_ok=True)

        tmp_path: Path | None = None
        written = 0
        try:
            with tempfile.NamedTemporaryFile(
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".part",
                delete=False,
            ) as tmp:
                tmp_path = Path(tmp.name)
                with zf.open(info, "r") as source:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > self._settings.max_single_file_bytes:
                            raise ArchiveLimitExceededError(
                                f"streamed size for {normalized!r} exceeds "
                                f"max_single_file_bytes="
                                f"{self._settings.max_single_file_bytes}"
                            )
                        tmp.write(chunk)
            assert tmp_path is not None
            tmp_path.replace(target)
        except Exception:
            if tmp_path is not None and tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            raise

        digest = sha256_file(target)
        format_, format_warnings = detect_format(target, relative_path=normalized)
        return ExtractedMember(
            relative_path=info.filename.replace("\\", "/"),
            normalized_path=normalized,
            absolute_path=target,
            size_bytes=written,
            compressed_size_bytes=max(info.compress_size, 0),
            sha256=digest,
            format=format_,
            mime_type=guess_mime(format_),
            warnings=format_warnings,
        )

    async def _batches(
        self,
        *,
        checkpoint: ConnectorCheckpoint | None,
    ) -> AsyncIterator[ConnectorBatch]:
        result = self.extract()
        start = 0
        if checkpoint is not None and "offset" in checkpoint.cursor:
            raw_offset = checkpoint.cursor["offset"]
            if isinstance(raw_offset, int):
                start = raw_offset
        batch_size = self._settings.connector_batch_size
        members = result.members[start:]
        offset = start
        for index in range(0, len(members), batch_size):
            chunk = members[index : index + batch_size]
            items = [
                ConnectorItem(
                    item_id=artifact_id_for(member.normalized_path, member.sha256),
                    source_path=member.normalized_path,
                    media_type=member.mime_type,
                    content_hash=member.sha256,
                    metadata={
                        "format": member.format.value,
                        "size_bytes": member.size_bytes,
                    },
                )
                for member in chunk
            ]
            offset += len(chunk)
            has_more = offset < len(result.members)
            yield ConnectorBatch(
                items=items,
                failures=[],
                checkpoint=ConnectorCheckpoint(
                    has_more=has_more,
                    cursor={"offset": offset},
                ),
            )

    def load_from_state(
        self,
        *,
        checkpoint: ConnectorCheckpoint | None = None,
    ) -> AsyncIterator[ConnectorBatch]:
        return self._batches(checkpoint=checkpoint)

    def poll_source(
        self,
        *,
        start: datetime,
        end: datetime,
        checkpoint: ConnectorCheckpoint | None = None,
    ) -> AsyncIterator[ConnectorBatch]:
        async def _empty() -> AsyncIterator[ConnectorBatch]:
            _ = (start, end, checkpoint)
            if False:  # pragma: no cover
                yield ConnectorBatch(
                    items=[],
                    failures=[],
                    checkpoint=ConnectorCheckpoint(has_more=False),
                )

        return _empty()

    async def retrieve_slim_ids(self) -> Sequence[str]:
        result = self.extract()
        return [artifact_id_for(member.normalized_path, member.sha256) for member in result.members]


def cleanup_workspace(path: Path) -> None:
    """Remove an inspection workspace tree."""
    if path.exists():
        shutil.rmtree(path)
