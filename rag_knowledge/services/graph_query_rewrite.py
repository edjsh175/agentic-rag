"""Graph-assisted retrieval query rewriting (medium summary → helper LLM)."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from rag_knowledge.config import Config
from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.graph_retrieval import GraphContext, LinkedEntity
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

    # Prefer relations already walked by expander; fall back to listing from linked entities.
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
    """Rewrite retrieval queries using a medium graph summary and helper LLM."""

    def __init__(self, config: Config | None = None, db: RelationalDB | None = None):
        self._cfg = config or Config()
        self._db = db
        self._llm_model = self._cfg.helper_llm_model
        self._ollama_base = self._cfg.ollama_base_url
        self._timeout = 15

    def rewrite(
        self,
        question: str,
        context: GraphContext,
        *,
        summary: GraphRewriteSummary | None = None,
    ) -> list[RetrievalQuery]:
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

        from rag_knowledge.services.query_entity_guard import protect_query_list

        protected = protect_query_list(q, texts)
        specs: list[RetrievalQuery] = []
        seen: set[str] = set()
        for text in protected:
            key = re.sub(r"\s+", " ", text).casefold()
            if not key or key in seen or key == q.casefold():
                continue
            seen.add(key)
            specs.append(RetrievalQuery(text, "graph_rewrite", _REWRITE_WEIGHT))
            if len(specs) >= _MAX_REWRITE_QUERIES:
                break
        return specs

    def _rewrite_via_llm(self, question: str, summary: GraphRewriteSummary) -> list[str]:
        prompt = _REWRITE_PROMPT.format(
            question=question,
            summary_json=json.dumps(summary.to_dict(), ensure_ascii=False),
        )
        resp = httpx.post(
            f"{self._ollama_base}/api/chat",
            json={
                "model": self._llm_model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {
                    "temperature": 0.0,
                    "num_predict": 256,
                    "top_k": 10,
                    "thinking": False,
                },
            },
            timeout=self._timeout,
        )
        resp.raise_for_status()
        raw = resp.json().get("message", {}).get("content", "").strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
        payload = json.loads(cleaned)
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
            # Drop if it only pushes an avoid sibling without anchoring a linked entity.
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
