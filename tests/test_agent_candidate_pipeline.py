from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from langchain_core.documents import Document

from rag_knowledge.services.agent_candidate_pipeline import AdmissionResult, AgentCandidatePipeline
from rag_knowledge.services.bm25_store import BM25Store
from rag_knowledge.services.exploration_grant import ExplorationGrant
from rag_knowledge.services.retrieval_strategy import RetrievalStrategy


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


def test_cross_document_candidate_is_admitted_without_identity_prefilter():
    valid = _doc("cross", "WebRTC", "PipelineWebRTC 用于建立实时音视频处理通道。")
    pipeline = AgentCandidatePipeline(
        vector_store=_Store([valid]), retrieval_strategy=_Strategy([valid], [valid]), graph_db=_Graph(),
    )

    candidates = pipeline.generate("PipelineWebRTC 的主要功能是什么？", target_entity="PipelineWebRTC", kb_name=None, review_status="approved", doc_category=None)
    guarded = pipeline.structural_guard(candidates, target_entity="PipelineWebRTC")
    admission = pipeline.admit("PipelineWebRTC 的主要功能是什么？", guarded[0], target_entity="PipelineWebRTC")

    assert guarded[0].document.metadata["document_entity"] == "WebRTC"
    assert {"graph_expansion", "exact_lexical", "bm25", "vector"} <= set(guarded[0].source_generators)
    assert admission.verdict == "PASS"


def test_structural_guard_rejects_explicit_sibling_without_target_signal():
    wrong = _doc("builder", "PipelineBuilder", "Pipeline 的构建与发布说明。")
    pipeline = AgentCandidatePipeline(
        vector_store=_Store([wrong]), retrieval_strategy=_Strategy([], [wrong]), graph_db=_Graph(),
    )

    candidates = pipeline.generate("PipelineWebRTC 的主要功能是什么？", target_entity="PipelineWebRTC", kb_name=None, review_status="approved", doc_category=None)
    assert pipeline.structural_guard(candidates, target_entity="PipelineWebRTC") == []


def test_admission_rejects_correct_entity_with_wrong_intent():
    deployment = _doc("deploy", "PipelineWebRTC", "PipelineWebRTC 上传到 /data/html 目录。")
    pipeline = AgentCandidatePipeline(
        vector_store=_Store([deployment]), retrieval_strategy=_Strategy([deployment], [deployment]), graph_db=_Graph(),
    )
    candidates = pipeline.generate("PipelineWebRTC 的主要功能是什么？", target_entity="PipelineWebRTC", kb_name=None, review_status="approved", doc_category=None)

    admission = pipeline.admit("PipelineWebRTC 的主要功能是什么？", candidates[0], target_entity="PipelineWebRTC")
    assert admission.entity_relevance == "HIGH"
    assert admission.intent_relevance == "LOW"
    assert admission.verdict == "REJECT"


def test_ambiguous_graph_candidate_uses_semantic_admission_protocol():
    generic = _doc("generic", "WebRTC", "该组件提供实时媒体处理能力。")
    pipeline = AgentCandidatePipeline(
        vector_store=_Store([generic]), retrieval_strategy=_Strategy([], []), graph_db=_Graph(),
    )
    candidates = pipeline.generate("PipelineWebRTC 的功能是什么？", target_entity="PipelineWebRTC", kb_name=None, review_status="approved", doc_category=None)
    candidate = pipeline.structural_guard(candidates, target_entity="PipelineWebRTC")[0]

    admission = pipeline.admit(
        "PipelineWebRTC 的功能是什么？",
        candidate,
        target_entity="PipelineWebRTC",
        semantic_admitter=lambda *_: AdmissionResult("PASS", "MEDIUM", "HIGH", "semantic_relation_context", ("graph_path",)),
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
