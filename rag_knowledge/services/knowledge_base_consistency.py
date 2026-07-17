from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from rag_knowledge.config import Config
from rag_knowledge.repository.vector_store import VectorStore

_DOCUMENT_PROFILES = {
    "section_based",
    "technical_manual",
    "procedure",
    "api_doc",
    "table_doc",
    "record_list",
}


def _profile_path(value: str) -> str:
    return str(value or "").replace("\\", "/")


def _document_key(metadata: dict[str, Any]) -> str:
    for key in ("source_snapshot_hash", "source_document_id", "source"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value
    return ""


class KnowledgeBaseConsistencyError(RuntimeError):
    def __init__(self, report: dict[str, Any]):
        self.report = report
        summary = report.get("summary") or {}
        super().__init__(
            "知识库一致性校验失败: "
            f"index_chunks={summary.get('index_chunk_total', 0)}, "
            f"chroma_chunks={summary.get('chroma_chunk_total', 0)}, "
            f"missing_indexed={summary.get('missing_indexed_chunk_total', 0)}, "
            f"unexpected_chroma={summary.get('unexpected_chroma_chunk_total', 0)}, "
            f"identity_errors={summary.get('identity_error_total', 0)}, "
            f"profile_errors={summary.get('profile_error_total', 0)}, "
            f"adjacency_errors={summary.get('adjacency_error_total', 0)}"
        )


class KnowledgeBaseConsistencyService:
    def __init__(
        self,
        *,
        cfg: Config | None = None,
        index_data: dict[str, Any] | None = None,
        chunk_snapshot: dict[str, Any] | None = None,
        profile_map: dict[str, Any] | None = None,
    ):
        self._cfg = cfg
        self._index_data = index_data
        self._chunk_snapshot = chunk_snapshot
        self._profile_map = profile_map

    def audit(self, *, source: str | None = None) -> dict[str, Any]:
        index_data = self._index_data if self._index_data is not None else self._load_index()
        chunk_snapshot = self._chunk_snapshot if self._chunk_snapshot is not None else VectorStore().get_chunk_stats_source()

        file_entries = list((index_data or {}).get("files", {}).values())
        indexed_chunk_ids: list[str] = []
        files_report: list[dict[str, Any]] = []

        chroma_ids = [str(item) for item in (chunk_snapshot.get("ids") or [])]
        chroma_documents = chunk_snapshot.get("documents") or []
        chroma_metadatas = chunk_snapshot.get("metadatas") or []
        chroma_id_set = set(chroma_ids)
        duplicate_chroma_ids = sorted(
            chunk_id for chunk_id, count in Counter(chroma_ids).items() if count > 1
        )
        metadata_by_id = {
            chunk_id: dict(chroma_metadatas[index] or {})
            for index, chunk_id in enumerate(chroma_ids)
            if index < len(chroma_metadatas)
        }
        identity_errors: list[dict[str, Any]] = []
        profile_errors: list[dict[str, Any]] = []
        adjacency_errors: list[dict[str, Any]] = []

        for chunk_id in duplicate_chroma_ids:
            identity_errors.append({"chunk_id": chunk_id, "reason": "duplicate_chroma_id"})
        for chunk_id in chroma_ids:
            metadata = metadata_by_id.get(chunk_id, {})
            if str(metadata.get("chunk_id") or "") != chunk_id:
                identity_errors.append({"chunk_id": chunk_id, "reason": "chunk_id_mismatch"})
            if str(metadata.get("chunk_uid") or "") != chunk_id:
                identity_errors.append({"chunk_id": chunk_id, "reason": "chunk_uid_mismatch"})

        normalized_profile_map = {
            _profile_path(path): (
                value.get("document_profile", "") if isinstance(value, dict) else value
            )
            for path, value in self._get_profile_map().items()
        }

        source_rows: list[dict[str, Any]] = []
        for chunk_id, content, metadata in zip(chroma_ids, chroma_documents, chroma_metadatas):
            meta = dict(metadata or {})
            source_rows.append(
                {
                    "chunk_id": chunk_id,
                    "content": content or "",
                    "source": str(meta.get("source") or ""),
                    "section_path": str(meta.get("section_path") or ""),
                }
            )

        for entry in file_entries:
            chunk_ids = [str(item) for item in (entry.get("chunk_ids") or [])]
            indexed_chunk_ids.extend(chunk_ids)
            found = [chunk_id for chunk_id in chunk_ids if chunk_id in chroma_id_set]
            missing = [chunk_id for chunk_id in chunk_ids if chunk_id not in chroma_id_set]
            files_report.append(
                {
                    "file_name": entry.get("file_name") or "",
                    "file_path": entry.get("file_path") or "",
                    "indexed_chunk_total": len(chunk_ids),
                    "found_chunk_total": len(found),
                    "missing_chunk_ids": missing,
                }
            )
            file_path = _profile_path(entry.get("file_path") or "")
            profile = str(entry.get("document_profile") or "")
            profile_source = str(entry.get("document_profile_source") or "")
            policy_id = str(entry.get("chunk_policy_id") or "")
            if profile not in _DOCUMENT_PROFILES:
                profile_errors.append({"file_path": file_path, "reason": "invalid_index_profile"})
            if not policy_id:
                profile_errors.append({"file_path": file_path, "reason": "missing_index_policy"})
            expected_profile = str(normalized_profile_map.get(file_path) or "")
            if profile_source == "profile_map" and expected_profile != profile:
                profile_errors.append({"file_path": file_path, "reason": "profile_map_mismatch"})
            for chunk_id in found:
                metadata = metadata_by_id.get(chunk_id, {})
                if str(metadata.get("document_profile") or "") != profile:
                    profile_errors.append({"file_path": file_path, "chunk_id": chunk_id, "reason": "chunk_profile_mismatch"})
                if str(metadata.get("chunk_policy_id") or "") != policy_id:
                    profile_errors.append({"file_path": file_path, "chunk_id": chunk_id, "reason": "chunk_policy_mismatch"})

        for chunk_id, metadata in metadata_by_id.items():
            for direction, reciprocal in (("prev_chunk_id", "next_chunk_id"), ("next_chunk_id", "prev_chunk_id")):
                target_id = str(metadata.get(direction) or "").strip()
                if not target_id:
                    continue
                target = metadata_by_id.get(target_id)
                if target is None:
                    adjacency_errors.append({"chunk_id": chunk_id, "target_id": target_id, "reason": "missing_target"})
                    continue
                if _document_key(metadata) != _document_key(target):
                    adjacency_errors.append({"chunk_id": chunk_id, "target_id": target_id, "reason": "cross_document"})
                if str(target.get(reciprocal) or "").strip() != chunk_id:
                    adjacency_errors.append({"chunk_id": chunk_id, "target_id": target_id, "reason": "not_reciprocal"})

        indexed_set = set(indexed_chunk_ids)
        missing_indexed_chunk_ids = sorted(chunk_id for chunk_id in indexed_chunk_ids if chunk_id not in chroma_id_set)
        unexpected_chroma_chunk_ids = sorted(chunk_id for chunk_id in chroma_ids if chunk_id not in indexed_set)

        summary = {
            "consistent": not any((missing_indexed_chunk_ids, unexpected_chroma_chunk_ids, identity_errors, profile_errors, adjacency_errors)),
            "index_file_total": len(file_entries),
            "index_chunk_total": len(indexed_chunk_ids),
            "chroma_chunk_total": len(chroma_ids),
            "missing_indexed_chunk_total": len(missing_indexed_chunk_ids),
            "unexpected_chroma_chunk_total": len(unexpected_chroma_chunk_ids),
            "identity_error_total": len(identity_errors),
            "profile_error_total": len(profile_errors),
            "adjacency_error_total": len(adjacency_errors),
        }

        report = {
            "summary": summary,
            "files": [item for item in files_report if item["missing_chunk_ids"]],
            "missing_indexed_chunk_ids": missing_indexed_chunk_ids,
            "unexpected_chroma_chunk_ids": unexpected_chroma_chunk_ids,
            "identity_errors": identity_errors,
            "profile_errors": profile_errors,
            "adjacency_errors": adjacency_errors,
        }
        if source is not None:
            filtered = [row for row in source_rows if row["source"] == source]
            report["source"] = {
                "source": source,
                "chunk_total": len(filtered),
                "section_paths": sorted({row["section_path"] for row in filtered if row["section_path"]}),
            }
        return report

    def assert_consistent(self, *, source: str | None = None) -> dict[str, Any]:
        report = self.audit(source=source)
        if not report["summary"]["consistent"]:
            raise KnowledgeBaseConsistencyError(report)
        return report

    def _load_index(self) -> dict[str, Any]:
        cfg = self._cfg or Config()
        path = Path(cfg.data_dir) / "file_index.json"
        if not path.exists():
            return {"files": {}}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {"files": {}}

    def _get_profile_map(self) -> dict[str, Any]:
        if self._profile_map is not None:
            return self._profile_map
        cfg = self._cfg or Config()
        path = Path(cfg.data_dir) / "document_profile_map.json"
        if not path.exists():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}
