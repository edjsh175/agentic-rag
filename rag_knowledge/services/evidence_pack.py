from dataclasses import dataclass, field
import re
from collections import defaultdict
from typing import Any


@dataclass
class GroundingVerdict:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    unsupported_segments: list[str] = field(default_factory=list)
    valid_citation_ids: set[int] = field(default_factory=set)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GroundedClaimUnit:
    claim: str
    citation_ids: frozenset[int] = field(default_factory=frozenset)


NO_KNOWLEDGE_ANSWER = "当前知识库中未查询到相关内容。"
DETERMINISTIC_GROUNDING_POLICY_VERSION = "strict-grounding-phase10b-v1"
_CITATION_RE = re.compile(r"\[(\d+)\]|\((\d+)\)")
_COMPLETE_RE = re.compile(r"完整|全部|所有步骤|分别说明|逐一|按顺序|端到端")
_KEY_VALUE_RE = re.compile(r"(?im)^\s*([A-Za-z][A-Za-z0-9_.-]{1,80})\s*(?:=|:|：)\s*([^\s,，;；]+)")
_TLS_PORT_RE = re.compile(
    r"(?im)(?:tls(?:/dtls)?[-_\s]?listening[-_\s]?port|tls\s*端口)\s*(?:=|:|：|\||为|是)\s*(\d{2,5})"
)
_LATIN_SUBJECT_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{2,}")
_CJK_SUBJECT_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
_QUESTION_STOPWORDS = {
    "什么", "怎么", "如何", "哪些", "哪个", "介绍", "一下", "属于", "区别",
    "相关", "内容", "问题", "查询", "请问", "是否", "可以", "怎么用",
}

# 严格模式下的高风险语义操作符。若回答使用这些关系/极性词，引用证据中
# 必须出现同类语义锚点；否则即使实体和关键词高度重合，也不能证明该关系。
_SEMANTIC_OPERATOR_RULES: tuple[tuple[str, re.Pattern[str], re.Pattern[str]], ...] = (
    (
        "negative_capability",
        re.compile(r"不支持|不允许|不能|不可|无法|禁止|未启用|未提供"),
        re.compile(r"不支持|不允许|不能|不可|无法|禁止|未启用|未提供"),
    ),
    (
        "positive_capability",
        re.compile(r"(?<!不)支持|(?<!不)允许|可以|能够|可用于"),
        re.compile(r"(?<!不)支持|(?<!不)允许|可以|能够|可用于"),
    ),
    (
        "negative_identity",
        re.compile(r"不属于|不是|并非"),
        re.compile(r"不属于|不是|并非"),
    ),
    (
        "identity_or_belonging",
        re.compile(r"(?<!不)属于|归属|隶属"),
        re.compile(r"(?<!不)属于|归属|隶属"),
    ),
    (
        "difference",
        re.compile(r"不同|差异|区别|相互独立|独立产品线"),
        re.compile(r"不同|差异|区别|相互独立|独立产品线|不同产品线"),
    ),
    (
        "sameness",
        re.compile(r"相同|一致|同一(?:个|套|种|条)?"),
        re.compile(r"相同|一致|同一(?:个|套|种|条)?"),
    ),
    (
        "dependency",
        re.compile(r"基于|依赖|取决于"),
        re.compile(r"基于|依赖|取决于"),
    ),
    (
        "containment",
        re.compile(r"包含|包括"),
        re.compile(r"包含|包括"),
    ),
    (
        "invocation",
        re.compile(r"调用|请求|访问"),
        re.compile(r"调用|请求|访问"),
    ),
    (
        "usage_or_implementation",
        re.compile(r"采用|使用|用于|用来|实现"),
        re.compile(r"采用|使用|用于|用来|实现"),
    ),
    (
        "necessity",
        re.compile(r"必须|需要|要求"),
        re.compile(r"必须|需要|要求"),
    ),
    (
        "exclusivity_or_completeness",
        re.compile(r"仅|只|唯一|全部|所有|完全"),
        re.compile(r"仅|只|唯一|全部|所有|完全"),
    ),
    (
        "causality",
        re.compile(r"因为|由于|导致|因此|所以|从而"),
        re.compile(r"因为|由于|导致|因此|所以|从而"),
    ),
    (
        "ordered_comparison",
        re.compile(r"高于|低于|大于|小于|超过|不少于|不超过|至少|至多|更高|更低"),
        re.compile(r"高于|低于|大于|小于|超过|不少于|不超过|至少|至多|更高|更低"),
    ),
)


def _split_relation_clauses(text: str) -> list[str]:
    """Split evidence finely enough that opposite relations in one chunk cannot cross-support."""
    return [
        part.strip()
        for part in re.split(r"[。；;！？\n]+|(?:但是|不过|然而|但|却)", text or "")
        if part.strip()
    ]


