"""Graph-assisted retrieval query rewriting (backbone anchor → helper LLM)."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from rag_knowledge.config import Config
from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.backbone_guard import (
    avoid_names_for_anchors,
    format_anchor_relation_summary,
    format_backbone_lexicon_for_rewrite,
    load_backbone_constraints,
    resolve_canonical,
    soft_match_backbone_entities,
)
from rag_knowledge.services.graph_retrieval import GraphContext
from rag_knowledge.services.query_contextualizer import RetrievalQuery

logger = logging.getLogger(__name__)

_MAX_LINKED = 4
_MAX_ALIASES = 4
_MAX_AVOID = 8
_MAX_EDGES = 12
_MAX_SECTION_PATHS = 6
_SECTION_PATH_CHARS = 80
_MAX_REWRITE_QUERIES = 3
_REWRITE_WEIGHT = 0.7
_ANCHORED_QUERY_WEIGHT = 1.1

_REWRITE_PROMPT = """你是 RAG 检索查询改写助手。你不会回答用户问题，只根据图谱摘要生成更适合检索的查询。

要求：
1. 根据用户问题与图谱摘要，输出 1～3 条检索向 query（中文或中英混合均可）。
2. 必须保留问题中的核心实体；优先使用摘要中的 canonical 名与别名。
3. 可利用一跳关系（如 requires / belongs_to）与 defined_in 章节路径补全检索词。
4. 不得把 avoid 列表中的实体当作检索目标。
5. 不得编造摘要中未出现的产品/实体名。
6. 输出严格 JSON，不要解释，不要 markdown 代码块。

用户问题：{question}

图谱摘要 JSON：
{summary_json}

输出 JSON：{{"queries":["检索查询1","检索查询2"]}}"""

_BACKBONE_ANCHOR_PROMPT = """你是产品主干实体锚定与检索改写助手。你不会回答用户问题。

任务：把用户口语映射到「产品主干词表」中的 canonical 名，并生成能命中正确产品介绍/关系证据的检索 query。

硬性规则：
1. canonical_entities / avoid / anchored_queries / relation_focus 中出现的实体名，必须来自词表 entities[].name 或其 aliases，禁止编造。
2. 问某个产品/模块是什么时：canonical 只锚该实体；anchored_queries 必须含其 canonical，并偏「介绍/定位/概述」，不要改成无关产品的步骤词。
3. 问产品关系（属于谁、依赖谁、和谁区分）时：primary_intent=product_relation，relation_focus 列出相关边端点或关系类型词。
4. avoid 填易混实体（词表不同名产品/工具），不得作为检索主体。
5. 若无法映射到词表，canonical_entities 与 anchored_queries 可为空数组。
6. 输出严格 JSON，不要解释，不要 markdown。

用户问题：{question}

软命中（可参考，可为空）：{soft_hits_json}

产品主干词表 JSON：
{lexicon_json}

