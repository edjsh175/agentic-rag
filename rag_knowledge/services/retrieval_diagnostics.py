"""Request-local retrieval diagnostics shared by sync/async/agent paths."""
from __future__ import annotations

from contextvars import ContextVar, Token
from copy import deepcopy
from typing import Any


_current: ContextVar[dict[str, Any] | None] = ContextVar(
    "retrieval_diagnostics",
    default=None,
)


def start(scope: Any = None) -> Token:
    norm_scope = getattr(scope, "evidence_scope", scope) if scope is not None else None
    payload: dict[str, Any] = {
        "scope_id": getattr(norm_scope, "scope_id", "") if norm_scope is not None else "",
        "scope_fingerprint": getattr(norm_scope, "fingerprint", "") if norm_scope is not None else "",
        "retriever_requests": [],
        "stages": {},
    }
    return _current.set(payload)


def snapshot() -> dict[str, Any]:
    payload = _current.get()
    return deepcopy(payload) if payload is not None else {}


def finish(token: Token | None) -> dict[str, Any]:
    if token is None:
        return {}
    payload = snapshot()
    try:
        _current.reset(token)
    except ValueError:
        _current.set(None)
    return payload


def scope_filter_summary(scope: Any) -> dict[str, Any] | None:
    norm_scope = getattr(scope, "evidence_scope", scope) if scope is not None else None
    if norm_scope is None:
        return None
    if getattr(norm_scope, "grant_id", None):
        return {
            "grant_id": getattr(norm_scope, "grant_id", ""),
            "identity_scope_id": getattr(norm_scope, "identity_scope_id", ""),
            "target_entities": sorted(getattr(norm_scope, "target_entities", None) or ()),
            "grant_source_type": getattr(norm_scope, "source_type", ""),
            "materialized_chunk_count": len(getattr(norm_scope, "materialized_chunk_ids", None) or ()),
        }
    if not getattr(norm_scope, "is_identity_locked", False):
        return None
    return {
        "scope_id": getattr(norm_scope, "scope_id", ""),
        "admissible_entities": sorted(getattr(norm_scope, "admissible_entities", None) or ()),
        "materialized_chunk_count": len(getattr(norm_scope, "materialized_chunk_ids", None) or ()),
    }


def _chunk_id(doc: Any) -> str:
    if hasattr(doc, "metadata"):
        meta = getattr(doc, "metadata", {}) or {}
    elif isinstance(doc, dict):
        meta = doc.get("metadata") or {}
    else:
        meta = {}
    return str(meta.get("chunk_id") or "")


def record_request(
    *,
    channel: str,
    query: str,
    requested_k: int | None,
    docs: list[Any] | None,
    method: str = "",
    structural_filter: Any = None,
) -> None:
    payload = _current.get()
    if payload is None:
        return
    result_docs = list(docs or [])
    payload["retriever_requests"].append({
        "channel": channel,
        "method": method,
        "query": str(query or "")[:500],
        "requested_k": requested_k,
        "returned_count": len(result_docs),
        "returned_chunk_ids": [cid for cid in (_chunk_id(doc) for doc in result_docs) if cid][:40],
        "structural_filter": structural_filter,
    })


def record_stage(stage: str, docs: list[Any] | None) -> None:
    payload = _current.get()
    if payload is None:
        return
    result_docs = list(docs or [])
    admission_reasons: dict[str, int] = {}
    provenance_sources: dict[str, int] = {}
    for doc in result_docs:
        if hasattr(doc, "metadata"):
            meta = getattr(doc, "metadata", {}) or {}
        elif isinstance(doc, dict):
            meta = doc.get("metadata") or {}
        else:
            meta = {}
        reason = str(meta.get("scope_admission_reason") or "").strip()
        provenance = str(meta.get("provenance_source_type") or "").strip()
        if reason:
            admission_reasons[reason] = admission_reasons.get(reason, 0) + 1
        if provenance:
            provenance_sources[provenance] = provenance_sources.get(provenance, 0) + 1
    payload["stages"][stage] = {
        "count": len(result_docs),
        "chunk_ids": [cid for cid in (_chunk_id(doc) for doc in result_docs) if cid][:40],
        "scope_admission_reasons": admission_reasons,
        "provenance_sources": provenance_sources,
    }


def record_guard(verdict: dict[str, Any] | None) -> None:
    payload = _current.get()
    if payload is None:
        return
    verdict = verdict or {}
    payload["final_guard"] = {
        "allow_knowledge_answer": bool(verdict.get("allow_knowledge_answer")),
        "reason": verdict.get("reason"),
        "provenance_reason": verdict.get("provenance_reason"),
    }
