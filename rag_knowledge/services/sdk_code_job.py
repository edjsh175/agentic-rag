"""J3 secondary-development (SDK code) job detection and gates (PRD V1.3+ Phase 0/1).

Job is a gate over QueryPlanner / backbone intents — not a third query writer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

# D2 — retrieval positive anchors (Phase 0)
J3_WHITELIST: frozenset[str] = frozenset({"StampWebRTC", "StampWebGL"})

# D3 — forbidden anchors for J3 (unless user explicitly names / forces them)
J3_BLOCKLIST: frozenset[str] = frozenset({
    "PipelineBuilder",
    "PipelineWebGL",
    "PipelineWebRTC",
})

# Clarification sentinels (not backbone forced-canonicals)
COM_SENTINEL = "COM"
EXPLORER_OPS_SENTINEL = "__explorer_ops__"

J3_PRIMARY_INTENT = "sdk_code"
GRAPH_REWRITE_POLICY_DROP = "drop"
GRAPH_REWRITE_POLICY_KEEP = "keep"

# Option source tags (FR-0b / FR-7)
OPTION_SOURCE_BACKBONE = "backbone_seed"
OPTION_SOURCE_TASK_EXIT = "task_exit"
OPTION_SOURCE_ROLLBACK = "rollback_static"

# Aux seeds: may appear on the card but must not be Hybrid retrieval canonicals.
J3_AUX_OPTION_NAMES: frozenset[str] = frozenset({"SDK", "二次开发与集成层"})

# J3 wide terms — isolated from J2「管线 / pipeline」family expansion.
_J3_WIDE_TERMS: tuple[str, ...] = (
    "二次开发",
    "接口开发",
    "接口说明书",
    "StampUtil",
    "SDK",
    "写代码",
    "写一段",
    "通过代码",
    "用代码",
    "代码示例",
    "示例代码",
    "接口调用",
    "WebRTC",
    "WebGL",
)

_J2_STAGE_WORDS: tuple[str, ...] = (
    "工程设置",
    "数据设置",
    "参数设置",
    "参数配置",
    "编译级别",
    "数据编译",
)

# High-confidence write-code intent (PRD §3.4) — style words alone must NOT force J3.
_J3_ACTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.I)
    for p in (
        r"写代码",
        r"写一段",
        r"通过代码",
        r"用代码",
        r"代码示例",
        r"示例代码",
        r"二次开发",
        r"接口调用",
        r"StampUtil",
        r"接口说明书",
    )
)

_J2_PROTECT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.I)
    for p in (
        r"PipelineBuilder",
        r"新建工程",
        r"工程设置",
        r"数据编译",
        r"编译级别",
        r"数据设置",
    )
)


@dataclass(frozen=True)
class JobDecision:
    """Resolved job gate for one turn."""

    job: str  # j1 | j2 | j3 | other
    subject_clear: bool
    needs_j3_clarify: bool
    canonical_hint: str | None = None
    reason: str = ""


# Surface names that never uniquely bind a retrieval canonical (FR-1).
_FAMILY_SURFACE_CANONICALS: frozenset[str] = frozenset({
    "WebGL",
    "WebRTC",
    "SDK",
    "Pipeline",
    "COM",
    "二次开发与集成层",
})


@dataclass(frozen=True)
class AnchorBinding:
    """Single adjudicator: is the legal retrieval canonical uniquely bound?"""

    bound: bool
    canonical: str | None
    legal: bool
    reason: str
    decision: JobDecision

    @property
    def show_j3_card(self) -> bool:
        if self.decision.job != "j3":
            return False
        if not self.legal:
            return True
        return self.decision.needs_j3_clarify

    @property
    def skip_generic_clarify(self) -> bool:
        return self.bound and self.legal


def _fold(text: str) -> str:
    return re.sub(r"[\s_\-]+", "", (text or "")).casefold()


def has_j3_action_intent(question: str) -> bool:
    q = (question or "").strip()
    if not q:
        return False
    return any(p.search(q) for p in _J3_ACTION_PATTERNS)


def has_j2_protect(question: str) -> bool:
    q = (question or "").strip()
    if not q:
        return False
    if not any(p.search(q) for p in _J2_PROTECT_PATTERNS):
        return False
    # Explicit write-code / StampUtil still wins as J3.
    if re.search(r"(?i)(写代码|写一段|通过代码|用代码|代码示例|StampUtil)", q):
        return False
    return True


def named_j3_product(question: str) -> str | None:
    """Return D2 product if question explicitly names it (or clear alias)."""
    q = question or ""
    fold = _fold(q)
    # Longer / more specific aliases first — avoid bare webrtc/webgl if possible.
    webrtc_keys = (
        "stampwebrtc",
        "stampgiswebrtc",
        "stampgis平台webrtc",
        "webrtc接口",
        "stamp webrtc",
    )
    webgl_keys = (
        "stampwebgl",
        "stampgiswebgl",
        "stampgis平台webgl",
        "webgl接口",
        "stamp webgl",
    )
    for key in webrtc_keys:
        if _fold(key) in fold:
            return "StampWebRTC"
    for key in webgl_keys:
        if _fold(key) in fold:
            return "StampWebGL"
    for name in ("StampWebRTC", "StampWebGL"):
        if _fold(name) in fold:
            return name
    return None


def is_j3_whitelist(name: str | None) -> bool:
    return bool(name) and str(name).strip() in J3_WHITELIST


def is_j3_blocklisted(name: str | None) -> bool:
    raw = (name or "").strip()
    if not raw:
        return False
    if raw in J3_BLOCKLIST:
        return True
    # Same-family Pipeline* product/tool intros
    if raw.startswith("Pipeline") and raw not in J3_WHITELIST:
        return True
    return False


def is_j3_aux_selection(name: str | None) -> bool:
    raw = (name or "").strip()
    return raw in J3_AUX_OPTION_NAMES


def is_com_selection(name: str | None) -> bool:
    raw = (name or "").strip()
    return raw.casefold() in {"com", "ax", "lib_ax", "libax"} or raw == COM_SENTINEL


def is_explorer_selection(name: str | None) -> bool:
    raw = (name or "").strip()
    if raw == EXPLORER_OPS_SENTINEL:
        return True
    fold = _fold(raw)
    return fold in {"stampexplorer", "explorer", "桌面端操作"}


def resolve_job(
    question: str,
    *,
    entity_name: str | None = None,
) -> JobDecision:
    """Rule-first job gate (D1)."""
    q = (question or "").strip()
    entity = (entity_name or "").strip() or None

    if is_explorer_selection(entity):
        return JobDecision(
            job="j2",
            subject_clear=True,
            needs_j3_clarify=False,
            canonical_hint=None,
            reason="explorer_ops_selected",
        )
    if is_com_selection(entity) and has_j3_action_intent(q):
        return JobDecision(
            job="j3",
            subject_clear=True,
            needs_j3_clarify=False,
            canonical_hint=COM_SENTINEL,
            reason="com_selected_phase0_reject",
        )
    # SDK / 层名：可作选项，不得当检索正锚 → 继续反问产品线
    if is_j3_aux_selection(entity) and has_j3_action_intent(q):
        return JobDecision(
            job="j3",
            subject_clear=False,
            needs_j3_clarify=True,
            canonical_hint=None,
            reason="j3_aux_needs_product",
        )
    if is_j3_whitelist(entity):
        return JobDecision(
            job="j3",
            subject_clear=True,
            needs_j3_clarify=False,
            canonical_hint=entity,
            reason="entity_whitelist",
        )

    if has_j2_protect(q):
        return JobDecision(
            job="j2",
            subject_clear=True,
            needs_j3_clarify=False,
            reason="j2_protect",
        )

    named = named_j3_product(q)
    action = has_j3_action_intent(q)
    if action:
        # StampUtil alone still needs product-line clarify (§十 default).
        clear = bool(named) or is_j3_whitelist(entity)
        return JobDecision(
            job="j3",
            subject_clear=clear,
            needs_j3_clarify=not clear,
            canonical_hint=named,
            reason="j3_named_product" if named else "j3_action",
        )

    return JobDecision(job="other", subject_clear=True, needs_j3_clarify=False, reason="none")


def map_clarification_text(text: str | None) -> str | None:
    """Map a card label / custom input to a bound entity or sentinel. None = unmapped."""
    raw = (text or "").strip()
    if not raw:
        return None
    if is_j3_whitelist(raw):
        return raw.strip()
    if is_com_selection(raw) or re.search(r"(?i)\bCOM\b|Ax", raw):
        return COM_SENTINEL
    if is_explorer_selection(raw) or "桌面端" in raw or re.search(r"(?i)Explorer", raw):
        return EXPLORER_OPS_SENTINEL
    if is_j3_aux_selection(raw):
        return raw.strip()
    named = named_j3_product(raw)
    if named:
        return named
    from rag_knowledge.services.query_surface import contains_term

    for name in ("PipelineBuilder", "PipelineWebGL", "PipelineWebRTC", "StampWebRTC", "StampWebGL"):
        if contains_term(raw, name):
            return name
    return None


def named_specific_canonical(question: str, constraints: dict | None = None) -> str | None:
    """Unique Product/Tool official name in the question, excluding family surface terms."""
    from rag_knowledge.services.query_surface import contains_term

    q = (question or "").strip()
    if not q:
        return None
    hits: list[str] = []
    named = named_j3_product(q)
    if named:
        hits.append(named)
    for name in ("PipelineBuilder", "PipelineWebGL", "PipelineWebRTC"):
        if contains_term(q, name):
            hits.append(name)
    types = (constraints or {}).get("entity_type_by_name") or {}
    skip = set(_FAMILY_SURFACE_CANONICALS) | set(J3_AUX_OPTION_NAMES) | set(J3_WHITELIST)
    extra: list[str] = []
    for name, typ in types.items():
        if typ not in {"Product", "Tool"}:
            continue
        if name in skip or name.startswith("Pipeline"):
            continue
        if contains_term(q, name):
            extra.append(name)
    extra.sort(key=len, reverse=True)
    hits.extend(extra)
    uniq: list[str] = []
    seen: set[str] = set()
    for name in hits:
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(name)
    if len(uniq) == 1:
        return uniq[0]
    return None


def resolve_anchor_binding(
    question: str,
    *,
    entity_name: str | None = None,
    clarification_selected: str | None = None,
    constraints: dict | None = None,
) -> AnchorBinding:
    """FR-1: one adjudicator for whether clarification may run."""
    mapped = (entity_name or "").strip() or map_clarification_text(clarification_selected)
    if is_j3_aux_selection(mapped):
        decision = resolve_job(question, entity_name=mapped)
        return AnchorBinding(
            bound=False,
            canonical=None,
            legal=True,
            reason=decision.reason or "j3_aux_needs_product",
            decision=decision,
        )

    decision = resolve_job(question, entity_name=mapped or None)

    if mapped:
        if is_j3_blocklisted(mapped) and has_j3_action_intent(question):
            return AnchorBinding(
                bound=True,
                canonical=mapped,
                legal=False,
                reason="j3_illegal_anchor",
                decision=JobDecision(
                    job="j3",
                    subject_clear=False,
                    needs_j3_clarify=True,
                    canonical_hint=None,
                    reason="j3_illegal_anchor",
                ),
            )
        canonical = (
            COM_SENTINEL if is_com_selection(mapped)
            else None if is_explorer_selection(mapped)
            else mapped
        )
        return AnchorBinding(
            bound=True,
            canonical=canonical,
            legal=True,
            reason=decision.reason or "entity_selected",
            decision=decision,
        )

    specific = named_specific_canonical(question, constraints)
    if decision.job == "j3":
        if specific and is_j3_blocklisted(specific):
            return AnchorBinding(
                bound=True,
                canonical=specific,
                legal=False,
                reason="j3_illegal_anchor",
                decision=JobDecision(
                    job="j3",
                    subject_clear=False,
                    needs_j3_clarify=True,
                    canonical_hint=None,
                    reason="j3_illegal_anchor",
                ),
            )
        if decision.needs_j3_clarify:
            return AnchorBinding(
                bound=False,
                canonical=None,
                legal=True,
                reason=decision.reason or "j3_action",
                decision=decision,
            )
        return AnchorBinding(
            bound=True,
            canonical=decision.canonical_hint,
            legal=True,
            reason=decision.reason or "j3_named_product",
            decision=decision,
        )

    if specific:
        return AnchorBinding(
            bound=True,
            canonical=specific,
            legal=True,
            reason="named_specific_canonical",
            decision=decision,
        )
    return AnchorBinding(
        bound=False,
        canonical=None,
        legal=True,
        reason=decision.reason or "unbound",
        decision=decision,
    )


def j3_subgraph_universe(constraints: dict) -> set[str]:
    """Candidate canonicals for J3 clarify cards (secondary-dev subgraph)."""
    types = constraints.get("entity_type_by_name") or {}
    docs = constraints.get("doc_category_by_name") or {}
    belongs = constraints.get("belongs_to") or {}
    universe: set[str] = set(J3_WHITELIST)
    for name in ("SDK", "COM", "二次开发与集成层"):
        if name in types:
            universe.add(name)
    for name, cat in docs.items():
        if cat == "二次开发与集成层" and name in types:
            universe.add(name)
    for src, targets in belongs.items():
        if "二次开发与集成层" in (targets or ()) and src in types:
            universe.add(src)
    return {name for name in universe if name in types and not is_j3_blocklisted(name)}


def _j3_wide_triggered(question: str) -> bool:
    q = question or ""
    if has_j3_action_intent(q):
        return True
    fold = _fold(q)
    for term in _J3_WIDE_TERMS:
        if _fold(term) in fold or term in q:
            return True
    return False


def collect_j3_clarify_seed_names(question: str, constraints: dict) -> list[str]:
    """问句 × 二次开发子图 → seeds；硬丢 D3；家族扩展不出子图。"""
    from rag_knowledge.services.backbone_guard import soft_match_backbone_entities

    types = constraints.get("entity_type_by_name") or {}
    universe = j3_subgraph_universe(constraints)
    names: list[str] = []
    seen: set[str] = set()

    def _add(name: str, *, require_typed: bool = True) -> None:
        raw = (name or "").strip()
        if not raw:
            return
        if is_j3_blocklisted(raw):
            return
        if require_typed and raw not in types:
            return
        if raw not in universe and raw not in J3_WHITELIST:
            return
        if raw in J3_WHITELIST:
            pass  # D2 always allowed
        elif raw not in universe:
            return
        key = raw.casefold()
        if key in seen:
            return
        seen.add(key)
        names.append(raw)

    soft_hits = soft_match_backbone_entities(question, constraints, max_hits=50)
    for hit in soft_hits:
        _add(hit)

    # Write-code / SDK 宽词：至少给出 D2 正锚候选（验收 G0-C2）
    if _j3_wide_triggered(question):
        for name in ("StampWebRTC", "StampWebGL"):
            _add(name, require_typed=False)
        for name in ("SDK", "COM", "二次开发与集成层"):
            _add(name)

    # different_from 兄弟仅保留子图内且非 D3（StampWebGL↔PipelineWebGL → Pipeline 被丢）
    from rag_knowledge.services.backbone_guard import avoid_names_for_anchors

    for sibling in avoid_names_for_anchors(names, constraints):
        _add(sibling)

    # Prefer D2 first for stable card order (compare folded keys, not official names).
    preferred = ["StampWebRTC", "StampWebGL", "COM"]
    folded_present = {_fold(n): n for n in names}
    ordered: list[str] = []
    used: set[str] = set()
    for item in preferred:
        hit = folded_present.get(_fold(item))
        if hit and _fold(hit) not in used:
            ordered.append(hit)
            used.add(_fold(hit))
    ordered.extend(n for n in names if _fold(n) not in used)
    return ordered


def j3_clarify_options() -> list[dict]:
    """Phase 0 static menu — rollback only (source=rollback_static)."""
    return [
        {
            "label": "StampWebRTC 二次开发（StampUtil）",
            "entity_name": "StampWebRTC",
            "doc_category": None,
            "source": OPTION_SOURCE_ROLLBACK,
        },
        {
            "label": "StampWebGL 二次开发（StampUtil）",
            "entity_name": "StampWebGL",
            "doc_category": None,
            "source": OPTION_SOURCE_ROLLBACK,
        },
        {
            "label": "COM / Ax 接口（首期仅 StampUtil）",
            "entity_name": COM_SENTINEL,
            "doc_category": None,
            "source": OPTION_SOURCE_ROLLBACK,
        },
        {
            "label": "Explorer / 桌面端操作（不是写代码）",
            "entity_name": EXPLORER_OPS_SENTINEL,
            "doc_category": None,
            "source": OPTION_SOURCE_ROLLBACK,
        },
    ]


def _j3_option_label(canonical: str) -> str:
    if canonical == "StampWebRTC":
        return "StampWebRTC 二次开发（StampUtil）"
    if canonical == "StampWebGL":
        return "StampWebGL 二次开发（StampUtil）"
    if canonical == "COM" or canonical == COM_SENTINEL:
        return "COM / Ax 接口（首期仅 StampUtil）"
    if canonical == "SDK":
        return "SDK（StampUtil）— 请再选产品线"
    if canonical == "二次开发与集成层":
        return "二次开发与集成层 — 请再选产品线"
    return canonical


def build_j3_clarify_options(
    question: str,
    constraints: dict,
    *,
    include_explorer: bool = True,
) -> list[dict]:
    """FR-4: submitable J3 options are D2 + COM + optional Explorer only."""
    _ = constraints
    options: list[dict] = []
    for name in ("StampWebRTC", "StampWebGL"):
        options.append(
            {
                "label": _j3_option_label(name),
                "entity_name": name,
                "doc_category": None,
                "source": OPTION_SOURCE_BACKBONE,
            }
        )
    options.append(
        {
            "label": _j3_option_label(COM_SENTINEL),
            "entity_name": COM_SENTINEL,
            "doc_category": None,
            "source": OPTION_SOURCE_BACKBONE,
        }
    )
    if include_explorer and has_j3_action_intent(question):
        options.append(
            {
                "label": "Explorer / 桌面端操作（不是写代码）",
                "entity_name": EXPLORER_OPS_SENTINEL,
                "doc_category": None,
                "source": OPTION_SOURCE_TASK_EXIT,
            }
        )
    return options


COM_PHASE0_REJECT_ANSWER = (
    "首期二次开发代码示例仅覆盖 StampUtil（StampWebRTC / StampWebGL 接口说明书）。"
    "COM / Ax 接口请另行查阅对应手册，或选择 WebRTC / WebGL 二次开发选项后重试。"
)


def strip_j2_stage_queries(queries: Iterable) -> list:
    """Remove planner_stage queries that inject Pipeline procedure words."""
    out = []
    for q in queries or []:
        kind = getattr(q, "kind", "") or ""
        text = str(getattr(q, "text", q) or "")
        if kind == "planner_stage" and any(w in text for w in _J2_STAGE_WORDS):
            continue
        if kind == "planner_stage" and any(
            w in text for w in ("编译", "发布", "部署", "启动", "验证", "注意事项", "操作步骤", "配置流程")
        ):
            # Broader J2 procedure stage table — drop all planner_stage under J3.
            continue
        if kind == "planner_stage":
            continue
        out.append(q)
    return out


def drop_pipeline_graph_rewrites(queries: Iterable) -> tuple[list, str]:
    """Drop graph_rewrite queries that mention Pipeline* (A6 / FR-3b default drop)."""
    kept = []
    dropped = False
    for q in queries or []:
        kind = getattr(q, "kind", "") or ""
        text = str(getattr(q, "text", q) or "")
        if kind == "graph_rewrite" and re.search(r"(?i)Pipeline", text):
            dropped = True
            continue
        kept.append(q)
    policy = GRAPH_REWRITE_POLICY_DROP if dropped else GRAPH_REWRITE_POLICY_KEEP
    return kept, policy


# Map-style / SDK evidence queries (broader than strict J3 action — used for bare Hybrid).
_SDK_STYLE_RETRIEVAL_RE = re.compile(
    r"二次开发|StampUtil|接口说明书|折线|多边形|线宽|线颜色|填充|覆盖物|透明|"
    r"polyline|polygon|linecolor|linewidth|fillcolor|绘制|Vue\s*3?",
    re.I,
)
_COOKBOOK_NAME_RE = re.compile(r"^0[1-5]-(?:webrtc|webgl)-", re.I)


def is_sdk_style_retrieval_query(question: str) -> bool:
    """True when bare Hybrid should prefer 接口说明书 over Cookbook/用户手册."""
    return bool(_SDK_STYLE_RETRIEVAL_RE.search(question or ""))


def build_sdk_manual_bm25_hint(question: str) -> str | None:
    """Single BM25 hint that pulls api_doc manuals into the Hybrid candidate pool.

    Does not require a resolved canonical (L1 / bare Hybrid path). Cookbook may still
    match these terms; callers should prefer api_doc via prefer_sdk_manual_docs.
    """
    if not is_sdk_style_retrieval_query(question):
        return None
    q = question or ""
    q_cf = q.casefold()
    parts: list[str] = ["接口说明书", "StampUtil"]
    if any(
        h in q_cf
        for h in ("折线", "线颜色", "线宽", "linecolor", "linewidth", "创建折线", "polyline", "画线")
    ):
        parts.extend(["createElementLineParams", "linecolor", "linewidth", "折线"])
    if any(
        h in q_cf
        for h in ("多边形", "填充", "fillcolor", "创建多边形", "polygon", "画面")
    ):
        parts.extend(["createElementPolygonParams", "fillcolor", "多边形"])
    if "透明" in q or "transparent" in q_cf or "transparency" in q_cf:
        parts.extend(["setLayerTransparent", "透明度"])
    if re.search(r"(?i)StampUtil|Vue|引入|初始化", q):
        parts.extend(["引入", "初始化", "环境准备"])
    if "覆盖物" in q:
        parts.extend(["覆盖物颜色", "颜色"])
    seen: set[str] = set()
    ordered: list[str] = []
    for term in parts:
        key = term.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(term)
    return " ".join(ordered)


def sdk_evidence_tier(doc) -> int:
    """0=接口说明书/api_doc, 1=other, 2=Cookbook对照轨（治本优先时后置）."""
    meta = getattr(doc, "metadata", None) or {}
    src = str(meta.get("source") or meta.get("file_name") or "")
    profile = str(meta.get("document_profile") or "")
    if profile == "api_doc" or "接口说明书" in src:
        return 0
    src_norm = src.replace("\\", "/")
    name = src_norm.rsplit("/", 1)[-1]
    if "sdk_cookbook" in src_norm.casefold() or _COOKBOOK_NAME_RE.match(name):
        return 2
    return 1


def prefer_sdk_manual_docs(docs: list) -> list:
    """Stable reorder: api_doc/接口说明书 first, Cookbook last; preserve RRF within tier."""
    from langchain_core.documents import Document

    decorated: list[tuple[int, float, int, object]] = []
    for index, doc in enumerate(docs or []):
        meta = getattr(doc, "metadata", None) or {}
        score = 0.0
        for key in ("rrf_score", "score", "similarity_score"):
            if key not in meta:
                continue
            try:
                score = float(meta[key])
                break
            except (TypeError, ValueError):
                continue
        decorated.append((sdk_evidence_tier(doc), -score, index, doc))
    decorated.sort()
    out: list = []
    for tier, _neg, _index, doc in decorated:
        meta = dict(getattr(doc, "metadata", None) or {})
        meta["sdk_evidence_tier"] = tier
        out.append(
            Document(
                page_content=getattr(doc, "page_content", "") or "",
                metadata=meta,
            )
        )
    return out


def build_j3_retrieval_texts(question: str, canonical: str) -> list[str]:
    """J3 rewrite texts after positive anchor (FR-2b / FR-3 / Phase 1). No intro / stage words."""
    q = (question or "").strip()
    q_cf = q.casefold()
    texts: list[str] = [
        f"{canonical} StampUtil 接口说明书",
        f"{canonical} 二次开发 接口调用",
    ]
    if any(h in q_cf for h in ("折线", "线颜色", "线宽", "linecolor", "linewidth", "创建折线", "polyline")):
        texts.append(f"{canonical} StampUtil 创建折线 linecolor linewidth")
        texts.append("StampUtil createElementLineParams linecolor linewidth")
    elif any(h in q_cf for h in ("多边形", "填充", "fillcolor", "创建多边形", "polygon")):
        texts.append(f"{canonical} StampUtil 创建多边形 fillcolor")
        texts.append("StampUtil createElementPolygonParams fillcolor")
    elif any(h in q_cf for h in ("透明", "transparency", "transparent")):
        texts.append(f"{canonical} StampUtil 透明度 setLayerTransparent")
    elif re.search(r"(?i)StampUtil|引入|初始化|Vue", q):
        texts.append(f"{canonical} StampUtil 引入 初始化 Vue")
        texts.append(f"{canonical} 接口说明书 环境准备")
    else:
        # Weak lexicon expansion — keep product + StampUtil, truncate original question.
        compact = re.sub(r"\s+", " ", q)[:48]
        texts.append(f"{canonical} StampUtil {compact}")

    # Dedup
    seen: set[str] = set()
    ordered: list[str] = []
    for t in texts:
        key = re.sub(r"\s+", " ", t).casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(t)
    return ordered[:3]


def should_skip_backbone_guess(decision: JobDecision) -> bool:
    """D10: J3 without clear subject must not LLM-guess Pipeline*."""
    return decision.job == "j3" and decision.needs_j3_clarify


def should_disable_graph_fusion(decision: JobDecision, canonicals: Iterable[str]) -> bool:
    """Close graph fuse when J3 and canonical not in whitelist (FR-3b)."""
    if decision.job != "j3":
        return False
    names = [str(c).strip() for c in (canonicals or ()) if str(c).strip()]
    if not names:
        return True
    return not any(is_j3_whitelist(n) for n in names)
