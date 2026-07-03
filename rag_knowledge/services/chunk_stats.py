"""Builds the `/stats/chunks` response from vector data and local summaries."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from rag_knowledge.config import Config
from rag_knowledge.models.api import (
    ChunkCountItem,
    ChunkHitCountItem,
    ChunkHitItem,
    ChunkStatsDistributions,
    ChunkStatsHitRates,
    ChunkStatsOfflineHitRates,
    ChunkStatsOnlineHitRates,
    ChunkStatsOverview,
    ChunkStatsResponse,
    FileChunkDistributionItem,
)
from rag_knowledge.repository.vector_store import VectorStore
from rag_knowledge.services.chunk_hit_telemetry import ChunkHitTelemetry


class ChunkStatsService:
    def __init__(
        self,
        *,
        cfg: Config | None = None,
        store: VectorStore | None = None,
        telemetry: ChunkHitTelemetry | None = None,
        file_index_path: Path | None = None,
        offline_summary_path: Path | None = None,
    ):
        self._cfg = cfg or Config()
        self._store = store or VectorStore()
        self._telemetry = telemetry or ChunkHitTelemetry()
        self._file_index_path = file_index_path or (self._cfg.data_dir / "file_index.json")
        self._offline_summary_path = offline_summary_path or (self._cfg.data_dir / "eval_summary.json")

    def build(self) -> ChunkStatsResponse:
        source = self._store.get_chunk_stats_source()
        ids = source.get("ids") or []
        documents = source.get("documents") or []
        metadatas = source.get("metadatas") or []
        file_entries = self._load_file_entries()
        file_by_chunk_id = self._build_file_lookup(file_entries)
        chunk_meta_map = self._build_chunk_meta_map(ids, metadatas, file_by_chunk_id)

        lengths = [len(doc or "") for doc in documents]
        total_chunks = len(ids)
        avg_length = round(sum(lengths) / total_chunks, 2) if total_chunks else 0.0
        chars_per_token = max(float(self._cfg.context_budget.chars_per_token or 1.0), 0.1)
        avg_tokens = round(avg_length / chars_per_token, 2) if total_chunks else 0.0

        review_counter = Counter(
            self._normalize_review_status(meta.get("review_status"))
            for meta in metadatas
        )
        file_type_counter = Counter()
        for entry in file_entries:
            file_type_counter[
                self._normalize_file_type(entry.get("category"), entry.get("file_name"), entry.get("file_path"))
            ] += len(entry.get("chunk_ids") or [])

        telemetry_payload = self._telemetry.read()
        online = self._build_online_hit_rates(telemetry_payload, chunk_meta_map)
        offline = self._load_offline_hit_rates()

        return ChunkStatsResponse(
            overview=ChunkStatsOverview(
                total_chunks=total_chunks,
                avg_chunk_tokens=avg_tokens,
                avg_chunk_length=avg_length,
                min_chunk_length=min(lengths) if lengths else 0,
                max_chunk_length=max(lengths) if lengths else 0,
            ),
            distributions=ChunkStatsDistributions(
                by_file=self._build_file_distribution(file_entries),
                by_file_type=self._sort_count_items(
                    ChunkCountItem(key=key, chunk_count=count)
                    for key, count in file_type_counter.items()
                ),
                by_review_status=self._sort_count_items(
                    ChunkCountItem(key=key, chunk_count=count)
                    for key, count in review_counter.items()
                ),
            ),
            hit_rates=ChunkStatsHitRates(
                online=online,
                offline=offline,
            ),
        )

    def _build_file_distribution(self, file_entries: list[dict]) -> list[FileChunkDistributionItem]:
        items = []
        for entry in file_entries:
            items.append(
                FileChunkDistributionItem(
                    file_path=str(entry.get("file_path", "")),
                    file_name=str(entry.get("file_name", "")),
                    kb_name=entry.get("kb_name"),
                    doc_category=entry.get("doc_category"),
                    file_type=self._normalize_file_type(
                        entry.get("category"),
                        entry.get("file_name"),
                        entry.get("file_path"),
                    ),
                    chunk_count=len(entry.get("chunk_ids") or []),
                )
            )
        return sorted(items, key=lambda item: (-item.chunk_count, item.file_path, item.file_name))

    def _build_online_hit_rates(self, payload: dict, chunk_meta_map: dict[str, dict]) -> ChunkStatsOnlineHitRates:
        total_queries = int(payload.get("total_queries", 0) or 0)
        hit_queries = int(payload.get("hit_queries", 0) or 0)
        chunk_hits = payload.get("chunk_hits") or {}

        top_chunks = []
        review_hits = Counter()
        file_type_hits = Counter()
        for chunk_id, hit_count in sorted(chunk_hits.items(), key=lambda item: (-item[1], item[0])):
            meta = chunk_meta_map.get(chunk_id, {})
            review_status = self._normalize_review_status(meta.get("review_status"))
            file_type = self._normalize_file_type(
                meta.get("file_type"),
                meta.get("file_name"),
                meta.get("file_path"),
            )
            review_hits[review_status] += int(hit_count)
            file_type_hits[file_type] += int(hit_count)
            top_chunks.append(
                ChunkHitItem(
                    chunk_id=chunk_id,
                    hit_count=int(hit_count),
                    file_name=meta.get("file_name"),
                    file_path=meta.get("file_path"),
                    review_status=review_status,
                    file_type=file_type,
                )
            )

        return ChunkStatsOnlineHitRates(
            total_queries=total_queries,
            hit_queries=hit_queries,
            query_hit_rate=round(hit_queries / total_queries, 4) if total_queries else 0.0,
            top_chunks=top_chunks[:10],
            by_review_status=self._sort_hit_items(
                ChunkHitCountItem(key=key, hit_count=count)
                for key, count in review_hits.items()
            ),
            by_file_type=self._sort_hit_items(
                ChunkHitCountItem(key=key, hit_count=count)
                for key, count in file_type_hits.items()
            ),
            last_updated_at=payload.get("last_updated_at"),
        )

    def _load_offline_hit_rates(self) -> ChunkStatsOfflineHitRates:
        if not self._offline_summary_path.exists():
            return ChunkStatsOfflineHitRates(available=False, recall_at_k={})
        try:
            payload = json.loads(self._offline_summary_path.read_text(encoding="utf-8"))
        except Exception:
            return ChunkStatsOfflineHitRates(available=False, recall_at_k={})

        recall = payload.get("recall_at_k") or {}
        return ChunkStatsOfflineHitRates(
            available=True,
            evaluated_at=payload.get("evaluated_at"),
            sample_count=int(payload.get("sample_count", payload.get("total_questions", 0)) or 0),
            hit_rate=float(payload.get("hit_rate", payload.get("overall_hit_rate", 0.0)) or 0.0),
            recall_at_k={str(key): float(value) for key, value in recall.items()},
        )

    def _load_file_entries(self) -> list[dict]:
        if not self._file_index_path.exists():
            return []
        try:
            payload = json.loads(self._file_index_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        files = payload.get("files") or {}
        if isinstance(files, dict):
            return list(files.values())
        if isinstance(files, list):
            return files
        return []

    @staticmethod
    def _build_file_lookup(file_entries: list[dict]) -> dict[str, dict]:
        lookup: dict[str, dict] = {}
        for entry in file_entries:
            for chunk_id in entry.get("chunk_ids") or []:
                lookup[str(chunk_id)] = entry
        return lookup

    def _build_chunk_meta_map(self, ids: list[str], metadatas: list[dict], file_lookup: dict[str, dict]) -> dict[str, dict]:
        result: dict[str, dict] = {}
        for chunk_id, meta in zip(ids, metadatas):
            base = dict(meta or {})
            file_entry = file_lookup.get(str(chunk_id), {})
            result[str(chunk_id)] = {
                **base,
                "file_name": file_entry.get("file_name") or base.get("source"),
                "file_path": file_entry.get("file_path"),
                "review_status": base.get("review_status"),
                "file_type": file_entry.get("category"),
            }
        return result

    @staticmethod
    def _normalize_review_status(value: str | None) -> str:
        normalized = str(value or "").strip().lower()
        return normalized or "unknown"

    @staticmethod
    def _normalize_file_type(category: str | None, file_name: str | None, file_path: str | None) -> str:
        if category:
            return str(category)
        candidate = str(file_name or file_path or "").strip()
        if "." in candidate:
            return candidate.rsplit(".", 1)[-1].lower() or "unknown"
        return "unknown"

    @staticmethod
    def _sort_count_items(items) -> list[ChunkCountItem]:
        return sorted(items, key=lambda item: (-item.chunk_count, item.key))

    @staticmethod
    def _sort_hit_items(items) -> list[ChunkHitCountItem]:
        return sorted(items, key=lambda item: (-item.hit_count, item.key))
