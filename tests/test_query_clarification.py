"""Tests for query clarification (反问)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from rag_knowledge.services.domain_catalog import DomainCatalogLoader
from rag_knowledge.services.query_clarification import QueryClarificationService


@pytest.fixture
def rules_file(tmp_path: Path) -> Path:
    path = tmp_path / "rules.json"
    path.write_text(
        json.dumps(
            [
                {
                    "trigger_entity": "管线发布",
                    "ask_question": "请选择管线发布方向：",
                    "context_options": [
                        {
                            "label": "工具 PipelineBuilder",
                            "filter": {"doc_category": "StampTools", "entity_name": "PipelineBuilder"},
                        },
                        {
                            "label": "服务 管线发布服务",
                            "filter": {"doc_category": "StampServer", "entity_name": "管线发布服务"},
                        },
                    ],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def test_clarify_pipeline_publish_ambiguity(rules_file: Path):
    svc = QueryClarificationService(rules_path=rules_file, enabled=True, llm_enabled=False)
    result = svc.analyze("管线发布怎么配置？")
    assert result.needs_clarification is True
    assert result.reason == "entity_ambiguity"
    assert len(result.options) == 2
    assert result.options[0].id == "a"
    assert result.options[1].id == "b"
    assert result.options[0].filter.doc_category == "StampTools"


def test_clarify_skips_when_doc_category_narrows_to_one(rules_file: Path):
    svc = QueryClarificationService(rules_path=rules_file, enabled=True, llm_enabled=False)
    result = svc.analyze("管线发布怎么配置？", doc_category="StampServer")
    assert result.needs_clarification is False


def test_clarify_disabled_returns_false(rules_file: Path):
    svc = QueryClarificationService(rules_path=rules_file, enabled=False)
    result = svc.analyze("管线发布怎么配置？")
    assert result.needs_clarification is False


def test_clarify_pipeline_vague_surface_term(isolated_storage):
    isolated_storage()
    svc = QueryClarificationService(enabled=True, llm_enabled=False)
    result = svc.analyze("pipeline")
    assert result.needs_clarification is True
    assert result.reason in {"vague_surface_term", "entity_ambiguity"}
    assert len(result.options) >= 2
    entity_names = {opt.filter.entity_name for opt in result.options}
    assert "PipelineBuilder" in entity_names
    assert any(name and "管线" in name for name in entity_names if name)


def test_clarify_skips_when_entity_already_chosen(isolated_storage):
    isolated_storage()
    svc = QueryClarificationService(enabled=True, llm_enabled=False)
    result = svc.analyze("pipeline", entity_name="PipelineBuilder")
    assert result.needs_clarification is False


def test_clarify_bare_guanxian_underspecified(isolated_storage):
    isolated_storage()
    svc = QueryClarificationService(enabled=True, llm_enabled=False)
    result = svc.analyze("管线")
    assert result.needs_clarification is True


def test_clarify_guanxian_in_specific_question_skips_bare_wide_term(tmp_path, isolated_storage):
    """「管线点表」类具体问法不应仅因含「管线」二字就走宽词反问。"""
    isolated_storage()
    rules = tmp_path / "empty_rules.json"
    rules.write_text("[]", encoding="utf-8")
    svc = QueryClarificationService(rules_path=rules, enabled=True, llm_enabled=False)
    result = svc.analyze("管线点表有哪些字段？")
    if result.needs_clarification:
        assert not (result.trigger == "管线" and result.reason == "vague_surface_term")


def test_catalog_different_from_generates_options(isolated_storage):
    isolated_storage()
    catalog = DomainCatalogLoader()
    svc = QueryClarificationService(
        rules_path=Path("/nonexistent/rules.json"),
        catalog=catalog,
        enabled=True,
        llm_enabled=False,
    )
    result = svc.analyze("PipelineBuilder 和 PipelinePublishConfig 有什么区别？")
    if result.needs_clarification:
        assert result.options
        categories = {opt.filter.doc_category for opt in result.options if opt.filter.doc_category}
        assert len(categories) >= 1


def test_clarify_llm_can_require_and_skip(isolated_storage, tmp_path):
    isolated_storage()
    rules = tmp_path / "empty_rules.json"
    rules.write_text("[]", encoding="utf-8")

    def ask_llm(_prompt: str):
        return {
            "needs_clarification": True,
            "ask_question": "请选择 pipeline 对应方向：",
            "trigger": "pipeline",
            "option_ids": ["a", "b"],
        }

    ask_svc = QueryClarificationService(
        rules_path=rules,
        enabled=True,
        llm_enabled=True,
        llm_caller=ask_llm,
    )
    # Seeds still come from catalog/wide terms even with empty rules file.
    asked = ask_svc.analyze("pipeline")
    assert asked.needs_clarification is True
    assert asked.reason == "llm_ambiguity"
    assert len(asked.options) >= 2

    def clear_llm(_prompt: str):
        return {
            "needs_clarification": False,
            "ask_question": "",
            "trigger": "",
            "option_ids": [],
        }

    clear_svc = QueryClarificationService(
        rules_path=rules,
        enabled=True,
        llm_enabled=True,
        llm_caller=clear_llm,
    )
    cleared = clear_svc.analyze("pipeline")
    assert cleared.needs_clarification is False
    assert cleared.reason == "llm_clear"


def test_clarify_llm_failure_falls_back_to_rules(rules_file: Path):
    def boom(_prompt: str):
        raise RuntimeError("ollama down")

    svc = QueryClarificationService(
        rules_path=rules_file,
        enabled=True,
        llm_enabled=True,
        llm_caller=boom,
    )
    result = svc.analyze("管线发布怎么配置？")
    assert result.needs_clarification is True
    assert result.reason == "entity_ambiguity"


def test_clarify_route(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from rag_knowledge.api import routes

    class FakeClarify:
        def analyze(self, question, *, doc_category=None, kb_name=None, entity_name=None):
            from rag_knowledge.services.query_clarification import (
                ClarificationFilter,
                ClarificationOption,
                ClarificationResult,
            )

            return ClarificationResult(
                needs_clarification=True,
                ask_question="请选择：",
                trigger="测试",
                reason="entity_ambiguity",
                options=[
                    ClarificationOption(
                        id="a",
                        label="选项A",
                        filter=ClarificationFilter(doc_category="StampTools"),
                    ),
                    ClarificationOption(
                        id="b",
                        label="选项B",
                        filter=ClarificationFilter(doc_category="StampServer"),
                    ),
                ],
            )

    monkeypatch.setattr(routes, "QueryClarificationService", FakeClarify)
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    resp = client.post("/query/clarify", json={"question": "测试歧义"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["needs_clarification"] is True
    assert len(body["options"]) == 2
    assert body["options"][0]["filter"]["doc_category"] == "StampTools"
