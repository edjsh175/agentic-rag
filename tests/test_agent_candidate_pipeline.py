from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from langchain_core.documents import Document

from rag_knowledge.services.agent_candidate_pipeline import AgentCandidatePipeline
from rag_knowledge.services.text_evidence_admission import (
    TextEvidenceAdmissionService,
    TextEvidenceQualification,
    valid_text_qualification_protocol,
)
from rag_knowledge.services.bm25_store import BM25Store
from rag_knowledge.services.exploration_grant import ExplorationGrant
from rag_knowledge.services.retrieval_strategy import RetrievalStrategy
from rag_knowledge.services.agent_orchestration.evidence_gate import evaluate_rules
from rag_knowledge.services.agent_orchestration.graph_working_set import (
    GraphEntityState,
    GraphRelationCandidate,
    GraphWorkingSet,
)
from rag_knowledge.services.agent_orchestration.models import ConversationContext, EvidencePool
from rag_knowledge.services.dialogue_understanding import SemanticTaskContext


class _Store:
    def __init__(self, docs):
        self.docs = docs

    def get_chunks_by_metadata(self, filters, limit=20):
        values = self.docs
        conditions = filters.get("$and", [filters])
        for condition in conditions:
            if "document_entity" in condition:
                values = [d for d in values if d.metadata.get("document_entity") == condition["document_entity"]]
            if "chunk_id" in condition:
                ids = set(condition["chunk_id"].get("$in", []))
                values = [d for d in values if d.metadata.get("chunk_id") in ids]
            for key in ("review_status", "kb_name", "doc_category"):
                if key in condition:
                    values = [d for d in values if d.metadata.get(key) == condition[key]]
        return values[:limit]


class _Graph:
    def __init__(self):
        self.entities = [
            {"id": "pipe", "name": "PipelineWebRTC", "canonical_name": "PipelineWebRTC", "review_status": "approved"},
            {"id": "webrtc", "name": "WebRTC", "canonical_name": "WebRTC", "review_status": "approved"},
            {"id": "builder", "name": "PipelineBuilder", "canonical_name": "PipelineBuilder", "review_status": "approved"},
        ]

    def list_entities(self, review_status=""):
        return list(self.entities)

    def list_links(self, entity_id="", chunk_id=""):
        return []

    def list_relations(self, entity_id="", relation_type="", review_status=""):
        rows = [
            {"source_entity_id": "pipe", "target_entity_id": "webrtc", "source_name": "PipelineWebRTC", "target_name": "WebRTC", "relation_type": "belongs_to"},
            {"source_entity_id": "pipe", "target_entity_id": "builder", "source_name": "PipelineWebRTC", "target_name": "PipelineBuilder", "relation_type": "different_from"},
        ]
        if entity_id:
            rows = [r for r in rows if entity_id in {r["source_entity_id"], r["target_entity_id"]}]
        if relation_type:
            rows = [r for r in rows if r["relation_type"] == relation_type]
        return rows


class _Strategy:
    def __init__(self, bm25_docs, vector_docs):
        self._bm25_docs = bm25_docs
        self._vector_docs = vector_docs

    def _get_bm25(self):
        return SimpleNamespace(search=lambda *args, **kwargs: list(self._bm25_docs)[:kwargs["top_k"]])

    def _retrieve_vector(self, *args, **kwargs):
        assert kwargs.get("scope") is None
        return list(self._vector_docs)


def _doc(chunk_id, entity, content):
    return Document(page_content=content, metadata={"chunk_id": chunk_id, "document_entity": entity, "review_status": "approved"})


