"""Tests for query clarification (反问) — backbone-driven full option list."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from rag_knowledge.services.query_clarification import QueryClarificationService


def _mini_backbone() -> dict:
    """Minimal constraints fixture covering pipeline family + Chinese services."""
    names = {
        "PipelineBuilder": "Tool",
        "PipelineWebGL": "Product",
        "PipelineWebRTC": "Product",
        "管线发布服务": "Service",
        "管线更新服务": "Service",
        "se_pipeline.so": "Service",
        "StampTools": "Product",
    }
    aliases = {
        "PipelineBuilder": "PipelineBuilder",
        "PipelineWebGL": "PipelineWebGL",
        "PipelineWebRTC": "PipelineWebRTC",
        "pipeline": "PipelineBuilder",
        "Pipeline": "PipelineBuilder",
        "管线工具": "PipelineBuilder",
        "管线发布工具": "PipelineBuilder",
        "pipelinewebgl": "PipelineWebGL",
        "Pipeline WebGL": "PipelineWebGL",
        "管线发布服务": "管线发布服务",
        "管线更新服务": "管线更新服务",
        "se_pipeline.so": "se_pipeline.so",
        "StampTools": "StampTools",
    }
    return {
        "belongs_to": {
            "PipelineBuilder": {"StampTools"},
            "PipelineWebGL": {"WebGL"},
            "PipelineWebRTC": {"WebRTC"},
        },
        "different_from": set(),
        "requires": set(),
        "relations": [],
        "canonical_by_alias": aliases,
        "entity_type_by_name": names,
        "doc_category_by_name": {
            "PipelineBuilder": "数据处理与发布",
            "PipelineWebGL": "业务应用层",
            "PipelineWebRTC": "引擎",
        },
        "doc_categories": {"数据处理与发布", "业务应用层", "引擎"},
    }


@pytest.fixture
def backbone_svc(isolated_storage) -> QueryClarificationService:
    isolated_storage()
    return QueryClarificationService(
        enabled=True,
        llm_enabled=False,
        constraints=_mini_backbone(),
    )


def test_clarify_pipeline_lists_full_family(backbone_svc: QueryClarificationService):
    result = backbone_svc.analyze("pipeline")
    assert result.needs_clarification is True
    entity_names = {opt.filter.entity_name for opt in result.options}
    assert "PipelineBuilder" in entity_names
    assert "PipelineWebGL" in entity_names
    assert "PipelineWebRTC" in entity_names
    assert "管线发布服务" in entity_names
    assert "管线更新服务" in entity_names
    assert "se_pipeline.so" not in entity_names
    assert len(result.options) >= 5


def test_clarify_no_max_options_cap(backbone_svc: QueryClarificationService):
    result = backbone_svc.analyze("pipeline")
    assert result.needs_clarification is True
    assert len(result.options) > 4
    assert result.options[-1].id  # ids assigned beyond a-d


def test_clarify_skips_when_entity_already_chosen(backbone_svc: QueryClarificationService):
    result = backbone_svc.analyze("pipeline", entity_name="PipelineBuilder")
    assert result.needs_clarification is False


def test_clarify_disabled_returns_false(backbone_svc: QueryClarificationService):
    svc = QueryClarificationService(
        enabled=False,
        llm_enabled=False,
        constraints=_mini_backbone(),
    )
    result = svc.analyze("pipeline")
    assert result.needs_clarification is False


def test_clarify_bare_guanxian_underspecified(backbone_svc: QueryClarificationService):
    result = backbone_svc.analyze("管线")
    assert result.needs_clarification is True
    entity_names = {opt.filter.entity_name for opt in result.options}
    assert "PipelineWebGL" in entity_names
    assert "PipelineWebRTC" in entity_names


def test_clarify_guanxian_in_specific_question_skips_bare_wide_term(
    backbone_svc: QueryClarificationService,
):
    """「管线点表」类具体问法不应仅因含「管线」二字就走宽词反问。"""
    result = backbone_svc.analyze("管线点表有哪些字段？")
    if result.needs_clarification:
        assert not (result.trigger == "管线" and result.reason == "vague_surface_term")


def test_clarify_explicit_comparison_skips(backbone_svc: QueryClarificationService):
    result = backbone_svc.analyze("PipelineBuilder 和 PipelineWebGL 有什么区别？")
    assert result.needs_clarification is False


def test_clarify_llm_ignores_option_ids_subset(isolated_storage):
    isolated_storage()

    def ask_llm(_prompt: str):
        return {
            "needs_clarification": True,
            "ask_question": "请选择 pipeline 对应方向：",
            "trigger": "pipeline",
            "option_ids": ["a", "b"],  # intentionally incomplete — must not truncate
        }

    svc = QueryClarificationService(
        enabled=True,
        llm_enabled=True,
        llm_caller=ask_llm,
        constraints=_mini_backbone(),
    )
    asked = svc.analyze("pipeline")
    assert asked.needs_clarification is True
    assert asked.reason == "llm_ambiguity"
    entity_names = {opt.filter.entity_name for opt in asked.options}
    assert "PipelineBuilder" in entity_names
    assert "PipelineWebGL" in entity_names
    assert "PipelineWebRTC" in entity_names
    assert len(asked.options) >= 5


def test_clarify_llm_can_skip(isolated_storage):
    isolated_storage()

    def clear_llm(_prompt: str):
        return {
            "needs_clarification": False,
            "ask_question": "",
            "trigger": "",
        }

    svc = QueryClarificationService(
        enabled=True,
        llm_enabled=True,
        llm_caller=clear_llm,
        constraints=_mini_backbone(),
    )
    cleared = svc.analyze("pipeline")
    assert cleared.needs_clarification is False
    assert cleared.reason == "llm_clear"


def test_clarify_llm_failure_falls_back_to_backbone(isolated_storage):
    isolated_storage()

    def boom(_prompt: str):
        raise RuntimeError("ollama down")

    svc = QueryClarificationService(
        enabled=True,
        llm_enabled=True,
        llm_caller=boom,
        constraints=_mini_backbone(),
    )
    result = svc.analyze("pipeline")
    assert result.needs_clarification is True
    entity_names = {opt.filter.entity_name for opt in result.options}
    assert "PipelineWebRTC" in entity_names


def test_clarify_doc_category_narrows(backbone_svc: QueryClarificationService):
    # Enrichment maps StampTools owner for PipelineBuilder via real catalog when present;
    # with fixture-only doc_category non-retrieval values, narrowing by StampTools may drop all.
    # Use entity-level filter enrichment: inject retrieval categories into constraints.
    constraints = _mini_backbone()
    constraints["doc_category_by_name"] = {
        "PipelineBuilder": "StampTools",
        "PipelineWebGL": "StampTools",
        "PipelineWebRTC": "StampWebRTC",
        "管线发布服务": "StampServer",
        "管线更新服务": "StampServer",
    }
    svc = QueryClarificationService(
        enabled=True,
        llm_enabled=False,
        constraints=constraints,
    )
    result = svc.analyze("pipeline", doc_category="StampServer")
    if result.needs_clarification:
        for opt in result.options:
            if opt.source != "fixed_other":
                assert opt.filter.doc_category == "StampServer"
    else:
        # Narrowed below min_options is also acceptable.
        assert result.needs_clarification is False


def test_clarify_route(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from rag_knowledge.api import routes
    from rag_knowledge.services.conversation_context import UnderstandingResult

    class FakeUnderstanding:
        def analyze(self, question, **kwargs):
            return UnderstandingResult(
                mode="clarify",
                user_utterance=question,
                resolved_question=question,
                rationale="multi_entity_match",
                clarify={
                    "needs_clarification": True,
                    "ask_question": "请选择：",
                    "trigger": "测试",
                    "reason": "multi_entity_match",
                    "options": [
                        {
                            "id": "a",
                            "label": "选项A",
                            "filter": {"doc_category": "StampTools"},
                            "source": "backbone",
                            "canonical_name": "PipelineBuilder",
                            "entity_type": "Tool",
                            "binding_status": "canonical",
                            "score": 0.91,
                        },
                        {
                            "id": "b",
                            "label": "选项B",
                            "filter": {"doc_category": "StampServer"},
                        },
                    ],
                },
            )

    monkeypatch.setattr(routes, "DialogueUnderstanding", FakeUnderstanding)
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    resp = client.post("/query/clarify", json={"question": "测试歧义"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["needs_clarification"] is True
    assert len(body["options"]) == 2
    assert body["options"][0]["filter"]["doc_category"] == "StampTools"
    assert body["options"][0]["source"] == "backbone"
    assert body["options"][0]["canonical_name"] == "PipelineBuilder"
    assert body["options"][0]["entity_type"] == "Tool"
    assert body["options"][0]["binding_status"] == "canonical"
    assert body["options"][0]["score"] == 0.91


def test_query_callback_resolves_option_id_and_preserves_legacy_label(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from rag_knowledge.api import routes

    captured = {}

    class FakeRag:
        async def aquery(self, question, history, **kwargs):
            captured.update(question=question, history=history, kwargs=kwargs)
            return {"answer": "ok", "source_documents": []}

    monkeypatch.setattr(routes, "_rag", FakeRag())
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    options = [
        {
            "id": "cand_01",
            "label": "PipelineWebGL",
            "filter": {"entity_name": "PipelineWebGL"},
            "source": "backbone",
            "canonical_name": "PipelineWebGL",
            "entity_type": "Product",
            "binding_status": "canonical",
            "score": 0.93,
        },
        {
            "id": "other",
            "label": "以上都不是",
            "filter": {},
            "source": "fixed_other",
            "binding_status": "unresolved",
        },
    ]

    resp = client.post(
        "/query",
        json={
            "question": "pipelien",
            "clarification_selected": "被篡改的旧标签",
            "clarification_option_id": "cand_01",
            "clarification_options": options,
            "clarification_selection_kind": "option",
        },
    )

    assert resp.status_code == 200
    assert captured["question"] == "pipelien"
    assert captured["kwargs"]["clarification_selected"] == "PipelineWebGL"
    assert captured["kwargs"]["clarification_option_id"] == "cand_01"
    assert captured["kwargs"]["clarification_selected_candidate"]["source"] == "backbone"
    assert captured["kwargs"]["clarification_selected_candidate"]["canonical_name"] == "PipelineWebGL"
    assert captured["kwargs"]["clarification_options"] == options
    assert captured["kwargs"]["clarification_selection_kind"] == "option"
    # Candidate metadata is never promoted directly to the legacy entity filter.
    assert captured["kwargs"]["entity_name"] is None


def test_query_callback_other_free_text_reenters_understanding(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from rag_knowledge.api import routes

    captured = {}

    class FakeRag:
        async def aquery(self, question, history, **kwargs):
            captured.update(question=question, kwargs=kwargs)
            return {"answer": "ok", "source_documents": []}

    monkeypatch.setattr(routes, "_rag", FakeRag())
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    options = [
        {
            "id": "other",
            "label": "以上都不是",
            "filter": {},
            "source": "fixed_other",
            "binding_status": "unresolved",
        },
    ]

    resp = client.post(
        "/query",
        json={
            "question": "pipelien",
            "clarification_option_id": "other",
            "clarification_options": options,
            "clarification_selection_kind": "free_text",
            "clarification_free_text": "我想问部署流水线服务",
        },
    )

    assert resp.status_code == 200
    assert captured["question"] == "pipelien"
    assert captured["kwargs"]["clarification_selected"] is None
    assert captured["kwargs"]["clarification_option_id"] == "other"
    assert captured["kwargs"]["clarification_selected_candidate"]["source"] == "fixed_other"
    assert captured["kwargs"]["clarification_selection_kind"] == "free_text"
    assert captured["kwargs"]["clarification_free_text"] == "我想问部署流水线服务"


def test_query_callback_rejects_unknown_option_id(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from rag_knowledge.api import routes

    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    resp = client.post(
        "/query",
        json={
            "question": "pipeline",
            "clarification_option_id": "missing",
            "clarification_options": [
                {"id": "cand_01", "label": "PipelineWebGL", "filter": {}},
            ],
        },
    )

    assert resp.status_code == 400
    assert "not present" in resp.json()["detail"]


def test_query_callback_keeps_legacy_label_contract():
    from rag_knowledge.api.routes import _resolve_clarification_callback
    from rag_knowledge.models.api import QueryRequest

    callback = _resolve_clarification_callback(
        QueryRequest(question="pipeline", clarification_selected="PipelineBuilder")
    )

    assert callback.question == "pipeline"
    assert callback.selected_label == "PipelineBuilder"
    assert callback.option_id is None
    assert callback.selected_candidate is None


def test_live_backbone_pipeline_includes_webgl_webrtc(isolated_storage):
    """Against the real product_relation_backbone.json under isolated_storage."""
    isolated_storage()
    svc = QueryClarificationService(enabled=True, llm_enabled=False)
    res = svc.analyze("pipeline")
    assert res.needs_clarification is True
    entity_names = {opt.filter.entity_name for opt in res.options}
    assert "PipelineBuilder" in entity_names
    assert "PipelineWebGL" in entity_names
    assert "PipelineWebRTC" in entity_names
    assert not any(name and name.endswith(".so") for name in entity_names)
