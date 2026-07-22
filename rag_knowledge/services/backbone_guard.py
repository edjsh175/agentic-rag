"""Product backbone constraints shared by extract staging and rebuild-safe review."""
from __future__ import annotations

import json
from pathlib import Path

CONFLICT_REASON = "conflicts_product_backbone"
BACKBONE_CONTEXT_MAX_CHARS = 3000


def load_backbone_constraints(path: Path | None = None) -> dict:
    root = Path(__file__).resolve().parents[2]
    backbone_path = path or (root / "data" / "product_relation_backbone.json")
    if not backbone_path.is_file():
        return {
            "belongs_to": {},
            "different_from": set(),
            "requires": set(),
            "relations": [],
            "canonical_by_alias": {},
            "entity_type_by_name": {},
            "doc_categories": set(),
        }

    data = json.loads(backbone_path.read_text(encoding="utf-8"))
    belongs_to: dict[str, set[str]] = {}
    different_from: set[frozenset[str]] = set()
    requires: set[tuple[str, str]] = set()
    relations: list[dict] = []
    canonical_by_alias: dict[str, str] = {}
    entity_type_by_name: dict[str, str] = {}
    doc_categories: set[str] = set()

    for item in data.get("entities") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        entity_type = str(item.get("entity_type") or "").strip()
        if not name:
            continue
        if entity_type:
            entity_type_by_name[name] = entity_type
        canonical_by_alias[name] = name
        doc_category = str(item.get("doc_category") or "").strip()
        if doc_category:
            doc_categories.add(doc_category)
        for alias in item.get("aliases") or []:
            alias_name = str(alias or "").strip()
            if alias_name:
                canonical_by_alias[alias_name] = name

    for item in data.get("relations") or []:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").strip()
        target = str(item.get("target") or "").strip()
        relation_type = str(item.get("relation_type") or "").strip()
        if not source or not target or not relation_type:
            continue
        relations.append({"source": source, "relation_type": relation_type, "target": target})
        if relation_type == "belongs_to":
            belongs_to.setdefault(source, set()).add(target)
        elif relation_type == "different_from":
            different_from.add(frozenset({source, target}))
        elif relation_type == "requires":
            requires.add((source, target))

    return {
        "belongs_to": belongs_to,
        "different_from": different_from,
        "requires": requires,
        "relations": relations,
        "canonical_by_alias": canonical_by_alias,
        "entity_type_by_name": entity_type_by_name,
        "doc_categories": doc_categories,
    }


def resolve_canonical(name: str, constraints: dict) -> str:
    value = str(name or "").strip()
    if not value:
        return ""
    aliases = constraints.get("canonical_by_alias") or {}
    return str(aliases.get(value) or value)


def relation_conflicts_with_backbone(payload: dict, constraints: dict) -> bool:
    return bool(describe_relation_conflict(payload, constraints))


def describe_relation_conflict(payload: dict, constraints: dict) -> str:
    source_raw = str(payload.get("source_name") or "").strip()
    target_raw = str(payload.get("target_name") or "").strip()
    relation_type = str(payload.get("relation_type") or "").strip()
    if not source_raw or not target_raw or not relation_type:
        return ""

    source = resolve_canonical(source_raw, constraints)
    target = resolve_canonical(target_raw, constraints)
    belongs_to = constraints.get("belongs_to") or {}
    different_from = constraints.get("different_from") or set()

    if relation_type == "belongs_to" and source in belongs_to and target not in belongs_to[source]:
        allowed = ",".join(sorted(belongs_to[source]))
        return (
            f"belongs_to conflict: {source_raw}(->{source}) must belong to [{allowed}], "
            f"got {target_raw}(->{target})"
        )

    if relation_type == "alias_of" and frozenset({source, target}) in different_from:
        return f"alias_of conflicts different_from: {source} vs {target}"

    if relation_type == "different_from":
        # Affirming an official different_from edge is allowed.
        return ""

    return ""


def entity_type_conflicts_with_backbone(payload: dict, constraints: dict) -> bool:
    return bool(describe_entity_type_conflict(payload, constraints))