def _working_set(*, include_links: bool = False, weak_relation: bool = False) -> GraphWorkingSet:
    ws = GraphWorkingSet()
    ws.add_root("PipelineWebRTC", entity_id="pipe")
    ws.add_entity(GraphEntityState("webrtc", "WebRTC", depth_from_root=1, origin_root="PipelineWebRTC"))
    ws.add_entity(GraphEntityState("builder", "PipelineBuilder", depth_from_root=1, origin_root="PipelineWebRTC"))
    ws.add_relation(GraphRelationCandidate("rel-webrtc", "PipelineWebRTC", "WebRTC", "belongs_to"))
    ws.add_relation(GraphRelationCandidate(
        "rel-builder",
        "PipelineWebRTC",
        "PipelineBuilder",
        "related_to" if weak_relation else "different_from",
    ))
    if include_links:
        ws.add_entity_chunk_links("WebRTC", ["strong-a", "strong-b"])
        ws.add_entity_chunk_links("PipelineBuilder", ["weak"])
    return ws


def test_cross_document_candidate_is_admitted_without_identity_prefilter():
    valid = _doc("cross", "WebRTC", "PipelineWebRTC 用于建立实时音视频处理通道。")
    pipeline = AgentCandidatePipeline(
        vector_store=_Store([valid]), retrieval_strategy=_Strategy([valid], [valid]), graph_db=_Graph(),
    )

    ws = _working_set()
    candidates = pipeline.generate("PipelineWebRTC 的主要功能是什么？", target_entity="PipelineWebRTC", kb_name=None, review_status="approved", doc_category=None, graph_working_set=ws)
    admission = TextEvidenceAdmissionService().qualify(
        candidates[0],
        retrieval_query="PipelineWebRTC 的主要功能是什么？",
        target_entity="PipelineWebRTC",
        semantic_admitter=lambda query, _candidate, pending: TextEvidenceQualification(
            verdict="PASS",
            evidence_class="TARGET_DIRECT",
            support_scope="TARGET_SPECIFIC",
            intent_relevance="HIGH",
            reason_code="semantic_direct_attribution",
            reason="Candidate directly attributes the described capability to PipelineWebRTC.",
            signals=pending.signals,
            canonical_question=query,
        ),
    )

    assert candidates[0].document.metadata["document_entity"] == "WebRTC"
    assert {"graph_expansion", "exact_lexical", "bm25", "vector"} <= set(candidates[0].source_generators)
    assert admission.verdict == "PASS"


def test_admission_rejects_explicit_sibling_conflict():
    wrong = _doc("builder", "PipelineBuilder", "Pipeline 的构建与发布说明。")
    pipeline = AgentCandidatePipeline(
        vector_store=_Store([wrong]), retrieval_strategy=_Strategy([], [wrong]), graph_db=_Graph(),
    )

    ws = _working_set()
    candidates = pipeline.generate("PipelineWebRTC 的主要功能是什么？", target_entity="PipelineWebRTC", kb_name=None, review_status="approved", doc_category=None, graph_working_set=ws)
    admission = TextEvidenceAdmissionService().qualify(
        candidates[0],
        retrieval_query="PipelineWebRTC 的主要功能是什么？",
        target_entity="PipelineWebRTC",
        graph_working_set=ws,
    )
    assert admission.verdict == "REJECT"
    assert admission.evidence_class == "CONFLICT"


def test_admission_rejects_correct_entity_with_wrong_intent():
    deployment = _doc("deploy", "PipelineWebRTC", "PipelineWebRTC 上传到 /data/html 目录。")
    pipeline = AgentCandidatePipeline(
        vector_store=_Store([deployment]), retrieval_strategy=_Strategy([deployment], [deployment]), graph_db=_Graph(),
    )
    candidates = pipeline.generate("PipelineWebRTC 的主要功能是什么？", target_entity="PipelineWebRTC", kb_name=None, review_status="approved", doc_category=None)

    admission = TextEvidenceAdmissionService().qualify(
        candidates[0], retrieval_query="PipelineWebRTC 的主要功能是什么？", target_entity="PipelineWebRTC"
    )
    assert "target_text_mention" in admission.signals
    assert admission.intent_relevance == "LOW"
    assert admission.verdict == "REJECT"