输出 JSON：
{{
  "deconstruct": {{
    "primary_intent": "product_intro|product_relation|operation|comparison|other",
    "surface_terms": ["用户原词"]
  }},
  "canonical_entities": ["CanonicalName"],
  "avoid": ["易混实体"],
  "anchored_queries": ["含 Canonical 的检索句"],
  "relation_focus": ["端点或关系提示"]
}}"""


@dataclass(frozen=True)
class GraphRewriteSummary:
    """Medium graph summary for query rewriting (no evidence_text / chunk bodies)."""

    linked: tuple[dict[str, Any], ...] = ()
    avoid: tuple[str, ...] = ()
    edges: tuple[dict[str, str], ...] = ()
    section_paths: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "linked": list(self.linked),
            "avoid": list(self.avoid),
            "edges": list(self.edges),
            "section_paths": list(self.section_paths),
        }

    def is_empty(self) -> bool:
        return not self.linked and not self.edges and not self.section_paths


@dataclass(frozen=True)
class BackboneAnchorResult:
    """Structured backbone anchoring output for retrieval + answer injection."""

    primary_intent: str = "other"
    surface_terms: tuple[str, ...] = ()
    canonical_entities: tuple[str, ...] = ()
    avoid: tuple[str, ...] = ()
    anchored_queries: tuple[str, ...] = ()
    relation_focus: tuple[str, ...] = ()
    relation_summary: str = ""
    retrieval_queries: tuple[RetrievalQuery, ...] = field(default_factory=tuple)

    def is_empty(self) -> bool:
        return not self.canonical_entities and not self.anchored_queries


def build_medium_graph_summary(
    context: GraphContext,
    db: RelationalDB | None = None,
) -> GraphRewriteSummary:
    """Build a capped medium summary from an expanded graph context."""
    db = db or RelationalDB()
    linked_items: list[dict[str, Any]] = []
    avoid: list[str] = []

    for linked in context.linked_entities[:_MAX_LINKED]:
        entity = db.get_entity(linked.entity_id) or {}
        name = linked.canonical_name or entity.get("canonical_name") or entity.get("name") or ""
        aliases = [
            alias["alias"]
            for alias in db.list_aliases(linked.entity_id)
            if alias.get("review_status") == "approved" and alias.get("alias")
        ][:_MAX_ALIASES]
        linked_items.append(
            {
                "name": name,
                "aliases": aliases,
                "type": linked.entity_type or entity.get("entity_type") or "",
            }
        )
        for other_id in linked.excluded_entity_ids:
            other = db.get_entity(other_id)
            if not other:
                continue
            other_name = other.get("canonical_name") or other.get("name") or ""
            if other_name and other_name not in avoid:
                avoid.append(other_name)
            for alias in db.list_aliases(other_id):
                if alias.get("review_status") != "approved":
                    continue
                alias_text = alias.get("alias") or ""
                if alias_text and alias_text not in avoid:
                    avoid.append(alias_text)
                if len(avoid) >= _MAX_AVOID:
                    break
            if len(avoid) >= _MAX_AVOID:
                break

    relation_id_set = set(context.relation_ids)
    edges: list[dict[str, str]] = []
    section_paths: list[str] = []
    seen_edge_keys: set[tuple[str, str, str]] = set()

    relation_rows: list[dict] = []
    if relation_id_set:
        with db._get_conn() as conn:
            for rid in context.relation_ids:
                row = conn.execute(
                    """
                    SELECT r.id, r.relation_type, r.review_status,
                           s.name AS source_name, t.name AS target_name
                    FROM relations r
                    JOIN entities s ON r.source_entity_id = s.id
                    JOIN entities t ON r.target_entity_id = t.id
                    WHERE r.id = ?
                    """,
                    (rid,),
                ).fetchone()
                if row:
                    relation_rows.append(dict(row))
    else:
        for linked in context.linked_entities[:_MAX_LINKED]:
            relation_rows.extend(
                db.list_relations(entity_id=linked.entity_id, review_status="approved")
            )

    for row in relation_rows:
        if row.get("review_status") and row["review_status"] != "approved":
            continue
        if relation_id_set and row.get("id") not in relation_id_set:
            continue
        src = (row.get("source_name") or "").strip()
        tgt = (row.get("target_name") or "").strip()
        rel = (row.get("relation_type") or "").strip()
        if not src or not tgt or not rel:
            continue
        key = (src, rel, tgt)
        if key in seen_edge_keys:
            continue
        seen_edge_keys.add(key)

        if rel == "different_from":
            for name in (src, tgt):
                if name and name not in avoid and name not in {item["name"] for item in linked_items}:
                    avoid.append(name)
            continue

        if len(edges) < _MAX_EDGES:
            edges.append({"src": src, "relation_type": rel, "tgt": tgt})

        if rel == "defined_in" and tgt and len(section_paths) < _MAX_SECTION_PATHS:
            path = tgt if len(tgt) <= _SECTION_PATH_CHARS else tgt[: _SECTION_PATH_CHARS - 1] + "…"
            if path not in section_paths:
                section_paths.append(path)

    return GraphRewriteSummary(
        linked=tuple(linked_items),
        avoid=tuple(avoid[:_MAX_AVOID]),
        edges=tuple(edges[:_MAX_EDGES]),
        section_paths=tuple(section_paths[:_MAX_SECTION_PATHS]),
    )


class GraphQueryRewriter:
    """Rewrite retrieval queries using backbone lexicon and/or expanded graph summary."""

    def __init__(self, config: Config | None = None, db: RelationalDB | None = None):
        self._cfg = config or Config()
        self._db = db
        self._llm_model = self._cfg.helper_llm_model
        self._ollama_base = self._cfg.ollama_base_url
        self._timeout = 15
        self._anchor_timeout = 60

    def anchor_from_backbone(
        self,
        question: str,
        *,
        constraints: dict | None = None,
    ) -> BackboneAnchorResult:
        """Deconstruct + map oral terms onto product backbone; emit anchored retrieval queries."""
        q = (question or "").strip()
        if not q:
            return BackboneAnchorResult()

        constraints = constraints if constraints is not None else load_backbone_constraints()
        types = constraints.get("entity_type_by_name") or {}
        if not types:
            return BackboneAnchorResult()

        soft_hits = soft_match_backbone_entities(q, constraints)
        lexicon = format_backbone_lexicon_for_rewrite(constraints)
        allowed_names = set(types.keys())
        for entity in lexicon.get("entities") or []:
            for alias in entity.get("aliases") or []:
                allowed_names.add(str(alias))

        try:
            payload = self._anchor_via_llm(q, soft_hits, lexicon)
        except Exception as exc:
            logger.warning("backbone anchor LLM failed, using heuristic: %s", exc)
            payload = None

        if not payload:
            return self._anchor_heuristic(q, soft_hits, constraints)

        return self._finalize_anchor_payload(q, payload, soft_hits, constraints, allowed_names)

    def rewrite(
        self,
        question: str,
        context: GraphContext,
        *,
        summary: GraphRewriteSummary | None = None,
    ) -> list[RetrievalQuery]:
        """Legacy graph-context rewrite (requires linked entities). Prefer anchor_from_backbone."""
        q = (question or "").strip()
        if not q or not context.linked_entities:
            return []

        summary = summary or build_medium_graph_summary(context, self._db)
        if summary.is_empty():
            return []

        try:
            texts = self._rewrite_via_llm(q, summary)
        except Exception as exc:
            logger.warning("graph query rewrite LLM failed, using heuristic: %s", exc)
            texts = self._rewrite_heuristic(q, summary)

        texts = [t.strip() for t in texts if t and str(t).strip()]
        linked_names = tuple(
            item.get("name") or ""
            for item in summary.linked
            if item.get("name")
        )
        texts = self._filter_avoid(texts, summary.avoid, linked_names)
        if not texts:
            texts = self._rewrite_heuristic(q, summary)
            texts = self._filter_avoid(texts, summary.avoid, linked_names)

        return self._to_retrieval_queries(q, texts, weight=_REWRITE_WEIGHT)

    @staticmethod
    def _robust_json_loads(raw: str) -> Any | None:
        """Extract JSON from LLM output that may contain prose, code fences, or extra text."""
        text = raw.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        brace_start = text.find("{")
        brace_end = text.rfind("}")
        if brace_start != -1 and brace_end > brace_start:
            candidate = text[brace_start : brace_end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

        bracket_start = text.find("[")
        bracket_end = text.rfind("]")
        if bracket_start != -1 and bracket_end > bracket_start:
            candidate = text[bracket_start : bracket_end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

        return None

    def _anchor_via_llm(
        self,
        question: str,
        soft_hits: list[str],
        lexicon: dict,
    ) -> dict[str, Any]:
        prompt = _BACKBONE_ANCHOR_PROMPT.format(
            question=question,
            soft_hits_json=json.dumps(soft_hits, ensure_ascii=False),
            lexicon_json=json.dumps(lexicon, ensure_ascii=False),
        )
        from rag_knowledge.llm_http import chat_role

        raw = chat_role(
            self._cfg,
            "llm",
            [{"role": "user", "content": prompt}],
            temperature=0.0,
            num_predict=384,
            timeout=float(self._anchor_timeout),
            think=False,
        ).strip()
        payload = self._robust_json_loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("anchor payload is not an object")
        return payload

    def _finalize_anchor_payload(
        self,
        question: str,
        payload: dict[str, Any],
        soft_hits: list[str],
        constraints: dict,
        allowed_names: set[str],
    ) -> BackboneAnchorResult:
        deconstruct = payload.get("deconstruct") if isinstance(payload.get("deconstruct"), dict) else {}
        primary_intent = str(deconstruct.get("primary_intent") or "other").strip() or "other"
        surface_terms = self._str_list(deconstruct.get("surface_terms"))

        canonicals = [
            resolve_canonical(name, constraints)
            for name in self._str_list(payload.get("canonical_entities"))
        ]
        canonicals = [name for name in canonicals if name in (constraints.get("entity_type_by_name") or {})]
        # Soft hits win: never let the LLM replace an explicitly matched backbone entity.
        if soft_hits:
            canonicals = list(dict.fromkeys(soft_hits))
        elif not canonicals:
            canonicals = []

        avoid = [
            resolve_canonical(name, constraints)
            for name in self._str_list(payload.get("avoid"))
        ]
        avoid.extend(avoid_names_for_anchors(canonicals, constraints))
        # Drop avoids that collide with anchors; keep backbone-only names.
        types = constraints.get("entity_type_by_name") or {}
        avoid = [
            name
            for name in dict.fromkeys(avoid)
            if name in types and name not in canonicals
        ][:_MAX_AVOID]

        queries = self._str_list(payload.get("anchored_queries"))
        queries = self._filter_queries_to_allowed(queries, allowed_names, canonicals)
        queries = self._filter_avoid(queries, tuple(avoid), tuple(canonicals))
        if not queries and canonicals:
            queries = self._heuristic_anchored_queries(question, canonicals, primary_intent)

        relation_focus = self._str_list(payload.get("relation_focus"))
        relation_summary = format_anchor_relation_summary(canonicals, constraints)
        retrieval = self._to_retrieval_queries(
            question,
            queries,
            weight=_ANCHORED_QUERY_WEIGHT,
            canonical_by_alias=constraints.get("canonical_by_alias") or {},
        )
        if not retrieval and canonicals:
            # protect/dedupe may collapse LLM queries back to the original question.
            retrieval = self._to_retrieval_queries(
                question,
                self._heuristic_anchored_queries(question, canonicals, primary_intent),
                weight=_ANCHORED_QUERY_WEIGHT,
                canonical_by_alias=constraints.get("canonical_by_alias") or {},
            )

        return BackboneAnchorResult(
            primary_intent=primary_intent,
            surface_terms=tuple(surface_terms),
            canonical_entities=tuple(dict.fromkeys(canonicals)),
            avoid=tuple(avoid),
            anchored_queries=tuple(q.text for q in retrieval),
            relation_focus=tuple(relation_focus),
            relation_summary=relation_summary,
            retrieval_queries=tuple(retrieval),
        )

    def _anchor_heuristic(
        self,
        question: str,
        soft_hits: list[str],
        constraints: dict,
    ) -> BackboneAnchorResult:
        canonicals = list(soft_hits)
        if not canonicals:
            return BackboneAnchorResult()
        avoid = avoid_names_for_anchors(canonicals, constraints)[:_MAX_AVOID]
        intent = "product_relation" if any(
            tip in question for tip in ("属于", "关系", "区别", "对比", "依赖", "和谁")
        ) else "product_intro"
        queries = self._heuristic_anchored_queries(question, canonicals, intent)
        retrieval = self._to_retrieval_queries(
            question,
            queries,
            weight=_ANCHORED_QUERY_WEIGHT,
            canonical_by_alias=constraints.get("canonical_by_alias") or {},
        )
        return BackboneAnchorResult(
            primary_intent=intent,
            surface_terms=(),
            canonical_entities=tuple(canonicals),
            avoid=tuple(avoid),
            anchored_queries=tuple(q.text for q in retrieval),
            relation_focus=tuple(canonicals),
            relation_summary=format_anchor_relation_summary(canonicals, constraints),
            retrieval_queries=tuple(retrieval),
        )

    @staticmethod
    def _heuristic_anchored_queries(
        question: str,
        canonicals: list[str],
        intent: str,
    ) -> list[str]:
        texts: list[str] = []
        q_cf = question.casefold()
        for name in canonicals[:2]:
            if "product_relation" in (intent or "") or intent == "comparison":
                texts.append(f"{name} 产品关系 belongs_to")
                texts.append(f"{name} 与相关产品 区别")
            else:
                texts.append(f"{name} 产品介绍")
                # Avoid a query that is only the bare name when the question
                # already contains it — those get dropped as near-duplicates.
                if name.casefold() not in q_cf:
                    texts.append(f"{name} {question}")
                else:
                    texts.append(f"{name} 概述 定位")
        # Dedup while preserving order
        seen: set[str] = set()
        ordered: list[str] = []
        for text in texts:
            key = re.sub(r"\s+", " ", text).casefold()
            if key in seen or key == q_cf:
                continue
            seen.add(key)
            ordered.append(text)
        return ordered[:_MAX_REWRITE_QUERIES]

    @staticmethod
    def _filter_queries_to_allowed(
        texts: list[str],
        allowed_names: set[str],
        canonicals: list[str],
    ) -> list[str]:
        if not texts:
            return []
        # When anchors exist, every kept query must mention at least one anchor
        # (prevents soft-hit PipelineBuilder + LLM query about PipelineWebGL).
        if canonicals:
            kept = [
                text
                for text in texts
                if any(name.casefold() in text.casefold() for name in canonicals if name)
            ]
            return kept
        kept = []
        for text in texts:
            lower = text.casefold()
            if any(name.casefold() in lower for name in allowed_names if len(name) >= 2):
                kept.append(text)
        return kept

    def _rewrite_via_llm(self, question: str, summary: GraphRewriteSummary) -> list[str]:
        prompt = _REWRITE_PROMPT.format(
            question=question,
            summary_json=json.dumps(summary.to_dict(), ensure_ascii=False),
        )
        from rag_knowledge.llm_http import chat_role

        raw = chat_role(
            self._cfg,
            "llm",
            [{"role": "user", "content": prompt}],
            temperature=0.0,
            num_predict=256,
            timeout=float(self._timeout),
            think=False,
        ).strip()
        payload = self._robust_json_loads(raw)
        if not isinstance(payload, dict):
            return []
        queries = payload.get("queries", [])
        if not isinstance(queries, list):
            return []
        return [str(item).strip() for item in queries if item]

    @staticmethod
    def _rewrite_heuristic(question: str, summary: GraphRewriteSummary) -> list[str]:
        texts: list[str] = []
        for item in summary.linked[:2]:
            name = item.get("name") or ""
            if name and name not in question:
                texts.append(f"{question} {name}")
            elif name:
                texts.append(f"{name} {question}")
        for edge in summary.edges[:2]:
            neighbor = edge.get("tgt") or edge.get("src") or ""
            if neighbor and neighbor.casefold() not in question.casefold():
                texts.append(f"{question} {neighbor}")
                break
        for path in summary.section_paths[:1]:
            leaf = path.split(">")[-1].strip() if path else ""
            if leaf and leaf.casefold() not in question.casefold():
                texts.append(f"{question} {leaf}")
                break
        return texts[:_MAX_REWRITE_QUERIES]

    @staticmethod
    def _to_retrieval_queries(
        question: str,
        texts: list[str],
        *,
        weight: float,
        canonical_by_alias: dict[str, str] | None = None,
    ) -> list[RetrievalQuery]:
        from rag_knowledge.services.query_entity_guard import protect_query_list

        protected = protect_query_list(
            question,
            texts,
            canonical_by_alias=canonical_by_alias,
        )
        specs: list[RetrievalQuery] = []
        seen: set[str] = set()
        for text in protected:
            key = re.sub(r"\s+", " ", text).casefold()
            if not key or key in seen or key == question.casefold():
                continue
            seen.add(key)
            specs.append(RetrievalQuery(text, "graph_rewrite", weight))
            if len(specs) >= _MAX_REWRITE_QUERIES:
                break
        # If protect collapsed everything back to the original question, keep
        # canonical-bearing heuristic texts without protect (still backbone-safe).
        if not specs and texts and canonical_by_alias is not None:
            for text in texts:
                key = re.sub(r"\s+", " ", text).casefold()
                if not key or key in seen or key == question.casefold():
                    continue
                seen.add(key)
                specs.append(RetrievalQuery(text, "graph_rewrite", weight))
                if len(specs) >= _MAX_REWRITE_QUERIES:
                    break
        return specs

    @staticmethod
    def _str_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if item and str(item).strip()]

    @staticmethod
    def _filter_avoid(
        texts: list[str],
        avoid: tuple[str, ...],
        linked_names: tuple[str, ...] = (),
    ) -> list[str]:
        if not avoid:
            return texts
        avoided = [name for name in avoid if name]
        linked_cf = [name.casefold() for name in linked_names if name]
        result: list[str] = []
        for text in texts:
            lower = text.casefold()
            hits_avoid = any(name.casefold() in lower for name in avoided)
            if not hits_avoid:
                result.append(text)
                continue
            hits_linked = any(name in lower for name in linked_cf)
            if not hits_linked:
                continue
            result.append(text)
        return result


def merge_graph_rewrite_queries(
    base_queries: list[RetrievalQuery],
    rewrite_queries: list[RetrievalQuery],
) -> list[RetrievalQuery]:
    """Append graph rewrite queries after base ones, de-duplicating by normalized text."""
    seen: set[str] = set()
    merged: list[RetrievalQuery] = []
    for query in [*base_queries, *rewrite_queries]:
        text = (query.text or "").strip()
        if not text:
            continue
        key = re.sub(r"\s+", " ", text).casefold()
        if key in seen:
            continue
        seen.add(key)
        merged.append(query)
    return merged
