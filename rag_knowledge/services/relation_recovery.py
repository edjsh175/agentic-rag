"""Recover endpoint-ready relations from rejected LLM extraction candidates.

Two-phase pattern proven in R5b/R6b/R6c:
1. Salvage safe leaf entities needed as endpoints
2. Stage legal relations whose endpoints exist (formal DB or this plan)
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

from rag_knowledge.models.graph_schema import (
    VALID_RELATION_PAIRS,
    RelationType,
    normalize_entity_name,
    validate_relation,
)
from rag_knowledge.services.entity_resolution import EntityResolutionService
from rag_knowledge.services.graph_governance import is_safe_review_candidate

# Recovery-only: slightly looser than conservative first-pass review (0.85).
DEFAULT_ENTITY_MIN_CONF = 0.80
DEFAULT_REL_MIN_CONF = 0.80

# Short / ambiguous names that should not be auto-salvaged.
GENERIC_ENTITY_NAMES = frozenset(
    {
        "编辑",
        "设置",
        "查询",
        "删除",
        "添加",
        "修改",
        "保存",
        "取消",
        "确定",
        "打开",
        "关闭",
        "新建",
        "导入",
        "导出",
        "刷新",
        "搜索",
        "配置",
        "管理",
        "操作",
        "功能",
        "参数",
        "选项",
        "其他",
        "全部",
        "默认",
    }
)

DEFAULT_RELATION_TYPES = ("has_step", "has_procedure", "runs_command")


def endpoint_entity_types(relation_types: Iterable[str]) -> set[str]:
    types: set[str] = set()
    for rt in relation_types:
        key = RelationType(rt)
        for src, tgt in VALID_RELATION_PAIRS.get(key, []):
            types.add(src.value)
            types.add(tgt.value)
    return types


def is_generic_entity_name(name: str) -> bool:
    n = normalize_entity_name(name)
    if not n:
        return True
    if n in GENERIC_ENTITY_NAMES:
        return True
    # Single CJK char or very short Latin token
    if len(n) <= 1:
        return True
    return False


def _is_llm_payload(payload: dict) -> bool:
    return str(payload.get("created_by") or "").startswith("llm:") or str(
        (payload.get("properties") or {}).get("created_by") or ""
    ).startswith("llm:")


def _fingerprint(kind: str, payload: dict) -> str:
    if kind == "entity":
        body = {"k": "entity", "t": payload.get("entity_type"), "n": payload.get("name")}
    else:
        body = {
            "k": "relation",
            "rt": payload.get("relation_type"),
            "s": payload.get("source_name"),
            "t": payload.get("target_name"),
        }
    digest = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()[:32]
    return f"recover:{digest}"


class _Cand:
    def __init__(self, name: str, entity_type: str):
        self.name = name
        self.entity_type = entity_type


@dataclass
class RecoveryItem:
    kind: str
    payload: dict
    source_chunk_id: str = ""
    evidence_text: str = ""
    source_batch: str = ""
    source_candidate_id: str = ""
    notes: list[str] = field(default_factory=list)


@dataclass
class RecoveryPlan:
    entities: list[RecoveryItem]
    relations: list[RecoveryItem]
    summary: dict

    @property
    def items(self) -> list[RecoveryItem]:
        return list(self.entities) + list(self.relations)


class RelationRecoveryService:
    def __init__(self, db):
        self.db = db
        self.resolver = EntityResolutionService(db)

    def plan(
        self,
        *,
        source_batches: list[str],
        relation_types: list[str] | None = None,
        entity_min_conf: float = DEFAULT_ENTITY_MIN_CONF,
        rel_min_conf: float = DEFAULT_REL_MIN_CONF,
        include_possible_duplicate: bool = False,
    ) -> RecoveryPlan:
        if not source_batches:
            raise ValueError("source_batches required")
        rel_types = list(relation_types or DEFAULT_RELATION_TYPES)
        wanted_entity_types = endpoint_entity_types(rel_types)

        name_type = self._load_name_type()
        existing_rel = self._load_existing_relations(rel_types)
        skip: Counter = Counter()
        diag_codes: Counter = Counter()

        entity_items: list[RecoveryItem] = []
        seen_names: set[str] = set()

        for batch_id in source_batches:
            batch = self.db.get_extraction_batch(batch_id) or {}
            for c in self.db.list_extraction_candidates(batch_id, "rejected"):
                if c["candidate_kind"] != "entity":
                    continue
                p = dict(c.get("payload") or {})
                if not _is_llm_payload(p):
                    continue
                et = p.get("entity_type")
                if et not in wanted_entity_types:
                    skip["entity_type_out_of_scope"] += 1
                    continue
                conf = float(p.get("confidence") or 0)
                if conf < entity_min_conf:
                    skip["entity_low_conf"] += 1
                    continue
                name = normalize_entity_name(p.get("name") or "")
                if not name:
                    continue
                if is_generic_entity_name(name):
                    skip["entity_generic"] += 1
                    continue
                if name in name_type:
                    if name_type[name] != et:
                        skip["entity_type_conflict"] += 1
                    else:
                        skip["entity_already"] += 1
                    continue

                action = p.get("resolution_action")
                notes: list[str] = []
                if action == "diagnostic":
                    if not include_possible_duplicate:
                        skip["entity_diagnostic_skipped"] += 1
                        continue
                    resolution = self.resolver.resolve(_Cand(p.get("name") or "", et))
                    codes = [d.code for d in resolution.diagnostics]
                    for code in codes:
                        diag_codes[code] += 1
                    if resolution.action != "diagnostic" or "type_conflict" in codes:
                        skip["entity_diagnostic_hard"] += 1
                        continue
                    if "possible_duplicate" not in codes:
                        skip["entity_diagnostic_unknown"] += 1
                        continue
                    notes = [d.message for d in resolution.diagnostics]
                    p = dict(p)
                    p["resolution_action"] = "new"
                    p["resolved_entity_id"] = ""
                    props = dict(p.get("properties") or {})
                    props["recovered_from_diagnostic"] = True
                    props["possible_duplicate_hints"] = notes
                    p["properties"] = props
                else:
                    if not is_safe_review_candidate(c, batch=batch):
                        skip["entity_unsafe"] += 1
                        continue

                evidence = str(p.get("evidence_text") or c.get("evidence_text") or "")
                if not evidence and not p.get("evidences"):
                    skip["entity_no_evidence"] += 1
                    continue
                if name in seen_names:
                    skip["entity_dup"] += 1
                    continue
                seen_names.add(name)
                entity_items.append(
                    RecoveryItem(
                        kind="entity",
                        payload=p,
                        source_chunk_id=c.get("source_chunk_id") or "",
                        evidence_text=evidence,
                        source_batch=batch_id,
                        source_candidate_id=c["id"],
                        notes=notes,
                    )
                )

        sim = dict(name_type)
        for item in entity_items:
            sim[normalize_entity_name(item.payload.get("name") or "")] = item.payload.get(
                "entity_type"
            )

        rel_items: list[RecoveryItem] = []
        seen_rel: set[tuple[str, str, str]] = set()
        for batch_id in source_batches:
            batch = self.db.get_extraction_batch(batch_id) or {}
            for c in self.db.list_extraction_candidates(batch_id, "rejected"):
                if c["candidate_kind"] != "relation":
                    continue
                p = dict(c.get("payload") or {})
                if not _is_llm_payload(p):
                    continue
                rt = p.get("relation_type")
                if rt not in rel_types:
                    skip["rel_type_filtered"] += 1
                    continue
                conf = float(p.get("confidence") or 0)
                if conf < rel_min_conf:
                    skip["rel_low_conf"] += 1
                    continue
                src = normalize_entity_name(p.get("source_name") or "")
                tgt = normalize_entity_name(p.get("target_name") or "")
                st, tt = sim.get(src), sim.get(tgt)
                if not st or not tt:
                    skip["rel_missing_endpoint"] += 1
                    continue
                ok, _ = validate_relation(st, rt, tt)
                if not ok:
                    skip[f"rel_illegal_{st}->{tt}"] += 1
                    continue
                key = (src, rt, tgt)
                if key in existing_rel or key in seen_rel:
                    skip["rel_exists_or_dup"] += 1
                    continue
                # Relations with resolution_action diagnostic are rare; still require evidence.
                evidence = str(p.get("evidence_text") or c.get("evidence_text") or "")
                if not evidence and not p.get("evidences"):
                    skip["rel_no_evidence"] += 1
                    continue
                if p.get("resolution_action") == "diagnostic":
                    skip["rel_diagnostic"] += 1
                    continue
                # Soft check: prefer candidates that would pass review if pending.
                # Rejected rows are not pending; reconstruct a pending-like view.
                probe = {
                    "candidate_kind": "relation",
                    "payload": p,
                    "evidence_text": evidence,
                }
                if not is_safe_review_candidate(probe, batch=batch):
                    skip["rel_unsafe"] += 1
                    continue
                seen_rel.add(key)
                rel_items.append(
                    RecoveryItem(
                        kind="relation",
                        payload=p,
                        source_chunk_id=c.get("source_chunk_id") or "",
                        evidence_text=evidence,
                        source_batch=batch_id,
                        source_candidate_id=c["id"],
                    )
                )

        summary = {
            "source_batches": source_batches,
            "relation_types": rel_types,
            "entity_min_conf": entity_min_conf,
            "rel_min_conf": rel_min_conf,
            "include_possible_duplicate": include_possible_duplicate,
            "entity_count": len(entity_items),
            "rel_count": len(rel_items),
            "entity_by_type": dict(
                Counter(i.payload.get("entity_type") for i in entity_items)
            ),
            "rel_by_type": dict(
                Counter(i.payload.get("relation_type") for i in rel_items)
            ),
            "diag_codes": dict(diag_codes),
            "skip": dict(skip),
            "entities": [
                {
                    "name": i.payload.get("name"),
                    "type": i.payload.get("entity_type"),
                    "conf": i.payload.get("confidence"),
                    "notes": i.notes,
                }
                for i in entity_items
            ],
            "relations": [
                {
                    "src": i.payload.get("source_name"),
                    "rt": i.payload.get("relation_type"),
                    "tgt": i.payload.get("target_name"),
                    "conf": i.payload.get("confidence"),
                }
                for i in rel_items
            ],
        }
        return RecoveryPlan(entities=entity_items, relations=rel_items, summary=summary)

    def stage(
        self,
        plan: RecoveryPlan,
        *,
        mode: str = "llm_relation_recovery",
        note: str = "",
    ) -> str:
        if not plan.items:
            raise ValueError("recovery plan is empty")
        filters = {
            "note": note or "recover-relations stage",
            "source_batches": plan.summary.get("source_batches"),
            "relation_types": plan.summary.get("relation_types"),
            "entity_count": plan.summary.get("entity_count"),
            "rel_count": plan.summary.get("rel_count"),
        }
        batch_id = self.db.create_extraction_batch(mode, filters, "recover-relations")
        approve_ids: list[str] = []
        for item in plan.items:
            cid = self.db.add_extraction_candidate(
                batch_id,
                item.kind,
                _fingerprint(item.kind, item.payload),
                item.payload,
                item.source_chunk_id,
                item.evidence_text,
            )
            approve_ids.append(cid)
        self.db.review_extraction_candidates(
            batch_id, approve_ids, "approved", "recover-relations stage"
        )
        self.db.set_extraction_batch_status(batch_id, "approved")
        return batch_id

    def _load_name_type(self) -> dict[str, str]:
        out: dict[str, str] = {}
        with self.db._get_conn() as conn:
            for row in conn.execute("SELECT name, entity_type FROM entities"):
                out[normalize_entity_name(row["name"])] = row["entity_type"]
        return out

    def _load_existing_relations(self, relation_types: list[str]) -> set[tuple[str, str, str]]:
        existing: set[tuple[str, str, str]] = set()
        if not relation_types:
            return existing
        placeholders = ", ".join("?" for _ in relation_types)
        with self.db._get_conn() as conn:
            rows = conn.execute(
                f"SELECT e1.name AS s, rel.relation_type AS rt, e2.name AS t "
                f"FROM relations rel "
                f"JOIN entities e1 ON e1.id = rel.source_entity_id "
                f"JOIN entities e2 ON e2.id = rel.target_entity_id "
                f"WHERE rel.relation_type IN ({placeholders})",
                list(relation_types),
            ).fetchall()
            for row in rows:
                existing.add(
                    (
                        normalize_entity_name(row["s"]),
                        str(row["rt"]),
                        normalize_entity_name(row["t"]),
                    )
                )
        return existing