def test_general_qa_admits_deployment_fact_despite_overview_search_plan():
    deployment = _doc("deploy-general", "PipelineWebRTC", "PipelineWebRTC 上传到 /data/html 目录。")
    pipeline = AgentCandidatePipeline(
        vector_store=_Store([deployment]), retrieval_strategy=_Strategy([deployment], [deployment]), graph_db=_Graph(),
    )
    candidate = pipeline.generate(
        "PipelineWebRTC 功能与用途概述", target_entity="PipelineWebRTC",
        kb_name=None, review_status="approved", doc_category=None,
    )[0]
    task = SemanticTaskContext(
        "PipelineWebRTC 的相关信息", "PipelineWebRTC", ("PipelineWebRTC",),
        "single_entity", 1.0, "general_qa", (), "clarification_default",
    )

    admitted = TextEvidenceAdmissionService().qualify(
        candidate,
        retrieval_query="PipelineWebRTC 功能与用途概述",
        target_entity="PipelineWebRTC",
        semantic_task=task,
        semantic_admitter=lambda query, _candidate, pending: TextEvidenceQualification(
            verdict="PASS",
            evidence_class="TARGET_DIRECT",
            support_scope="TARGET_SPECIFIC",
            intent_relevance="HIGH",
            reason_code="semantic_direct_attribution",
            reason="The canonical general question allows this directly attributed deployment fact.",
            signals=pending.signals,
            canonical_question=query,
        ),
    )

    assert admitted.verdict == "PASS"
    assert admitted.canonical_question == "PipelineWebRTC 的相关信息"
    assert admitted.answer_intent == "general_qa"


def test_admission_ignores_unrelated_overview_terms_in_merged_chunk():
    merged = _doc(
        "deploy-merged",
        "StampServer",
        "PipelineWebRTC 的 IP 地址需要在部署时修改。\n\n"
        "多显卡渲染系统支持超高分辨率大屏展示。",
    )
    pipeline = AgentCandidatePipeline(
        vector_store=_Store([merged]), retrieval_strategy=_Strategy([merged], [merged]), graph_db=_Graph(),
    )
    candidate = pipeline.generate(
        "PipelineWebRTC 的主要功能是什么？",
        target_entity="PipelineWebRTC",
        kb_name=None,
        review_status="approved",
        doc_category=None,
    )[0]

    admission = TextEvidenceAdmissionService().qualify(
        candidate,
        retrieval_query="PipelineWebRTC 的主要功能是什么？",
        target_entity="PipelineWebRTC",
        semantic_admitter=lambda query, _candidate, pending: TextEvidenceQualification(
            verdict="REJECT",
            evidence_class="IRRELEVANT",
            support_scope="NONE",
            intent_relevance="LOW",
            reason_code="semantic_intent_mismatch",
            reason="The PipelineWebRTC sentence is about deployment, not its primary function.",
            signals=pending.signals,
            canonical_question=query,
        ),
    )

    assert "target_text_mention" in admission.signals
    assert admission.intent_relevance == "LOW"
    assert admission.verdict == "REJECT"
    assert admission.reason_code == "semantic_intent_mismatch"


def test_unlisted_intent_does_not_auto_pass_without_semantic_admission():
    deployment = _doc("deploy-limit", "PipelineWebRTC", "PipelineWebRTC 上传到 /data/html 目录。")
    pipeline = AgentCandidatePipeline(
        vector_store=_Store([deployment]), retrieval_strategy=_Strategy([deployment], [deployment]), graph_db=_Graph(),
    )
    candidate = pipeline.generate("PipelineWebRTC 有哪些限制？", target_entity="PipelineWebRTC", kb_name=None, review_status="approved", doc_category=None)[0]

    admission = TextEvidenceAdmissionService().qualify(
        candidate, retrieval_query="PipelineWebRTC 有哪些限制？", target_entity="PipelineWebRTC"
    )
    assert "target_text_mention" in admission.signals
    assert admission.intent_relevance == "LOW"
    assert admission.verdict == "REJECT"


