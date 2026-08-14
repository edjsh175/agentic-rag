"""Graph Resync Service — Sync graph evidence and chunk links after vector store rebuild."""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.repository.vector_store import VectorStore

logger = logging.getLogger(__name__)


def _text_md5(text: str) -> str:
    return hashlib.md5((text or "").encode("utf-8")).hexdigest()


def _jaccard_similarity(text1: str, text2: str) -> float:
    t1 = set((text1 or "").strip())
    t2 = set((text2 or "").strip())
    if not t1 or not t2:
        return 0.0
    intersection = len(t1 & t2)
    union = len(t1 | t2)
    return intersection / union if union > 0 else 0.0


def _chunk_source(meta: dict[str, Any] | None, fallback: str = "") -> str:
    meta = meta or {}
    for key in ("source_file", "source", "file_path"):
        value = str(meta.get(key) or "").strip()
        if value:
            return value
    return str(fallback or "").strip()


def _chunk_section(meta: dict[str, Any] | None) -> str:
    meta = meta or {}
    for key in ("section_title", "section_path"):
        value = str(meta.get(key) or "").strip()
        if value:
            return value
    return ""


class GraphResyncService:
    """Resynchronize graph entity-chunk links and relation evidence chunk IDs after vector store rebuild."""

    def __init__(self, db: RelationalDB | None = None, store: VectorStore | None = None):
        self.db = db or RelationalDB()
        self.store = store or VectorStore()

    def resync(
        self,
        index_backup_path: Path | str | None = None,
        backup_collection: str | None = None,
        backup_store: Any | None = None,
    ) -> dict[str, Any]:
        """Perform chunk ID remapping in relational database for graph elements."""
        logger.info("Starting graph chunk ID resynchronization...")

        # 1. Fetch all current live chunks from VectorStore
        live_snapshot = self.store.get_chunk_stats_source()
        live_ids = set(live_snapshot.get("ids") or [])
        live_docs = live_snapshot.get("documents") or []
        live_metas = live_snapshot.get("metadatas") or []

        # Index live chunks by (source_file, section_title) -> list of (chunk_id, doc, md5)
        live_index_map: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
        for cid, doc, meta in zip(live_ids, live_docs, live_metas):
            src_file = _chunk_source(meta)
            sec_title = _chunk_section(meta)
            key = (src_file, sec_title)
            live_index_map.setdefault(key, []).append(
                (str(cid), doc or "", _text_md5(doc or ""))
            )

        # 2. Collect unique old chunk IDs referenced in relational DB
        old_chunk_ids = set()
        with self.db._get_conn() as conn:
            for row in conn.execute(
                "SELECT DISTINCT chunk_id FROM entity_chunk_links WHERE chunk_id != ''"
            ).fetchall():
                old_chunk_ids.add(str(row[0]))
            for row in conn.execute(
                "SELECT DISTINCT source_chunk_id FROM relations WHERE source_chunk_id != ''"
            ).fetchall():
                old_chunk_ids.add(str(row[0]))
            for row in conn.execute(
                "SELECT DISTINCT source_chunk_id FROM extraction_candidates WHERE source_chunk_id != ''"
            ).fetchall():
                old_chunk_ids.add(str(row[0]))

        if not old_chunk_ids:
            logger.info("No chunk references found in relational DB to resync.")
            return {
                "total_checked": 0,
                "remapped_exact": 0,
                "remapped_similar": 0,
                "orphaned": 0,
            }

        # 3. Load old chunk meta: prefer backup index `chunks[]`, else chunk_ids + backup collection
        resolved_backup = backup_store
        if resolved_backup is None and backup_collection:
            resolved_backup = self.store.fork(str(backup_collection))
        old_chunks_meta = self._load_old_chunks_meta(
            index_backup_path=index_backup_path,
            backup_store=resolved_backup,
        )

        # 4. Map old_chunk_id -> new_chunk_id
        id_mapping: dict[str, str] = {}
        exact_count = 0
        similar_count = 0
        orphaned_count = 0

        for old_cid in old_chunk_ids:
            # If the chunk ID still exists in live VectorStore, no remapping needed
            if old_cid in live_ids:
                continue

            old_info = old_chunks_meta.get(old_cid)
            if not old_info:
                orphaned_count += 1
                continue

            src_file = old_info["source_file"]
            sec_title = old_info["section_title"]
            old_text = old_info["text"]
            old_md5 = _text_md5(old_text)

            key = (src_file, sec_title)
            candidates = live_index_map.get(key) or []
            matched_id = None

            # L1: Exact match by (source_file, section_title, text_md5)
            for new_cid, new_doc, new_md5 in candidates:
                if new_md5 == old_md5:
                    matched_id = new_cid
                    exact_count += 1
                    break

            # L2: Jaccard similarity match > 0.8
            if not matched_id and old_text:
                best_sim = 0.0
                best_id = None
                for new_cid, new_doc, _ in candidates:
                    sim = _jaccard_similarity(old_text, new_doc)
                    if sim > best_sim and sim >= 0.8:
                        best_sim = sim
                        best_id = new_cid
                if best_id:
                    matched_id = best_id
                    similar_count += 1

            if matched_id:
                id_mapping[old_cid] = matched_id
            else:
                orphaned_count += 1

        # 5. Apply remapping in SQLite relational DB
        if id_mapping:
            with self.db._get_conn() as conn:
                for old_cid, new_cid in id_mapping.items():
                    conn.execute(
                        "UPDATE entity_chunk_links SET chunk_id = ? WHERE chunk_id = ?",
                        (new_cid, old_cid),
                    )
                    conn.execute(
                        "UPDATE relations SET source_chunk_id = ? WHERE source_chunk_id = ?",
                        (new_cid, old_cid),
                    )
                    conn.execute(
                        "UPDATE extraction_candidates SET source_chunk_id = ? WHERE source_chunk_id = ?",
                        (new_cid, old_cid),
                    )

        logger.info(
            "Graph resync completed: %d remapped (%d exact, %d similar), %d orphaned out of %d total.",
            len(id_mapping),
            exact_count,
            similar_count,
            orphaned_count,
            len(old_chunk_ids),
        )
        return {
            "total_checked": len(old_chunk_ids),
            "remapped_exact": exact_count,
            "remapped_similar": similar_count,
            "orphaned": orphaned_count,
            "remapped_total": len(id_mapping),
        }

    def _load_old_chunks_meta(
        self,
        *,
        index_backup_path: Path | str | None,
        backup_store: Any | None,
    ) -> dict[str, dict[str, str]]:
        old_chunks_meta: dict[str, dict[str, str]] = {}
        chunk_ids_by_file: list[tuple[str, str]] = []

        if index_backup_path:
            bp = Path(index_backup_path)
            if bp.exists():
                try:
                    raw_data = json.loads(bp.read_text(encoding="utf-8"))
                    files = raw_data.get("files") or {}
                    for fpath, finfo in files.items():
                        if not isinstance(finfo, dict):
                            continue
                        file_path = str(finfo.get("file_path") or fpath or "")
                        for c in finfo.get("chunks") or []:
                            if not isinstance(c, dict):
                                continue
                            cid = str(c.get("chunk_id") or "")
                            if not cid:
                                continue
                            old_chunks_meta[cid] = {
                                "source_file": _chunk_source(c, file_path),
                                "section_title": _chunk_section(c),
                                "text": str(c.get("text") or ""),
                            }
                        for cid in finfo.get("chunk_ids") or []:
                            chunk_ids_by_file.append((str(cid), file_path))
                except Exception as exc:
                    logger.warning("Failed to load old index backup %s: %s", bp, exc)

        # Scanner/rebuild backups only store chunk_ids; pull text from backup collection.
        missing_ids = [
            cid
            for cid, _ in chunk_ids_by_file
            if cid and cid not in old_chunks_meta
        ]
        if missing_ids and backup_store is not None:
            try:
                snapshot = backup_store.get_chunk_stats_source()
            except Exception as exc:
                logger.warning("Failed to read backup collection snapshot: %s", exc)
                snapshot = {}
            docs = snapshot.get("documents") or []
            metas = snapshot.get("metadatas") or []
            ids = snapshot.get("ids") or []
            by_id: dict[str, tuple[str, dict[str, Any]]] = {}
            for cid, doc, meta in zip(ids, docs, metas):
                by_id[str(cid)] = (doc or "", dict(meta or {}))

            file_fallback = {cid: fpath for cid, fpath in chunk_ids_by_file}
            for cid in missing_ids:
                row = by_id.get(cid)
                if not row:
                    continue
                doc, meta = row
                old_chunks_meta[cid] = {
                    "source_file": _chunk_source(meta, file_fallback.get(cid, "")),
                    "section_title": _chunk_section(meta),
                    "text": doc,
                }

        return old_chunks_meta
