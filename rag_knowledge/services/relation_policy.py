"""Shared relation semantics for EvidenceScope and graph traversal."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RelationRule:
    """Stable semantic policy for one graph relation type."""

    identity_equivalent: bool = False
    scope_traversal: bool = False
    # Candidate expansion is deliberately distinct from evidence authorization.
    # ``scope_traversal`` remains only as a legacy compatibility field.
    candidate_expansion: str = "none"
    graph_intents: frozenset[str] = frozenset()
    answer_evidence: bool = False
    evidence_intents: frozenset[str] = frozenset()
    weak_provenance: bool = False
    path_composable: bool = False


RELATION_RULES: dict[str, RelationRule] = {
    "alias_of": RelationRule(
        identity_equivalent=True,
        graph_intents=frozenset({"definition"}),
        answer_evidence=True,
        evidence_intents=frozenset({"definition", "comparison", "general_qa"}),
    ),
    "belongs_to": RelationRule(
        scope_traversal=True,
        candidate_expansion="strong",
        graph_intents=frozenset({"procedure", "deployment", "config", "definition", "comparison"}),
        answer_evidence=True,
        evidence_intents=frozenset({"definition", "comparison", "deployment", "procedure", "general_qa", "multi_entity_relation"}),
    ),
    "depends_on": RelationRule(
        scope_traversal=True,
        candidate_expansion="medium",
        graph_intents=frozenset({"procedure", "deployment", "troubleshooting", "comparison"}),
        answer_evidence=True,
        evidence_intents=frozenset({"procedure", "deployment", "troubleshooting", "comparison", "general_qa", "multi_entity_relation"}),
    ),
    "requires": RelationRule(
        scope_traversal=True,
        candidate_expansion="strong",
        graph_intents=frozenset({"procedure", "deployment", "comparison", "troubleshooting"}),
        answer_evidence=True,
        evidence_intents=frozenset({"procedure", "deployment", "troubleshooting", "comparison", "general_qa", "multi_entity_relation"}),
    ),
    "has_service": RelationRule(
        scope_traversal=True,
        candidate_expansion="strong",
        graph_intents=frozenset({"deployment", "config", "procedure"}),
        answer_evidence=True,
        evidence_intents=frozenset({"deployment", "config", "procedure", "general_qa", "multi_entity_relation"}),
    ),
    "has_module": RelationRule(
        scope_traversal=True,
        candidate_expansion="strong",
        graph_intents=frozenset({"deployment", "config", "procedure"}),
        answer_evidence=True,
        evidence_intents=frozenset({"deployment", "config", "procedure", "general_qa", "multi_entity_relation"}),
    ),
    "implements": RelationRule(
        candidate_expansion="strong",
        graph_intents=frozenset({"definition", "comparison"}),
        answer_evidence=True,
        evidence_intents=frozenset({"definition", "comparison", "general_qa", "multi_entity_relation"}),
    ),
    "uses": RelationRule(
        candidate_expansion="medium",
        graph_intents=frozenset({"procedure", "config"}),
        answer_evidence=True,
        evidence_intents=frozenset({"procedure", "config", "general_qa", "multi_entity_relation"}),
    ),
    "related_to": RelationRule(
        candidate_expansion="weak",
        graph_intents=frozenset({"definition", "comparison"}),
        answer_evidence=False,
    ),
    "different_from": RelationRule(
        scope_traversal=True,
        candidate_expansion="none",
        graph_intents=frozenset({"definition", "comparison"}),
        answer_evidence=True,
        evidence_intents=frozenset({"definition", "comparison", "general_qa", "multi_entity_relation"}),
    ),
    "has_step": RelationRule(
        graph_intents=frozenset({"procedure"}),
        answer_evidence=True,
        evidence_intents=frozenset({"procedure", "general_qa"}),
    ),
    "defined_in": RelationRule(
        graph_intents=frozenset({
            "procedure",
            "deployment",
            "config",
            "definition",
            "comparison",
            "troubleshooting",
        }),
        answer_evidence=True,
        evidence_intents=frozenset({
            "procedure",
            "deployment",
            "config",
            "definition",
            "comparison",
            "troubleshooting",
            "general_qa",
        }),
    ),
    "uses_config": RelationRule(
        graph_intents=frozenset({"deployment", "config", "troubleshooting"}),
        answer_evidence=True,
        evidence_intents=frozenset({"deployment", "config", "troubleshooting", "general_qa"}),
    ),
    "has_table": RelationRule(
        graph_intents=frozenset({"config"}),
        answer_evidence=True,
        evidence_intents=frozenset({"config", "general_qa"}),
    ),
    "has_field": RelationRule(
        graph_intents=frozenset({"config"}),
        answer_evidence=True,
        evidence_intents=frozenset({"config", "general_qa"}),
    ),
    "causes": RelationRule(
        graph_intents=frozenset({"troubleshooting"}),
        answer_evidence=True,
        evidence_intents=frozenset({"troubleshooting", "general_qa"}),
    ),
    "solved_by": RelationRule(
        graph_intents=frozenset({"troubleshooting"}),
        answer_evidence=True,
        evidence_intents=frozenset({"troubleshooting", "general_qa"}),
    ),
    "mentions": RelationRule(weak_provenance=True, candidate_expansion="none", answer_evidence=False),
}


SCOPE_TRAVERSAL_RELATIONS: frozenset[str] = frozenset(
    relation_type
    for relation_type, rule in RELATION_RULES.items()
    if rule.scope_traversal
)

GRAPH_RELATIONS_BY_INTENT: dict[str, frozenset[str]] = {
    intent: frozenset(
        relation_type
        for relation_type, rule in RELATION_RULES.items()
        if intent in rule.graph_intents
    )
    for intent in (
        "procedure",
        "deployment",
        "config",
        "definition",
        "comparison",
        "troubleshooting",
        "multi_entity_relation",
    )
}


def relation_rule(relation_type: str) -> RelationRule | None:
    return RELATION_RULES.get(str(relation_type or "").strip())


def is_scope_traversal_relation(relation_type: str) -> bool:
    rule = relation_rule(relation_type)
    return bool(rule and rule.scope_traversal)


def is_candidate_expansion_relation(relation_type: str) -> bool:
    """Whether an approved graph edge may produce candidate text chunk searches."""
    rule = relation_rule(relation_type)
    return bool(rule and rule.candidate_expansion != "none")


def is_answer_evidence_relation(relation_type: str, intent: str | None = None) -> bool:
    """Whether an approved graph edge has qualification to be admitted as factual Evidence."""
    rule = relation_rule(relation_type)
    if not rule or not rule.answer_evidence:
        return False
    if intent:
        norm_intent = str(intent or "").strip().lower()
        if norm_intent and norm_intent not in rule.evidence_intents:
            return False
    return True


def graph_relations_for_intent(intent: str) -> frozenset[str]:
    return GRAPH_RELATIONS_BY_INTENT.get(
        str(intent or "").strip(),
        GRAPH_RELATIONS_BY_INTENT.get("definition", frozenset()),
    )