def test_ambiguous_graph_candidate_uses_semantic_admission_protocol():
    generic = _doc("generic", "WebRTC", "该组件提供实时媒体处理能力。")
    pipeline = AgentCandidatePipeline(
        vector_store=_Store([generic]), retrieval_strategy=_Strategy([], []), graph_db=_Graph(),
    )
    ws = _working_set()
    candidates = pipeline.generate("PipelineWebRTC 的功能是什么？", target_entity="PipelineWebRTC", kb_name=None, review_status="approved", doc_category=None, graph_working_set=ws)
    candidate = candidates[0]

    admission = TextEvidenceAdmissionService().qualify(
        candidate,
        retrieval_query="PipelineWebRTC 的功能是什么？",
        target_entity="PipelineWebRTC",
        semantic_admitter=lambda query, _candidate, _det: TextEvidenceQualification(
            verdict="PASS",
            evidence_class="RELATED_CONTEXT",
            support_scope="CONTEXT_ONLY",
            intent_relevance="HIGH",
            reason_code="semantic_relation_context",
            reason="semantic_relation_context",
            signals=("graph_path",),
            canonical_question=query,
        ),
    )

    assert admission.verdict == "PASS"
    assert admission.reason == "semantic_relation_context"


def test_v2_grant_keeps_only_hard_chroma_boundary_and_bm25_is_unscoped():
    grant = ExplorationGrant(
        grant_id="g", identity_scope_id="i", target_entities=("PipelineWebRTC",),
        source_type="user_explicit_mention", source_ref="query", candidate_pipeline_v2=True,
    )
    strategy = object.__new__(RetrievalStrategy)
    strategy._cfg = SimpleNamespace(retrieval_top_k=4, retrieval_fetch_k=20, retrieval_lambda_mult=0.5)
    chroma = MagicMock()
    chroma.as_retriever.return_value.invoke.return_value = []
    strategy._store = MagicMock()
    strategy._store.get_chroma.return_value = chroma
    strategy._retrieve_vector("q", "kb", None, "approved", "similarity", 2, scope=grant)
    assert chroma.as_retriever.call_args.kwargs["search_kwargs"]["filter"] == {"$and": [{"kb_name": "kb"}, {"review_status": "approved"}]}

    store = BM25Store()
    store.build_index_from_documents([
        _doc("a", "PipelineWebRTC", "共同关键词 alpha"),
        _doc("b", "WebRTC", "共同关键词 alpha alpha"),
    ])
    hits = store.search("共同关键词", top_k=2, scope=grant)
    assert {doc.metadata["document_entity"] for doc in hits} == {"PipelineWebRTC", "WebRTC"}


def test_unbound_pipeline_generates_candidates_and_requires_intent_admission():
    topic = _doc("topic", "WebRTC", "实时媒体处理组件的配置说明。")
    pipeline = AgentCandidatePipeline(
        vector_store=_Store([topic]), retrieval_strategy=_Strategy([topic], [topic]), graph_db=_Graph(),
    )
    candidates = pipeline.generate("如何配置实时媒体组件？", target_entity="", kb_name=None, review_status="approved", doc_category=None)

    assert candidates
    assert {"bm25", "vector"} <= set(candidates[0].source_generators)
    assert "exact_lexical" not in candidates[0].source_generators
    rejected = TextEvidenceAdmissionService().qualify(
        candidates[0], retrieval_query="如何配置实时媒体组件？", target_entity=""
    )
    admitted = TextEvidenceAdmissionService().qualify(
        candidates[0],
        retrieval_query="如何配置实时媒体组件？",
        target_entity="",
        semantic_admitter=lambda query, _candidate, _det: TextEvidenceQualification(
            verdict="PASS",
            evidence_class="TARGET_DIRECT",
            support_scope="TARGET_SPECIFIC",
            intent_relevance="HIGH",
            reason_code="topic_intent_match",
            reason="topic_intent_match",
            signals=("semantic_task",),
            canonical_question=query,
        ),
    )
    assert rejected.verdict == "REJECT"
    assert admitted.verdict == "PASS"


