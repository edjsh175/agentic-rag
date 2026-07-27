#!/usr/bin/env python3
"""Build Round-4 GraphRAG eval set from seed questions + live entity_chunk_links."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag_knowledge.models.graph_schema import normalize_entity_name
from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.repository.vector_store import VectorStore

OUT = ROOT / "data" / "eval_graph_rag_dataset.json"

# Prefer concrete business types over Section/Document duplicates.
_PREF = {
    "DataTable": 0,
    "Tool": 1,
    "Product": 2,
    "Procedure": 3,
    "ConfigItem": 4,
    "Error": 5,
    "EnvironmentComponent": 6,
    "Command": 7,
    "Field": 8,
    "Module": 9,
    "Section": 20,
    "Document": 21,
}

SEEDS: list[dict] = [
    {
        "id": "graphrag-001",
        "question": "管线点表包含哪些核心字段？",
        "intent": "definition",
        "linked_entity_names": ["管线点表"],
        "expected_evidence_hint": "字段定义、字段含义",
        "answer_key_points": ["能列出管线点表的主要字段", "答案来自 StampTools/PipelineBuilder 数据规范"],
        "kb_name": "文章附件",
        "doc_category": "StampTools",
    },
    {
        "id": "graphrag-002",
        "question": "PipelineBuilder 的主要职责是什么？",
        "intent": "definition",
        "linked_entity_names": ["PipelineBuilder"],
        "expected_evidence_hint": "组件定义、用途说明",
        "answer_key_points": ["说明 PipelineBuilder 用途", "能关联到 StampTools 管线工具"],
        "kb_name": "文章附件",
        "doc_category": "StampTools",
    },
    {
        "id": "graphrag-003",
        "question": "PipelineBuilder 数据规范里管线点表描述什么内容？",
        "intent": "definition",
        "linked_entity_names": ["PipelineBuilder", "管线点表"],
        "expected_evidence_hint": "数据规范、表结构",
        "answer_key_points": ["说明管线点表在数据规范中的角色", "能提到字段或表结构要点"],
        "kb_name": "文章附件",
        "doc_category": "StampTools",
        "notes": "原草案 DataSpec 在正式图中不存在，改为 PipelineBuilder+管线点表",
    },
    {
        "id": "graphrag-004",
        "question": "值域映射用于解决什么问题？",
        "intent": "definition",
        "linked_entity_names": ["值域映射"],
        "expected_evidence_hint": "状态映射、枚举转换",
        "answer_key_points": ["说明值域映射用途", "能关联到 PipelineBuilder 映射配置"],
        "kb_name": "文章附件",
        "doc_category": "StampTools",
    },
    {
        "id": "graphrag-005",
        "question": "StampTools 工具概述里讲了哪些关键能力？",
        "intent": "definition",
        "linked_entity_names": ["StampTools"],
        "expected_evidence_hint": "工具概述、产品能力",
        "answer_key_points": ["能概括 StampTools 能力或模块", "证据来自工具概述相关 chunk"],
        "kb_name": "文章附件",
        "doc_category": "StampTools",
    },
    {
        "id": "graphrag-006",
        "question": "PipelinePublishConfig 配置项用于做什么？",
        "intent": "config",
        "linked_entity_names": ["PipelinePublishConfig"],
        "expected_evidence_hint": "发布参数、配置项",
        "answer_key_points": ["说明 PipelinePublishConfig 用途", "能关联 PipelineBuilder 发布"],
        "kb_name": "文章附件",
        "doc_category": "StampTools",
    },
    {
        "id": "graphrag-007",
        "question": "PIPELINE_Config 配置项包含哪些关键参数？",
        "intent": "config",
        "linked_entity_names": ["PIPELINE_Config"],
        "expected_evidence_hint": "管线配置、参数项",
        "answer_key_points": ["提到 PIPELINE_Config 相关参数或位置", "答案可追溯到 StampTools chunk"],
        "kb_name": "文章附件",
        "doc_category": "StampTools",
    },
    {
        "id": "graphrag-008",
        "question": "值域映射的配置规则有哪些约束？",
        "intent": "config",
        "linked_entity_names": ["值域映射"],
        "expected_evidence_hint": "映射规则、约束条件",
        "answer_key_points": ["说明映射配置约束或步骤", "证据来自值域映射章节"],
        "kb_name": "文章附件",
        "doc_category": "StampTools",
    },
    {
        "id": "graphrag-009",
        "question": "使用状态映射应按什么步骤配置？",
        "intent": "config",
        "linked_entity_names": ["使用状态映射"],
        "expected_evidence_hint": "操作步骤、配置顺序",
        "answer_key_points": ["能描述使用状态映射配置步骤", "命中使用状态映射相关 chunk"],
        "kb_name": "文章附件",
        "doc_category": "StampTools",
    },
    {
        "id": "graphrag-010",
        "question": "材质映射相关配置要注意什么？",
        "intent": "config",
        "linked_entity_names": ["材质映射"],
        "expected_evidence_hint": "材质映射、配置要点",
        "answer_key_points": ["提到材质映射配置要点", "证据可追溯"],
        "kb_name": "文章附件",
        "doc_category": "StampTools",
    },
    {
        "id": "graphrag-011",
        "question": "PipelineBuilder 发布相关流程涉及哪些配置？",
        "intent": "procedure",
        "linked_entity_names": ["PipelineBuilder", "PipelinePublishConfig"],
        "expected_evidence_hint": "发布步骤、发布配置",
        "answer_key_points": ["能关联 PipelineBuilder 与发布配置", "答案有文档依据"],
        "kb_name": "文章附件",
        "doc_category": "StampTools",
    },
    {
        "id": "graphrag-012",
        "question": "从数据规范到管线生成的处理链路是什么？",
        "intent": "procedure",
        "linked_entity_names": ["PipelineBuilder", "管线点表"],
        "expected_evidence_hint": "规范解析、生成流程",
        "answer_key_points": ["体现数据规范到生成的链路", "能提到管线表或 PipelineBuilder"],
        "kb_name": "文章附件",
        "doc_category": "StampTools",
    },
    {
        "id": "graphrag-013",
        "question": "管线生成方式有哪些步骤或选项？",
        "intent": "procedure",
        "linked_entity_names": ["管线生成方式"],
        "expected_evidence_hint": "生成方式、操作步骤",
        "answer_key_points": ["说明管线生成方式", "命中对应 Procedure chunk"],
        "kb_name": "文章附件",
        "doc_category": "StampTools",
    },
    {
        "id": "graphrag-014",
        "question": "新增管线字段时通常要改哪些环节？",
        "intent": "procedure",
        "linked_entity_names": ["管线点表", "字段管理"],
        "expected_evidence_hint": "字段配置、生成、校验",
        "answer_key_points": ["提到字段管理或表结构变更", "证据来自 StampTools"],
        "kb_name": "文章附件",
        "doc_category": "StampTools",
    },
    {
        "id": "graphrag-015",
        "question": "PipelineBuilder 与管线点表之间是什么关系？",
        "intent": "dependency",
        "linked_entity_names": ["PipelineBuilder", "管线点表"],
        "expected_evidence_hint": "has_table、数据规范依赖",
        "answer_key_points": ["说明 PipelineBuilder 使用/包含管线点表", "能体现表与工具关系"],
        "kb_name": "文章附件",
        "doc_category": "StampTools",
    },
    {
        "id": "graphrag-016",
        "question": "PipelineBuilder 与管线线表之间有什么关联？",
        "intent": "dependency",
        "linked_entity_names": ["PipelineBuilder", "管线线表"],
        "expected_evidence_hint": "has_table、线表规范",
        "answer_key_points": ["说明工具与管线线表关系", "证据可追溯"],
        "kb_name": "文章附件",
        "doc_category": "StampTools",
    },
    {
        "id": "graphrag-017",
        "question": "管线点表与值域映射之间有什么关联？",
        "intent": "dependency",
        "linked_entity_names": ["管线点表", "值域映射"],
        "expected_evidence_hint": "字段值、映射关系",
        "answer_key_points": ["说明表字段与映射配置的关联", "两边证据可检索"],
        "kb_name": "文章附件",
        "doc_category": "StampTools",
    },
    {
        "id": "graphrag-018",
        "question": "管线生成结果缺少字段时应先检查哪里？",
        "intent": "troubleshooting",
        "linked_entity_names": ["管线点表", "PipelineBuilder"],
        "expected_evidence_hint": "缺字段、排查路径",
        "answer_key_points": ["指向数据规范/字段表排查", "关联 PipelineBuilder"],
        "kb_name": "文章附件",
        "doc_category": "StampTools",
    },
    {
        "id": "graphrag-019",
        "question": "状态值没有正确映射时可能是什么原因？",
        "intent": "troubleshooting",
        "linked_entity_names": ["值域映射", "使用状态映射"],
        "expected_evidence_hint": "映射失败、配置错误",
        "answer_key_points": ["指向值域/使用状态映射配置", "给出可操作排查方向"],
        "kb_name": "文章附件",
        "doc_category": "StampTools",
    },
    {
        "id": "graphrag-020",
        "question": "出现 UV展开错误时应如何排查？",
        "intent": "troubleshooting",
        "linked_entity_names": ["UV展开错误"],
        "expected_evidence_hint": "错误说明、排查建议",
        "answer_key_points": ["命中 UV展开错误相关证据", "给出排查或处理线索"],
        "kb_name": "文章附件",
        "doc_category": "StampTools",
        "notes": "原草案「图谱重建」非语料实体，改为图内 Error 叶子",
    },
]


def _pick_entity(db: RelationalDB, name: str) -> dict | None:
    nn = normalize_entity_name(name)
    with db._get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, entity_type, doc_category FROM entities WHERE name = ?",
            (nn,),
        ).fetchall()
        if not rows:
            rows = conn.execute(
                "SELECT id, name, entity_type, doc_category FROM entities WHERE name LIKE ?",
                (f"%{nn}%",),
            ).fetchall()
        if not rows:
            return None
        ranked = sorted(
            rows,
            key=lambda r: (_PREF.get(r["entity_type"], 50), len(r["name"])),
        )
        return dict(ranked[0])


def _links_for(db: RelationalDB, entity_id: str, limit: int = 12) -> list[str]:
    with db._get_conn() as conn:
        rows = conn.execute(
            "SELECT chunk_id FROM entity_chunk_links WHERE entity_id = ? LIMIT ?",
            (entity_id, limit),
        ).fetchall()
    return [r["chunk_id"] for r in rows]


def _existing_chunk_ids(store: VectorStore, chunk_ids: list[str]) -> list[str]:
    if not chunk_ids:
        return []
    col = store.get_chroma()._collection
    got = col.get(ids=chunk_ids, include=[])
    return list(got.get("ids") or [])


def main() -> int:
    db = RelationalDB()
    store = VectorStore()
    items: list[dict] = []
    missing: list[str] = []

    for seed in SEEDS:
        chunk_ids: list[str] = []
        resolved_entities: list[str] = []
        for name in seed["linked_entity_names"]:
            ent = _pick_entity(db, name)
            if not ent:
                missing.append(f"{seed['id']}:{name}")
                continue
            resolved_entities.append(ent["name"])
            chunk_ids.extend(_links_for(db, ent["id"]))
        # de-dupe preserve order
        seen: set[str] = set()
        uniq = []
        for cid in chunk_ids:
            if cid not in seen:
                seen.add(cid)
                uniq.append(cid)
        live = _existing_chunk_ids(store, uniq)
        if not live:
            missing.append(f"{seed['id']}:no_live_chunks")
        item = {
            **seed,
            "linked_entity_names": resolved_entities or seed["linked_entity_names"],
            "relevant_chunk_ids": live[:8],
        }
        items.append(item)

    payload = {
        "version": "graph_rag_v1",
        "generated_for": "round4_graphrag_ab",
        "count": len(items),
        "items": items,
        "missing": missing,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUT), "count": len(items), "missing": missing}, ensure_ascii=False, indent=2))
    return 1 if any("no_live_chunks" in m or m.endswith(":no_live_chunks") for m in missing) or len(items) < 20 else 0


if __name__ == "__main__":
    raise SystemExit(main())
