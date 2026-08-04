"""Relation type label authority: schema-filtered + optional LLM arbiter."""
from __future__ import annotations

from rag_knowledge.services.relation_type import (
    RelationTypeService,
    TypeLabelAction,
)


class FakeTypeLabelArbiter:
    def __init__(self, verdict="replace", chosen="depends_on", confidence=0.93):
        self.verdict = verdict
        self.chosen = chosen
        self.confidence = confidence
        self.calls = []

    def arbitrate(self, source_name, relation_type, target_name, **kwargs):
        self.calls.append((source_name, relation_type, target_name, kwargs))
        return (self.verdict, self.chosen, self.confidence)


def test_non_confusable_type_kept():
    svc = RelationTypeService(arbiter=None)
    decision = svc.decide(
        "A", "runs_command", "cmd",
        source_type="Procedure", target_type="Command",
    )
    assert decision.action == TypeLabelAction.KEEP
    assert decision.reason == "not_confusable"


def test_schema_unique_replace_has_step_mislabel():
    """Procedure→Step only allows has_step; has_procedure is illegal → replace."""
    svc = RelationTypeService(arbiter=None)
    decision = svc.decide(
        "导出流程",
        "has_procedure",
        "打开面板",
        source_type="Procedure",
        target_type="Step",
    )
    assert decision.action == TypeLabelAction.REPLACE
    assert decision.relation_type == "has_step"
    assert decision.used_llm is False


def test_both_legal_uses_llm_replace():
    arbiter = FakeTypeLabelArbiter(verdict="replace", chosen="depends_on", confidence=0.91)
    svc = RelationTypeService(arbiter=arbiter)
    decision = svc.decide(
        "StampServer",
        "requires",
        "Redis",
        source_type="Service",
        target_type="EnvironmentComponent",
        evidence_text="StampServer 依赖 Redis",
    )
    assert decision.action == TypeLabelAction.REPLACE
    assert decision.relation_type == "depends_on"
    assert decision.used_llm is True
    assert arbiter.calls


def test_llm_reject_marks_reject():
    arbiter = FakeTypeLabelArbiter(verdict="reject", chosen="requires", confidence=0.88)
    svc = RelationTypeService(arbiter=arbiter)
    decision = svc.decide(
        "StampServer",
        "requires",
        "Redis",
        source_type="Service",
        target_type="EnvironmentComponent",
    )
    assert decision.action == TypeLabelAction.REJECT


def test_llm_unsure_below_threshold():
    arbiter = FakeTypeLabelArbiter(verdict="keep", chosen="requires", confidence=0.40)
    svc = RelationTypeService(arbiter=arbiter)
    decision = svc.decide(
        "StampServer",
        "requires",
        "Redis",
        source_type="Service",
        target_type="EnvironmentComponent",
    )
    assert decision.action == TypeLabelAction.UNSURE


def test_pipeline_injects_relation_type_arbiter(isolated_storage, monkeypatch):
    isolated_storage()
    from rag_knowledge.services.graph_extraction import pipeline as pipeline_mod
    from rag_knowledge.services.graph_extraction.pipeline import GraphBuilder
    import rag_knowledge.config as config_mod
    from rag_knowledge.repository.relational_db import RelationalDB

    captured = {}

    class CapturingTypeService(pipeline_mod.RelationTypeService):
        def __init__(self, arbiter=None):
            captured["arbiter"] = arbiter
            super().__init__(arbiter=arbiter)

    monkeypatch.setattr(pipeline_mod, "RelationTypeService", CapturingTypeService)
    monkeypatch.setattr(
        "rag_knowledge.services.ollama_health.assert_ollama_reachable",
        lambda **kwargs: None,
    )

    class FakeCfg:
        class graph_extraction_llm:
            enabled = False
            entity_resolve_enabled = False
            relation_direction_resolve_enabled = False
            entity_type_resolve_enabled = False
            relation_type_resolve_enabled = True
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
    builder.build_full(force_rebuild=True, include_relation_type_resolve=True, limit=1)
    assert captured.get("arbiter") is not None
    assert getattr(captured["arbiter"], "use_graph_endpoint", False) is True
