"""Quality control and feedback loop monitoring service."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any

from rag_knowledge.config import Config
from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.repository.vector_store import VectorStore
from rag_knowledge.services.chunk_admin import ChunkAdminService
from rag_knowledge.services.qa_trace import QaTraceStore

logger = logging.getLogger(__name__)


def compute_simhash(text: str) -> int:
    """Compute 64-bit SimHash fingerprint for given text."""
    if not text:
        return 0
    # Extract tokens: Chinese characters and words/alphanumerics
    tokens = re.findall(r"[\u4e00-\u9fa5]{1,2}|[a-zA-Z0-9]+", text)
    if not tokens:
        tokens = [text[i:i+2] for i in range(max(1, len(text)-1))]

    v = [0] * 64
    for token in tokens:
        # MD5 to 64-bit integer
        digest = hashlib.md5(token.encode("utf-8")).digest()
        h = int.from_bytes(digest[:8], byteorder="big")
        for i in range(64):
            if (h >> i) & 1:
                v[i] += 1
            else:
                v[i] -= 1

    fingerprint = 0
    for i in range(64):
        if v[i] > 0:
            fingerprint |= (1 << i)
    return fingerprint


def simhash_similarity(h1: int, h2: int) -> float:
    """Calculate similarity ratio between two 64-bit SimHash fingerprints."""
    hamming_distance = bin(h1 ^ h2).count("1")
    return round(1.0 - (hamming_distance / 64.0), 4)


class QualityService:
    """Service to compute knowledge base health metrics and process feedback loop."""

    def __init__(
        self,
        store: VectorStore | None = None,
        db: RelationalDB | None = None,
        chunk_admin: ChunkAdminService | None = None,
    ):
        self._cfg = Config()
        self._store = store or VectorStore()
        self._db = db or RelationalDB()
        self._chunk_admin = chunk_admin or ChunkAdminService(store=self._store)
        self._simhash_cache: dict[str, int] = {}

    def process_user_feedback(
        self,
        user_id: str,
        query_text: str,
        answer_text: str,
        referenced_chunk_ids: list[str],
        rating: str,
        reason: str = "",
        trace_id: str = "",
    ) -> dict[str, Any]:
        """Record user feedback and trigger automated feedback loop for negative ratings."""
        feedback_id = self._db.create_feedback(
            user_id=user_id,
            query_text=query_text,
            answer_text=answer_text,
            referenced_chunk_ids=referenced_chunk_ids,
            rating=rating,
            reason=reason,
            trace_id=trace_id,
        )

        triggered_chunks: list[dict[str, Any]] = []

        if rating == "down" and referenced_chunk_ids:
            for chunk_id in set(referenced_chunk_ids):
                down_count = self._db.count_chunk_down_ratings(chunk_id)
                if down_count >= 2:
                    review_reason = f"用户反馈差评累计{down_count}次，等待重新审核"
                    try:
                        self._chunk_admin.update_chunk(
                            chunk_id=chunk_id,
                            changes={
                                "review_status": "pending",
                                "review_reason": review_reason,
                            },
                        )
                        logger.info(
                            "Automated Feedback Loop: Chunk %s reset to pending due to %d down votes",
                            chunk_id, down_count,
                        )
                        triggered_chunks.append({
                            "chunk_id": chunk_id,
                            "down_count": down_count,
                            "reason": review_reason,
                        })
                    except Exception as e:
                        logger.error("Failed to reset chunk %s to pending: %s", chunk_id, e)

        return {
            "feedback_id": feedback_id,
            "rating": rating,
            "triggered_chunks": triggered_chunks,
        }

    def detect_duplicate_chunks(self, similarity_threshold: float = 0.95) -> list[dict[str, Any]]:
        """Detect chunk pairs with text similarity exceeding similarity_threshold."""
        source = self._store.get_chunk_stats_source()
        ids = source.get("ids") or []
        documents = source.get("documents") or []
        metadatas = source.get("metadatas") or []

        duplicates: list[dict[str, Any]] = []
        simhashes: list[tuple[str, str, int]] = []

        for cid, doc, meta in zip(ids, documents, metadatas):
            chunk_id = str(cid)
            text = str(doc or "")
            if chunk_id not in self._simhash_cache:
                self._simhash_cache[chunk_id] = compute_simhash(text)
            sh = self._simhash_cache[chunk_id]
            source_file = str((meta or {}).get("source") or (meta or {}).get("file_name") or "")
            simhashes.append((chunk_id, source_file, sh))

        n = len(simhashes)
        for i in range(n):
            id_a, file_a, sh_a = simhashes[i]
            for j in range(i + 1, n):
                id_b, file_b, sh_b = simhashes[j]
                sim = simhash_similarity(sh_a, sh_b)
                if sim >= similarity_threshold:
                    duplicates.append({
                        "chunk_id_a": id_a,
                        "chunk_id_b": id_b,
                        "source_file_a": file_a,
                        "source_file_b": file_b,
                        "similarity": sim,
                    })

        return duplicates

    def get_dashboard_data(self) -> dict[str, Any]:
        """Compute full quality metrics and active alerts for the dashboard."""
        source = self._store.get_chunk_stats_source()
        ids = source.get("ids") or []
        metadatas = source.get("metadatas") or []

        total_chunks = len(ids)
        approved_count = 0
        pending_count = 0

        chunk_id_set = set(str(c) for c in ids)
        chunk_file_map: dict[str, str] = {}

        for cid, meta in zip(ids, metadatas):
            chunk_id = str(cid)
            m = meta or {}
            status = str(m.get("review_status") or "pending")
            file_name = str(m.get("source") or m.get("file_name") or "")
            chunk_file_map[chunk_id] = file_name

            if status == "approved":
                approved_count += 1
            elif status == "pending":
                pending_count += 1

        approved_ratio = round(approved_count / total_chunks, 3) if total_chunks > 0 else 1.0

        # M4: Isolated entities count
        all_entities = self._db.list_entities()
        linked_links = self._db.list_links()
        linked_entity_ids = set(str(l["entity_id"]) for l in linked_links)
        linked_chunk_ids = set(str(l["chunk_id"]) for l in linked_links)

        isolated_entities_count = sum(1 for e in all_entities if str(e["id"]) not in linked_entity_ids)

        # M5: Isolated chunks count
        isolated_chunks_count = sum(1 for cid in chunk_id_set if cid not in linked_chunk_ids)

        # M6: Chunk duplicate ratio
        duplicates = self.detect_duplicate_chunks(similarity_threshold=0.95)
        duplicate_ratio = round(len(duplicates) / total_chunks, 3) if total_chunks > 0 else 0.0

        # M7: 7-day no-result ratio
        no_result_ratio_7d = self._compute_7d_no_result_ratio()

        # M8: 7-day user satisfaction ratio
        feedback_stats = self._db.get_7d_feedback_stats()
        total_fb = feedback_stats.get("total", 0)
        up_fb = feedback_stats.get("up", 0)
        satisfaction_ratio_7d = round(up_fb / total_fb, 3) if total_fb > 0 else 1.0

        # Build alerts list
        alerts: list[dict[str, Any]] = []

        # Negative feedback alerts (chunks with >= 2 down votes)
        down_feedbacks = self._db.list_feedbacks(rating="down", limit=500)
        chunk_down_counts: dict[str, int] = {}
        for fb in down_feedbacks:
            for cid in fb.get("referenced_chunk_ids") or []:
                chunk_down_counts[cid] = chunk_down_counts.get(cid, 0) + 1

        for cid, d_count in chunk_down_counts.items():
            if d_count >= 2:
                alerts.append({
                    "type": "negative_feedback",
                    "chunk_id": cid,
                    "source_file": chunk_file_map.get(cid) or "未知文件",
                    "down_count": d_count,
                    "reason": f"用户反馈差评累计{d_count}次",
                })

        # High duplicate alerts
        for dup in duplicates[:10]:
            alerts.append({
                "type": "duplicate",
                "chunk_id": dup["chunk_id_a"],
                "source_file": dup["source_file_a"],
                "down_count": 0,
                "reason": f"与 {dup['chunk_id_b']} 文本相似度达到 {int(dup['similarity'] * 100)}%",
            })

        return {
            "metrics": {
                "total_chunks": total_chunks,
                "approved_ratio": approved_ratio,
                "pending_chunks": pending_count,
                "isolated_entities": isolated_entities_count,
                "isolated_chunks": isolated_chunks_count,
                "duplicate_ratio": duplicate_ratio,
                "no_result_ratio_7d": no_result_ratio_7d,
                "satisfaction_ratio_7d": satisfaction_ratio_7d,
            },
            "alerts": alerts,
        }

    def _compute_7d_no_result_ratio(self) -> float:
        """Compute ratio of QA turns with 0 retrieved candidates over the last 7 days."""
        try:
            traces_info = QaTraceStore(self._cfg).list(limit=500)
            items = traces_info.get("items") or []

            cutoff = datetime.now() - timedelta(days=7)
            recent_items = []
            for item in items:
                created_str = str(item.get("created_at") or "")
                if not created_str:
                    continue
                try:
                    dt = datetime.fromisoformat(created_str[:19])
                    if dt >= cutoff:
                        recent_items.append(item)
                except ValueError:
                    continue

            if not recent_items:
                return 0.0

            no_res_count = sum(1 for item in recent_items if item.get("candidate_count", 0) == 0)
            return round(no_res_count / len(recent_items), 3)
        except Exception as e:
            logger.warning("Failed to compute 7-day no-result ratio: %s", e)
            return 0.0
