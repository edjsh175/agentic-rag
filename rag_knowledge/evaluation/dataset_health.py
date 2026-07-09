"""Evaluation dataset health checks."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class DatasetHealthReport:
    dataset: str
    total_questions: int
    total_chunk_ids: int
    existing_chunk_ids: int
    missing_chunk_ids: list[str]
    chunk_health: float
    total_expected_targets: int
    matched_expected_targets: int
    target_health: float
    total_sources: int
    existing_sources: int
    missing_sources: list[str]
    source_health: float
    total_sections: int
    existing_sections: int
    missing_sections: list[dict[str, str]]
    section_health: float
    invalid_questions: int
    needs_chunk_id_refresh: bool
    status: str
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def check_eval_dataset_health(
    dataset_path: str | Path,
    *,
    collection: Any | None = None,
) -> DatasetHealthReport:
    """Check whether an eval dataset can be trusted against the current KB."""
    path = Path(dataset_path)
    dataset = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(dataset, list):
        raise ValueError("evaluation dataset must be a JSON array")

    if collection is None:
        from rag_knowledge.repository.vector_store import VectorStore

        collection = VectorStore().get_chroma()._collection

    chunk_ids = _unique(
        chunk_id
        for item in dataset
        for chunk_id in _item_chunk_ids(item)
    )
    existing_ids = _existing_chunk_ids(collection, chunk_ids)
    missing_ids = [chunk_id for chunk_id in chunk_ids if chunk_id not in existing_ids]

    chunks = _load_collection_chunks(collection)
    target_stats = _target_stats(dataset, chunks)
    invalid_questions = sum(
        1
        for item in dataset
        if not _item_has_existing_evidence(item, existing_ids, chunks)
    )

    total_chunk_ids = len(chunk_ids)
    existing_chunk_count = len(existing_ids)
    chunk_health = _ratio(existing_chunk_count, total_chunk_ids)
    target_health = _ratio(
        target_stats["matched_expected_targets"],
        target_stats["total_expected_targets"],
    )
    source_health = _ratio(target_stats["existing_sources"], target_stats["total_sources"])
    section_health = _ratio(target_stats["existing_sections"], target_stats["total_sections"])
    needs_refresh = bool(missing_ids and target_stats["matched_expected_targets"])
    status = "PASS" if invalid_questions == 0 else "BLOCK"
    warnings = []
    if needs_refresh:
        warnings.append("chunk ids are stale, but expected_targets still match current chunks")
    if status == "BLOCK":
        warnings.append("dataset invalid; refresh or recalibrate before running retrieval A/B")

    return DatasetHealthReport(
        dataset=path.as_posix(),
        total_questions=len(dataset),
        total_chunk_ids=total_chunk_ids,
        existing_chunk_ids=existing_chunk_count,
        missing_chunk_ids=missing_ids,
        chunk_health=chunk_health,
        total_expected_targets=target_stats["total_expected_targets"],
        matched_expected_targets=target_stats["matched_expected_targets"],
        target_health=target_health,
        total_sources=target_stats["total_sources"],
        existing_sources=target_stats["existing_sources"],
        missing_sources=target_stats["missing_sources"],
        source_health=source_health,
        total_sections=target_stats["total_sections"],
        existing_sections=target_stats["existing_sections"],
        missing_sections=target_stats["missing_sections"],
        section_health=section_health,
        invalid_questions=invalid_questions,
        needs_chunk_id_refresh=needs_refresh,
        status=status,
        warnings=warnings,
    )


def _existing_chunk_ids(collection: Any, chunk_ids: list[str]) -> set[str]:
    if not chunk_ids:
        return set()
    result = collection.get(ids=chunk_ids, include=["metadatas"])
    return {str(chunk_id) for chunk_id in (result.get("ids") or [])}


def _load_collection_chunks(collection: Any) -> list[dict[str, Any]]:
    result = collection.get(include=["documents", "metadatas"])
    ids = result.get("ids") or []
    documents = result.get("documents") or []
    metadatas = result.get("metadatas") or []
    chunks = []
    for index, chunk_id in enumerate(ids):
        chunks.append({
            "id": str(chunk_id),
            "document": documents[index] if index < len(documents) else "",
            "metadata": dict(metadatas[index] or {}) if index < len(metadatas) else {},
        })
    return chunks


def _target_stats(dataset: list[dict[str, Any]], chunks: list[dict[str, Any]]) -> dict[str, Any]:
    targets = [
        target
        for item in dataset
        for target in _item_expected_targets(item)
    ]
    source_targets = [target for target in targets if str(target.get("source") or "").strip()]
    section_targets = [
        target for target in targets if str(target.get("section_path") or "").strip()
    ]
    usable_targets = [target for target in targets if _is_usable_target(target)]

    missing_sources = [
        str(target.get("source") or "")
        for target in source_targets
        if not any(_source_matches(chunk["metadata"], target["source"]) for chunk in chunks)
    ]
    missing_sections = [
        {"source": str(target.get("source") or ""), "section_path": str(target.get("section_path") or "")}
        for target in section_targets
        if not any(_section_matches(chunk["metadata"], target) for chunk in chunks)
    ]
    matched_targets = sum(
        1 for target in usable_targets if any(_target_matches(chunk, target) for chunk in chunks)
    )

    return {
        "total_expected_targets": len(usable_targets),
        "matched_expected_targets": matched_targets,
        "total_sources": len(source_targets),
        "existing_sources": len(source_targets) - len(missing_sources),
        "missing_sources": _unique(missing_sources),
        "total_sections": len(section_targets),
        "existing_sections": len(section_targets) - len(missing_sections),
        "missing_sections": missing_sections,
    }


def _item_has_existing_evidence(
    item: dict[str, Any],
    existing_ids: set[str],
    chunks: list[dict[str, Any]],
) -> bool:
    if any(chunk_id in existing_ids for chunk_id in _item_chunk_ids(item)):
        return True
    targets = [target for target in _item_expected_targets(item) if _is_usable_target(target)]
    return bool(targets) and any(
        _target_matches(chunk, target)
        for target in targets
        for chunk in chunks
    )


def _item_chunk_ids(item: dict[str, Any]) -> list[str]:
    values = item.get("chunk_ids")
    if values is None:
        values = item.get("relevant_chunk_ids")
    if not isinstance(values, list):
        return []
    return [str(value) for value in values if str(value or "").strip()]


def _item_expected_targets(item: dict[str, Any]) -> list[dict[str, Any]]:
    targets = item.get("expected_targets")
    if isinstance(targets, list):
        return [target for target in targets if isinstance(target, dict)]
    source = str(item.get("source") or "").strip()
    section_path = str(item.get("section_path") or "").strip()
    if source or section_path:
        return [{"source": source, "section_path": section_path, "keywords": []}]
    return []


def _is_usable_target(target: dict[str, Any]) -> bool:
    source = str(target.get("source") or "").strip()
    section_path = str(target.get("section_path") or "").strip()
    keywords = _target_keywords(target)
    return bool(source and (section_path or keywords))


def _target_matches(chunk: dict[str, Any], target: dict[str, Any]) -> bool:
    metadata = chunk["metadata"]
    if not _source_matches(metadata, target.get("source")):
        return False
    section_path = str(target.get("section_path") or "").strip()
    if section_path and not _section_matches(metadata, target):
        return False
    keywords = _target_keywords(target)
    if keywords:
        content = str(chunk.get("document") or "").casefold()
        return all(keyword.casefold() in content for keyword in keywords)
    return bool(section_path)


def _source_matches(metadata: dict[str, Any], source: Any) -> bool:
    expected = str(source or "").strip().replace("\\", "/")
    if not expected:
        return False
    candidates = [
        str(metadata.get("source") or ""),
        str(metadata.get("file_path") or ""),
        str(metadata.get("file_name") or ""),
    ]
    normalized = [candidate.strip().replace("\\", "/") for candidate in candidates]
    return any(candidate == expected or candidate.endswith(f"/{expected}") for candidate in normalized)


def _section_matches(metadata: dict[str, Any], target: dict[str, Any]) -> bool:
    expected = str(target.get("section_path") or "").strip()
    if not expected:
        return False
    candidates = [
        str(metadata.get("section_path") or "").strip(),
        str(metadata.get("section_title") or "").strip(),
    ]
    if not any(candidate == expected for candidate in candidates):
        return False
    source = str(target.get("source") or "").strip()
    return not source or _source_matches(metadata, source)


def _target_keywords(target: dict[str, Any]) -> list[str]:
    keywords = target.get("keywords") or []
    if not isinstance(keywords, list):
        return []
    return [str(keyword).strip() for keyword in keywords if str(keyword or "").strip()]


def _ratio(part: int, total: int) -> float:
    if total == 0:
        return 1.0
    return round(part / total, 4)


def _unique(values) -> list:
    seen = set()
    result = []
    for value in values:
        key = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, dict) else value
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return result
