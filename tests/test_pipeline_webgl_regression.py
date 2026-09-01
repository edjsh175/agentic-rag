"""Regression test suite for PipelineWebGL retrieval mis-association and strict entity scope enforcement."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from langchain_core.documents import Document

from rag_knowledge.services.anchor_chunk_filter import (
    chunk_matches_anchor,
    filter_docs_by_backbone_anchor,
)
from rag_knowledge.services.retrieval_scope import RetrievalScope
from rag_knowledge.services.agent_orchestration.evidence_gate import evaluate_rules
from rag_knowledge.services.agent_orchestration.models import (
    ConversationContext,
    EvidencePool,
)
from rag_knowledge.services.entity_candidate_resolver import get_entity_candidate_resolver


def test_retrieval_scope_creation():
    """Verify RetrievalScope accepts only explicit entity_name as an entity binding."""
    # Explicit entity_name provided
    scope1 = RetrievalScope.create("pipeline", entity_name="PipelineWebGL", doc_category="StampTools")
    assert scope1.canonical_entity == "PipelineWebGL"
    assert scope1.explicit_selection is True
    assert scope1.allowed_document_entity == "PipelineWebGL"

    # A label-only callback cannot become an entity binding.
    scope2 = RetrievalScope.create("pipeline", clarification_selected="PipelineWebGL（StampTools）")
    assert scope2.canonical_entity == ""
    assert scope2.explicit_selection is False
    assert scope2.allowed_document_entity is None


def test_clarification_selection_collapses_semantic_task_to_chosen_entity():
    resolver = get_entity_candidate_resolver()
    snapshot = resolver.create_clarification_snapshot(
        resolver.resolve_identity("PipelineWebGL")
    )
    candidate = snapshot.display_candidates[0].to_dict()
    conv = ConversationContext.from_request(
        "pipeline",
        [],
        entity_name="PipelineWebGL",
        clarification_selected="PipelineWebGL",
        clarification_option_id="a",
        clarification_snapshot_id=snapshot.clarification_id,
        clarification_selected_candidate=candidate,
    )

    assert conv.head_entity == "PipelineWebGL"
    assert conv.resolved_question == "PipelineWebGL 的相关信息"
    assert conv.semantic_task.primary_entity == "PipelineWebGL"
    assert conv.semantic_task.mentioned_entities == ("PipelineWebGL",)
    assert conv.semantic_task.task_type == "single_entity"


def test_anchor_filter_exclusive_selection():
    """Verify PipelineBuilder chunk is demoted/filtered out when PipelineWebGL is targeted."""
    pw_doc = Document(
        page_content="PipelineWebGL 用户手册内容...",
        metadata={
            "document_entity": "PipelineWebGL",
            "doc_category": "StampTools",
            "source": "PipelineWebGL用户手册.docx",
            "section_path": "PipelineWebGL > 概览",
        },
    )
    pb_doc = Document(
        page_content="PipelineBuilder 材质设置说明...",
        metadata={
            "document_entity": "PipelineBuilder",
            "doc_category": "StampTools",
            "source": "StampTools用户手册.docx",
            "section_path": "PipelineBuilder > 材质设置",
        },
    )

    constraints = {
        "canonical_by_alias": {
            "pipelinewebgl": "PipelineWebGL",
            "pipelinebuilder": "PipelineBuilder",
        },
        "entity_type_by_name": {
            "PipelineWebGL": "Product",
            "PipelineBuilder": "Tool",
            "StampTools": "Product",
        },
        "belongs_to": {
            "PipelineWebGL": ["StampTools"],
            "PipelineBuilder": ["StampTools"],
        },
    }

    # When targeting PipelineWebGL, pw_doc matches while pb_doc does not
    assert chunk_matches_anchor(pw_doc, canonicals=["PipelineWebGL"], constraints=constraints) is True
    assert chunk_matches_anchor(pb_doc, canonicals=["PipelineWebGL"], constraints=constraints) is False

    # Filter with strict_explicit_target=True
    filtered = filter_docs_by_backbone_anchor(
        [pw_doc, pb_doc],
        backbone_canonical=["PipelineWebGL"],
        enabled=True,
        constraints=constraints,
        strict_explicit_target=True,
    )
    assert len(filtered) == 1
    assert filtered[0].metadata["document_entity"] == "PipelineWebGL"


def test_anchor_filter_strict_refusal_when_no_aligned_chunks():
    """Verify that when only PipelineBuilder chunks are present and PipelineWebGL is targeted, filter returns empty list under strict mode."""
    pb_doc = Document(
        page_content="PipelineBuilder 材质设置说明...",
        metadata={
            "document_entity": "PipelineBuilder",
            "doc_category": "StampTools",
            "source": "StampTools用户手册.docx",
            "section_path": "PipelineBuilder > 材质设置",
        },
    )
    constraints = {
        "canonical_by_alias": {"pipelinebuilder": "PipelineBuilder"},
        "entity_type_by_name": {"PipelineBuilder": "Tool"},
        "belongs_to": {"PipelineBuilder": ["StampTools"]},
    }

    filtered = filter_docs_by_backbone_anchor(
        [pb_doc],
        backbone_canonical=["PipelineWebGL"],
        enabled=True,
        constraints=constraints,
        strict_explicit_target=True,
    )
    # Must be empty (no fallback to non-matching chunk)
    assert filtered == []


def test_evidence_gate_refuses_when_no_aligned_chunks():
    """Verify Evidence Gate refuses KB text that never passed Text Admission (no protocol fields)."""
    conv = ConversationContext.from_request("pipeline", [])
    conv.selected_entity = "PipelineWebGL"
    conv.head_entity = "PipelineWebGL"

    evidence = EvidencePool(question_id="q1")
    evidence.add_retrieve(
        [
            {
                "content": "PipelineBuilder 材质设置说明...",
                "metadata": {
                    "chunk_id": "chk_pb123",
                    "document_entity": "PipelineBuilder",
                    "doc_category": "StampTools",
                    "source": "StampTools用户手册.docx",
                    "section_path": "PipelineBuilder > 材质设置",
                },
            }
        ],
        head_entity="PipelineWebGL",
    )

    verdict = evaluate_rules(conv, evidence)
    assert verdict["allow_knowledge_answer"] is False
    assert verdict["reason"] in {"empty_pool", "query_admission_failed"}


def test_generic_entity_refusal_for_other_entities():
    """Verify that protocol refusal generalizes to any target entity (e.g. ObliqueModelBuilder or StampServer)."""
    conv = ConversationContext.from_request("怎么配置", [])
    conv.selected_entity = "ObliqueModelBuilder"
    conv.head_entity = "ObliqueModelBuilder"

    evidence = EvidencePool(question_id="q2")
    evidence.add_retrieve(
        [
            {
                "content": "StampServer 基础配置...",
                "metadata": {
                    "chunk_id": "chk_ss123",
                    "document_entity": "StampServer",
                    "doc_category": "StampServer",
                    "source": "StampServer用户手册.docx",
                    "section_path": "StampServer > 基础配置",
                },
            }
        ],
        head_entity="ObliqueModelBuilder",
    )

    verdict = evaluate_rules(conv, evidence)
    assert verdict["allow_knowledge_answer"] is False
    assert verdict["reason"] in {"empty_pool", "query_admission_failed"}


def test_stream_retrieval_propagates_explicit_scope():
    """Streaming linear retrieval must not bypass the explicit entity boundary."""
    import asyncio
    from rag_knowledge.services.query_planner import RetrievalPlan
    from rag_knowledge.services.rag import RagChain

    async def _run():
        chain = object.__new__(RagChain)
        resolver = get_entity_candidate_resolver()
        snapshot = resolver.create_clarification_snapshot(
            resolver.resolve_identity("PipelineWebGL")
        )
        plan = RetrievalPlan("definition", [], 4, 12, True, False, 0.9)
        chain._build_retrieval_query_specs = lambda question, history: ["question"]
        chain._plan_retrieval = lambda question, queries, force_rerank=False: plan
        chain._prepare_graph_plan = MagicMock(return_value=(plan, None, []))
        chain._build_graph_kwargs = lambda *args, **kwargs: {}
        chain._aretrieve_multi_uncached = AsyncMock(return_value=([], ""))
        chain._query_cache = MagicMock()
        chain._aretrieve_uncached = AsyncMock(return_value=([], ""))
        chain._allow_general_knowledge = False

        events = [
            event
            async for event in chain.stream_query(
                "question",
                doc_category="StampTools",
                clarification_selected="PipelineWebGL",
                clarification_option_id="a",
                clarification_snapshot_id=snapshot.clarification_id,
                clarification_selected_candidate=snapshot.display_candidates[0].to_dict(),
                allow_general_knowledge=False,
            )
        ]

        call = chain._aretrieve_multi_uncached.await_args.kwargs
        assert call["backbone_canonical"] == ("PipelineWebGL",)
        assert call["strict_explicit_target"] is True
        assert any("PipelineWebGL" in str(event.get("data")) for event in events)

    asyncio.run(_run())


@pytest.mark.integration
def test_end_to_end_linear_aquery_pipeline_webgl():
    """Test full RagChain.aquery() linear execution path with clarification_selected='PipelineWebGL（StampTools）'."""
    import asyncio
    from unittest.mock import patch
    from rag_knowledge.services.rag import RagChain

    async def _run():
        chain = RagChain()
        with patch("rag_knowledge.llm_http.chat", return_value="PipelineWebGL 的硬件要求为 NVIDIA 显卡..."):
            res = await chain.aquery(
                "硬件要求",
                clarification_selected="PipelineWebGL（StampTools）",
                doc_category="StampTools",
                agent_orchestration_enabled=False,
            )
            assert res is not None
            assert len(res.get("source_documents", [])) > 0
            assert any(
                doc.get("metadata", {}).get("document_entity") == "PipelineWebGL"
                for doc in res.get("source_documents", [])
            )

    asyncio.run(_run())


@pytest.mark.integration
def test_end_to_end_linear_aquery_refusal_when_unmatched():
    """Test full RagChain.aquery() linear execution path triggers strict refusal when no aligned chunks exist."""
    import asyncio
    from unittest.mock import patch
    from rag_knowledge.services.rag import RagChain

    async def _run():
        chain = RagChain()
        with patch("rag_knowledge.llm_http.chat", return_value="虚构回复"):
            res = await chain.aquery(
                "硬件配置",
                clarification_selected="NonExistentProduct（StampTools）",
                doc_category="StampTools",
                agent_orchestration_enabled=False,
            )
            assert res is not None
            assert res.get("source_documents") == []
            assert "知识库中暂未找到与 NonExistentProduct 对齐" in res.get("answer", "")

    asyncio.run(_run())


def _make_test_app():
    from fastapi import FastAPI
    import rag_knowledge.api.routes as routes
    from rag_knowledge.services.rag import RagChain
    routes._rag = RagChain()
    test_app = FastAPI()
    test_app.include_router(routes.router, prefix="/api")
    return test_app


@pytest.mark.integration
def test_real_http_api_query_pipeline_webgl_success():
    """Real business scenario: HTTP POST /query with clarification_selected='PipelineWebGL（StampTools）'."""
    import asyncio
    from httpx import ASGITransport, AsyncClient
    from unittest.mock import patch

    async def _run():
        app = _make_test_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            with patch("rag_knowledge.llm_http.chat", return_value="PipelineWebGL 硬件要求为 NVIDIA 显卡..."):
                response = await client.post(
                    "/api/query",
                    json={
                        "question": "硬件要求",
                        "clarification_selected": "PipelineWebGL（StampTools）",
                        "doc_category": "StampTools",
                        "mode": "linear",
                    },
                )
                assert response.status_code == 200
                data = response.json()
                assert len(data.get("source_documents", [])) > 0
                assert any(
                    doc.get("metadata", {}).get("document_entity") == "PipelineWebGL"
                    for doc in data.get("source_documents", [])
                )

    asyncio.run(_run())


@pytest.mark.integration
def test_real_http_api_query_stream_pipeline_webgl_success():
    """Real business scenario: HTTP SSE POST /query/stream with clarification_selected='PipelineWebGL（StampTools）'."""
    import asyncio
    from httpx import ASGITransport, AsyncClient
    from unittest.mock import patch

    async def _run():
        app = _make_test_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            with patch("rag_knowledge.llm_http.chat", return_value="PipelineWebGL 硬件要求..."):
                response = await client.post(
                    "/api/query/stream",
                    json={
                        "question": "硬件要求",
                        "clarification_selected": "PipelineWebGL（StampTools）",
                        "doc_category": "StampTools",
                        "mode": "linear",
                    },
                )
                assert response.status_code == 200
                text = response.text
                assert "PipelineWebGL" in text
                assert "sources" in text or "token" in text

    asyncio.run(_run())


@pytest.mark.integration
def test_real_http_api_query_unmatched_refusal():
    """Real business scenario: HTTP POST /query with non-existent entity triggers refusal response."""
    import asyncio
    from httpx import ASGITransport, AsyncClient
    from unittest.mock import patch

    async def _run():
        app = _make_test_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            with patch("rag_knowledge.llm_http.chat", return_value="虚构回复"):
                response = await client.post(
                    "/api/query",
                    json={
                        "question": "硬件配置",
                        "clarification_selected": "NonExistentProduct（StampTools）",
                        "doc_category": "StampTools",
                        "mode": "linear",
                    },
                )
                assert response.status_code == 200
                data = response.json()
                assert data.get("source_documents") == []
                assert "知识库中暂未找到与 NonExistentProduct 对齐" in data.get("answer", "")

    asyncio.run(_run())


@pytest.mark.integration
def test_real_http_api_query_stream_unmatched_refusal():
    """Real business scenario: HTTP SSE POST /query/stream with non-existent entity triggers stream refusal."""
    import asyncio
    from httpx import ASGITransport, AsyncClient
    from unittest.mock import patch

    async def _run():
        app = _make_test_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            with patch("rag_knowledge.llm_http.chat", return_value="虚构回复"):
                response = await client.post(
                    "/api/query/stream",
                    json={
                        "question": "硬件配置",
                        "clarification_selected": "NonExistentProduct（StampTools）",
                        "doc_category": "StampTools",
                        "mode": "linear",
                    },
                )
                assert response.status_code == 200
                text = response.text
                assert "知识库中暂未找到与 NonExistentProduct 对齐" in text

    asyncio.run(_run())
