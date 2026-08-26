"""Shared relation semantics for EvidenceScope and graph traversal."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RelationRule:
    """Stable semantic policy for one graph relation type."""

    identity_equivalent: bool = False
    scope_traversal: bool = False
    # Candidate expansion is deliberately distinct from evidence authorization.
    # ``scope_traversal`` remains only as a legacy compatibility field.
    candidate_expansion: str = "none"
    graph_intents: frozenset[str] = frozenset()
    weak_provenance: bool = False


RELATION_RULES: dict[str, RelationRule] = {
    "alias_of": RelationRule(
        identity_equivalent=True,
        graph_intents=frozenset({"definition"}),
    ),
    "belongs_to": RelationRule(
        scope_traversal=True,
        candidate_expansion="strong",
        graph_intents=frozenset({"procedure", "deployment", "config", "definition", "comparison"}),
    ),
    "depends_on": RelationRule(scope_traversal=True, candidate_expansion="medium"),
    "requires": RelationRule(
        scope_traversal=True,
        candidate_expansion="strong",
        graph_intents=frozenset({"procedure", "deployment", "comparison", "troubleshooting"}),
    ),
    "has_service": RelationRule(scope_traversal=True, candidate_expansion="strong"),
    "has_module": RelationRule(scope_traversal=True, candidate_expansion="strong"),
    "implements": RelationRule(candidate_expansion="strong"),
    "uses": RelationRule(candidate_expansion="medium"),
    "related_to": RelationRule(candidate_expansion="weak"),
    "different_from": RelationRule(
        scope_traversal=True,
        graph_intents=frozenset({"definition", "comparison"}),
    ),
    "has_step": RelationRule(graph_intents=frozenset({"procedure"})),
    "defined_in": RelationRule(
        graph_intents=frozenset({
            "procedure",
            "deployment",
            "config",
            "definition",
            "comparison",
            "troubleshooting",
        }),
    ),
    "uses_config": RelationRule(
        graph_intents=frozenset({"deployment", "config", "troubleshooting"}),
    ),
    "has_table": RelationRule(graph_intents=frozenset({"config"})),
    "has_field": RelationRule(graph_intents=frozenset({"config"})),
    "causes": RelationRule(graph_intents=frozenset({"troubleshooting"})),
    "solved_by": RelationRule(graph_intents=frozenset({"troubleshooting"})),
    "mentions": RelationRule(weak_provenance=True),
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
    )
}


def relation_rule(relation_type: str) -> RelationRule | None:
    return RELATION_RULES.get(str(relation_type or "").strip())


def is_scope_traversal_relation(relation_type: str) -> bool:
    rule = relation_rule(relation_type)
    return bool(rule and rule.scope_traversal)


def is_candidate_expansion_relation(relation_type: str) -> bool:
    """Whether an approved graph edge may produce candidates, never evidence."""
    rule = relation_rule(relation_type)
    return bool(rule and rule.candidate_expansion != "none")


def graph_relations_for_intent(intent: str) -> frozenset[str]:
    return GRAPH_RELATIONS_BY_INTENT.get(
        str(intent or "").strip(),
        GRAPH_RELATIONS_BY_INTENT["definition"],
    )
