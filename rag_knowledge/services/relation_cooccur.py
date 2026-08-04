"""Co-occurrence relation proposals — formal endpoints share a chunk but no edge yet.

Deterministic first: if schema admits exactly one directed type → propose it.
Optional LLM: choose among multiple legal (src, type, tgt) options, or reject.
Results stage into extraction_candidates (pending); never write formal DB directly.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import combinations
from typing import Any, Protocol

from rag_knowledge.models.graph_schema import normalize_entity_name, validate_relation
from rag_knowledge.services.relation_recovery import (
    RecoveryItem,
    RecoveryPlan,
    is_generic_entity_name,
)

# Business / capability edges worth backfilling from co-occurrence.
DEFAULT_COOCCUR_RELATION_TYPES = (
    "has_step",
    "has_procedure",
    "runs_command",
    "belongs_to",
    "uses_config",
    "configured_by",
    "requires",
    "depends_on",
    "solved_by",
    "causes",
)

# Skip pure document scaffolding endpoints.
COOCCUR_ENTITY_TYPES = frozenset(
    {
        "Tool",
        "Service",
        "Module",
        "FunctionArea",
        "Feature",
        "Constraint",
        "Procedure",
        "Step",
        "Command",
        "ConfigItem",
        "Error",
        "Solution",
        "EnvironmentComponent",
        "DataTable",
        "Field",
    }
)

DEFAULT_MAX_ENTITIES_PER_CHUNK = 8
DEFAULT_MAX_PAIRS_PER_CHUNK = 12
DEFAULT_MIN_CONF = 0.80


@dataclass(frozen=True)
class DirectedOption:
    source_name: str
    relation_type: str
    target_name: str
    source_type: str
    target_type: str


class CooccurRelationArbiterProtocol(Protocol):
    def arbitrate(
        self,
        *,
        name_a: str,
        type_a: str,
        name_b: str,
        type_b: str,
        evidence_text: str,
        options: list[DirectedOption],
    ) -> tuple[str, DirectedOption | None, float]:
        """Return (verdict, chosen_or_None, confidence). verdict: accept|reject|unsure."""
        ...


def legal_directed_options(
    name_a: str,
    type_a: str,
    name_b: str,
    type_b: str,
    relation_types: list[str],
) -> list[DirectedOption]:
    out: list[DirectedOption] = []
    seen: set[tuple[str, str, str]] = set()
    for rt in relation_types:
        for src_n, src_t, tgt_n, tgt_t in (
            (name_a, type_a, name_b, type_b),
            (name_b, type_b, name_a, type_a),
        ):
            ok, _ = validate_relation(src_t, rt, tgt_t)
            if not ok:
                continue
            key = (src_n, rt, tgt_n)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                DirectedOption(
                    source_name=src_n,
                    relation_type=rt,
                    target_name=tgt_n,
                    source_type=src_t,
                    target_type=tgt_t,
                )
            )
    return out


def _fingerprint(payload: dict) -> str:
    body = {
        "k": "relation",
        "rt": payload.get("relation_type"),
        "s": payload.get("source_name"),
        "t": payload.get("target_name"),
        "src": "cooccur",
    }
    digest = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()[:32]
    return f"cooccur:{digest}"


class CooccurRelationService:
    """Single authority for co-occurrence long-tail relation proposals."""

    def __init__(
        self,
        db,
        *,
        arbiter: CooccurRelationArbiterProtocol | None = None,
    ):
        self.db = db
        self.arbiter = arbiter

    def plan(
        self,
        *,
        relation_types: list[str] | None = None,
        chunk_ids: list[str] | None = None,
        include_llm: bool = False,
        min_confidence: float = DEFAULT_MIN_CONF,
        max_entities_per_chunk: int = DEFAULT_MAX_ENTITIES_PER_CHUNK,
        max_pairs_per_chunk: int = DEFAULT_MAX_PAIRS_PER_CHUNK,
        limit_chunks: int = 0,
    ) -> RecoveryPlan:
        rel_types = list(relation_types or DEFAULT_COOCCUR_RELATION_TYPES)
        skip: Counter = Counter()
        name_type, name_id = self._load_entities()
        existing = self._load_existing_entity_pairs()
        by_chunk = self._load_chunk_entities(chunk_ids)

        chunk_keys = sorted(by_chunk.keys())
        if limit_chunks and limit_chunks > 0:
            chunk_keys = chunk_keys[: int(limit_chunks)]

        rel_items: list[RecoveryItem] = []
        seen_rel: set[tuple[str, str, str]] = set()

        for chunk_id in chunk_keys:
            ents = by_chunk[chunk_id]
            # Deduplicate by normalized name within chunk.
            uniq: dict[str, dict] = {}
            for row in ents:
                name = normalize_entity_name(row["name"])
                et = str(row["entity_type"] or "")
                if not name or et not in COOCCUR_ENTITY_TYPES:
                    skip["entity_type_filtered"] += 1
                    continue
                if is_generic_entity_name(name):
                    skip["entity_generic"] += 1
                    continue
                if name not in name_type:
                    skip["entity_not_formal"] += 1
                    continue
                if name_type[name] != et:
                    # Prefer formal DB type.
                    et = name_type[name]
                uniq[name] = {
                    "name": name,
                    "entity_type": et,
                    "entity_id": name_id.get(name) or row.get("entity_id") or "",
                    "evidence_text": str(row.get("evidence_text") or ""),
                }
            names = sorted(uniq.keys())
            if len(names) < 2:
                skip["chunk_too_few"] += 1
                continue
            if len(names) > max_entities_per_chunk:
                skip["chunk_entity_cap"] += 1
                names = names[:max_entities_per_chunk]

            pair_budget = max_pairs_per_chunk
            for a, b in combinations(names, 2):
                if pair_budget <= 0:
                    skip["pair_budget"] += 1
                    break
                ea, eb = uniq[a], uniq[b]
                id_a, id_b = ea["entity_id"], eb["entity_id"]
                if id_a and id_b:
                    pair_key = tuple(sorted((id_a, id_b)))
                    if pair_key in existing:
                        skip["pair_already_linked"] += 1
                        continue

                options = legal_directed_options(
                    ea["name"], ea["entity_type"], eb["name"], eb["entity_type"], rel_types
                )
                if not options:
                    skip["no_legal_option"] += 1
                    continue

                evidence = self._merge_evidence(
                    ea["evidence_text"], eb["evidence_text"], ea["name"], eb["name"]
                )
                if not evidence:
                    skip["no_evidence"] += 1
                    continue

                chosen: DirectedOption | None = None
                used_llm = False
                conf = 1.0
                method = "schema_unique"

                if len(options) == 1:
                    chosen = options[0]
                elif include_llm and self.arbiter is not None:
                    verdict, pick, conf = self.arbiter.arbitrate(
                        name_a=ea["name"],
                        type_a=ea["entity_type"],
                        name_b=eb["name"],
                        type_b=eb["entity_type"],
                        evidence_text=evidence,
                        options=options,
                    )
                    used_llm = True
                    method = "llm"
                    if verdict != "accept" or pick is None:
                        skip[f"llm_{verdict or 'reject'}"] += 1
                        continue
                    if conf < min_confidence:
                        skip["llm_low_conf"] += 1
                        continue
                    chosen = pick
                else:
                    skip["multi_option_needs_llm"] += 1
                    continue

                assert chosen is not None
                key = (chosen.source_name, chosen.relation_type, chosen.target_name)
                if key in seen_rel:
                    skip["rel_dup"] += 1
                    continue
                # Also skip if identical directed edge already in formal graph.
                if self._directed_exists(key[0], key[1], key[2], name_id):
                    skip["rel_exists"] += 1
                    continue

                seen_rel.add(key)
                pair_budget -= 1
                payload = {
                    "source_name": chosen.source_name,
                    "relation_type": chosen.relation_type,
                    "target_name": chosen.target_name,
                    "confidence": float(conf),
                    "evidence_text": evidence,
                    "evidences": [
                        {"source_chunk_id": chunk_id, "evidence_text": evidence}
                    ],
                    "source_chunk_id": chunk_id,
                    "created_by": "cooccur:schema" if not used_llm else "cooccur:llm",
                    "properties": {
                        "created_by": "cooccur:schema" if not used_llm else "cooccur:llm",
                        "cooccur_method": method,
                        "cooccur_options": [
                            f"{o.source_name}-[{o.relation_type}]->{o.target_name}"
                            for o in options
                        ],
                    },
                }
                rel_items.append(
                    RecoveryItem(
                        kind="relation",
                        payload=payload,
                        source_chunk_id=chunk_id,
                        evidence_text=evidence,
                        notes=[method],
                    )
                )

        summary = {
            "relation_types": rel_types,
            "include_llm": bool(include_llm),
            "min_confidence": float(min_confidence),
            "max_entities_per_chunk": int(max_entities_per_chunk),
            "max_pairs_per_chunk": int(max_pairs_per_chunk),
            "chunks_scanned": len(chunk_keys),
            "entity_count": 0,
            "rel_count": len(rel_items),
            "rel_by_type": dict(
                Counter(i.payload.get("relation_type") for i in rel_items)
            ),
            "rel_by_method": dict(Counter((i.notes or ["?"])[0] for i in rel_items)),
            "skip": dict(skip),
            "relations": [
                {
                    "src": i.payload.get("source_name"),
                    "rt": i.payload.get("relation_type"),
                    "tgt": i.payload.get("target_name"),
                    "conf": i.payload.get("confidence"),
                    "method": (i.notes or [""])[0],
                    "chunk_id": i.source_chunk_id,
                }
                for i in rel_items
            ],
        }
        return RecoveryPlan(entities=[], relations=rel_items, summary=summary)

    def stage(
        self,
        plan: RecoveryPlan,
        *,
        mode: str = "cooccur_relation_propose",
        note: str = "",
        auto_approve: bool = False,
    ) -> str:
        if not plan.relations:
            raise ValueError("cooccur plan has no relations")
        filters = {
            "note": note or "propose-cooccur-relations stage",
            "rel_count": plan.summary.get("rel_count"),
            "include_llm": plan.summary.get("include_llm"),
            "relation_types": plan.summary.get("relation_types"),
        }
        batch_id = self.db.create_extraction_batch(mode, filters, "cooccur-relations")
        ids: list[str] = []
        for item in plan.relations:
            cid = self.db.add_extraction_candidate(
                batch_id,
                item.kind,
                _fingerprint(item.payload),
                item.payload,
                item.source_chunk_id,
                item.evidence_text,
            )
            ids.append(cid)
        if auto_approve:
            self.db.review_extraction_candidates(
                batch_id, ids, "approved", "cooccur-relations stage"
            )
            self.db.set_extraction_batch_status(batch_id, "approved")
        return batch_id

    def _merge_evidence(self, a: str, b: str, name_a: str, name_b: str) -> str:
        a = (a or "").strip()
        b = (b or "").strip()
        if a and b and a != b:
            # Prefer the longer span that mentions both names when possible.
            if name_a in a and name_b in a:
                return a
            if name_a in b and name_b in b:
                return b
            return f"{a} | {b}"
        if a:
            return a
        if b:
            return b
        # Weak but reviewable evidence: both names co-mentioned as link anchors.
        if name_a and name_b:
            return f"{name_a} … {name_b}"
        return ""

    def _load_entities(self) -> tuple[dict[str, str], dict[str, str]]:
        name_type: dict[str, str] = {}
        name_id: dict[str, str] = {}
        with self.db._get_conn() as conn:
            for row in conn.execute(
                "SELECT id, name, entity_type FROM entities WHERE review_status = 'approved'"
            ):
                n = normalize_entity_name(row["name"])
                if not n:
                    continue
                name_type[n] = row["entity_type"]
                name_id[n] = row["id"]
        return name_type, name_id

    def _load_existing_entity_pairs(self) -> set[tuple[str, str]]:
        pairs: set[tuple[str, str]] = set()
        with self.db._get_conn() as conn:
            rows = conn.execute(
                "SELECT source_entity_id, target_entity_id FROM relations"
            ).fetchall()
            for row in rows:
                a, b = row["source_entity_id"], row["target_entity_id"]
                if a and b:
                    pairs.add(tuple(sorted((a, b))))
        return pairs

    def _directed_exists(
        self, src: str, rt: str, tgt: str, name_id: dict[str, str]
    ) -> bool:
        sid, tid = name_id.get(src), name_id.get(tgt)
        if not sid or not tid:
            return False
        with self.db._get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM relations WHERE source_entity_id = ? "
                "AND target_entity_id = ? AND relation_type = ? LIMIT 1",
                (sid, tid, rt),
            ).fetchone()
            return row is not None

    def _load_chunk_entities(
        self, chunk_ids: list[str] | None
    ) -> dict[str, list[dict]]:
        sql = (
            "SELECT l.chunk_id AS chunk_id, l.entity_id AS entity_id, "
            "l.evidence_text AS evidence_text, e.name AS name, e.entity_type AS entity_type "
            "FROM entity_chunk_links l "
            "JOIN entities e ON e.id = l.entity_id "
            "WHERE e.review_status = 'approved'"
        )
        params: list[Any] = []
        if chunk_ids:
            placeholders = ", ".join("?" for _ in chunk_ids)
            sql += f" AND l.chunk_id IN ({placeholders})"
            params.extend(chunk_ids)
        sql += " ORDER BY l.chunk_id, e.name"
        by_chunk: dict[str, list[dict]] = defaultdict(list)
        with self.db._get_conn() as conn:
            for row in conn.execute(sql, params):
                by_chunk[str(row["chunk_id"])].append(dict(row))
        return by_chunk


class LLMCooccurRelationArbiter:
    """LLM backend: pick one legal directed relation or reject the co-occurrence pair."""

    def __init__(self, llm_client: Any = None, *, use_graph_endpoint: bool = False):
        self.llm_client = llm_client
        self.use_graph_endpoint = use_graph_endpoint

    def arbitrate(
        self,
        *,
        name_a: str,
        type_a: str,
        name_b: str,
        type_b: str,
        evidence_text: str,
        options: list[DirectedOption],
    ) -> tuple[str, DirectedOption | None, float]:
        if not options:
            return ("reject", None, 1.0)
        if not self.llm_client and not self.use_graph_endpoint:
            return ("unsure", None, 0.0)
        try:
            opt_lines = []
            for i, o in enumerate(options):
                opt_lines.append(
                    f"{i}: {o.source_name}-[{o.relation_type}]->{o.target_name} "
                    f"({o.source_type}->{o.target_type})"
                )
            evidence = (evidence_text or "").strip()
            prompt = (
                "你是知识图谱补边裁决器。两个已入库实体在同一知识块共现，但尚无关系边。"
                "请判断是否应建立关系，以及哪条有向边最贴切。\n"
                f"实体A: {name_a} ({type_a})\n"
                f"实体B: {name_b} ({type_b})\n"
                f"证据摘录: {evidence}\n"
                "可选边(必须从中选择 index，或 reject):\n"
                + "\n".join(opt_lines)
                + "\n\n只回答 JSON:\n"
                '{"verdict":"accept"|"reject"|"unsure","option_index":0,"confidence":0.0}\n'
                "accept 时 option_index 必须是上方编号；reject=共现不足以建边。"
            )
            if self.llm_client is not None:
                raw_response = self.llm_client.invoke(prompt)
                raw_text = (
                    raw_response.content
                    if hasattr(raw_response, "content")
                    else str(raw_response)
                )
            else:
                raw_text = self._call_graph_llm(prompt)
            if "```" in raw_text:
                match = re.search(r"```(?:json)?\s*({.*?})\s*```", raw_text, re.DOTALL)
                if match:
                    raw_text = match.group(1)
            data = json.loads(raw_text.strip())
            verdict = str(data.get("verdict") or "unsure").lower()
            if verdict not in {"accept", "reject", "unsure"}:
                verdict = "unsure"
            confidence = float(data.get("confidence") or 0.0)
            if verdict != "accept":
                return (verdict, None, confidence)
            idx = int(data.get("option_index"))
            if idx < 0 or idx >= len(options):
                return ("unsure", None, confidence)
            return ("accept", options[idx], confidence)
        except Exception:
            return ("unsure", None, 0.0)

    def _call_graph_llm(self, prompt: str) -> str:
        from rag_knowledge.config import Config
        from rag_knowledge.llm_http import chat

        cfg = Config()
        llm_cfg = cfg.graph_extraction_llm
        return chat(
            cfg.graph_extraction_endpoint,
            [{"role": "user", "content": prompt}],
            default_ollama=cfg.ollama_base_url,
            temperature=llm_cfg.temperature,
            format_json=True,
            timeout=120.0,
            think=False,
        )