def test_graph_rrf_rank_is_continuous_and_strength_weighted():
    strong_a = _doc("strong-a", "WebRTC", "PipelineWebRTC 功能 A")
    strong_b = _doc("strong-b", "WebRTC", "PipelineWebRTC 功能 B")
    weak = _doc("weak", "PipelineBuilder", "PipelineWebRTC 相关说明")

    class StrengthGraph(_Graph):
        def list_relations(self, entity_id="", relation_type="", review_status=""):
            rows = [
                {"source_entity_id": "pipe", "target_entity_id": "webrtc", "source_name": "PipelineWebRTC", "target_name": "WebRTC", "relation_type": "belongs_to"},
                {"source_entity_id": "pipe", "target_entity_id": "builder", "source_name": "PipelineWebRTC", "target_name": "PipelineBuilder", "relation_type": "related_to"},
            ]
            if entity_id:
                rows = [r for r in rows if entity_id in {r["source_entity_id"], r["target_entity_id"]}]
            if relation_type:
                rows = [r for r in rows if r["relation_type"] == relation_type]
            return rows

    pipeline = AgentCandidatePipeline(
        vector_store=_Store([strong_a, strong_b, weak]), retrieval_strategy=_Strategy([], []), graph_db=StrengthGraph(),
    )
    candidates = pipeline.generate("PipelineWebRTC", target_entity="PipelineWebRTC", kb_name=None, review_status="approved", doc_category=None, graph_working_set=_working_set(include_links=True, weak_relation=True))
    graph = {
        item.chunk_id: next(p for p in item.provenance if p.generator == "graph_expansion")
        for item in candidates if any(p.generator == "graph_expansion" for p in item.provenance)
    }

    assert graph["strong-a"].rank < graph["strong-b"].rank
    assert graph["strong-a"].weight > graph["weak"].weight


def test_v2_evidence_gate_requires_current_admission_and_retrieve_group():
    conv = ConversationContext(user_question="q", session=SimpleNamespace(turns=[]))
    pool = EvidencePool(question_id="q")
    pool.add_retrieve([{
        "content": "unreviewed candidate",
        "metadata": {"chunk_id": "x", "candidate_pipeline_v2": True, "admission_verdict": "", "grant_id": "g", "grant_admitted": True},
    }], grant=SimpleNamespace(grant_id="g", target_entities=(), source_type="", source_ref="", hop_depth=0, primary_root=None))
    assert evaluate_rules(conv, pool)["reason"] == "query_admission_failed"

    relation_pool = EvidencePool(question_id="q")
    relation_pool.add_relation(relation_key="A -[depends_on]-> B", admission_verdict="PASS")
    relation_pool.groups[0].kind = "unknown"
    relation_pool.groups[0].docs[0]["metadata"]["candidate_pipeline_v2"] = True
    relation_pool.groups[0].docs[0]["metadata"]["admission_verdict"] = "PASS"
    assert evaluate_rules(conv, relation_pool)["reason"] == "v2_non_retrieve_evidence"


def test_graph_relation_gate_rejects_missing_admission_without_v2_marker():
    pool = EvidencePool(question_id="q")
    pool.groups.append(SimpleNamespace(
        status="ACTIVE",
        kind="relation",
        docs=[{"metadata": {"chunk_id": "graph-r1", "source_type": "graph_relation", "admission_verdict": ""}}],
    ))

    assert evaluate_rules(ConversationContext(user_question="q", session=SimpleNamespace(turns=[])), pool)["reason"] == "graph_relation_admission_failed"


