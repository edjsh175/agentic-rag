from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from langchain_core.documents import Document

from rag_knowledge.services.agent_orchestration.evidence_gate import evaluate_rules
from rag_knowledge.services.agent_orchestration.models import ConversationContext, EvidencePool
from rag_knowledge.services.agent_orchestration.runtime import build_agent_registry
from rag_knowledge.services.conversation_context import UnderstandingResult
from rag_knowledge.services.dialogue_understanding import (
    SemanticTaskContext,
    build_semantic_task_context,
)
from rag_knowledge.services.exploration_grant import ExplorationGrant, ExplorationGrantResolver
from rag_knowledge.services.identity_scope import IdentityScopeResolver
from rag_knowledge.services.query_cache import QueryCache


_TEST_CONSTRAINTS = {
    "entity_type_by_name": {
        "EntityA": "Tool",
        "EntityB": "Tool",
        "ServiceX": "Service",
        "ConfigY": "ConfigItem",
    },
    "canonical_by_alias": {},
    "different_from": set(),
    "relations": [],
}


class _FakeGraphDB:
    def __init__(self):
        self.entities = [
            {"id": "a", "name": "EntityA", "canonical_name": "EntityA", "entity_type": "Tool", "review_status": "approved"},
            {"id": "b", "name": "EntityB", "canonical_name": "EntityB", "entity_type": "Tool", "review_status": "approved"},
            {"id": "svc", "name": "ServiceX", "canonical_name": "ServiceX", "entity_type": "Service", "review_status": "approved"},
            {"id": "cfg", "name": "ConfigY", "canonical_name": "ConfigY", "entity_type": "ConfigItem", "review_status": "approved"},
        ]
        self.relations = [
            {
                "id": "different",
                "source_entity_id": "a",
                "target_entity_id": "b",
                "relation_type": "different_from",
                "source_name": "EntityA",
                "source_type": "Tool",
                "target_name": "EntityB",
                "target_type": "Tool",
                "review_status": "approved",
            },
            {
                "id": "dep",
                "source_entity_id": "a",
                "target_entity_id": "svc",
                "relation_type": "depends_on",
                "source_name": "EntityA",
                "source_type": "Tool",
                "target_name": "ServiceX",
                "target_type": "Service",
                "review_status": "approved",
            },
            {
                "id": "req",
                "source_entity_id": "svc",
                "target_entity_id": "cfg",
                "relation_type": "requires",
                "source_name": "ServiceX",
                "source_type": "Service",
                "target_name": "ConfigY",
                "target_type": "ConfigItem",
                "review_status": "approved",
            },
        ]
        self.links = {
            "a": [{"id": "la", "entity_id": "a", "chunk_id": "chunk-a", "entity_name": "EntityA"}],
            "b": [{"id": "lb", "entity_id": "b", "chunk_id": "chunk-b", "entity_name": "EntityB"}],
            "svc": [{"id": "ls", "entity_id": "svc", "chunk_id": "chunk-svc", "entity_name": "ServiceX"}],
            "cfg": [{"id": "lc", "entity_id": "cfg", "chunk_id": "chunk-cfg", "entity_name": "ConfigY"}],
        }

    def list_entities(self, review_status: str = ""):
        if not review_status:
            return list(self.entities)
        return [item for item in self.entities if item.get("review_status") == review_status]

    def list_relations(self, entity_id: str = "", relation_type: str = "", review_status: str = ""):
        rows = list(self.relations)
        if entity_id:
            rows = [
                item for item in rows
                if item["source_entity_id"] == entity_id or item["target_entity_id"] == entity_id
            ]
        if relation_type:
            rows = [item for item in rows if item["relation_type"] == relation_type]
        if review_status:
            rows = [item for item in rows if item.get("review_status") == review_status]
        return rows

    def list_links(self, entity_id: str = "", chunk_id: str = ""):
        rows = []
        if entity_id:
            rows.extend(self.links.get(entity_id, []))
        else:
            for items in self.links.values():
                rows.extend(items)
        if chunk_id:
            rows = [item for item in rows if item["chunk_id"] == chunk_id]
        return rows


def _semantic(*entities: str, primary: str | None = None) -> SemanticTaskContext:
    values = tuple(entities)
    return SemanticTaskContext(
        resolved_question=" ".join(values) or "generic question",
        primary_entity=primary if primary is not None else (values[0] if values else None),
        mentioned_entities=values,
        task_type="multi_entity_relation" if len(values) >= 2 else ("single_entity" if values else "unbound"),
        confidence=1.0,
    )


def _resolver(semantic: SemanticTaskContext, db=None, *, max_hops: int = 2, max_entities: int = 8):
    identity = IdentityScopeResolver.resolve(semantic, constraints=_TEST_CONSTRAINTS)
    return identity, ExplorationGrantResolver(
        identity_scope=identity,
        semantic_task=semantic,
        graph_db=db,
        max_hops=max_hops,
        max_entities=max_entities,
    )


