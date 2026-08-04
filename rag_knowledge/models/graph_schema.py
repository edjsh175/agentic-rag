"""
知识图谱本体定义 (Knowledge Graph Ontology Schema)

集中管理图谱的实体类型、关系类型、审核状态、置信度规则、
合法关系组合验证，以及实体归一化规则。

三层图谱结构：
  第一层：文档结构图谱 — 来源、章节、chunk、证据链
  第二层：领域概念图谱 — 产品、工具、服务、数据表、字段、配置项
  第三层：业务能力图谱 — 操作流程、步骤、错误、解决方案
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# =====================================================================
# 实体类型枚举
# =====================================================================

class EntityType(str, Enum):
    """图谱实体类型 — 覆盖三层图谱"""
    # 第一层：文档结构
    document = "Document"
    section = "Section"

    # 第二层：领域概念
    product = "Product"
    tool = "Tool"
    service = "Service"
    module = "Module"
    function_area = "FunctionArea"
    feature = "Feature"              # 能力概念 (如 材质映射、矢量切片)
    constraint = "Constraint"        # 限制/参数 (如 EPSG:4490、端口 6379)
    data_table = "DataTable"
    field = "Field"
    config_item = "ConfigItem"
    format = "Format"

    # 第三层：业务能力
    procedure = "Procedure"
    step = "Step"
    error = "Error"
    solution = "Solution"

    # LLM/Ontology extension
    environment_component = "EnvironmentComponent"
    command = "Command"


# =====================================================================
# 关系类型枚举
# =====================================================================

class RelationType(str, Enum):
    """图谱关系类型"""
    # 文档结构关系
    has_section = "has_section"       # Document/Section/DataTable -> Section
    has_chunk = "has_chunk"           # Section -> Chunk
    defined_in = "defined_in"        # Entity -> Document/Section
    derived_from = "derived_from"      # Entity -> Chunk (知识实体的证据来源)

    # 领域概念关系
    alias_of = "alias_of"            # Entity -> Entity (同一事物不同叫法)
    different_from = "different_from" # Entity -> Entity (明确不同的概念)
    belongs_to = "belongs_to"        # 子 -> 父 (PipelineBuilder -> StampTools)
    has_table = "has_table"          # Tool -> DataTable
    has_field = "has_field"          # DataTable -> Field
    uses_config = "uses_config"      # Service/Tool -> ConfigItem
    supports_format = "supports_format"  # Tool -> Format
    produces = "produces"            # Tool/Service -> DataObject
    consumes = "consumes"            # Tool/Service -> DataObject
    requires = "requires"            # Entity -> Entity (依赖)

    # 业务能力关系
    has_step = "has_step"            # Procedure -> Step
    causes = "causes"               # Error -> 症状/后果
    solved_by = "solved_by"         # Error -> Solution

    # LLM/Ontology extension
    depends_on = "depends_on"
    has_procedure = "has_procedure"
    runs_command = "runs_command"
    configured_by = "configured_by"


# =====================================================================
# 审核状态
# =====================================================================

class ReviewStatus(str, Enum):
    """审核状态"""
    approved = "approved"   # 已审核通过
    pending = "pending"     # 待审核
    rejected = "rejected"   # 已拒绝


# =====================================================================
# 证据来源类型
# =====================================================================

class EvidenceSource(str, Enum):
    """实体/关系的来源标识"""
    rule_section_path = "rule:section_path"   # 从 section_path 规则抽取
    rule_table = "rule:table"                 # 从 Markdown 表格规则抽取
    rule_code_block = "rule:code_block"       # 从代码块规则抽取
    llm_extraction = "llm:extraction"         # LLM 辅助抽取
    manual = "manual"                         # 人工录入


# =====================================================================
# 合法关系组合（source_type -> relation_type -> target_type）
# =====================================================================

# 每个 relation_type 允许的 (source_type, target_type) 组合
VALID_RELATION_PAIRS: dict[str, list[tuple[str, str]]] = {
    # 文档结构
    RelationType.has_section: [
        (EntityType.document, EntityType.section),
        (EntityType.section, EntityType.section),
        (EntityType.data_table, EntityType.section),
    ],
    RelationType.defined_in: [
        (entity_type, target_type)
        for entity_type in EntityType
        if entity_type is not EntityType.document
        for target_type in (EntityType.document, EntityType.section, EntityType.function_area)
    ],
    # 领域概念
    RelationType.belongs_to: [
        (EntityType.tool, EntityType.product),
        (EntityType.tool, EntityType.module),
        (EntityType.service, EntityType.product),
        (EntityType.service, EntityType.module),
        (EntityType.module, EntityType.product),
        (EntityType.module, EntityType.module),
        (EntityType.module, EntityType.tool),
        (EntityType.function_area, EntityType.tool),
        (EntityType.function_area, EntityType.service),
        (EntityType.function_area, EntityType.function_area),
        (EntityType.feature, EntityType.function_area),
        (EntityType.feature, EntityType.tool),
        (EntityType.feature, EntityType.service),
        (EntityType.constraint, EntityType.feature),
        (EntityType.constraint, EntityType.procedure),
        (EntityType.constraint, EntityType.tool),
        (EntityType.constraint, EntityType.service),
        (EntityType.constraint, EntityType.function_area),
        (EntityType.data_table, EntityType.tool),
        (EntityType.data_table, EntityType.function_area),
        (EntityType.config_item, EntityType.service),
        (EntityType.config_item, EntityType.tool),
        (EntityType.config_item, EntityType.function_area),
        (EntityType.procedure, EntityType.feature),
        (EntityType.procedure, EntityType.tool),
        (EntityType.procedure, EntityType.service),
        (EntityType.procedure, EntityType.function_area),
        (EntityType.environment_component, EntityType.product),
        (EntityType.environment_component, EntityType.tool),
    ],
    RelationType.has_table: [
        (EntityType.tool, EntityType.data_table),
        (EntityType.service, EntityType.data_table),
        (EntityType.function_area, EntityType.data_table),
        (EntityType.feature, EntityType.data_table),
    ],
    RelationType.has_field: [
        (EntityType.data_table, EntityType.field),
    ],
    RelationType.uses_config: [
        (EntityType.service, EntityType.config_item),
        (EntityType.tool, EntityType.config_item),
        (EntityType.function_area, EntityType.config_item),
        (EntityType.feature, EntityType.constraint),
        (EntityType.procedure, EntityType.constraint),
    ],
    RelationType.supports_format: [
        (EntityType.tool, EntityType.format),
        (EntityType.service, EntityType.format),
        (EntityType.feature, EntityType.format),
    ],
    RelationType.has_step: [
        (EntityType.procedure, EntityType.step),
    ],
    RelationType.causes: [
        (EntityType.error, EntityType.error),
        (EntityType.error, EntityType.solution),
        (EntityType.config_item, EntityType.error),
        (EntityType.constraint, EntityType.error),
    ],
    RelationType.solved_by: [
        (EntityType.error, EntityType.solution),
    ],
    RelationType.depends_on: [
        (EntityType.tool, EntityType.environment_component),
        (EntityType.tool, EntityType.tool),
        (EntityType.tool, EntityType.service),
        (EntityType.service, EntityType.environment_component),
        (EntityType.service, EntityType.tool),
        (EntityType.service, EntityType.service),
        (EntityType.environment_component, EntityType.environment_component),
        (EntityType.feature, EntityType.feature),
    ],
    RelationType.has_procedure: [
        (EntityType.tool, EntityType.procedure),
        (EntityType.service, EntityType.procedure),
        (EntityType.product, EntityType.procedure),
        (EntityType.function_area, EntityType.procedure),
        (EntityType.feature, EntityType.procedure),
    ],
    RelationType.runs_command: [
        (EntityType.step, EntityType.command),
        (EntityType.procedure, EntityType.command),
        (EntityType.tool, EntityType.command),
        (EntityType.service, EntityType.command),
        (EntityType.environment_component, EntityType.command),
    ],
    RelationType.configured_by: [
        (EntityType.tool, EntityType.config_item),
        (EntityType.service, EntityType.config_item),
        (EntityType.environment_component, EntityType.config_item),
        (EntityType.feature, EntityType.constraint),
        (EntityType.tool, EntityType.constraint),
        (EntityType.service, EntityType.constraint),
    ],
}

# 这些关系类型不做 source/target 类型限制
UNRESTRICTED_RELATIONS = {
    RelationType.alias_of,
    RelationType.different_from,
    RelationType.produces,
    RelationType.consumes,
    RelationType.requires,
}


# =====================================================================
# 置信度规则
# =====================================================================

@dataclass
class ConfidenceRule:
    """置信度和自动审核规则"""
    rule_based_confidence: float = 1.0
    rule_based_review_status: str = ReviewStatus.approved.value
    llm_based_review_status: str = ReviewStatus.pending.value
    min_confidence_for_retrieval: float = 0.5


# =====================================================================
# 辅助分类词典
# =====================================================================

DATA_SPEC_KEYWORDS: set[str] = {"数据规范", "数据结构", "数据格式"}
SERVICE_KEYWORDS: set[str] = {"服务部署", "服务配置", "Stamp服务部署"}
ERROR_KEYWORDS: set[str] = {"常见错误", "故障排查", "错误处理", "问题解决"}


# =====================================================================
# 实体归一化
# =====================================================================

def normalize_entity_name(name: str) -> str:
    """
    归一化实体名称，用于去重和合并。
    """
    name = name.strip()
    name = re.sub(r'\s+', ' ', name)
    return name


def make_section_entity_name(source: str, section_path: str) -> str:
    """
    为 Section 实体生成唯一名称，与业务实体区分。

    名称清洗与 CandidateNormalizer 对齐（全角括号、空白），避免实体候选被
    归一化后与 relation/link 端点字符串不一致而触发 missing_relation_endpoint。
    """
    source_base = source.rsplit('.', 1)[0] if '.' in source else source
    source_base = " ".join(source_base.strip().replace("（", "(").replace("）", ")").split())
    path = " ".join(section_path.strip().replace("（", "(").replace("）", ")").split())
    return f"{source_base}::{path}"


def make_field_entity_name(table_name: str, field_name: str) -> str:
    """为 Field 实体生成限定名 canonical：{DataTable}.{leaf}。"""
    return f"{normalize_entity_name(table_name)}.{normalize_entity_name(field_name)}"


# =====================================================================
# 关系验证
# =====================================================================

def validate_relation(
    source_type: str,
    relation_type: str,
    target_type: str,
) -> tuple[bool, str]:
    """
    验证关系组合是否合法。
    """
    if relation_type in {r.value for r in UNRESTRICTED_RELATIONS}:
        return True, ""

    valid_pairs = VALID_RELATION_PAIRS.get(relation_type, [])
    if not valid_pairs:
        return True, ""  # 未定义的关系类型不做限制

    for src_type, tgt_type in valid_pairs:
        src_val = src_type.value if isinstance(src_type, Enum) else src_type
        tgt_val = tgt_type.value if isinstance(tgt_type, Enum) else tgt_type
        if source_type == src_val and target_type == tgt_val:
            return True, ""

    return False, (
        f"关系 {relation_type} 不允许 {source_type} → {target_type}，"
        f"允许的组合：{[(s.value, t.value) for s, t in valid_pairs]}"
    )


# =====================================================================
# DDL 定义（v1 初始 Schema）
# =====================================================================

SCHEMA_VERSION = 3

KG_DDL_V1 = """
-- 图谱 Schema 版本表
CREATE TABLE IF NOT EXISTS kg_schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    description TEXT DEFAULT ''
);