def test_v2_pinned_chunk_is_admitted_instead_of_inserted_after_admission():
    import asyncio
    from rag_knowledge.services.rag import RagChain

    pinned = {
        "content": "PipelineWebRTC 上传到 /data/html 目录。",
        "metadata": {"chunk_id": "pinned", "document_entity": "PipelineWebRTC", "review_status": "approved", "pinned": True},
    }
    chain = object.__new__(RagChain)
    chain._store = _Store([])
    chain._strategy = _Strategy([], [])
    chain._graph_retriever = None
    chain._fetch_pinned_chunks = lambda _: [pinned]
    chain._postprocess_docs = lambda _q, docs, *_args, **_kwargs: asyncio.sleep(0, result=docs)
    chain._normalize_source = lambda content, metadata, _index: {"content": content, "metadata": dict(metadata)}
    chain._apply_pinned_excluded = RagChain._apply_pinned_excluded.__get__(chain, RagChain)
    chain._record_chunk_hit_query = lambda _docs: None
    grant = ExplorationGrant(
        grant_id="g", identity_scope_id="i", target_entities=("PipelineWebRTC",),
        source_type="user_explicit_mention", source_ref="q", candidate_pipeline_v2=True,
    )
    plan = SimpleNamespace(enable_rerank=False, top_k=8)

    docs, _ = asyncio.run(chain._retrieve_agent_candidates_v2(
        "PipelineWebRTC 的主要功能是什么？", plan=plan, scope=grant,
        kb_name=None, doc_category=None, pinned_chunk_ids=["pinned"], excluded_chunk_ids=None,
    ))
    assert docs == []


def test_v2_reuse_is_re_admitted_for_the_current_question():
    import asyncio
    from rag_knowledge.services.rag import RagChain

    chain = object.__new__(RagChain)
    chain._store = _Store([])
    chain._strategy = _Strategy([], [])
    chain._graph_retriever = None
    chain._normalize_source = lambda content, metadata, _index: {"content": content, "metadata": dict(metadata)}
    grant = ExplorationGrant(
        grant_id="g", identity_scope_id="i", target_entities=("PipelineWebRTC",),
        source_type="user_explicit_mention", source_ref="q", candidate_pipeline_v2=True,
    )
    reused = [{
        "content": "PipelineWebRTC 上传到 /data/html 目录。",
        "metadata": {"chunk_id": "old-deploy", "document_entity": "PipelineWebRTC", "review_status": "approved"},
    }]

    docs = asyncio.run(chain._admit_existing_agent_docs_v2(
        "PipelineWebRTC 的主要功能是什么？", reused, grant=grant,
    ))
    assert docs == []


def test_v2_reuse_respects_current_kb_and_category_boundary():
    import asyncio
    from rag_knowledge.services.rag import RagChain

    chain = object.__new__(RagChain)
    chain._store = _Store([])
    chain._strategy = _Strategy([], [])
    chain._graph_retriever = None
    chain._normalize_source = lambda content, metadata, _index: {"content": content, "metadata": dict(metadata)}
    grant = ExplorationGrant(
        grant_id="g", identity_scope_id="i", target_entities=("PipelineWebRTC",),
        source_type="user_explicit_mention", source_ref="q", candidate_pipeline_v2=True,
    )
    previous = [{
        "content": "PipelineWebRTC 用于实时音视频处理。",
        "metadata": {
            "chunk_id": "old", "document_entity": "PipelineWebRTC", "review_status": "approved",
            "kb_name": "KB-A", "doc_category": "manual",
        },
    }]

    rejected_kb = asyncio.run(chain._admit_existing_agent_docs_v2(
        "PipelineWebRTC 的功能是什么？", previous, grant=grant, kb_name="KB-B", doc_category="manual",
    ))
    rejected_category = asyncio.run(chain._admit_existing_agent_docs_v2(
        "PipelineWebRTC 的功能是什么？", previous, grant=grant, kb_name="KB-A", doc_category="guide",
    ))
    assert rejected_kb == []
    assert rejected_category == []