def _grant_doc(grant: ExplorationGrant, entity: str, chunk_id: str, content: str) -> dict:
    return {
        "content": content,
        "metadata": {
            "chunk_id": chunk_id,
            "document_entity": entity,
            "evidence_target_entity": entity,
            "grant_id": grant.grant_id,
            "grant_admitted": True,
            "identity_scope_id": grant.identity_scope_id,
            "provenance_source_type": grant.source_type,
            "provenance_path": {
                "source_type": grant.source_type,
                "source_ref": grant.source_ref,
            },
            "evidence_class": "TARGET_DIRECT",
            "support_scope": "TARGET_SPECIFIC",
        },
    }


def test_v16_stage1_semantic_task_recognizes_multi_entity_without_scope_regex():
    result = UnderstandingResult(
        mode="retrieve",
        user_utterance="PipelineWebGL 和 PipelineBuilder 如何协同部署？",
        resolved_question="PipelineWebGL 和 PipelineBuilder 如何协同部署？",
        confidence=0.95,
    )
    semantic = build_semantic_task_context(result.user_utterance, result)

    assert semantic.task_type == "multi_entity_relation"
    assert semantic.primary_entity == "PipelineWebGL"
    assert semantic.mentioned_entities[:2] == ("PipelineWebGL", "PipelineBuilder")


def test_evidence_digest_exposes_facts_and_relations_for_controller_decisions():
    pool = EvidencePool(question_id="q")
    pool.add_retrieve(
        [{
            "content": "PipelineWebGL 支持三维管线查询与场景浏览。",
            "metadata": {
                "chunk_id": "pipeline-webgl-overview",
                "document_entity": "PipelineWebGL",
                "section_path": "PipelineWebGL > 概览",
            },
        }],
        query="PipelineWebGL 概览",
    )
    pool.add_relation(
        relation_key="PipelineBuilder -[different_from]-> PipelineWebGL",
        target_entity="PipelineWebGL",
        relation_relevance="DIRECT",
    )

    digest = pool.decision_digest()

    assert "entity=PipelineWebGL" in digest
    assert "section=PipelineWebGL > 概览" in digest
    assert "支持三维管线查询" in digest
    assert "relation=PipelineBuilder -[different_from]-> PipelineWebGL" in digest


def test_v16_identity_materializes_after_semantic_task_and_stays_primary():
    semantic = _semantic("EntityA", "EntityB", primary="EntityA")
    identity = IdentityScopeResolver.resolve(semantic, constraints=_TEST_CONSTRAINTS)

    assert identity.primary_entity == "EntityA"
    assert identity.is_identity_locked is True
    assert not hasattr(identity, "admissible_entities")


def test_v16_explicit_multi_entity_gets_independent_grants_without_rebinding():
    semantic = _semantic("EntityA", "EntityB", primary="EntityA")
    identity, resolver = _resolver(semantic, _FakeGraphDB())

    grant_a = resolver.authorize("EntityA")
    grant_b = resolver.authorize("EntityB")

    assert identity.primary_entity == "EntityA"
    assert grant_a.authorized is True
    assert grant_b.authorized is True
    assert grant_b.grant is not None
    assert grant_b.grant.target_entities == ("EntityB",)
    assert grant_b.grant.source_type == "user_explicit_mention"
    assert identity.primary_entity == "EntityA"


def test_v21_different_from_sibling_is_an_exploration_target_with_graph_provenance():
    semantic = _semantic("EntityA", primary="EntityA")
    identity, resolver = _resolver(semantic, _FakeGraphDB())

    result = resolver.authorize("EntityB")

    assert identity.primary_entity == "EntityA"
    assert result.authorized is True
    assert result.grant is not None
    assert result.grant.source_type == "graph_relation"
    assert result.grant.allowed_relations == frozenset({"different_from"})


def test_v16_approved_graph_relation_can_authorize_new_target_and_two_hops():
    semantic = _semantic("EntityA", primary="EntityA")
    identity, resolver = _resolver(semantic, _FakeGraphDB())

    service = resolver.authorize("ServiceX")
    assert service.authorized is True
    assert service.grant is not None
    assert service.grant.source_type == "graph_relation"
    assert service.grant.source_ref == "relation:dep"
    assert service.grant.hop_depth == 1

    config = resolver.authorize("ConfigY")
    assert config.authorized is True
    assert config.grant is not None
    assert config.grant.source_type == "graph_relation"
    assert config.grant.source_ref == "relation:req"
    assert config.grant.hop_depth == 2
    assert identity.primary_entity == "EntityA"


def test_v21_graph_hop_budget_falls_back_to_exploratory_grant():
    semantic = _semantic("EntityA", primary="EntityA")
    _, resolver = _resolver(semantic, _FakeGraphDB(), max_hops=1)

    assert resolver.authorize("ServiceX").authorized is True
    exploratory = resolver.authorize("ConfigY")
    assert exploratory.authorized is True
    assert exploratory.grant.source_type == "exploratory_query"


