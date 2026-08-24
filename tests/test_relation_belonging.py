"""belongs_to parent-attachment: catalog/path first, optional LLM in neighborhood."""
from __future__ import annotations

from rag_knowledge.services.relation_belonging import (
    BelongingAction,
    RelationBelongingService,
    preferred_parent_from_section_path,
)


class FakeBelongingArbiter:
    def __init__(self, verdict="replace", parent="PipelineBuilder::数据管理", confidence=0.92):
        self.verdict = verdict
        self.parent = parent
        self.confidence = confidence
        self.calls = []

    def arbitrate(self, child_name, parent_name, **kwargs):
        self.calls.append((child_name, parent_name, kwargs))
        return (self.verdict, self.parent, self.confidence)


def test_out_of_neighborhood_skips():
    svc = RelationBelongingService(arbiter=FakeBelongingArbiter())
    decision = svc.decide(
        "材质映射",
        "PipelineBuilder",
        child_type="Procedure",
        parent_type="Tool",
        in_neighborhood=False,
        candidate_parents=["PipelineBuilder", "PipelineBuilder::数据管理"],
    )
    assert decision.action == BelongingAction.KEEP
    assert decision.reason == "out_of_neighborhood"
    assert not decision.used_llm


def test_catalog_owner_replace():
    svc = RelationBelongingService(arbiter=None)
    decision = svc.decide(
        "PipelineBuilder",
        "StampServer",  # wrong parent
        child_type="Tool",
        parent_type="Product",
        in_neighborhood=True,
    )
    assert decision.action == BelongingAction.REPLACE
    assert decision.target_name == "StampTools"
    assert decision.reason == "catalog_owner_replace"


def test_section_path_prefers_function_area():
    preferred = preferred_parent_from_section_path(
        "PipelineBuilder > 数据管理 > 材质映射",
        ["PipelineBuilder", "PipelineBuilder::数据管理", "StampTools"],
        "PipelineBuilder",
    )
    assert preferred == "PipelineBuilder::数据管理"

    svc = RelationBelongingService(arbiter=None)
    decision = svc.decide(
        "材质映射",
        "PipelineBuilder",
        child_type="Procedure",
        parent_type="Tool",
        section_path="PipelineBuilder > 数据管理 > 材质映射",
        in_neighborhood=True,
        candidate_parents=["PipelineBuilder", "PipelineBuilder::数据管理"],
    )
    assert decision.action == BelongingAction.REPLACE
    assert decision.target_name == "PipelineBuilder::数据管理"


def test_llm_replace_in_neighborhood():
    arbiter = FakeBelongingArbiter(
        verdict="replace", parent="PipelineBuilder::数据管理", confidence=0.91
    )
    svc = RelationBelongingService(arbiter=arbiter)
    decision = svc.decide(
        "材质映射",
        "StampTools",
        child_type="Procedure",
        parent_type="Product",
        section_path="",
        in_neighborhood=True,
        candidate_parents=["StampTools", "PipelineBuilder", "PipelineBuilder::数据管理"],
    )
    assert decision.action == BelongingAction.REPLACE
    assert decision.target_name == "PipelineBuilder::数据管理"
    assert decision.used_llm is True
    assert arbiter.calls


def test_pipeline_injects_belonging_arbiter(isolated_storage, monkeypatch):
    isolated_storage()
    from rag_knowledge.services.graph_extraction import pipeline as pipeline_mod
    from rag_knowledge.services.graph_extraction.pipeline import GraphBuilder
    import rag_knowledge.config as config_mod
    from rag_knowledge.repository.relational_db import RelationalDB

    captured = {}

    class CapturingBelonging(pipeline_mod.RelationBelongingService):
        def __init__(self, arbiter=None, catalog=None, backbone_constraints=None):
            captured["arbiter"] = arbiter
            super().__init__(
                arbiter=arbiter, catalog=catalog, backbone_constraints=backbone_constraints
            )

    monkeypatch.setattr(pipeline_mod, "RelationBelongingService", CapturingBelonging)
    monkeypatch.setattr(pipeline_mod, "assert_ollama_reachable", lambda **kwargs: None)

    class FakeCfg:
        class graph_extraction_llm:
            enabled = False
            entity_resolve_enabled = False
            relation_direction_resolve_enabled = False
            entity_type_resolve_enabled = False
            relation_type_resolve_enabled = False
            relation_belonging_resolve_enabled = True
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
                "content": "PipelineBuilder 材质映射",
                "metadata": {
                    "doc_category": "StampTools",
                    "section_path": "PipelineBuilder > 数据管理",
                    "source": "t.md",
                },
            }
        ],
    )
    builder.build_full(force_rebuild=True, include_relation_belonging_resolve=True, limit=1)
    assert captured.get("arbiter") is not None
    assert getattr(captured["arbiter"], "use_graph_endpoint", False) is True