def test_semantic_admission_pass_protocol_is_fail_closed():
    assert valid_text_qualification_protocol(TextEvidenceQualification(
        verdict="PASS", evidence_class="CONFLICT", support_scope="NONE",
        intent_relevance="HIGH", reason_code="bad", reason="bad",
    ), target_entity="PipelineWebRTC") is False
    assert valid_text_qualification_protocol(TextEvidenceQualification(
        verdict="PASS", evidence_class="TARGET_DIRECT", support_scope="TARGET_SPECIFIC",
        intent_relevance="LOW", reason_code="bad", reason="bad",
    ), target_entity="PipelineWebRTC") is False
    assert valid_text_qualification_protocol(TextEvidenceQualification(
        verdict="PASS", evidence_class="RELATED_CONTEXT", support_scope="CONTEXT_ONLY",
        intent_relevance="HIGH", reason_code="ok", reason="ok",
    ), target_entity="") is True


def test_multi_path_trace_reports_actual_generator_contributions():
    doc = _doc("trace", "PipelineWebRTC", "PipelineWebRTC 用于实时音视频处理。")
    pipeline = AgentCandidatePipeline(
        vector_store=_Store([doc]), retrieval_strategy=_Strategy([doc], [doc]), graph_db=_Graph(),
    )
    candidates = pipeline.generate("PipelineWebRTC 的功能是什么？", target_entity="PipelineWebRTC", kb_name=None, review_status="approved", doc_category=None)
    trace = pipeline.generator_trace(candidates)

    assert set(trace) >= {"direct_document_entity", "exact_lexical", "bm25", "vector"}
    assert all("rrf_contribution" in value for value in trace.values())


def test_v2_agent_entrypoint_retrieves_unbound_topic_instead_of_returning_empty():
    import asyncio
    from rag_knowledge.services.rag import RagChain

    topic = _doc("topic-main", "WebRTC", "实时媒体组件配置包括编码参数。")
    chain = object.__new__(RagChain)
    chain._store = _Store([topic])
    chain._strategy = _Strategy([topic], [topic])
    chain._graph_retriever = None
    chain._build_retrieval_query_specs = lambda *_: ["实时媒体组件配置"]
    chain._plan_retrieval = lambda *_args, **_kwargs: SimpleNamespace(enable_rerank=False, top_k=8)
    chain._fetch_pinned_chunks = lambda _: []
    chain._postprocess_docs = lambda _q, docs, *_args, **_kwargs: asyncio.sleep(0, result=docs)
    chain._normalize_source = lambda content, metadata, _index: {"content": content, "metadata": dict(metadata)}
    chain._apply_pinned_excluded = RagChain._apply_pinned_excluded.__get__(chain, RagChain)
    chain._record_chunk_hit_query = lambda _docs: None
    chain._cfg = SimpleNamespace()
    grant = ExplorationGrant(
        grant_id="topic", identity_scope_id="i", target_entities=(),
        source_type="confirmed_topic", source_ref="topic:media", candidate_pipeline_v2=True,
    )

    semantic_pass = TextEvidenceQualification(
        verdict="PASS",
        evidence_class="TARGET_DIRECT",
        support_scope="TARGET_SPECIFIC",
        intent_relevance="HIGH",
        reason_code="topic_match",
        reason="topic_match",
        signals=("semantic_task",),
        canonical_question="实时媒体组件如何配置？",
        answer_intent="config",
    )
    with patch.object(
        TextEvidenceAdmissionService,
        "_semantic_qualify_via_llm",
        return_value=semantic_pass,
    ):
        docs, _, _ = asyncio.run(chain._retrieve_kb_for_agent(
            "实时媒体组件如何配置？", history=None, kb_name=None, doc_category=None,
            entity_name=None, web_search=False, pinned_chunk_ids=None, excluded_chunk_ids=None,
            retrieval_scope=grant,
        ))
    assert [doc["metadata"]["chunk_id"] for doc in docs] == ["topic-main"]