def test_v16_grant_fingerprint_and_cache_key_isolate_targets():
    semantic = _semantic("EntityA", "EntityB", primary="EntityA")
    _, resolver = _resolver(semantic, _FakeGraphDB())
    grant_a = resolver.authorize("EntityA").grant
    grant_b = resolver.authorize("EntityB").grant
    assert grant_a is not None and grant_b is not None

    assert grant_a.fingerprint != grant_b.fingerprint
    common = dict(
        rewritten_query="same query",
        kb_name="kb",
        doc_category=None,
        review_status="approved",
        method="hybrid",
        rerank=True,
        web_search=False,
    )
    key_a = QueryCache.make_key(**common, scope_fingerprint=grant_a.fingerprint)
    key_b = QueryCache.make_key(**common, scope_fingerprint=grant_b.fingerprint)
    assert key_a != key_b


def test_v16_evidence_pool_groups_retrieval_by_target_and_grant():
    semantic = _semantic("EntityA", "EntityB", primary="EntityA")
    identity, resolver = _resolver(semantic, _FakeGraphDB())
    grant_a = resolver.authorize("EntityA").grant
    grant_b = resolver.authorize("EntityB").grant
    assert grant_a is not None and grant_b is not None

    pool = EvidencePool(question_id="q")
    pool.add_retrieve([_grant_doc(grant_a, "EntityA", "a", "A fact")], target_entity="EntityA", grant=grant_a)
    pool.add_retrieve([_grant_doc(grant_b, "EntityB", "b", "B fact")], target_entity="EntityB", grant=grant_b)

    groups = [group for group in pool.groups if group.kind == "retrieve"]
    assert [(group.target_entity, group.grant_id) for group in groups] == [
        ("EntityA", grant_a.grant_id),
        ("EntityB", grant_b.grant_id),
    ]
    assert all(group.provenance for group in groups)
    assert identity.primary_entity == "EntityA"


def test_v16_structural_gate_rejects_cross_grant_chunk():
    semantic = _semantic("EntityA", primary="EntityA")
    identity, resolver = _resolver(semantic, _FakeGraphDB())
    grant = resolver.authorize("EntityA").grant
    assert grant is not None
    conv = ConversationContext(
        user_question="EntityA",
        session=SimpleNamespace(turns=[]),
        head_entity="EntityA",
        scope=identity,
    )
    pool = EvidencePool(question_id="q")
    bad_doc = _grant_doc(grant, "EntityA", "a", "A fact")
    bad_doc["metadata"]["grant_id"] = "wrong-grant"
    pool.add_retrieve([bad_doc], target_entity="EntityA", grant=grant)

    verdict = evaluate_rules(conv, pool)

    assert verdict["allow_knowledge_answer"] is False
    assert verdict["reason"] == "grant_id_mismatch"


def test_v16_tool_schema_exposes_search_focus_and_retires_target_entity_and_link():
    registry = build_agent_registry()
    retrieve_props = registry.get("retrieve_kb").input_schema["properties"]

    assert "search_focus_text" in retrieve_props
    assert "focus_entity_id" in retrieve_props
    assert "target_entity" not in retrieve_props
    assert registry.get("link_entities") is None


def test_v16_agent_loop_inherits_state_across_resumes():
    """Verify AgentLoop inherits gap_registry, continuous_no_progress_count, and budget accounting across resumes."""
    import asyncio
    from rag_knowledge.services.agent_orchestration.models import (
        AgentBudget,
        AgentDecision,
        AttemptedGapRegistry,
        ToolObservation,
    )
    from rag_knowledge.services.agent_orchestration.runtime import AgentLoop

    conv = ConversationContext(user_question="test question", session=SimpleNamespace(turns=[]))
    pool = EvidencePool(question_id="q-resume")

    # 1. First run creates a gap failure
    registry_v1 = AttemptedGapRegistry()
    registry_v1.record(
        gap="missing port",
        target_scope="StampServer",
        status="NO_PROGRESS",
        tool="retrieve_kb",
        gap_support_delta=0,
    )

    # Budget with existing accounting
    budget = AgentBudget(max_steps=6, max_retrieve_attempts=3)
    budget.steps_used = 2
    budget.retrieve_attempts = 1
    budget.retrieval_requested = 1
    budget.retrieval_executed = 1

    decisions = [
        AgentDecision(action="finalize"),
    ]

    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=budget,
        registry=build_agent_registry(),
        handlers={},
        gap_registry=registry_v1,
        continuous_no_progress_count=1,
        decide_fn=lambda *_: decisions.pop(0),
    )

    result = asyncio.run(loop.run())

    assert result.gap_registry["entries"][0]["gap"] == "missing port"
    assert result.continuous_no_progress_count == 1
    assert result.budget["steps_used"] == 3
    assert result.budget["retrieval_accounting"]["executed"] == 1