def describe_entity_type_conflict(payload: dict, constraints: dict) -> str:
    name_raw = str(payload.get("name") or "").strip()
    entity_type = str(payload.get("entity_type") or "").strip()
    if not name_raw or not entity_type:
        return ""
    canonical = resolve_canonical(name_raw, constraints)
    expected = (constraints.get("entity_type_by_name") or {}).get(canonical)
    if expected and expected != entity_type:
        return (
            f"entity type conflict: {name_raw}(->{canonical}) backbone={expected}, "
            f"candidate={entity_type}"
        )
    return ""


def alias_conflicts_with_backbone(payload: dict, constraints: dict) -> bool:
    return bool(describe_alias_conflict(payload, constraints))


def describe_alias_conflict(payload: dict, constraints: dict) -> str:
    entity_raw = str(payload.get("entity_name") or "").strip()
    alias_raw = str(payload.get("alias") or "").strip()
    if not entity_raw or not alias_raw:
        return ""
    entity = resolve_canonical(entity_raw, constraints)
    alias = resolve_canonical(alias_raw, constraints)
    if not entity or not alias or entity == alias:
        return ""
    different_from = constraints.get("different_from") or set()
    if frozenset({entity, alias}) in different_from:
        return f"alias conflicts different_from: {entity} vs {alias}"
    return ""


def describe_conflict(kind: str, payload: dict, constraints: dict) -> str:
    if kind == "entity":
        return describe_entity_type_conflict(payload, constraints)
    if kind == "relation":
        return describe_relation_conflict(payload, constraints)
    if kind == "alias":
        return describe_alias_conflict(payload, constraints)
    return ""


def format_backbone_context(
    constraints: dict | None = None,
    *,
    max_chars: int = BACKBONE_CONTEXT_MAX_CHARS,
) -> str:
    """Medium backbone summary for LLM prompt injection."""
    constraints = constraints if constraints is not None else load_backbone_constraints()
    types = constraints.get("entity_type_by_name") or {}
    relations = constraints.get("relations") or []
    if not types and not relations:
        return "(none)"

    lines: list[str] = ["Official product backbone (do not contradict):", "Entities:"]
    for name in sorted(types.keys()):
        lines.append(f"- {name} ({types[name]})")
        # Include a few aliases for readability
        aliases = [
            alias
            for alias, canonical in (constraints.get("canonical_by_alias") or {}).items()
            if canonical == name and alias != name
        ]
        if aliases:
            lines.append(f"  aliases: {', '.join(sorted(aliases)[:6])}")

    interesting = {"belongs_to", "requires", "different_from", "depends_on"}
    edge_lines = [
        f"- {item['source']} -[{item['relation_type']}]-> {item['target']}"
        for item in relations
        if item.get("relation_type") in interesting
    ]
    if edge_lines:
        lines.append("Relations:")
        lines.extend(edge_lines)

    text = "\n".join(lines)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20].rstrip() + "\n... (truncated)"


def rule_result_hits_backbone(result: object, constraints: dict) -> bool:
    """True if rule ExtractionResult already mentions a backbone entity."""
    types = constraints.get("entity_type_by_name") or {}
    if not types:
        return False
    entities = getattr(result, "entities", None) or []
    for item in entities:
        name = str(getattr(item, "name", "") or "").strip()
        if not name:
            continue
        if resolve_canonical(name, constraints) in types:
            return True
    return False


def chunk_in_backbone_neighborhood(chunk: dict, constraints: dict) -> bool:
    """Whether a chunk is near the official product backbone (for LLM scoping).

    If backbone is empty, returns True so LLM is not accidentally disabled.
    """
    types = constraints.get("entity_type_by_name") or {}
    if not types:
        return True

    metadata = chunk.get("metadata") or {}
    doc_category = str(metadata.get("doc_category") or "").strip()
    doc_categories = constraints.get("doc_categories") or set()
    if doc_category and doc_category in doc_categories:
        return True

    section_path = str(metadata.get("section_path") or "")
    content = str(chunk.get("content") or "")
    haystack = f"{section_path}\n{content}"
    # Longer names first to reduce accidental short-token hits.
    terms = sorted((constraints.get("canonical_by_alias") or {}).keys(), key=len, reverse=True)
    for term in terms:
        if len(term) < 2:
            continue
        if term in haystack:
            return True
    return False