def _unsupported_semantic_operators(
    claim: str,
    evidence_chunks: list[str],
    *,
    latin_terms: list[str] | None = None,
    concept_terms: list[str] | None = None,
) -> list[str]:
    """Require relation operators and their claim anchors to co-occur in one evidence clause."""
    latin = [term.casefold() for term in (latin_terms or []) if term]
    concepts = [term.casefold() for term in (concept_terms or []) if term]
    concept_threshold = max(1, (2 * len(concepts) + 2) // 3) if concepts else 0
    clauses = [clause for chunk in evidence_chunks for clause in _split_relation_clauses(chunk)]

    unsupported: list[str] = []
    for name, claim_pattern, evidence_pattern in _SEMANTIC_OPERATOR_RULES:
        if not claim_pattern.search(claim):
            continue

        supported = False
        for clause in clauses:
            if not evidence_pattern.search(clause):
                continue
            folded = clause.casefold()
            if latin and not all(term in folded for term in latin):
                continue
            if concepts:
                overlap = sum(1 for term in concepts if term in folded)
                if overlap < concept_threshold:
                    continue
            supported = True
            break

        if not supported:
            unsupported.append(name)
    return unsupported


_DEPENDENCY_RE = re.compile(r"基于|依赖|取决于")
_BELONGING_RE = re.compile(r"(?<!不)属于|归属|隶属")
_CONTAINMENT_RE = re.compile(r"包含|包括")
_INVOCATION_RE = re.compile(r"调用|请求|访问")
_CAUSAL_DIRECTION_RE = re.compile(r"导致|造成|引发")
_COMPARISON_RE = re.compile(r"高于|低于|大于|小于|超过|不少于|不超过|至少|至多|更高|更低")
_ORDER_RE = re.compile(
    r"先[^A-Za-z0-9]{0,24}(?P<first>[A-Za-z][A-Za-z0-9_.-]{2,})"
    r".*?(?:再|然后|随后)[^A-Za-z0-9]{0,24}(?P<second>[A-Za-z][A-Za-z0-9_.-]{2,})"
)
_ORDER_SPAN_RE = re.compile(
    r"先(?P<first>[^，,。；;\n]{1,48}?)[，,]?\s*(?:再|然后|随后)(?P<second>[^，,。；;\n]{1,48})"
)
_AFTER_ORDER_RE = re.compile(
    r"(?P<first>[^，,。；;\n]{1,48}?)后[，,]?\s*(?P<second>[^，,。；;\n]{1,48})"
)
_CONDITION_RE = re.compile(
    r"(?:当|如果|若|仅当|只有).{0,36}(?:时|才)|(?:启用|开启|关闭|设置).{0,24}(?:时|后|前)"
)
_CAPABILITY_RE = re.compile(r"不支持|不允许|不能|不可|无法|禁止|(?<!不)支持|(?<!不)允许|可以|能够|可用于")


def _latin_pair_around_operator(text: str, pattern: re.Pattern[str]) -> tuple[str, str] | None:
    """Return the nearest Latin technical entities on both sides of an operator."""
    match = pattern.search(text or "")
    if match is None:
        return None
    left = _LATIN_SUBJECT_RE.findall((text or "")[:match.start()])
    right = _LATIN_SUBJECT_RE.findall((text or "")[match.end():])
    if not left or not right:
        return None
    return left[-1].casefold(), right[0].casefold()


def _comparison_signature(text: str) -> tuple[str, str, str] | None:
    match = _COMPARISON_RE.search(text or "")
    if match is None:
        return None
    pair = _latin_pair_around_operator(text, _COMPARISON_RE)
    if pair is None:
        return None
    left, right = pair
    op = match.group(0)
    if op in {"低于", "小于", "更低"}:
        return right, left, "gt"
    if op in {"不超过", "至多"}:
        return right, left, "ge"
    if op in {"不少于", "至少"}:
        return left, right, "ge"
    return left, right, "gt"


def _directional_relation_violations(claim: str, evidence_chunks: list[str]) -> list[str]:
    """Catch mechanically provable direction reversals before probabilistic verification."""
    clauses = [clause for chunk in evidence_chunks for clause in _split_relation_clauses(chunk)]
    violations: list[str] = []

    for name, pattern in (("dependency_direction", _DEPENDENCY_RE), ("belonging_direction", _BELONGING_RE)):
        claim_pair = _latin_pair_around_operator(claim, pattern)
        if claim_pair is None:
            continue
        evidence_pairs = {
            pair
            for clause in clauses
            if (pair := _latin_pair_around_operator(clause, pattern)) is not None
        }
        if claim_pair not in evidence_pairs and (claim_pair[1], claim_pair[0]) in evidence_pairs:
            violations.append(name)

    claim_comparison = _comparison_signature(claim)
    if claim_comparison is not None:
        evidence_comparisons = {
            signature
            for clause in clauses
            if (signature := _comparison_signature(clause)) is not None
        }
        left, right, relation = claim_comparison
        if (
            claim_comparison not in evidence_comparisons
            and (right, left, relation) in evidence_comparisons
        ):
            violations.append("ordered_comparison_direction")

    claim_order = _ORDER_RE.search(claim or "")
    if claim_order is not None:
        claim_pair = (
            claim_order.group("first").casefold(),
            claim_order.group("second").casefold(),
        )
        evidence_orders = set()
        for clause in clauses:
            match = _ORDER_RE.search(clause)
            if match is not None:
                evidence_orders.add((match.group("first").casefold(), match.group("second").casefold()))
        if claim_pair not in evidence_orders and (claim_pair[1], claim_pair[0]) in evidence_orders:
            violations.append("procedure_order_direction")

    return violations


def _relation_side_tokens(text: str) -> set[str]:
    import jieba

    cleaned = re.sub(r"(?:完成后|之后|以后|随后|然后|先|再)", " ", text or "")
    stop = {
        "导致", "造成", "引发", "因此", "所以", "从而", "会", "将", "使得", "出现", "进入",
        "系统", "状态", "进行", "发生", "产生", "启动", "完成",
    }
    tokens = {
        token.strip().casefold()
        for token in jieba.cut(cleaned)
        if len(token.strip()) >= 2 and token.strip().casefold() not in stop
    }
    tokens.update(term.casefold() for term in _LATIN_SUBJECT_RE.findall(cleaned))
    return tokens


def _token_overlap(left: set[str], right: set[str]) -> float:
    return len(left & right) / max(1, len(left))


def _token_direction_reversed(
    claim: str,
    evidence_chunks: list[str],
    pattern: re.Pattern[str],
) -> bool:
    claim_match = pattern.search(claim or "")
    if claim_match is None:
        return False
    claim_left = _relation_side_tokens(claim[:claim_match.start()])
    claim_right = _relation_side_tokens(claim[claim_match.end():])
    if not claim_left or not claim_right:
        return False

    saw_reverse = False
    for chunk in evidence_chunks:
        for clause in _split_relation_clauses(chunk):
            match = pattern.search(clause)
            if match is None:
                continue
            ev_left = _relation_side_tokens(clause[:match.start()])
            ev_right = _relation_side_tokens(clause[match.end():])
            if not ev_left or not ev_right:
                continue
            direct_left = _token_overlap(claim_left, ev_left)
            direct_right = _token_overlap(claim_right, ev_right)
            if direct_left >= 0.6 and direct_right >= 0.6:
                return False
            cross_left = _token_overlap(claim_left, ev_right)
            cross_right = _token_overlap(claim_right, ev_left)
            if cross_left >= 0.6 and cross_right >= 0.6:
                saw_reverse = True
    return saw_reverse


def _comparison_token_signature(text: str) -> tuple[set[str], set[str], str] | None:
    match = _COMPARISON_RE.search(text or "")
    if match is None:
        return None
    left = _relation_side_tokens(text[:match.start()])
    right = _relation_side_tokens(text[match.end():])
    if not left or not right:
        return None
    op = match.group(0)
    if op in {"低于", "小于", "更低"}:
        return right, left, "gt"
    if op in {"不超过", "至多"}:
        return right, left, "ge"
    if op in {"不少于", "至少"}:
        return left, right, "ge"
    return left, right, "gt"


def _comparison_token_direction_reversed(claim: str, evidence_chunks: list[str]) -> bool:
    claim_signature = _comparison_token_signature(claim)
    if claim_signature is None:
        return False
    claim_left, claim_right, claim_relation = claim_signature
    saw_reverse = False
    for chunk in evidence_chunks:
        for clause in _split_relation_clauses(chunk):
            signature = _comparison_token_signature(clause)
            if signature is None:
                continue
            ev_left, ev_right, ev_relation = signature
            if ev_relation != claim_relation:
                continue
            if _token_overlap(claim_left, ev_left) >= 0.6 and _token_overlap(claim_right, ev_right) >= 0.6:
                return False
            if _token_overlap(claim_left, ev_right) >= 0.6 and _token_overlap(claim_right, ev_left) >= 0.6:
                saw_reverse = True
    return saw_reverse


def _order_pair_tokens(text: str) -> tuple[set[str], set[str]] | None:
    for pattern in (_ORDER_SPAN_RE, _AFTER_ORDER_RE):
        match = pattern.search(text or "")
        if match is None:
            continue
        first = _relation_side_tokens(match.group("first"))
        second = _relation_side_tokens(match.group("second"))
        if first and second:
            return first, second
    return None


def _order_direction_reversed(claim: str, evidence_chunks: list[str]) -> bool:
    claim_pair = _order_pair_tokens(claim)
    if claim_pair is None:
        return False
    claim_first, claim_second = claim_pair

    saw_reverse = False
    for chunk in evidence_chunks:
        for clause in _split_relation_clauses(chunk):
            evidence_pair = _order_pair_tokens(clause)
            if evidence_pair is None:
                continue
            ev_first, ev_second = evidence_pair
            if _token_overlap(claim_first, ev_first) >= 0.6 and _token_overlap(claim_second, ev_second) >= 0.6:
                return False
            if _token_overlap(claim_first, ev_second) >= 0.6 and _token_overlap(claim_second, ev_first) >= 0.6:
                saw_reverse = True
    return saw_reverse


def _causal_direction_reversed(claim: str, evidence_chunks: list[str]) -> bool:
    claim_match = _CAUSAL_DIRECTION_RE.search(claim or "")
    if claim_match is None:
        return False
    claim_left = _relation_side_tokens(claim[:claim_match.start()])
    claim_right = _relation_side_tokens(claim[claim_match.end():])
    if not claim_left or not claim_right:
        return False

    for chunk in evidence_chunks:
        for clause in _split_relation_clauses(chunk):
            match = _CAUSAL_DIRECTION_RE.search(clause)
            if match is None:
                continue
            ev_left = _relation_side_tokens(clause[:match.start()])
            ev_right = _relation_side_tokens(clause[match.end():])
            if not ev_left or not ev_right:
                continue
            cross_left = len(claim_left & ev_right) / max(1, len(claim_left))
            cross_right = len(claim_right & ev_left) / max(1, len(claim_right))
            direct_left = len(claim_left & ev_left) / max(1, len(claim_left))
            direct_right = len(claim_right & ev_right) / max(1, len(claim_right))
            if cross_left >= 0.6 and cross_right >= 0.6 and (direct_left < 0.6 or direct_right < 0.6):
                return True
    return False


def _condition_scope_erased(
    claim: str,
    evidence_chunks: list[str],
    latin_terms: list[str],
    concept_terms: list[str],
) -> bool:
    """Reject unconditional capability claims when all matching cited support is conditional."""
    if _CONDITION_RE.search(claim or "") or not _CAPABILITY_RE.search(claim or ""):
        return False

    anchors = [term.casefold() for term in latin_terms if term]
    if not anchors:
        # Chinese-only claims still need deterministic scope protection. Require
        # at least two substantive concept anchors to avoid matching on one vague noun.
        anchors = [term.casefold() for term in concept_terms if term][:6]
        if len(anchors) < 2:
            return False

    matching_clauses: list[str] = []
    for chunk in evidence_chunks:
        for clause in _split_relation_clauses(chunk):
            folded = clause.casefold()
            if not _CAPABILITY_RE.search(clause):
                continue
            overlap = sum(1 for anchor in anchors if anchor in folded)
            threshold = len(anchors) if latin_terms else max(2, (2 * len(anchors) + 2) // 3)
            if overlap < threshold:
                continue
            matching_clauses.append(clause)

    return bool(matching_clauses) and all(_CONDITION_RE.search(clause) for clause in matching_clauses)


def citation_ids(answer: str) -> set[int]:
    return {int(left or right) for left, right in _CITATION_RE.findall(answer or "")}


def extract_claim_units(answer: str) -> list[GroundedClaimUnit]:
    """Split answer text into factual claim units while preserving local citation scope."""
    units: list[GroundedClaimUnit] = []
    lines = [line.strip() for line in (answer or "").split("\n") if line.strip()]
    for line in lines:
        if (
            line.startswith("#")
            or line.startswith("（提示：")
            or line.startswith("(提示：")
            or line.startswith("（说明：")
            or line.startswith("(说明：")
            or line == NO_KNOWLEDGE_ANSWER
        ):
            continue
        sub_sentences = [
            s.strip()
            for s in re.split(r"[。；\n]+(?!\s*(?:\[\d+\]|\(\d+\)))", line)
            if len(s.strip()) > 6
        ]
        for sent in sub_sentences:
            matches = list(re.finditer(
                r"(?P<claim>.*?)(?P<cites>(?:\s*(?:\[\d+\]|\(\d+\)))+)",
                sent,
            ))
            if matches:
                raw_units = [
                    (match.group("claim"), citation_ids(match.group("cites")))
                    for match in matches
                ]
                trailing = sent[matches[-1].end():].strip(" ，,：:")
                if trailing:
                    raw_units.append((trailing, set()))
            else:
                raw_units = [(sent, set())]

            for raw_claim, cids in raw_units:
                claim = re.sub(_CITATION_RE, "", raw_claim).strip(" -:：")
                if len(claim) <= 6:
                    continue
                units.append(GroundedClaimUnit(claim=claim, citation_ids=frozenset(cids)))
    return units


def cited_sources(answer: str, source_docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    wanted = citation_ids(answer)
    return [
        source for source in source_docs
        if source.get("metadata", {}).get("citation_id") in wanted
    ]


def _evidence_item(source: dict[str, Any], *, drop_reason: str | None = None) -> dict[str, Any]:
    meta = source.get("metadata", {})
    item = {
        "index": meta.get("citation_id"),
        "document": meta.get("source") or meta.get("file_name") or "",
        "source": meta.get("source") or meta.get("file_name") or "",
        "section_id": meta.get("section_id") or "",
        "section_path": meta.get("section_path") or meta.get("section_title") or "",
        "chunk_id": meta.get("chunk_id") or "",
        "snippet": str(source.get("content") or "")[:500],
    }
    if drop_reason:
        item["drop_reason"] = drop_reason
    return item


def _conflicts(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    values_by_key: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for source in sources:
        content = str(source.get("content") or "")
        for key, value in _KEY_VALUE_RE.findall(content):
            item = _evidence_item(source)
            item["value"] = value
            values_by_key[key.lower()][value].append(item)
        for value in _TLS_PORT_RE.findall(content):
            item = _evidence_item(source)
            item["value"] = value
            values_by_key["tls_port"][value].append(item)
    conflicts = []
    for key, values in values_by_key.items():
        if len(values) <= 1:
            continue
        entries = []
        seen: set[tuple[Any, ...]] = set()
        for value, items in values.items():
            for item in items:
                marker = (value, item.get("chunk_id"), item.get("index"))
                if marker in seen:
                    continue
                seen.add(marker)
                entries.append(item)
        conflicts.append({"key": key, "values": entries})
    return conflicts


def build_evidence_pack(
    answer: str,
    retrieved_docs: list[dict[str, Any]],
    context_docs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a request-local trace without persisting query content."""
    cited = cited_sources(answer, context_docs)
    cited_ids = {item.get("metadata", {}).get("citation_id") for item in cited}
    context_ids = {item.get("metadata", {}).get("citation_id") for item in context_docs}
    uncited: list[dict[str, Any]] = []
    for source in retrieved_docs:
        citation_id = source.get("metadata", {}).get("citation_id")
        if citation_id in cited_ids:
            continue
        reason = "not_cited" if citation_id in context_ids else "budget_trim"
        uncited.append(_evidence_item(source, drop_reason=reason))
    gaps = []
    if retrieved_docs and not cited and answer.strip() != NO_KNOWLEDGE_ANSWER:
        gaps.append({"status": "insufficient_evidence", "reason": "no_valid_citation"})
    return {
        "cited": [_evidence_item(source) for source in cited],
        "retrieved_uncited": uncited,
        "gaps": gaps,
        "conflicts": _conflicts(retrieved_docs),
    }


def _conflict_notice(sources: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for conflict in _conflicts(sources):
        values: list[str] = []
        seen: set[tuple[Any, Any]] = set()
        for item in conflict["values"]:
            marker = (item.get("value"), item.get("index"))
            if marker in seen:
                continue
            seen.add(marker)
            values.append(f"{item['value']} [{item['index']}]")
        lines.append(f"- `{conflict['key']}`: {'；'.join(values)}")
    if not lines:
        return ""
    return "\n\n检测到同一配置项存在不同证据值：\n" + "\n".join(lines) + "\n请核对原文。"


def question_subjects(question: str) -> list[str]:
    """Extract coarse subject tokens from a question for grounded partial answers."""
    text = (question or "").strip()
    if not text:
        return []
    subjects: list[str] = []
    seen: set[str] = set()
    for match in _LATIN_SUBJECT_RE.findall(text):
        key = match.casefold()
        if key in seen:
            continue
        seen.add(key)
        subjects.append(match)
    for match in _CJK_SUBJECT_RE.findall(text):
        if match in _QUESTION_STOPWORDS:
            continue
        key = match.casefold()
        if key in seen:
            continue
        seen.add(key)
        subjects.append(match)
    return subjects


def _doc_blob(source: dict[str, Any]) -> str:
    meta = source.get("metadata") or {}
    return " ".join(
        [
            str(source.get("content") or ""),
            str(meta.get("section_path") or ""),
            str(meta.get("section_title") or ""),
            str(meta.get("source") or ""),
            str(meta.get("file_name") or ""),
        ]
    ).casefold()


def matching_context_docs(
    question: str,
    context_docs: list[dict[str, Any]],
    *,
    max_docs: int = 3,
) -> list[dict[str, Any]]:
    """Prefer context docs that mention question subjects; else keep top docs."""
    docs = [doc for doc in (context_docs or []) if isinstance(doc, dict)]
    if not docs:
        return []
    subjects = [s.casefold() for s in question_subjects(question)]
    if subjects:
        matched = [doc for doc in docs if any(subject in _doc_blob(doc) for subject in subjects)]
        if matched:
            return matched[:max_docs]
    return docs[:max_docs]


def build_partial_grounded_answer(
    question: str,
    context_docs: list[dict[str, Any]],
) -> str | None:
    """Rule-4 fallback: keep subject-related citations when the model omitted them."""
    matched = matching_context_docs(question, context_docs)
    if not matched:
        return None
    citation_ids_ordered: list[int] = []
    section_hints: list[str] = []
    for doc in matched:
        meta = doc.get("metadata") or {}
        try:
            cid = int(meta.get("citation_id"))
        except (TypeError, ValueError):
            continue
        if cid in citation_ids_ordered:
            continue
        citation_ids_ordered.append(cid)
        hint = str(meta.get("section_path") or meta.get("section_title") or "").strip()
        if hint and hint not in section_hints:
            section_hints.append(hint)
    if not citation_ids_ordered:
        return None
    subjects = question_subjects(question)
    subject = subjects[0] if subjects else "该主题"
    hint_text = "、".join(section_hints[:3]) if section_hints else "相关章节"
    cites = "".join(f"[{cid}]" for cid in citation_ids_ordered)
    aspect = (question or "").strip() or "该问题"
    return (
        f"知识库中查到了{subject}的部分相关内容（如{hint_text}），"
        f"但未检索到关于「{aspect}」的完整说明。{cites}"
    )


_THIN_PARTIAL_RE = re.compile(
    r"^知识库中查到了.+?的部分相关内容（如.+?），"
    r"但未检索到关于[「\[][^」\]]+[」\]]的完整说明。"
    r"(?:\[\d+\])*$",
    re.DOTALL,
)


def _is_thin_partial_answer(answer: str) -> bool:
    return bool(_THIN_PARTIAL_RE.match((answer or "").strip()))


def _append_evidence_bullets(
    answer: str,
    question: str,
    context_docs: list[dict[str, Any]],
    *,
    max_docs: int = 3,
    snippet_chars: int = 160,
) -> str:
    """Attach short grounded bullets when the model only emitted a rule-4 shell."""
    if "相关原文要点" in (answer or ""):
        return answer
    matched = matching_context_docs(question, context_docs, max_docs=max_docs)
    bullets: list[str] = []
    for doc in matched:
        meta = doc.get("metadata") or {}
        try:
            cid = int(meta.get("citation_id"))
        except (TypeError, ValueError):
            continue
        section = str(meta.get("section_path") or meta.get("section_title") or "").strip()
        snippet = " ".join(str(doc.get("content") or "").split())
        if len(snippet) > snippet_chars:
            snippet = snippet[:snippet_chars] + "…"
        label = f"{section}：" if section else ""
        bullets.append(f"- {label}{snippet} [{cid}]")
    if not bullets:
        return answer
    return answer.rstrip() + "\n\n相关原文要点：\n" + "\n".join(bullets)


def _clean_invalid_citations(answer: str, valid_cids: set[int]) -> str:
    """剔除越界/编造的引用标号，例如 context 中只有 [1, 2] 但模型输出了 [99]。"""
    if not valid_cids:
        return answer

    def _replace_cid(m: re.Match) -> str:
        cid_str = m.group(1) or m.group(2)
        try:
            cid = int(cid_str)
            if cid in valid_cids:
                return m.group(0)
            return ""
        except (ValueError, TypeError):
            return ""

    cleaned = _CITATION_RE.sub(_replace_cid, answer)
    return re.sub(r"[ \t]+", " ", cleaned).strip()


def _check_ungrounded_parameters(answer: str, context_docs: list[dict[str, Any]]) -> str:
    """关键参数与片段锚定校验：若输出具体端口或路径但无原文依据，追加安全提示。"""
    if not context_docs or "缺少明确原文依据" in answer:
        return answer

    all_context_blob = " ".join(_doc_blob(d) for d in context_docs)
    all_context_blob_lower = all_context_blob.casefold()

    ports = re.findall(r"(?:port|端口)\s*(?:=|:|为|是)?\s*(\d{2,5})\b", answer, flags=re.I)
    paths = re.findall(r"(?:[A-Za-z]:[/\\][A-Za-z0-9_.-]+|[/\\][A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)", answer)

    has_ungrounded = False
    for p in ports:
        if p not in all_context_blob:
            has_ungrounded = True
            break
    if not has_ungrounded:
        for p in paths:
            if len(p) > 3 and p.casefold() not in all_context_blob_lower:
                has_ungrounded = True
                break

    if has_ungrounded:
        return answer.rstrip() + "\n\n（提示：部分参数缺少明确原文依据，请以官方文档为准。）"
    return answer


def govern_answer(answer: str, question: str, context_docs: list[dict[str, Any]]) -> str:
    """Prevent an uncited or completeness-sensitive answer from overclaiming."""
    answer = (answer or "").strip()
    docs = [doc for doc in (context_docs or []) if isinstance(doc, dict)]

    valid_cids: set[int] = set()
    for doc in docs:
        cid = doc.get("metadata", {}).get("citation_id")
        if cid is not None:
            try:
                valid_cids.add(int(cid))
            except (ValueError, TypeError):
                pass

    # 1. 过滤越界虚假引用标号（Citation Range Check）
    if valid_cids:
        answer = _clean_invalid_citations(answer, valid_cids)

    # Empty / fixed miss: repair to rule-4 partial answer when subject context exists.
    if (not answer or answer == NO_KNOWLEDGE_ANSWER) and docs:
        repaired = build_partial_grounded_answer(question, docs)
        if repaired:
            return _append_evidence_bullets(repaired, question, docs)
        return answer or NO_KNOWLEDGE_ANSWER
    if not answer or answer == NO_KNOWLEDGE_ANSWER:
        return answer or NO_KNOWLEDGE_ANSWER

    cited = cited_sources(answer, docs)
    if not cited:
        if not docs:
            from rag_knowledge.services.agent_orchestration.runtime import is_meta_or_direct_chat

            if is_meta_or_direct_chat(question):
                return answer
            return "检索到相关片段，但没有可验证的引用证据，当前无法给出有依据的回答。"
        repaired = build_partial_grounded_answer(question, docs)
        if repaired:
            return _append_evidence_bullets(repaired, question, docs)
        return "检索到相关片段，但没有可验证的引用证据，当前无法给出有依据的回答。"
    conflict_notice = _conflict_notice(docs)
    if conflict_notice and "请核对原文" not in answer:
        answer += conflict_notice
    if _COMPLETE_RE.search(question or "") and "证据不足" not in answer and "未查询到" not in answer:
        citation_id = cited[0].get("metadata", {}).get("citation_id")
        return f"{answer}\n\n以上仅覆盖已引用证据，不能据此确认完整流程。[{citation_id}]"
    # Model (or prior repair) emitted only the rule-4 shell — keep it, attach evidence bullets.
    if docs and _is_thin_partial_answer(answer):
        return _append_evidence_bullets(answer, question, docs)

    # 2. 关键参数后置校验（Parameter Grounding Check）
    answer = _check_ungrounded_parameters(answer, docs)
    return answer


def verify_grounding(
    answer: str,
    context_docs: list[dict[str, Any]],
    *,
    is_direct_chat: bool = False,
) -> GroundingVerdict:
    """严格证据校验：确保答案中的每个事实断言均有真实且对应的证据支撑，杜绝外部幻觉。"""
    if is_direct_chat:
        return GroundingVerdict(ok=True)

    text = (answer or "").strip()
    if not text or text == NO_KNOWLEDGE_ANSWER:
        return GroundingVerdict(ok=True)

    docs = [d for d in (context_docs or []) if isinstance(d, dict)]
    if not docs:
        return GroundingVerdict(
            ok=False,
            reasons=["no_context_docs_for_factual_answer"],
            unsupported_segments=[text[:120]],
        )

    # 提取合法 citation_id 与对应单个 chunk 内容
    valid_cids: set[int] = set()
    doc_blobs: dict[int, str] = {}
    doc_blobs_lower: dict[int, str] = {}
    for doc in docs:
        meta = doc.get("metadata") or {}
        try:
            cid = int(meta.get("citation_id"))
            valid_cids.add(cid)
            blob = _doc_blob(doc)
            doc_blobs[cid] = blob
            doc_blobs_lower[cid] = blob.lower()
        except (ValueError, TypeError):
            continue

    cids_in_answer = citation_ids(text)
    invalid_cids = cids_in_answer - valid_cids
    if invalid_cids:
        return GroundingVerdict(
            ok=False,
            reasons=[f"invalid_citation_ids:{sorted(invalid_cids)}"],
            unsupported_segments=[f"引用了不存在的编号: {invalid_cids}"],
            valid_citation_ids=valid_cids,
        )

    if not cids_in_answer:
        return GroundingVerdict(
            ok=False,
            reasons=["missing_all_citations"],
            unsupported_segments=["回答中未标注任何 [编号] 知识库引用"],
            valid_citation_ids=valid_cids,
        )

    import jieba

    _COMMON_STOPWORDS = {
        "的", "了", "和", "是", "就", "都", "而", "及", "与", "在", "这", "有", "我", "你", "他", "它",
        "采用", "使用", "支持", "进行", "基于", "通过", "包括", "属于", "可以", "主要", "为", "用于",
        "提供", "实现", "以及", "根据", "按照", "具体", "如下", "相关", "说明", "配置", "并且", "同时",
        "不同", "技术", "路线", "方式", "方案", "系统", "平台", "服务", "模块", "功能", "建立", "连接",
        "渲染", "传输", "处理", "管理", "操作", "步骤", "方法", "规范", "设置", "默认", "分别",
        "依赖", "归属", "隶属", "高于", "低于", "大于", "小于", "超过", "不少于", "不超过", "至少", "至多",
        "更高", "更低", "导致", "造成", "引发", "先", "再", "然后", "随后", "包含", "调用", "请求", "访问",
        "the", "and", "for", "with", "from", "this", "that", "uses", "use", "based", "via",
        "http", "https", "true", "false", "null", "none", "config", "data", "info", "set", "get",
    }

    unsupported_segments: list[str] = []
    reasons: list[str] = []

    for unit in extract_claim_units(text):
        claim = unit.claim
        sent_cids = set(unit.citation_ids)
        numbers = re.findall(r"\b\d{2,}\b", claim)
        latin_terms = [
            t for t in re.findall(
                r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9_.-]{2,})(?![A-Za-z0-9])",
                claim,
            )
            if t.casefold() not in _COMMON_STOPWORDS
        ]

        # 1. 若无引用，检查是否包含事实断言
        if not sent_cids:
            if latin_terms or numbers or len(claim) > 20:
                unsupported_segments.append(f"事实断言未标注引用: '{claim[:60]}'")
                reasons.append("missing_citation_on_assertion")
            continue

        # 2. 严格按该断言所引用的具体 chunk 文本限定检验范围。
        target_blobs_lower = [doc_blobs_lower.get(c, "") for c in sent_cids if c in doc_blobs_lower]
        target_blobs_raw = [doc_blobs.get(c, "") for c in sent_cids if c in doc_blobs]
        combined_target_lower = " ".join(target_blobs_lower)
        combined_target_raw = " ".join(target_blobs_raw)

        # 2.1 检查数字/端口
        for num in numbers:
            if num not in combined_target_raw:
                unsupported_segments.append(
                    f"数字/端口在所引证据 [{list(sent_cids)}] 中无依据: '{num}' in '{claim[:60]}'"
                )
                reasons.append("unsupported_number_or_port")

        # 2.2 检查英文专有名词/技术实体（必须存在于该断言的引证文档中）
        for term in latin_terms:
            if term.casefold() not in combined_target_lower:
                unsupported_segments.append(
                    f"技术实体在所引证据 [{list(sent_cids)}] 中无依据: '{term}' in '{claim[:60]}'"
                )
                reasons.append("unsupported_latin_term")

        # 2.3 两个以上实体的关系不能由多个文档的术语共现伪造。
        if len(latin_terms) >= 2 and not any(
            all(term.casefold() in blob for term in latin_terms)
            for blob in target_blobs_lower
        ):
            unsupported_segments.append(
                f"断言关系未由同一证据片段支持: '{claim[:60]}'"
            )
            reasons.append("unsupported_semantic_relation")

        # 2.4 提取断言中的实质概念，用于关系操作符的同句锚定与后续重合度检查。
        words = [
            w.strip() for w in jieba.cut(claim)
            if len(w.strip()) >= 2
            and w.strip().casefold() not in _COMMON_STOPWORDS
            and not re.match(r"^\d+$", w)
        ]
        concept_terms = [
            w for w in words
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{2,}", w)
        ]

        # 2.5 高风险语义操作符检查：关系/极性与关键实体/概念必须在同一证据子句共现。
        unsupported_operators = _unsupported_semantic_operators(
            claim,
            target_blobs_raw,
            latin_terms=latin_terms,
            concept_terms=concept_terms,
        )
        if unsupported_operators:
            unsupported_segments.append(
                "语义关系操作符未与断言锚点在同一证据子句中得到支持 "
                f"({', '.join(unsupported_operators)}): '{claim[:60]}'"
            )
            reasons.append("unsupported_semantic_operator")

        # 2.6 可机械判定的方向一致性：依赖/归属/比较/步骤顺序不得主客体反转。
        directional_violations = _directional_relation_violations(claim, target_blobs_raw)
        for name, pattern in (
            ("dependency_direction", _DEPENDENCY_RE),
            ("belonging_direction", _BELONGING_RE),
            ("containment_direction", _CONTAINMENT_RE),
            ("invocation_direction", _INVOCATION_RE),
        ):
            if _token_direction_reversed(claim, target_blobs_raw, pattern):
                directional_violations.append(name)
        if _comparison_token_direction_reversed(claim, target_blobs_raw):
            directional_violations.append("ordered_comparison_direction")
        directional_violations = list(dict.fromkeys(directional_violations))
        if directional_violations:
            unsupported_segments.append(
                "方向性关系与引证据不一致 "
                f"({', '.join(directional_violations)}): '{claim[:60]}'"
            )
            reasons.append("unsupported_directional_relation")

        if _order_direction_reversed(claim, target_blobs_raw):
            unsupported_segments.append(
                f"步骤顺序与引证据相反: '{claim[:60]}'"
            )
            reasons.append("unsupported_directional_relation")

        # 2.7 因果方向反转：仅在两侧语义锚点均能高置信交叉匹配时判定。
        if _causal_direction_reversed(claim, target_blobs_raw):
            unsupported_segments.append(
                f"因果方向与引证据相反: '{claim[:60]}'"
            )
            reasons.append("unsupported_causal_direction")

        # 2.8 条件范围不可被无条件化。证据仅在条件成立时支持能力，回答不得删除条件。
        if _condition_scope_erased(claim, target_blobs_raw, latin_terms, concept_terms):
            unsupported_segments.append(
                f"条件范围被扩大为无条件断言: '{claim[:60]}'"
            )
            reasons.append("unsupported_condition_scope")

        # 2.9 语义关系共现检查（Semantic Relation & Concept Overlap）
        if words:
            overlap_count = sum(1 for w in words if w.casefold() in combined_target_lower)
            overlap_ratio = overlap_count / len(words)
            if len(words) >= 3 and overlap_ratio < 0.45:
                unsupported_segments.append(
                    f"断言关系概念重合度过低 ({overlap_ratio:.0%}): '{claim[:60]}'"
                )
                reasons.append("unsupported_semantic_relation")

    is_ok = len(unsupported_segments) == 0 and len(cids_in_answer) > 0
    if not is_ok and not reasons:
        reasons.append("insufficient_grounding_overlap")

    return GroundingVerdict(
        ok=is_ok,
        reasons=list(set(reasons)),
        unsupported_segments=unsupported_segments[:5],
        valid_citation_ids=valid_cids,
        details={"total_cites": len(cids_in_answer), "unsupported_count": len(unsupported_segments)},
    )


def synthesize_grounded_fallback(
    context_docs: list[dict[str, Any]],
    question: str,
    *,
    max_bullets: int = 4,
    snippet_chars: int = 200,
) -> str:
    """当模型生成多次无法通过证据校验时，直接输出确定性的结构化证据摘要。"""
    docs = [d for d in (context_docs or []) if isinstance(d, dict)]
    if not docs:
        return NO_KNOWLEDGE_ANSWER

    bullets: list[str] = []
    seen_snippets: set[str] = set()

    for doc in docs[:max_bullets]:
        meta = doc.get("metadata") or {}
        try:
            cid = int(meta.get("citation_id"))
        except (TypeError, ValueError):
            continue
        section = str(meta.get("section_path") or meta.get("section_title") or meta.get("source") or "").strip()
        raw_content = str(doc.get("content") or "").strip()
        # 清理多余空行与标记
        cleaned = " ".join(raw_content.split())
        if len(cleaned) > snippet_chars:
            cleaned = cleaned[:snippet_chars] + "…"
        if cleaned in seen_snippets:
            continue
        seen_snippets.add(cleaned)
        label = f"**{section}**：" if section else ""
        bullets.append(f"- {label}{cleaned} [{cid}]")

    if not bullets:
        return NO_KNOWLEDGE_ANSWER

    body = "\n".join(bullets)
    return (
        f"根据知识库现有相关文档，检索到以下明确记录的事实：\n\n"
        f"{body}\n\n"
        f"（说明：以上仅为当前检索证据中可直接引用的内容；未被这些证据覆盖的细节不作补充。）"
    )
