"""QA pipeline trace recorder — persist full-turn RAG diagnostics for the admin monitor."""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from rag_knowledge.config import Config

logger = logging.getLogger(__name__)

_request_id_var: ContextVar[str | None] = ContextVar("qa_trace_request_id", default=None)
_path_var: ContextVar[str] = ContextVar("qa_trace_path", default="query")


def set_request_context(*, request_id: str | None = None, path: str | None = None) -> None:
    if request_id is not None:
        _request_id_var.set(request_id)
    if path is not None:
        _path_var.set(path)


def get_request_id() -> str | None:
    return _request_id_var.get()


def get_trace_path() -> str:
    return _path_var.get() or "query"


def serialize_queries(queries: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in queries or ():
        if hasattr(item, "text"):
            out.append({
                "text": getattr(item, "text", "") or "",
                "kind": getattr(item, "kind", "") or "",
                "weight": float(getattr(item, "weight", 1.0) or 1.0),
            })
        else:
            out.append({"text": str(item), "kind": "unknown", "weight": 1.0})
    return out


def serialize_linked_entities(linked: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in linked or ():
        if hasattr(item, "canonical_name"):
            out.append({
                "entity_id": getattr(item, "entity_id", "") or "",
                "canonical_name": getattr(item, "canonical_name", "") or "",
                "entity_type": getattr(item, "entity_type", "") or "",
                "confidence": float(getattr(item, "confidence", 0.0) or 0.0),
                "match_method": getattr(item, "match_method", "") or "",
            })
        elif isinstance(item, dict):
            out.append(item)
    return out


def serialize_plan(plan: Any) -> dict[str, Any]:
    if plan is None:
        return {}
    queries = serialize_queries(getattr(plan, "queries", ()) or ())
    linked_entities = serialize_linked_entities(getattr(plan, "linked_entities", ()) or ())
    return {
        "intent": getattr(plan, "intent", "") or "",
        "confidence": float(getattr(plan, "confidence", 0.0) or 0.0),
        "queries": queries,
        "top_k": int(getattr(plan, "top_k", 0) or 0),
        "candidate_k": int(getattr(plan, "candidate_k", 0) or 0),
        "enable_rerank": bool(getattr(plan, "enable_rerank", False)),
        "expand_neighbors": bool(getattr(plan, "expand_neighbors", False)),
        "backbone_canonical": list(getattr(plan, "backbone_canonical", ()) or ()),
        "backbone_avoid": list(getattr(plan, "backbone_avoid", ()) or ()),
        "backbone_primary_intent": getattr(plan, "backbone_primary_intent", "") or "",
        "backbone_relation_summary": (getattr(plan, "backbone_relation_summary", "") or "")[:1000],
        "linked_entities": linked_entities,
        "graph_queries": list(getattr(plan, "graph_queries", ()) or ()),
        "graph_chunk_ids": list(getattr(plan, "graph_chunk_ids", ()) or ())[:40],
        "graph_fallback_reason": getattr(plan, "graph_fallback_reason", None),
    }


def serialize_candidates(
    docs: list[dict[str, Any]] | None,
    *,
    max_candidates: int,
    preview_chars: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for doc in (docs or [])[:max_candidates]:
        meta = doc.get("metadata") or {}
        content = str(doc.get("content") or doc.get("page_content") or "")
        matched_kinds = list(meta.get("matched_query_kinds") or doc.get("matched_query_kinds") or [])

        if "graph" in matched_kinds and "retrieval" not in matched_kinds:
            source_type = "graph_only"
        elif "graph" in matched_kinds and "retrieval" in matched_kinds:
            source_type = "hybrid_hit"
        else:
            source_type = "text_only"

        out.append({
            "chunk_id": meta.get("chunk_id") or "",
            "source": meta.get("source") or meta.get("file_name") or "",
            "section_title": meta.get("section_title") or meta.get("section_path") or "",
            "kb_name": meta.get("kb_name") or "",
            "score": meta.get("score") or meta.get("rerank_score") or doc.get("score"),
            "citation_id": meta.get("citation_id"),
            "matched_query_kinds": matched_kinds,
            "retrieval_source": source_type,
            "content_preview": content[:preview_chars],
        })
    return out


def runtime_fingerprint(cfg: Config | None = None) -> dict[str, Any]:
    if cfg is None:
        return {}
    return {
        "retrieval_method": cfg.retrieval_strategy,
        "reranker_enabled": bool(cfg.reranker_enabled),
        "graph_retrieval_enabled": bool(cfg.graph_retrieval.enabled),
        "query_rewrite_enabled": bool(cfg.graph_retrieval.query_rewrite_enabled),
        "retrieval_quality_enabled": bool(cfg.retrieval_quality.enabled),
        "llm_model": cfg.llm_model,
        "helper_llm_model": cfg.helper_llm_model,
    }


class QaTraceBuilder:
    """In-memory assembler for one QA turn."""

    def __init__(
        self,
        *,
        question: str,
        path: str | None = None,
        request_id: str | None = None,
        collection_name: str | None = None,
        kb_name: str | None = None,
        doc_category: str | None = None,
        entity_name: str | None = None,
        llm_model: str | None = None,
        vision_model: str | None = None,
        thinking: bool | None = None,
        web_search: bool | None = None,
        allow_general_knowledge: bool | None = None,
        agent_prompt: str | None = None,
        pinned_chunk_ids: list[str] | None = None,
        excluded_chunk_ids: list[str] | None = None,
        history_rounds: int = 0,
        cfg: Config | None = None,
        clarification_question: str | None = None,
        clarification_selected: str | None = None,
    ):
        # cfg=None means "do not fall back to live Config()" — used by RagChain test
        # stubs that never set self._cfg. Production RagChain always passes Config.
        self._cfg = cfg
        self._enabled = bool(cfg is not None and getattr(cfg, "qa_trace", None) and cfg.qa_trace.enabled)
        self.trace_id = uuid.uuid4().hex
        self._t0 = time.perf_counter()
        self._stage_marks: dict[str, float] = {"start": self._t0}
        self._stages_ms: dict[str, float] = {}
        self._plan: dict[str, Any] = {}
        self._retrieval: dict[str, Any] = {"query_hits": [], "candidates": []}
        self._request = {
            "question": question or "",
            "collection_name": collection_name,
            "kb_name": kb_name,
            "doc_category": doc_category,
            "entity_name": entity_name,
            "llm_model": llm_model,
            "vision_model": vision_model,
            "thinking": thinking,
            "web_search": web_search,
            "allow_general_knowledge": allow_general_knowledge,
            "agent_prompt": agent_prompt,
            "pinned_chunk_ids": list(pinned_chunk_ids or []),
            "excluded_chunk_ids": list(excluded_chunk_ids or []),
            "history_rounds": int(history_rounds or 0),
            "clarification_question": clarification_question,
            "clarification_selected": clarification_selected,
        }
        self._meta_path = path or get_trace_path()
        self._request_id = request_id or get_request_id()


    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def stages_ms(self) -> dict[str, float]:
        return dict(self._stages_ms)

    @property
    def plan_payload(self) -> dict[str, Any]:
        return dict(self._plan)

    @property
    def retrieval_payload(self) -> dict[str, Any]:
        return dict(self._retrieval)

    def mark(self, stage: str) -> None:
        if not self._enabled:
            return
        now = time.perf_counter()
        prev_name = next(reversed(self._stage_marks))
        prev_t = self._stage_marks[prev_name]
        self._stages_ms[stage] = round((now - prev_t) * 1000, 1)
        self._stage_marks[stage] = now

    def set_plan(self, plan: Any) -> None:
        if not self._enabled:
            return
        self._plan = serialize_plan(plan)

    def set_retrieval(
        self,
        docs: list[dict[str, Any]] | None,
        *,
        query_hits: list[dict[str, Any]] | None = None,
    ) -> None:
        if not self._enabled:
            return
        qt = self._cfg.qa_trace
        self._retrieval = {
            "query_hits": query_hits or [],
            "candidates": serialize_candidates(
                docs,
                max_candidates=qt.max_candidates,
                preview_chars=qt.max_content_preview,
            ),
            "candidate_count": len(docs or []),
        }

    def finish(
        self,
        *,
        answer: str = "",
        source_documents: list[dict[str, Any]] | None = None,
        evidence: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> str | None:
        if not self._enabled:
            return None
        elapsed_ms = round((time.perf_counter() - self._t0) * 1000, 1)
        now = datetime.now(timezone.utc).astimezone()
        payload = {
            "meta": {
                "trace_id": self.trace_id,
                "request_id": self._request_id,
                "created_at": now.isoformat(timespec="seconds"),
                "path": self._meta_path,
                "elapsed_ms": elapsed_ms,
                "error": error,
            },
            "request": self._request,
            "runtime": runtime_fingerprint(self._cfg),
            "stages": self._stages_ms,
            "plan": self._plan,
            "retrieval": self._retrieval,
            "evidence": evidence or {},
            "answer": {
                "text": answer or "",
                "source_documents": source_documents or [],
            },
        }
        try:
            QaTraceStore(self._cfg).save(payload)
        except Exception as exc:  # noqa: BLE001 — tracing must not break QA
            logger.warning("qa_trace save failed: %s", exc)
            return None
        return self.trace_id


class QaTraceStore:
    """JSON + jsonl index persistence under data/qa_traces/."""

    def __init__(self, cfg: Config | None = None):
        if cfg is None:
            raise ValueError("QaTraceStore requires an explicit Config; refusing live Config() fallback")
        self._cfg = cfg
        self._root = Path(self._cfg.data_dir) / "qa_traces"
        self._root.mkdir(parents=True, exist_ok=True)
        self._index_path = self._root / "index.jsonl"
        self._lock = threading.Lock()

    def save(self, payload: dict[str, Any]) -> Path:
        meta = payload.get("meta") or {}
        trace_id = str(meta.get("trace_id") or "")
        if not trace_id:
            raise ValueError("trace_id required")
        created = str(meta.get("created_at") or "")
        day = created[:10].replace("-", "") if len(created) >= 10 else datetime.now().strftime("%Y%m%d")
        day_dir = self._root / day
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / f"{trace_id}.json"
        summary = {
            "trace_id": trace_id,
            "request_id": meta.get("request_id"),
            "created_at": meta.get("created_at"),
            "path": meta.get("path"),
            "elapsed_ms": meta.get("elapsed_ms"),
            "error": meta.get("error"),
            "question": (payload.get("request") or {}).get("question", "")[:200],
            "answer_preview": ((payload.get("answer") or {}).get("text") or "")[:160],
            "candidate_count": (payload.get("retrieval") or {}).get("candidate_count", 0),
            "cited_count": len(((payload.get("evidence") or {}).get("cited") or [])),
            "runtime": payload.get("runtime") or {},
            "feedback": payload.get("feedback"),
            "file": str(path.relative_to(self._root)).replace("\\", "/"),
        }
        with self._lock:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            with self._index_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
            self._prune_locked()
        return path

    def update_feedback(self, trace_id: str, feedback: str | None) -> bool:
        tid = (trace_id or "").strip()
        if not tid:
            return False
        with self._lock:
            updated = False
            for path in list(self._root.glob(f"*/{tid}.json")):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    data["feedback"] = feedback
                    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                    updated = True
                except Exception as exc:  # noqa: BLE001
                    logger.warning("qa_trace update_feedback file failed: %s", exc)
            if self._index_path.exists():
                rows = self._iter_index()
                for r in rows:
                    if r.get("trace_id") == tid:
                        r["feedback"] = feedback
                self._rewrite_index(rows)
        return updated

    def get(self, trace_id: str) -> dict[str, Any] | None:
        tid = (trace_id or "").strip()
        if not tid:
            return None
        for path in self._root.glob(f"*/{tid}.json"):
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                logger.warning("qa_trace read failed %s: %s", path, exc)
                return None
        # fallback: look up index file field
        for row in self._iter_index():
            if row.get("trace_id") == tid and row.get("file"):
                full = self._root / str(row["file"])
                if full.exists():
                    try:
                        return json.loads(full.read_text(encoding="utf-8"))
                    except Exception:
                        return None
        return None

    def list(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        q: str | None = None,
        errors_only: bool = False,
    ) -> dict[str, Any]:
        rows = list(self._iter_index())
        rows.reverse()  # newest last in file → newest first
        needle = (q or "").strip().lower()
        if needle:
            rows = [
                r for r in rows
                if needle in str(r.get("question", "")).lower()
                or needle in str(r.get("answer_preview", "")).lower()
                or needle in str(r.get("trace_id", "")).lower()
            ]
        if errors_only:
            rows = [r for r in rows if r.get("error")]
        total = len(rows)
        page = rows[offset: offset + max(1, limit)]
        return {"total": total, "items": page, "limit": limit, "offset": offset}

    def delete(self, trace_id: str) -> bool:
        tid = (trace_id or "").strip()
        if not tid:
            return False
        removed = False
        with self._lock:
            for path in list(self._root.glob(f"*/{tid}.json")):
                try:
                    path.unlink(missing_ok=True)
                    removed = True
                except Exception as exc:  # noqa: BLE001
                    logger.warning("qa_trace delete file failed: %s", exc)
            if self._index_path.exists():
                kept = [r for r in self._iter_index() if r.get("trace_id") != tid]
                self._rewrite_index(kept)
        return removed

    def _iter_index(self) -> list[dict[str, Any]]:
        if not self._index_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        try:
            for line in self._index_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except Exception as exc:  # noqa: BLE001
            logger.warning("qa_trace index read failed: %s", exc)
        return rows

    def _rewrite_index(self, rows: list[dict[str, Any]]) -> None:
        tmp = self._index_path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        tmp.replace(self._index_path)

    def _prune_locked(self) -> None:
        qt = self._cfg.qa_trace
        rows = self._iter_index()
        if not rows:
            return
        cutoff = None
        if qt.retain_days > 0:
            cutoff = datetime.now().astimezone() - timedelta(days=qt.retain_days)
        keep: list[dict[str, Any]] = []
        dropped_ids: set[str] = set()
        for row in rows:
            drop = False
            if cutoff is not None:
                created = str(row.get("created_at") or "")
                try:
                    ts = datetime.fromisoformat(created)
                    if ts.tzinfo is None:
                        ts = ts.astimezone()
                    if ts < cutoff:
                        drop = True
                except ValueError:
                    pass
            if drop:
                dropped_ids.add(str(row.get("trace_id") or ""))
            else:
                keep.append(row)
        if qt.max_traces > 0 and len(keep) > qt.max_traces:
            overflow = keep[: len(keep) - qt.max_traces]
            keep = keep[len(keep) - qt.max_traces:]
            for row in overflow:
                dropped_ids.add(str(row.get("trace_id") or ""))
        if not dropped_ids:
            return
        for tid in dropped_ids:
            if not tid:
                continue
            for path in self._root.glob(f"*/{tid}.json"):
                path.unlink(missing_ok=True)
        self._rewrite_index(keep)
