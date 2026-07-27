#!/usr/bin/env python3
"""Build GraphRAG eval set from seed questions + live entity_chunk_links.

Round-4 baseline: StampTools × 20 (SEEDS).
v2 expand: keep SEEDS + NEW_SEEDS (~20) across StampServer / StampWebRTC / 基础环境
plus adversarial probes (overview / troubleshooting / multi-entity / weak-link).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag_knowledge.models.graph_schema import normalize_entity_name
from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.repository.vector_store import VectorStore

DEFAULT_OUT = ROOT / "data" / "eval_graph_rag_dataset.json"

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
        "answer_key_points": ["说明 PipelinePublishConfig 用途", "能关联管线发布服务配置"],
        "kb_name": "文章附件",
        "doc_category": "StampServer",
        "notes": "正式图 ConfigItem 与 evidence 属 StampServer（非 StampTools）",
    },
    {
        "id": "graphrag-007",
        "question": "PIPELINE_Config 配置项包含哪些关键参数？",
        "intent": "config",
        "linked_entity_names": ["PIPELINE_Config"],
        "expected_evidence_hint": "管线配置、参数项",
        "answer_key_points": ["提到 PIPELINE_Config 相关参数或位置", "答案可追溯到 StampServer chunk"],
        "kb_name": "文章附件",
        "doc_category": "StampServer",
        "notes": "正式图 ConfigItem 与 evidence 属 StampServer（非 StampTools）",
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

# v2 expand: calibrated against live graph (2026-07-27). Prefer concrete leaves
# over Product/Section when possible; keep a few wide Product probes on purpose.
NEW_SEEDS: list[dict] = [
    # --- StampServer ---
    {
        "id": "graphrag-021",
        "question": "StampServer 产品概述里覆盖哪些部署与运维能力？",
        "intent": "definition",
        "linked_entity_names": ["StampServer"],
        "expected_evidence_hint": "产品概述、部署能力",
        "answer_key_points": ["能概括 StampServer 部署/运维相关能力", "证据来自 StampServer 文档"],
        "kb_name": "文章附件",
        "doc_category": "StampServer",
        "notes": "对抗：宽 Product 实体（多链接）易邻域挤占；金标手标服务部署/能力块，避免 SQL LIMIT 抽到 OS 安装叶",
        # 手标：Stamp服务部署下的能力/运维相关块（均在 StampServer entity_chunk_links）
        "curated_relevant_chunk_ids": [
            "chk_1528ea0c1092cefdf25c95a2",  # Stamp服务部署
            "chk_08034b82ec212b9142e713c5",  # 管线查询服务
            "chk_63a6d9234085c75288d9cd9d",  # 三维分析服务
            "chk_6f80c5b187ae5acfb4221b19",  # StampNodeServer部署
            "chk_be8144817a3ada7ca50b0741",  # 三维搜索服务
            "chk_c463de877d23f14f64d3304b",  # 运维代理设置
            "chk_cdbc062481ebe4565d2ec168",  # 创建数据库目录
            "chk_05346d1f09fb96cfb102abb7",  # 服务端总配置
        ],
    },
    {
        "id": "graphrag-022",
        "question": "ArcConfig 配置项用于做什么？",
        "intent": "config",
        "linked_entity_names": ["ArcConfig"],
        "expected_evidence_hint": "ArcConfig、配置参数",
        "answer_key_points": ["说明 ArcConfig 用途", "答案可追溯到 StampServer chunk"],
        "kb_name": "文章附件",
        "doc_category": "StampServer",
    },
    {
        "id": "graphrag-023",
        "question": "DataEngineConfig 和 DB_Config 分别管什么？",
        "intent": "config",
        "linked_entity_names": ["DataEngineConfig", "DB_Config"],
        "expected_evidence_hint": "数据引擎配置、数据库配置",
        "answer_key_points": ["能区分两类配置职责", "两边实体证据可检索"],
        "kb_name": "文章附件",
        "doc_category": "StampServer",
        "notes": "对抗：多实体歧义",
    },
    {
        "id": "graphrag-024",
        "question": "StampManager 部署需要完成哪些步骤？",
        "intent": "procedure",
        "linked_entity_names": ["StampServer用户手册_Rocky9::Stamp服务部署 > StampManager部署"],
        "expected_evidence_hint": "StampManager部署、服务部署",
        "answer_key_points": ["命中 StampManager 部署相关证据", "能描述部署要点"],
        "kb_name": "文章附件",
        "doc_category": "StampServer",
        "notes": "回避无链接 Product「StampManager」；锚定有 live chunk 的 Section",
    },
    {
        "id": "graphrag-025",
        "question": "HTTPS 配置与私有 CA 配置之间怎么衔接？",
        "intent": "procedure",
        "linked_entity_names": ["HTTPS配置", "私有CA配置"],
        "expected_evidence_hint": "HTTPS、私有CA、证书",
        "answer_key_points": ["体现 HTTPS 与私有 CA 流程关系", "证据来自 StampServer"],
        "kb_name": "文章附件",
        "doc_category": "StampServer",
    },
    {
        "id": "graphrag-026",
        "question": "MINIO 部署依赖哪些环境组件或前置？",
        "intent": "dependency",
        "linked_entity_names": ["MINIO部署"],
        "expected_evidence_hint": "MINIO部署、磁盘挂载",
        "answer_key_points": ["说明 MINIO 部署依赖或步骤", "命中 MINIO 相关 chunk"],
        "kb_name": "文章附件",
        "doc_category": "StampServer",
    },
    {
        "id": "graphrag-027",
        "question": "StampServer 侧 coturn 服务如何启动与启用？",
        "intent": "dependency",
        "linked_entity_names": ["coturn", "systemctl start coturn"],
        "expected_evidence_hint": "coturn、systemctl",
        "answer_key_points": ["关联 coturn 组件与启动命令", "证据可追溯"],
        "kb_name": "文章附件",
        "doc_category": "StampServer",
    },
    {
        "id": "graphrag-028",
        "question": "Redis 服务起不来时应先检查哪条启动命令与配置？",
        "intent": "troubleshooting",
        "linked_entity_names": ["systemctl start redis", "/etc/redis/redis.conf"],
        "expected_evidence_hint": "redis 启动、redis.conf",
        "answer_key_points": ["指向 redis 启动命令或配置文件", "给出可操作排查方向"],
        "kb_name": "文章附件",
        "doc_category": "StampServer",
        "notes": "对抗：排错类 + 弱链接叶子",
    },
    # --- StampWebRTC ---
    {
        "id": "graphrag-029",
        "question": "StampWebRTC 概述里介绍了哪些基本能力？",
        "intent": "definition",
        "linked_entity_names": ["StampWebRTC"],
        "expected_evidence_hint": "概述、基本操作",
        "answer_key_points": ["能概括 StampWebRTC 能力", "证据来自概述相关 chunk"],
        "kb_name": "文章附件",
        "doc_category": "StampWebRTC",
        "notes": "对抗：概述类 + 宽 Product（多链接）",
    },
    {
        "id": "graphrag-030",
        "question": "StampWebRTC 本地启动常用哪些 run_local 脚本？",
        "intent": "procedure",
        "linked_entity_names": ["run_local_1.bat", "run_local_2.bat"],
        "expected_evidence_hint": "run_local、本地启动",
        "answer_key_points": ["提到 run_local 脚本", "证据可追溯"],
        "kb_name": "文章附件",
        "doc_category": "StampWebRTC",
    },
    {
        "id": "graphrag-031",
        "question": "推流启动流程要注意什么？",
        "intent": "procedure",
        "linked_entity_names": ["推流启动"],
        "expected_evidence_hint": "推流启动步骤",
        "answer_key_points": ["说明推流启动要点", "命中对应 Procedure chunk"],
        "kb_name": "文章附件",
        "doc_category": "StampWebRTC",
    },
    {
        "id": "graphrag-032",
        "question": "地质查询功能用于做什么？",
        "intent": "definition",
        "linked_entity_names": ["地质查询"],
        "expected_evidence_hint": "地质查询、分析能力",
        "answer_key_points": ["说明地质查询用途", "证据来自 StampWebRTC"],
        "kb_name": "文章附件",
        "doc_category": "StampWebRTC",
    },
    {
        "id": "graphrag-033",
        "question": "渲染模式配置项影响什么显示效果？",
        "intent": "config",
        "linked_entity_names": ["渲染模式"],
        "expected_evidence_hint": "渲染模式、样式设置",
        "answer_key_points": ["说明渲染模式配置作用", "答案可追溯"],
        "kb_name": "文章附件",
        "doc_category": "StampWebRTC",
    },
    {
        "id": "graphrag-034",
        "question": "StampWebRTC 对 Win11 与 Edge 浏览器环境有什么要求？",
        "intent": "dependency",
        "linked_entity_names": ["StampWebRTC", "Win11", "Edge"],
        "expected_evidence_hint": "浏览器、操作系统环境",
        "answer_key_points": ["关联产品与环境组件", "能提到 Win11 或 Edge"],
        "kb_name": "文章附件",
        "doc_category": "StampWebRTC",
        "notes": "对抗：多实体 + 产品宽实体",
    },
    {
        "id": "graphrag-035",
        "question": "瓦片透明异常时应优先检查哪类标绘/显示设置？",
        "intent": "troubleshooting",
        "linked_entity_names": ["瓦片透明"],
        "expected_evidence_hint": "瓦片透明、显示异常",
        "answer_key_points": ["指向瓦片透明相关证据", "给出排查方向"],
        "kb_name": "文章附件",
        "doc_category": "StampWebRTC",
        "notes": "对抗：排错类",
    },
    # --- 基础环境 ---
    {
        "id": "graphrag-036",
        "question": "linux 系统安装显卡驱动的主要步骤是什么？",
        "intent": "procedure",
        "linked_entity_names": ["linux系统安装显卡驱动", "显卡驱动安装"],
        "expected_evidence_hint": "显卡驱动安装步骤",
        "answer_key_points": ["描述驱动安装步骤要点", "证据来自基础环境"],
        "kb_name": "文章附件",
        "doc_category": "基础环境",
    },
    {
        "id": "graphrag-037",
        "question": "VMwareESXi 安装与创建虚拟机流程如何衔接？",
        "intent": "procedure",
        "linked_entity_names": ["VMwareESXi安装", "VMwareESXI创建虚拟机"],
        "expected_evidence_hint": "ESXi 安装、创建虚拟机",
        "answer_key_points": ["体现安装到建机的链路", "两边证据可检索"],
        "kb_name": "文章附件",
        "doc_category": "基础环境",
    },
    {
        "id": "graphrag-038",
        "question": "PixelStreamingIP 与 PixelStreamingPort 配置项分别做什么？",
        "intent": "config",
        "linked_entity_names": ["PixelStreamingIP", "PixelStreamingPort"],
        "expected_evidence_hint": "推流 IP/端口配置",
        "answer_key_points": ["说明 IP 与端口配置用途", "证据可追溯"],
        "kb_name": "文章附件",
        "doc_category": "基础环境",
    },
    {
        "id": "graphrag-039",
        "question": "NVIDIA 显卡驱动与 nvidia-smi 命令之间是什么关系？",
        "intent": "dependency",
        "linked_entity_names": ["NVIDIA显卡驱动", "nvidia-smi"],
        "expected_evidence_hint": "驱动、nvidia-smi 校验",
        "answer_key_points": ["说明驱动与检测命令关系", "证据来自基础环境"],
        "kb_name": "文章附件",
        "doc_category": "基础环境",
    },
    {
        "id": "graphrag-040",
        "question": "显卡驱动冲突时 blacklist nouveau 用于解决什么问题？",
        "intent": "troubleshooting",
        "linked_entity_names": ["blacklist nouveau", "NVIDIA显卡驱动"],
        "expected_evidence_hint": "blacklist nouveau、驱动冲突",
        "answer_key_points": ["指向 nouveau 黑名单排查", "关联 NVIDIA 驱动"],
        "kb_name": "文章附件",
        "doc_category": "基础环境",
        "notes": "对抗：排错类",
    },
    # --- 跨类目 / 邻域对抗补强 ---
    {
        "id": "graphrag-041",
        "question": "StampServer 手册里 WebRTC 部署和 StampWebRTC 产品本身有何区别？",
        "intent": "definition",
        "linked_entity_names": ["StampServer", "StampWebRTC"],
        "expected_evidence_hint": "服务端 WebRTC 部署 vs 客户端产品",
        "answer_key_points": ["能区分服务端部署与 WebRTC 产品", "避免跨类目串扰"],
        "kb_name": "文章附件",
        "doc_category": "StampServer",
        "notes": "对抗：跨类目多实体歧义（题面 doc_category 取 StampServer）",
    },
    {
        "id": "graphrag-042",
        "question": "TerrainBuilder 工具的主要职责是什么？",
        "intent": "definition",
        "linked_entity_names": ["TerrainBuilder"],
        "expected_evidence_hint": "TerrainBuilder 用途",
        "answer_key_points": ["说明 TerrainBuilder 职责", "不被 PipelineBuilder 邻居挤占"],
        "kb_name": "文章附件",
        "doc_category": "StampTools",
        "notes": "对抗：同 StampTools 宽工具邻域挤占",
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


def _hint_tokens(seed: dict) -> tuple[str, ...]:
    """Keywords from question + evidence hint for ranking entity_chunk_links."""
    blob = f"{seed.get('question') or ''} {seed.get('expected_evidence_hint') or ''}"
    seeds = (
        "概述", "简介", "介绍", "能力", "部署", "运维", "安装", "配置",
        "排查", "错误", "启动", "脚本", "字段", "映射", "服务", "功能说明",
    )
    found = [tok for tok in seeds if tok in blob]
    # also keep short latin tokens from the question (run_local, HTTPS, …)
    for part in (seed.get("question") or "").replace("？", " ").replace("?", " ").split():
        p = part.strip("，。、；:：()（）[]【】\"'")
        if 2 <= len(p) <= 24 and any(ch.isascii() and ch.isalnum() for ch in p):
            found.append(p)
    # dedupe preserve order
    out: list[str] = []
    seen: set[str] = set()
    for tok in found:
        key = tok.casefold()
        if key not in seen:
            seen.add(key)
            out.append(tok)
    return tuple(out)


def _links_for(
    db: RelationalDB,
    entity_id: str,
    limit: int = 12,
    tokens: tuple[str, ...] = (),
) -> list[str]:
    with db._get_conn() as conn:
        rows = conn.execute(
            "SELECT chunk_id, evidence_text FROM entity_chunk_links WHERE entity_id = ?",
            (entity_id,),
        ).fetchall()
    scored: list[tuple[int, int, str]] = []
    for r in rows:
        cid = r["chunk_id"]
        text = f"{r['evidence_text'] or ''} {cid}".casefold()
        hit = sum(1 for tok in tokens if tok and tok.casefold() in text) if tokens else 0
        bonus = 0
        if "服务部署" in text:
            bonus += 3
        if "功能说明" in text:
            bonus += 2
        depth = text.count(">")
        scored.append((-(hit + bonus), depth, cid))
    scored.sort()
    return [cid for _, _, cid in scored[: max(limit, 24)]]


def _existing_chunk_ids(store: VectorStore, chunk_ids: list[str]) -> list[str]:
    if not chunk_ids:
        return []
    col = store.get_chroma()._collection
    got = col.get(ids=chunk_ids, include=[])
    present = set(got.get("ids") or [])
    # preserve caller order
    return [cid for cid in chunk_ids if cid in present]


def _build_item(db: RelationalDB, store: VectorStore, seed: dict, missing: list[str]) -> dict:
    resolved_entities: list[str] = []
    for name in seed["linked_entity_names"]:
        ent = _pick_entity(db, name)
        if not ent:
            missing.append(f"{seed['id']}:{name}")
            continue
        resolved_entities.append(ent["name"])

    curated = list(seed.get("curated_relevant_chunk_ids") or [])
    if curated:
        live = _existing_chunk_ids(store, curated)
        if not live:
            missing.append(f"{seed['id']}:curated_chunks_missing")
        item = {k: v for k, v in seed.items() if k != "curated_relevant_chunk_ids"}
        return {
            **item,
            "linked_entity_names": resolved_entities or seed["linked_entity_names"],
            "relevant_chunk_ids": live[:8],
        }

    tokens = _hint_tokens(seed)
    chunk_ids: list[str] = []
    for name in resolved_entities or seed["linked_entity_names"]:
        ent = _pick_entity(db, name)
        if not ent:
            continue
        chunk_ids.extend(_links_for(db, ent["id"], tokens=tokens))
    seen: set[str] = set()
    uniq: list[str] = []
    for cid in chunk_ids:
        if cid not in seen:
            seen.add(cid)
            uniq.append(cid)
    live = _existing_chunk_ids(store, uniq)
    if not live:
        missing.append(f"{seed['id']}:no_live_chunks")
    item = {k: v for k, v in seed.items() if k != "curated_relevant_chunk_ids"}
    return {
        **item,
        "linked_entity_names": resolved_entities or seed["linked_entity_names"],
        "relevant_chunk_ids": live[:8],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help="Output dataset path (default: data/eval_graph_rag_dataset.json)",
    )
    parser.add_argument(
        "--include-new",
        action="store_true",
        help="Force include NEW_SEEDS even if --out is not a v2 path",
    )
    parser.add_argument(
        "--baseline-only",
        action="store_true",
        help="Only emit original StampTools SEEDS (round-4 baseline)",
    )
    args = parser.parse_args()
    out: Path = args.out
    # Default: v2 path → include new; legacy default path → baseline-only unless --include-new
    if args.baseline_only:
        include_new = False
    elif args.include_new:
        include_new = True
    else:
        include_new = "v2" in out.name.lower()

    seeds = list(SEEDS) + (list(NEW_SEEDS) if include_new else [])
    db = RelationalDB()
    store = VectorStore()
    items: list[dict] = []
    missing: list[str] = []
    for seed in seeds:
        items.append(_build_item(db, store, seed, missing))

    cats = sorted({str(i.get("doc_category") or "") for i in items if i.get("doc_category")})
    intents = sorted({str(i.get("intent") or "") for i in items if i.get("intent")})
    live_ok = sum(1 for i in items if i.get("relevant_chunk_ids"))
    version = "graph_rag_v2" if include_new else "graph_rag_v1"
    payload = {
        "version": version,
        "generated_for": "round4_graphrag_expand_v2" if include_new else "round4_graphrag_ab",
        "count": len(items),
        "live_with_chunks": live_ok,
        "doc_categories": cats,
        "intents": intents,
        "items": items,
        "missing": missing,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(out),
                "version": version,
                "count": len(items),
                "live_with_chunks": live_ok,
                "doc_categories": cats,
                "intents": intents,
                "missing": missing,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    no_live = [m for m in missing if m.endswith(":no_live_chunks")]
    min_n = 35 if include_new else 20
    ok = live_ok >= min_n and len(no_live) == 0 and len(cats) >= (3 if include_new else 1) and len(intents) >= 5
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
