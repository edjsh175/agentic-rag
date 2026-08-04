"""Evidence span authority — match and repair LLM evidence to real chunk excerpts.

Deterministic first: exact → normalized → fuzzy window.
Does not call LLM; optional boundary arbiter can wrap this later.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher


def normalize_for_evidence_match(text: str) -> str:
    """Fold whitespace / full-width punctuation for evidence substring checks only."""
    if not text:
        return ""
    chars: list[str] = []
    for ch in str(text).replace("\u3000", " "):
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            chars.append(chr(code - 0xFEE0))
        else:
            chars.append(ch)
    folded = "".join(chars).replace("（", "(").replace("）", ")")
    return re.sub(r"\s+", " ", folded).strip()


def evidence_matches(evidence: str, content: str, section_path: str = "") -> bool:
    """True if evidence is a direct excerpt, allowing blank/fullwidth folding only."""
    return repair_evidence_span(evidence, content, section_path) is not None


@dataclass(frozen=True)
class EvidenceRepair:
    text: str
    method: str  # exact | normalized | fuzzy


def _build_norm_index_map(text: str) -> tuple[str, list[int]]:
    """Return normalized text and map: each norm char index → original char index."""
    mapping: list[int] = []
    out: list[str] = []
    pending_space = False
    started = False
    i = 0
    n = len(text or "")
    while i < n:
        ch = text[i]
        if ch == "\u3000":
            ch = " "
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            ch = chr(code - 0xFEE0)
        elif ch == "（":
            ch = "("
        elif ch == "）":
            ch = ")"
        if ch.isspace():
            if started:
                pending_space = True
            i += 1
            continue
        if pending_space and out:
            out.append(" ")
            mapping.append(i)
            pending_space = False
        out.append(ch)
        mapping.append(i)
        started = True
        i += 1
    return "".join(out), mapping


def _span_from_norm(
    haystack: str, n_hay: str, mapping: list[int], n_start: int, n_end: int
) -> str | None:
    if n_start < 0 or n_end > len(n_hay) or n_start >= n_end:
        return None
    if n_end > len(mapping):
        return None
    orig_start = mapping[n_start]
    orig_end = mapping[n_end - 1] + 1
    return haystack[orig_start:orig_end]


def _exact_or_normalized(evidence: str, haystack: str) -> EvidenceRepair | None:
    ev = str(evidence or "").strip()
    if not ev or not haystack:
        return None
    if ev in haystack:
        return EvidenceRepair(text=ev, method="exact")
    n_ev = normalize_for_evidence_match(ev)
    if not n_ev:
        return None
    n_hay, mapping = _build_norm_index_map(haystack)
    idx = n_hay.find(n_ev)
    if idx < 0:
        return None
    span = _span_from_norm(haystack, n_hay, mapping, idx, idx + len(n_ev))
    if not span:
        return None
    return EvidenceRepair(text=span, method="normalized")


def _fuzzy_window(
    evidence: str, haystack: str, *, min_ratio: float
) -> EvidenceRepair | None:
    ev = str(evidence or "").strip()
    if not ev or not haystack:
        return None
    n_ev = normalize_for_evidence_match(ev)
    if len(n_ev) < 4:
        return None
    n_hay, mapping = _build_norm_index_map(haystack)
    if len(n_hay) < 4:
        return None

    ev_len = len(n_ev)
    lo = max(4, ev_len - max(2, ev_len // 5))
    hi = min(len(n_hay), ev_len + max(8, ev_len // 3))
    step = max(1, ev_len // 6)
    scored: list[tuple[float, int, int]] = []

    for win in range(lo, hi + 1):
        last_start = len(n_hay) - win
        starts = list(range(0, last_start + 1, step))
        if last_start >= 0 and (not starts or starts[-1] != last_start):
            starts.append(last_start)
        for start in starts:
            end = start + win
            cand = n_hay[start:end]
            ratio = SequenceMatcher(None, n_ev, cand, autojunk=False).ratio()
            if ratio >= min_ratio - 0.05:
                scored.append((ratio, start, end))

    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    best_ratio, best_start, best_end = scored[0]
    if best_ratio < min_ratio:
        return None
    if len(scored) > 1 and scored[1][0] >= min_ratio and (best_ratio - scored[1][0]) < 0.05:
        # Ambiguous near-ties → refuse auto-repair (leave for optional LLM).
        a0, b0 = best_start, best_end
        a1, b1 = scored[1][1], scored[1][2]
        if abs(a0 - a1) > 2 or abs(b0 - b1) > 2:
            return None
    span = _span_from_norm(haystack, n_hay, mapping, best_start, best_end)
    if not span:
        return None
    return EvidenceRepair(text=span, method="fuzzy")


def _name_anchor_span(
    anchor: str, haystack: str, *, radius: int = 48
) -> EvidenceRepair | None:
    name = str(anchor or "").strip()
    if len(name) < 2 or not haystack or name not in haystack:
        return None
    idx = haystack.find(name)
    start = max(0, idx - radius)
    end = min(len(haystack), idx + len(name) + radius)
    span = haystack[start:end].strip()
    if not span:
        return None
    return EvidenceRepair(text=span, method="name_anchor")


def repair_evidence_span(
    evidence: str,
    content: str,
    section_path: str = "",
    *,
    anchor: str = "",
    min_fuzzy_ratio: float = 0.88,
) -> EvidenceRepair | None:
    """Map evidence to a real excerpt from content or section_path, or None."""
    ev = str(evidence or "").strip()
    if not ev:
        return None
    content = content or ""
    section_path = section_path or ""

    for hay in (content, section_path):
        got = _exact_or_normalized(ev, hay)
        if got is not None:
            return got

    for hay in (content, section_path):
        got = _fuzzy_window(ev, hay, min_ratio=min_fuzzy_ratio)
        if got is not None:
            return got

    # Last deterministic resort: window around a known entity/endpoint name.
    for hay in (content, section_path):
        got = _name_anchor_span(anchor, hay)
        if got is not None:
            return got
    return None
