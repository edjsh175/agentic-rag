from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rag_knowledge.config import Config
from rag_knowledge.repository.vector_store import VectorStore


class KnowledgeBaseConsistencyError(RuntimeError):
    def __init__(self, report: dict[str, Any]):
        self.report = report
        summary = report.get("summary") or {}
        super().__init__(
            "知识库一致性校验失败: "
            f"index_chunks={summary.get('index_chunk_total', 0)}, "
            f"chroma_chunks={summary.get('chroma_chunk_total', 0)}, "
            f"missing_indexed={summary.get('missing_indexed_chunk_total', 0)}, "
            f"unexpected_chroma={summary.get('unexpected_chroma_chunk_total', 0)}"
        )


class KnowledgeBaseConsistencyService:
    def __init__(
        self,
        *,
        cfg: Config | None = None,
        index_data: dict[str, Any] | None = None,
        chunk_snapshot: dict[str, Any] | None = None,
    ):
        self._cfg = cfg
        self._index_data = index_data
        self._chunk_snapshot = chunk_snapshot

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

        indexed_set = set(indexed_chunk_ids)
        missing_indexed_chunk_ids = sorted(chunk_id for chunk_id in indexed_chunk_ids if chunk_id not in chroma_id_set)
        unexpected_chroma_chunk_ids = sorted(chunk_id for chunk_id in chroma_ids if chunk_id not in indexed_set)

        summary = {
            "consistent": not missing_indexed_chunk_ids and not unexpected_chroma_chunk_ids,
            "index_file_total": len(file_entries),
            "index_chunk_total": len(indexed_chunk_ids),
            "chroma_chunk_total": len(chroma_ids),
            "missing_indexed_chunk_total": len(missing_indexed_chunk_ids),
            "unexpected_chroma_chunk_total": len(unexpected_chroma_chunk_ids),
        }

        report = {
            "summary": summary,
            "files": [item for item in files_report if item["missing_chunk_ids"]],
            "missing_indexed_chunk_ids": missing_indexed_chunk_ids,
            "unexpected_chroma_chunk_ids": unexpected_chroma_chunk_ids,
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