-- 实体表 (扩展)
CREATE TABLE IF NOT EXISTS entities (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    canonical_name  TEXT,
    entity_type     TEXT NOT NULL,
    description     TEXT DEFAULT '',
    properties_json TEXT DEFAULT '{}',
    doc_category    TEXT DEFAULT '',
    confidence      REAL DEFAULT 1.0,
    review_status   TEXT DEFAULT 'approved',
    created_by      TEXT NOT NULL DEFAULT 'system',
    created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_name ON entities(name);
CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_canonical_unique
ON entities(canonical_name)
WHERE canonical_name IS NOT NULL AND canonical_name != '';

CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_entities_category ON entities(doc_category);
CREATE INDEX IF NOT EXISTS idx_entities_review ON entities(review_status);

-- 别名表 (新增)
CREATE TABLE IF NOT EXISTS aliases (
    id              TEXT PRIMARY KEY,
    entity_id       TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    alias           TEXT NOT NULL,
    confidence      REAL DEFAULT 1.0,
    source_chunk_id TEXT DEFAULT '',
    evidence_text   TEXT DEFAULT '',
    review_status   TEXT DEFAULT 'pending',
    created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_aliases_entity ON aliases(entity_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_aliases_alias_unique ON aliases(alias);

-- 关系表 (扩展)
CREATE TABLE IF NOT EXISTS relations (
    id                TEXT PRIMARY KEY,
    source_entity_id  TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    target_entity_id  TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    relation_type     TEXT NOT NULL,
    properties_json   TEXT DEFAULT '{}',
    confidence        REAL DEFAULT 1.0,
    evidence_text     TEXT DEFAULT '',
    source_chunk_id   TEXT DEFAULT '',
    review_status     TEXT DEFAULT 'approved',
    created_by        TEXT NOT NULL DEFAULT 'system',
    created_at        TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(source_entity_id, target_entity_id, relation_type)
);
CREATE INDEX IF NOT EXISTS idx_relations_source ON relations(source_entity_id);
CREATE INDEX IF NOT EXISTS idx_relations_target ON relations(target_entity_id);
CREATE INDEX IF NOT EXISTS idx_relations_type ON relations(relation_type);
CREATE INDEX IF NOT EXISTS idx_relations_review ON relations(review_status);

-- 实体-知识块链接表 (扩展)
CREATE TABLE IF NOT EXISTS entity_chunk_links (
    id              TEXT PRIMARY KEY,
    entity_id       TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    chunk_id        TEXT NOT NULL,
    link_type       TEXT NOT NULL DEFAULT 'primary',
    section_path    TEXT DEFAULT '',
    page_label      TEXT DEFAULT '',
    evidence_text   TEXT DEFAULT '',
    source          TEXT DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(entity_id, chunk_id)
);
CREATE INDEX IF NOT EXISTS idx_links_entity ON entity_chunk_links(entity_id);
CREATE INDEX IF NOT EXISTS idx_links_chunk ON entity_chunk_links(chunk_id);

-- 字段表 (新增)
CREATE TABLE IF NOT EXISTS fields (
    id                TEXT PRIMARY KEY,
    table_entity_id   TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    field_name        TEXT NOT NULL,
    description       TEXT DEFAULT '',
    required          INTEGER DEFAULT 0,
    unit              TEXT DEFAULT '',
    value_range       TEXT DEFAULT '',
    source_chunk_id   TEXT DEFAULT '',
    created_at        TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_fields_table ON fields(table_entity_id);

-- 操作流程表 (新增)
CREATE TABLE IF NOT EXISTS procedures (
    id          TEXT PRIMARY KEY,
    entity_id   TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_procedures_entity ON procedures(entity_id);

-- 流程步骤表 (新增)
CREATE TABLE IF NOT EXISTS procedure_steps (
    id              TEXT PRIMARY KEY,
    procedure_id    TEXT NOT NULL REFERENCES procedures(id) ON DELETE CASCADE,
    step_order      INTEGER NOT NULL,
    step_name       TEXT NOT NULL,
    description     TEXT DEFAULT '',
    source_chunk_id TEXT DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_steps_procedure ON procedure_steps(procedure_id);
"""

KG_DDL_V2 = """
-- 图谱 Schema 版本表
CREATE TABLE IF NOT EXISTS kg_schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    description TEXT DEFAULT ''
);

-- 实体表 (扩展)
CREATE TABLE IF NOT EXISTS entities (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    canonical_name  TEXT,
    entity_type     TEXT NOT NULL,
    description     TEXT DEFAULT '',
    properties_json TEXT DEFAULT '{}',
    doc_category    TEXT DEFAULT '',
    confidence      REAL DEFAULT 1.0,
    review_status   TEXT DEFAULT 'approved',
    created_by      TEXT NOT NULL DEFAULT 'system',
    created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_name ON entities(name);
CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_canonical_unique
ON entities(canonical_name)
WHERE canonical_name IS NOT NULL AND canonical_name != '';

CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_entities_category ON entities(doc_category);
CREATE INDEX IF NOT EXISTS idx_entities_review ON entities(review_status);

-- 别名表 (新增)
CREATE TABLE IF NOT EXISTS aliases (
    id              TEXT PRIMARY KEY,
    entity_id       TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    alias           TEXT NOT NULL,
    confidence      REAL DEFAULT 1.0,
    source_chunk_id TEXT DEFAULT '',
    evidence_text   TEXT DEFAULT '',
    review_status   TEXT DEFAULT 'pending',
    created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_aliases_entity ON aliases(entity_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_aliases_alias_unique ON aliases(alias);

-- 关系表 (扩展)
CREATE TABLE IF NOT EXISTS relations (
    id                TEXT PRIMARY KEY,
    source_entity_id  TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    target_entity_id  TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    relation_type     TEXT NOT NULL,
    properties_json   TEXT DEFAULT '{}',
    confidence        REAL DEFAULT 1.0,
    evidence_text     TEXT DEFAULT '',
    source_chunk_id   TEXT DEFAULT '',
    review_status     TEXT DEFAULT 'approved',
    created_by        TEXT NOT NULL DEFAULT 'system',
    created_at        TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(source_entity_id, target_entity_id, relation_type)
);
CREATE INDEX IF NOT EXISTS idx_relations_source ON relations(source_entity_id);
CREATE INDEX IF NOT EXISTS idx_relations_target ON relations(target_entity_id);
CREATE INDEX IF NOT EXISTS idx_relations_type ON relations(relation_type);
CREATE INDEX IF NOT EXISTS idx_relations_review ON relations(review_status);

-- 实体-知识块链接表 (扩展)
CREATE TABLE IF NOT EXISTS entity_chunk_links (
    id              TEXT PRIMARY KEY,
    entity_id       TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    chunk_id        TEXT NOT NULL,
    link_type       TEXT NOT NULL DEFAULT 'primary',
    section_path    TEXT DEFAULT '',
    page_label      TEXT DEFAULT '',
    evidence_text   TEXT DEFAULT '',
    source          TEXT DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(entity_id, chunk_id)
);
CREATE INDEX IF NOT EXISTS idx_links_entity ON entity_chunk_links(entity_id);
CREATE INDEX IF NOT EXISTS idx_links_chunk ON entity_chunk_links(chunk_id);

-- 字段表 (新增 v2)
CREATE TABLE IF NOT EXISTS fields (
    id                TEXT PRIMARY KEY,
    table_entity_id   TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    field_name        TEXT NOT NULL,
    description       TEXT DEFAULT '',
    required          INTEGER DEFAULT 0,
    unit              TEXT DEFAULT '',
    value_range       TEXT DEFAULT '',
    source_chunk_id   TEXT DEFAULT '',
    created_at        TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    field_entity_id   TEXT REFERENCES entities(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_fields_table ON fields(table_entity_id);
CREATE INDEX IF NOT EXISTS idx_fields_field_entity ON fields(field_entity_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_fields_table_field_unique
ON fields(table_entity_id, field_name);

-- 操作流程表 (新增)
CREATE TABLE IF NOT EXISTS procedures (
    id          TEXT PRIMARY KEY,
    entity_id   TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_procedures_entity ON procedures(entity_id);

-- 流程步骤表 (新增)
CREATE TABLE IF NOT EXISTS procedure_steps (
    id              TEXT PRIMARY KEY,
    procedure_id    TEXT NOT NULL REFERENCES procedures(id) ON DELETE CASCADE,
    step_order      INTEGER NOT NULL,
    step_name       TEXT NOT NULL,
    description     TEXT DEFAULT '',
    source_chunk_id TEXT DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_steps_procedure ON procedure_steps(procedure_id);
"""

KG_DDL_V3_STAGING = """
CREATE TABLE IF NOT EXISTS extraction_batches (
    id                   TEXT PRIMARY KEY,
    mode                 TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'draft',
    source_snapshot_hash TEXT NOT NULL,
    filters_json         TEXT NOT NULL DEFAULT '{}',
    stats_json           TEXT NOT NULL DEFAULT '{}',
    error_text           TEXT NOT NULL DEFAULT '',
    created_at           TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    reviewed_at          TEXT,
    applied_at           TEXT
);
CREATE INDEX IF NOT EXISTS idx_extraction_batches_status ON extraction_batches(status);
CREATE INDEX IF NOT EXISTS idx_extraction_batches_snapshot ON extraction_batches(source_snapshot_hash);

CREATE TABLE IF NOT EXISTS extraction_candidates (
    id                TEXT PRIMARY KEY,
    batch_id          TEXT NOT NULL REFERENCES extraction_batches(id) ON DELETE CASCADE,
    candidate_kind    TEXT NOT NULL,
    fingerprint       TEXT NOT NULL,
    payload_json      TEXT NOT NULL,
    source_chunk_id   TEXT NOT NULL DEFAULT '',
    evidence_text     TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'pending',
    rejection_reason  TEXT NOT NULL DEFAULT '',
    applied_target_id TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    reviewed_at       TEXT,
    applied_at        TEXT,
    UNIQUE(batch_id, fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_extraction_candidates_batch ON extraction_candidates(batch_id);
CREATE INDEX IF NOT EXISTS idx_extraction_candidates_status ON extraction_candidates(status);
"""

KG_DDL_V3 = KG_DDL_V2 + KG_DDL_V3_STAGING

LEGACY_TABLE_NAMES = {"entities", "relations", "entity_chunk_links"}
NEW_TABLE_NAMES = {
    "kg_schema_version", "entities", "aliases", "relations",
    "entity_chunk_links", "fields", "procedures", "procedure_steps",
    "extraction_batches", "extraction_candidates",
}
