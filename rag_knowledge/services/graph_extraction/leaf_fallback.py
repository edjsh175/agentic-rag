"""Leaf-rule fallback when LLM is the primary extraction path (strategy B).

ChapterLeaf / ServerLeaf run after LLM; skip entities/relations already covered
by LLM navigational leaves (Procedure / Format / Command and key relations).
"""
from __future__ import annotations

from rag_knowledge.models.graph_schema import normalize_entity_name
from rag_knowledge.services.graph_extraction import ExtractionResult

LEAF_ENTITY_TYPES = frozenset({"Procedure", "Format", "Command"})
LEAF_RELATION_TYPES = frozenset({"has_procedure", "supports_format", "runs_command"})


def _norm(name: str) -> str:
    return normalize_entity_name(str(name or ""))


def llm_leaf_entity_keys(llm_result: ExtractionResult | None) -> set[tuple[str, str]]:
    if llm_result is None:
        return set()
    return {
        (_norm(ent.name), ent.entity_type)
        for ent in llm_result.entities
        if ent.entity_type in LEAF_ENTITY_TYPES
    }


def llm_leaf_relation_keys(llm_result: ExtractionResult | None) -> set[tuple[str, str, str]]:
    if llm_result is None:
        return set()
    return {
        (_norm(rel.source_name), rel.relation_type, _norm(rel.target_name))
        for rel in llm_result.relations
        if rel.relation_type in LEAF_RELATION_TYPES
    }


def apply_leaf_rule_fallback(
    rule_leaf: ExtractionResult,
    llm_result: ExtractionResult | None,
) -> ExtractionResult:
    """Keep rule leaf candidates not already produced by LLM.

    If LLM emitted no navigational leaves, return rule_leaf unchanged (full fallback).
    """
    if rule_leaf is None:
        return ExtractionResult()
    ent_keys = llm_leaf_entity_keys(llm_result)
    rel_keys = llm_leaf_relation_keys(llm_result)
    if not ent_keys and not rel_keys:
        return rule_leaf

    out = ExtractionResult()
    dropped_names: set[str] = set()
    for ent in rule_leaf.entities:
        key = (_norm(ent.name), ent.entity_type)
        if ent.entity_type in LEAF_ENTITY_TYPES and key in ent_keys:
            dropped_names.add(_norm(ent.name))
            continue
        out.entities.append(ent)

    for rel in rule_leaf.relations:
        if rel.relation_type in LEAF_RELATION_TYPES:
            key = (_norm(rel.source_name), rel.relation_type, _norm(rel.target_name))
            if key in rel_keys:
                continue
        out.relations.append(rel)

    for link in rule_leaf.links:
        if _norm(link.entity_name) in dropped_names:
            continue
        out.links.append(link)

    out.diagnostics.extend(rule_leaf.diagnostics)
    out.aliases.extend(rule_leaf.aliases)
    out.fields.extend(rule_leaf.fields)
    out.relation_metadata.update(rule_leaf.relation_metadata)
    return out
