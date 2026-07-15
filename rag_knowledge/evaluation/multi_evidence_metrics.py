"""FR-10 multi-evidence answer scoring (rule-based, offline)."""

from __future__ import annotations

import re
from typing import Any

NO_KNOWLEDGE_SNIPPETS = (
    "当前知识库中未查询到相关内容",
    "未查询到相关内容",
)
CONFLICT_HINT_PATTERNS = (
    re.compile(r"冲突"),
    re.compile(r"不一致"),
    re.compile(r"两(?:个|处|种)"),
    re.compile(r"分别"),
    re.compile(r"同时展示"),
    re.compile(r"核对"),
)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").lower())


def fact_hit(answer: str, fact: str) -> bool:
    """Match required fact; support simple 'A 或 B' alternatives."""
    ans = _normalize(answer)
    raw = (fact or "").strip()
    if not raw:
        return True
    alternatives = re.split(r"\s*或\s*", raw)
    for alt in alternatives:
        piece = _normalize(alt)
        if piece and piece in ans:
            return True
    return False


def required_facts_score(answer: str, required_facts: list[str]) -> dict[str, Any]:
    if not required_facts:
        return {"score": 1.0, "hits": [], "misses": []}
    hits = [f for f in required_facts if fact_hit(answer, f)]
    misses = [f for f in required_facts if f not in hits]
    return {
        "score": len(hits) / len(required_facts),
        "hits": hits,
        "misses": misses,
    }


def forbidden_claims_triggered(answer: str, forbidden_claims: list[str]) -> list[str]:
    ans = answer or ""
    triggered: list[str] = []
    for claim in forbidden_claims or []:
        # Parenthetical notes like "（证据不足时）" are guidance, not literal phrases.
        core = re.sub(r"[（(].*?[）)]", "", claim).strip()
        if not core:
            continue
        if core in ans or _normalize(core) in _normalize(ans):
            triggered.append(claim)
    return triggered


def is_refusal(answer: str) -> bool:
    text = answer or ""
    return any(snippet in text for snippet in NO_KNOWLEDGE_SNIPPETS)


def has_conflict_hint(answer: str) -> bool:
    text = answer or ""
    return any(p.search(text) for p in CONFLICT_HINT_PATTERNS)


def evidence_anchor_recall(
    sources: list[dict[str, Any]],
    anchors: list[dict[str, Any]],
) -> dict[str, Any]:
    """Match soft anchors on source filename and optional section_path substring."""
    if not anchors:
        return {"score": 1.0, "matched": [], "missed": []}

    matched: list[dict[str, Any]] = []
    missed: list[dict[str, Any]] = []
    for anchor in anchors:
        want_source = str(anchor.get("source") or "")
        want_section = str(anchor.get("section_path_contains") or "")
        hit = False
        for src in sources or []:
            name = str(src.get("source") or src.get("filename") or "")
            section = str(src.get("section_path") or "")
            if want_source and want_source not in name and name not in want_source:
                continue
            if want_section and want_section not in section:
                continue
            hit = True
            break
        if hit:
            matched.append(anchor)
        else:
            missed.append(anchor)
    return {
        "score": len(matched) / len(anchors),
        "matched": matched,
        "missed": missed,
    }


def score_answer(
    item: dict[str, Any],
    answer: str,
    sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Score one gold item against a model answer (+ optional retrieved sources)."""
    answerability = str(item.get("answerability") or "full")
    facts = required_facts_score(answer, list(item.get("required_facts") or []))
    forbidden = forbidden_claims_triggered(answer, list(item.get("forbidden_claims") or []))
    anchors = evidence_anchor_recall(list(sources or []), list(item.get("evidence_anchors") or []))
    refusal = is_refusal(answer)
    conflict_hint = has_conflict_hint(answer)

    checks: dict[str, bool] = {
        "no_forbidden_claims": len(forbidden) == 0,
    }

    if answerability == "none":
        checks["correct_refusal"] = refusal
        # Do not require fact coverage for none items.
        completeness = 1.0 if refusal else 0.0
    elif answerability == "conflict":
        multi_value_hits = len(facts["hits"]) >= 2 or facts["score"] >= 0.66
        checks["conflict_multi_value"] = multi_value_hits
        checks["conflict_hint"] = conflict_hint
        completeness = facts["score"]
    elif answerability == "partial":
        checks["partial_or_refusal_ok"] = facts["score"] >= 0.3 or refusal or "证据不足" in (answer or "")
        completeness = facts["score"]
    else:  # full
        checks["fact_coverage_ge_0_7"] = facts["score"] >= 0.7
        completeness = facts["score"]

    passed = all(checks.values()) and checks.get("no_forbidden_claims", True)
    if answerability == "conflict":
        passed = checks["no_forbidden_claims"] and checks.get("conflict_multi_value", False) and checks.get(
            "conflict_hint", False
        )
    elif answerability == "none":
        passed = checks["correct_refusal"] and checks["no_forbidden_claims"]
    elif answerability == "partial":
        passed = checks["no_forbidden_claims"] and checks.get("partial_or_refusal_ok", False)
    else:
        passed = checks["no_forbidden_claims"] and checks.get("fact_coverage_ge_0_7", False)

    return {
        "id": item.get("id"),
        "category": item.get("category"),
        "answerability": answerability,
        "passed": passed,
        "completeness": completeness,
        "required_facts": facts,
        "forbidden_triggered": forbidden,
        "evidence_anchor_recall": anchors,
        "is_refusal": refusal,
        "has_conflict_hint": conflict_hint,
        "checks": checks,
        "pending_media": bool(item.get("pending_media")),
    }


def summarize_scores(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "total": 0,
            "passed": 0,
            "pass_rate": 0.0,
            "mean_completeness": 0.0,
            "mean_evidence_recall": 0.0,
            "forbidden_rate": 0.0,
            "by_category": {},
            "by_answerability": {},
        }

    def _bucket(key: str) -> dict[str, Any]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            groups.setdefault(str(row.get(key) or "unknown"), []).append(row)
        out: dict[str, Any] = {}
        for name, items in groups.items():
            out[name] = {
                "total": len(items),
                "passed": sum(1 for i in items if i.get("passed")),
                "pass_rate": sum(1 for i in items if i.get("passed")) / len(items),
                "mean_completeness": sum(float(i.get("completeness") or 0.0) for i in items) / len(items),
            }
        return out

    return {
        "total": len(rows),
        "passed": sum(1 for r in rows if r.get("passed")),
        "pass_rate": sum(1 for r in rows if r.get("passed")) / len(rows),
        "mean_completeness": sum(float(r.get("completeness") or 0.0) for r in rows) / len(rows),
        "mean_evidence_recall": sum(
            float((r.get("evidence_anchor_recall") or {}).get("score") or 0.0) for r in rows
        )
        / len(rows),
        "forbidden_rate": sum(1 for r in rows if r.get("forbidden_triggered")) / len(rows),
        "by_category": _bucket("category"),
        "by_answerability": _bucket("answerability"),
    }
