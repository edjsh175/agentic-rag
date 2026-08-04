"""Relation direction authority: schema-first + optional LLM semantic arbiter."""
from __future__ import annotations

from rag_knowledge.services.relation_direction import (
    DirectionAction,
    RelationDirectionService,
)


class FakeDirectionArbiter:
    def __init__(self, verdict: str = "flip", confidence: float = 0.95):
        self.verdict = verdict
        self.confidence = confidence
        self.calls = []

    def arbitrate(self, source_name, relation_type, target_name, **kwargs):
        self.calls.append((source_name, relation_type, target_name, kwargs))
        return (self.verdict, self.confidence)


def test_schema_unique_reverse_flips_belongs_to():
    svc = RelationDirectionService(arbiter=None)
    decision = svc.decide(
        "StampTools",
        "belongs_to",
        "PipelineBuilder",
        source_type="Product",
        target_type="Tool",
    )
    assert decision.action == DirectionAction.FLIP
    assert decision.source_name == "PipelineBuilder"
    assert decision.target_name == "StampTools"
    assert decision.used_llm is False


def test_schema_unique_forward_keeps():
    svc = RelationDirectionService(arbiter=None)
    decision = svc.decide(
        "PipelineBuilder",
        "belongs_to",
        "StampTools",
        source_type="Tool",
        target_type="Product",
    )
    assert decision.action == DirectionAction.KEEP
    assert decision.reason == "schema_forward_unique"


def test_both_legal_depends_on_uses_llm_flip():
    arbiter = FakeDirectionArbiter(verdict="flip", confidence=0.91)
    svc = RelationDirectionService(arbiter=arbiter)
    # Tool↔Tool both schema-legal → LLM decides who depends on whom
    decision = svc.decide(
        "ToolB",
        "depends_on",
        "ToolA",
        source_type="Tool",
        target_type="Tool",
        evidence_text="ToolA 依赖 ToolB",
    )
    assert decision.action == DirectionAction.FLIP
    assert decision.used_llm is True
    assert decision.source_name == "ToolA"
    assert decision.target_name == "ToolB"
    assert arbiter.calls


def test_both_legal_llm_unsure_marks_unsure():
    arbiter = FakeDirectionArbiter(verdict="unsure", confidence=0.40)
    svc = RelationDirectionService(arbiter=arbiter)
    decision = svc.decide(
        "ToolA",
        "depends_on",
        "ToolB",
        source_type="Tool",
        target_type="Tool",
    )
    assert decision.action == DirectionAction.UNSURE
    assert decision.used_llm is True


def test_requires_unrestricted_uses_llm():
    arbiter = FakeDirectionArbiter(verdict="keep", confidence=0.88)
    svc = RelationDirectionService(arbiter=arbiter)
    decision = svc.decide(
        "A",
        "requires",
        "B",
        source_type="Tool",
        target_type="Service",
        evidence_text="A requires B",
    )
    assert decision.action == DirectionAction.KEEP
    assert decision.used_llm is True


def test_pipeline_injects_direction_arbiter(isolated_storage, monkeypatch):
    isolated_storage()
    from rag_knowledge.services.graph_extraction import pipeline as pipeline_mod
    from rag_knowledge.services.graph_extraction.pipeline import GraphBuilder
    import rag_knowledge.config as config_mod
    from rag_knowledge.repository.relational_db import RelationalDB

    captured = {}

    class CapturingDirection(pipeline_mod.RelationDirectionService):
        def __init__(self, arbiter=None):
            captured["arbiter"] = arbiter
            super().__init__(arbiter=arbiter)

    monkeypatch.setattr(pipeline_mod, "RelationDirectionService", CapturingDirection)
    monkeypatch.setattr(
        "rag_knowledge.services.ollama_health.assert_ollama_reachable",
        lambda **kwargs: None,
    )

    class FakeCfg:
        class graph_extraction_llm:
            enabled = False
            entity_resolve_enabled = False
            relation_direction_resolve_enabled = True
            entity_type_resolve_enabled = False
            relation_type_resolve_enabled = False
            relation_belonging_resolve_enabled = False
            leak_salvage_enabled = False
            provider = "ollama"
            api_key_env = ""
            prompt_version = "v4"
            extractor_version = "v1"
            rate_limit_delay = 0.0
            temperature = 0.0
            max_retries = 1
            entity_resolve_min_confidence = 0.80
            relation_direction_min_confidence = 0.80
            entity_type_resolve_min_confidence = 0.80
            relation_type_min_confidence = 0.80
            relation_belonging_min_confidence = 0.80
            leak_salvage_enabled = False

        ollama_base_url = "http://127.0.0.1:11434"

        def graph_llm_endpoint(self):
            return self.ollama_base_url

        @property
        def graph_extraction_endpoint(self):
            from rag_knowledge.config import GraphLLMExtractorConfig

            return GraphLLMExtractorConfig().as_endpoint()

    monkeypatch.setattr(config_mod, "Config", FakeCfg)

    builder = GraphBuilder(
        db=RelationalDB(),
        chunk_source=lambda: [
            {
                "chunk_id": "c1",
                "content": "hello",
                "metadata": {"doc_category": "其他", "section_path": "", "source": "t.md"},
            }
        ],
    )
    builder.build_full(
        force_rebuild=True,
        include_relation_direction_resolve=True,
        limit=1,
    )
    assert captured.get("arbiter") is not None
    assert getattr(captured["arbiter"], "use_graph_endpoint", False) is True


def test_early_check_flips_belongs_to_via_shared_service():
    from rag_knowledge.services.graph_extraction.llm_extractor import (
        early_check_relation_endpoints,
    )

    idx = {"StampTools": "Product", "PipelineBuilder": "Tool"}
    src, tgt, flipped, reason = early_check_relation_endpoints(
        "StampTools", "belongs_to", "PipelineBuilder", idx
    )
    assert reason is None
    assert flipped
    assert src == "PipelineBuilder" and tgt == "StampTools"
