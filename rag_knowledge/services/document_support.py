"""Shared format support and ingestion-decision persistence."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal


PHASE1_EXTENSIONS = {".docx", ".md", ".txt", ".xlsx"}
PHASE2_EXTENSIONS = {
    ".pdf", ".pptx", ".html", ".htm", ".sql",
    ".cnf", ".conf", ".cfg", ".ini", ".xml",
}
SUPPORTED_EXTENSIONS = PHASE1_EXTENSIONS | PHASE2_EXTENSIONS
MEDIA_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp",
    ".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv",
}
DEPENDENCY_EXTENSIONS = {".jar", ".css", ".js", ".map", ".dll", ".exe"}
ARCHIVE_EXTENSIONS = {".rar", ".zip", ".7z", ".tar", ".gz"}

REASON_MESSAGES = {
    "PDF_PAGE_REQUIRES_OCR": "PDF page has no extractable text and requires future OCR processing.",
    "MEDIA_PROCESSING_DEFERRED": "Image and video ingestion is deferred for this phase.",
    "EMBEDDED_MEDIA_PROCESSING_DEFERRED": "Embedded media processing is deferred for this phase.",
    "LEGACY_DOC_REQUIRES_CONVERSION": "Legacy DOC must be converted before ingestion.",
    "LEGACY_SPREADSHEET_REQUIRES_CONVERSION": "Legacy XLS must be converted before ingestion.",
    "DEPENDENCY_ASSET": "Dependency asset is intentionally excluded from the knowledge corpus.",
    "ARCHIVE_ASSET": "Archive asset is intentionally excluded from the knowledge corpus.",
    "FORMAT_PARSE_FAILED": "The supported format could not be parsed safely.",
    "DISABLED_BY_CONFIG": "The format is supported but disabled by scanner configuration.",
    "UNSUPPORTED_EXTENSION": "The file extension is not supported.",
    "DUPLICATE_CONTENT": "Another watched file already has the same content hash.",
}


@dataclass(frozen=True)
class FormatDisposition:
    action: Literal["process", "queued", "excluded"]
    reason_code: str = ""


@dataclass(frozen=True)
class IngestionDecision:
    status: Literal["queued", "excluded"]
    reason_code: str
    file_path: str
    file_name: str
    file_hash: str
    format: str
    locator: str | None = None
    message: str = ""
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def with_source(self, *, file_path: str, file_hash: str) -> "IngestionDecision":
        return replace(
            self,
            file_path=file_path,
            file_name=Path(file_path).name,
            file_hash=file_hash,
        )

    def to_dict(self) -> dict:
        return asdict(self)


def classify_suffix(
    suffix: str,
    *,
    enabled_extensions: set[str] | None = None,
) -> FormatDisposition:
    value = suffix.lower()
    if value in SUPPORTED_EXTENSIONS:
        if enabled_extensions is not None and value not in enabled_extensions:
            return FormatDisposition("excluded", "DISABLED_BY_CONFIG")
        return FormatDisposition("process")
    if value == ".doc":
        return FormatDisposition("queued", "LEGACY_DOC_REQUIRES_CONVERSION")
    if value == ".xls":
        return FormatDisposition("queued", "LEGACY_SPREADSHEET_REQUIRES_CONVERSION")
    if value in MEDIA_EXTENSIONS:
        return FormatDisposition("queued", "MEDIA_PROCESSING_DEFERRED")
    if value in DEPENDENCY_EXTENSIONS:
        return FormatDisposition("excluded", "DEPENDENCY_ASSET")
    if value in ARCHIVE_EXTENSIONS:
        return FormatDisposition("excluded", "ARCHIVE_ASSET")
    return FormatDisposition("excluded", "UNSUPPORTED_EXTENSION")


def make_decision(
    path: str | Path,
    *,
    status: Literal["queued", "excluded"],
    reason_code: str,
    file_hash: str = "",
    locator: str | None = None,
    message: str | None = None,
) -> IngestionDecision:
    source = Path(path)
    return IngestionDecision(
        status=status,
        reason_code=reason_code,
        file_path=str(source),
        file_name=source.name,
        file_hash=file_hash,
        format=source.suffix.lower() or "[no_extension]",
        locator=locator,
        message=message or REASON_MESSAGES.get(reason_code, reason_code),
    )


class IngestionDecisionStore:
    """Versioned, atomic JSON store keyed by stable decision identity."""

    VERSION = 1

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._data = self._load()

    def reload(self) -> None:
        self._data = self._load()

    def reset(self) -> None:
        self._data = {"version": self.VERSION, "decisions": {}}
        self.save()

    def replace_for_file(
        self,
        *,
        file_path: str,
        file_hash: str,
        decisions: list[IngestionDecision],
    ) -> None:
        entries = self._data.setdefault("decisions", {})
        replacement = {}
        for decision in decisions:
            normalized = decision.with_source(file_path=file_path, file_hash=file_hash)
            replacement[self._decision_id(normalized)] = normalized.to_dict()
        existing = {
            key: item
            for key, item in entries.items()
            if item.get("file_path") == file_path
        }
        if self._decisions_match(existing, replacement):
            return
        for key in existing:
            entries.pop(key, None)
        entries.update(replacement)
        self.save()

    def relocate(self, *, file_hash: str, file_path: str) -> None:
        entries = self._data.setdefault("decisions", {})
        matching = [
            IngestionDecision(**item)
            for item in entries.values()
            if item.get("file_hash") == file_hash
        ]
        if matching:
            for key in [key for key, item in entries.items() if item.get("file_hash") == file_hash]:
                entries.pop(key, None)
            self.replace_for_file(file_path=file_path, file_hash=file_hash, decisions=matching)

    def prune_missing(self, base: Path) -> None:
        entries = self._data.setdefault("decisions", {})
        stale = [
            key for key, item in entries.items()
            if not (base / str(item.get("file_path", ""))).exists()
        ]
        if not stale:
            return
        for key in stale:
            entries.pop(key, None)
        self.save()

    def snapshot(self) -> dict:
        return json.loads(json.dumps(self._data, ensure_ascii=False))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        temp.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            for attempt in range(3):
                try:
                    os.replace(temp, self.path)
                    return
                except PermissionError:
                    if attempt == 2:
                        raise
                    time.sleep(0.1 * (attempt + 1))
        finally:
            temp.unlink(missing_ok=True)

    def _load(self) -> dict:
        if not self.path.exists():
            return {"version": self.VERSION, "decisions": {}}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("decision store must be an object")
            value["version"] = self.VERSION
            value.setdefault("decisions", {})
            return value
        except (OSError, ValueError, json.JSONDecodeError):
            return {"version": self.VERSION, "decisions": {}}

    @staticmethod
    def _decision_id(decision: IngestionDecision) -> str:
        raw = "|".join(
            (
                decision.file_hash,
                decision.file_path,
                decision.locator or "",
                decision.reason_code,
            )
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _decisions_match(existing: dict, replacement: dict) -> bool:
        def without_timestamp(entries: dict) -> dict:
            return {
                key: {field: value for field, value in item.items() if field != "updated_at"}
                for key, item in entries.items()
            }

        return without_timestamp(existing) == without_timestamp(replacement)
