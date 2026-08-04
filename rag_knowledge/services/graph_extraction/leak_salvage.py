"""Leak salvage — rule-gated second LLM extract when first pass misses business leaves."""
from __future__ import annotations

from dataclasses import replace

from rag_knowledge.services.graph_extraction import ExtractionResult


BUSINESS_ENTITY_TYPES = frozenset({
    "Procedure",
    "Step",
    "Feature",
    "ConfigItem",
    "Error",
    "Solution",
    "Command",
    "Constraint",
    "EnvironmentComponent",
    "DataTable",
    "Field",
})

# Leaf/title keywords that historically leaked as Section-only (Round-2 samples).
BUSINESS_KEYWORD_HINTS = (
    "映射",
    "安装",
    "配置",
    "错误",
    "导出",
    "流程",
    "参数",
    "驱动",
    "环境",
    "蓝图",
    "加密",
    "投影",
    "运行",
)


def section_leaf(section_path: str) -> str:
    path = str(section_path or "").strip()
    if not path:
        return ""
    parts = [p.strip() for p in path.replace("::", ">").split(">") if p.strip()]
    return parts[-1] if parts else ""


def section_depth(section_path: str) -> int:
    path = str(section_path or "").strip()
    if not path:
        return 0
    return len([p for p in path.replace("::", ">").split(">") if p.strip()])


def count_business_entities(result: ExtractionResult | None) -> int:
    if result is None:
        return 0
    return sum(1 for e in result.entities if str(e.entity_type or "") in BUSINESS_ENTITY_TYPES)


def assess_leak_risk(
    chunk: dict,
    llm_result: ExtractionResult | None,
    *,
    rule_result: ExtractionResult | None = None,
) -> str | None:
    """
    Return a leak reason if a salvage pass is warranted; else None.

    Only triggers when first LLM yield has no business entities (Section-only / empty).
    """
    business = count_business_entities(llm_result) + count_business_entities(rule_result)
    if business > 0:
        return None

    metadata = chunk.get("metadata") or {}
    section_path = str(metadata.get("section_path") or "")
    content = str(chunk.get("content") or "")
    leaf = section_leaf(section_path)
    haystack = f"{leaf}\n{content[:800]}"

    if any(kw in haystack for kw in BUSINESS_KEYWORD_HINTS):
        return "keyword_suggests_business_entity"
    if section_depth(section_path) >= 3 and len(leaf) >= 2:
        return "deep_section_no_business_entity"
    return None


def build_salvage_note(reason: str, chunk: dict) -> str:
    metadata = chunk.get("metadata") or {}
    leaf = section_leaf(str(metadata.get("section_path") or ""))
    focus = leaf or "当前段落中的业务能力/配置/错误/步骤"
    return (
        "\n\n# Salvage Pass (second extract)\n"
        f"Previous extraction produced no business entities (Procedure/Feature/ConfigItem/Error/…). "
        f"Suspected miss reason: {reason}.\n"
        f"Focus on the leaf topic「{focus}」and extract concrete business entities with evidence. "
        "Do NOT only emit Section. Prefer Procedure/Feature/ConfigItem/Error/Command when the text supports them.\n"
    )


def merge_salvage_result(
    primary: ExtractionResult,
    salvage: ExtractionResult,
) -> tuple[ExtractionResult, int, int]:
    """
    Merge salvage entities/relations not already present.
    Tag new items via properties.created_by = llm:leak_salvage when possible.
    Returns (merged, entities_added, relations_added).
    """
    existing_entities = {(e.name, e.entity_type) for e in primary.entities}
    existing_relations = {
        (r.source_name, r.relation_type, r.target_name) for r in primary.relations
    }
    entities_added = 0
    relations_added = 0

    for entity in salvage.entities:
        key = (entity.name, entity.entity_type)
        if key in existing_entities:
            continue
        props = dict(entity.properties or {})
        props["created_by"] = "llm:leak_salvage"
        props["salvage"] = True
        try:
            tagged = replace(entity, properties=props)
        except TypeError:
            tagged = entity
        primary.entities.append(tagged)
        existing_entities.add(key)
        entities_added += 1

    for relation in salvage.relations:
        key = (relation.source_name, relation.relation_type, relation.target_name)
        if key in existing_relations:
            continue
        primary.relations.append(relation)
        existing_relations.add(key)
        relations_added += 1
        meta_key = key
        if meta_key in salvage.relation_metadata:
            primary.relation_metadata[meta_key] = {
                **salvage.relation_metadata[meta_key],
                "created_by": "llm:leak_salvage",
                "salvage": True,
            }

    for diagnostic in salvage.diagnostics:
        if diagnostic not in primary.diagnostics:
            primary.diagnostics.append(diagnostic)
    for alias in salvage.aliases:
        if alias not in primary.aliases:
            primary.aliases.append(alias)

    return primary, entities_added, relations_added
