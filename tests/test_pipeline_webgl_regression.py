"""Regression test suite for PipelineWebGL retrieval mis-association and strict entity scope enforcement."""

import pytest
from types import SimpleNamespace
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


def test_retrieval_scope_creation():
    """Verify RetrievalScope correctly resolves explicit entity_name vs clarification_selected."""
    # Explicit entity_name provided
    scope1 = RetrievalScope.create("pipeline", entity_name="PipelineWebGL", doc_category="StampTools")
    assert scope1.canonical_entity == "PipelineWebGL"
    assert scope1.explicit_selection is True
    assert scope1.allowed_document_entity == "PipelineWebGL"

    # Selection from clarification string
    scope2 = RetrievalScope.create("pipeline", clarification_selected="PipelineWebGL（StampTools）")
    assert scope2.canonical_entity == "PipelineWebGL"
    assert scope2.explicit_selection is True
    assert scope2.allowed_document_entity == "PipelineWebGL"


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
    """Verify Evidence Gate evaluates to refusal when citable docs contain no PipelineWebGL chunks."""
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
    assert verdict["reason"] == "strict_entity_alignment_failed"
    assert "无法可靠回答" in verdict["refusal_text"]


def test_generic_entity_refusal_for_other_entities():
    """Verify that refusal generalization works seamlessly for any target entity (e.g. ObliqueModelBuilder or StampServer)."""
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
    assert verdict["reason"] == "strict_entity_alignment_failed"
    assert "ObliqueModelBuilder" in verdict["refusal_text"]


def test_agent_retrieval_propagates_explicit_scope():
    """Agent KB retrieval must enforce the same canonical scope as linear retrieval."""
    import asyncio
    from rag_knowledge.services.rag import RagChain

    async def _run():
        chain = object.__new__(RagChain)
        plan = SimpleNamespace(
            queries=["pipeline"],
            enable_rerank=False,
            top_k=8,
            candidate_k=16,
            expand_neighbors=False,
            intent_plan=None,
            backbone_canonical=("PipelineBuilder",),
            linked_entities=(),
        )
        target = {
            "content": "PipelineWebGL 硬件要求",
            "metadata": {"document_entity": "PipelineWebGL", "chunk_id": "chk_pw"},
        }
        chain._build_retrieval_query_specs = lambda question, history: ["pipeline"]
        chain._plan_retrieval = lambda question, queries, force_rerank=False: plan
        chain._prepare_graph_plan = MagicMock(return_value=(plan, None, []))
        chain._build_graph_kwargs = lambda *args, **kwargs: {}
        chain._anchor_protect_names = lambda current_plan: ()
        chain._aretrieve_multi_uncached = AsyncMock(return_value=([target], "ctx"))
        chain._record_chunk_hit_query = MagicMock()

        docs, _, _ = await chain._retrieve_kb_for_agent(
            "pipeline",
            history=None,
            kb_name=None,
            doc_category="StampTools",
            entity_name="PipelineWebGL",
            web_search=False,
            pinned_chunk_ids=None,
            excluded_chunk_ids=None,
        )

        assert len(docs) == 1
        assert docs[0]["content"] == target["content"]
        meta = docs[0]["metadata"]
        assert meta["chunk_id"] == "chk_pw"
        assert meta["scope_admitted"] is True
        assert meta["scope_admission_reason"] == "admissible_entity"
        assert meta["provenance_source_type"] == "direct_entity_chunk"
        assert meta["provenance_path"]["root_entity"] == "PipelineWebGL"
        call = chain._aretrieve_multi_uncached.await_args.kwargs
        assert call["backbone_canonical"] == ("PipelineWebGL",)
        assert call["strict_explicit_target"] is True

    asyncio.run(_run())


def test_stream_retrieval_propagates_explicit_scope():
    """Streaming linear retrieval must not bypass the explicit entity boundary."""
    import asyncio
    from rag_knowledge.services.query_planner import RetrievalPlan
    from rag_knowledge.services.rag import RagChain

    async def _run():
        chain = object.__new__(RagChain)
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
                clarification_selected="PipelineWebGL（StampTools）",
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
