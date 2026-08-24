"""Unit tests for EvidenceScope, ProvenancePath, ScopePolicy and ScopeResolver."""
from __future__ import annotations

import pytest
from langchain_core.documents import Document

from rag_knowledge.services.agent_orchestration.evidence_gate import evaluate_rules
from rag_knowledge.services.agent_orchestration.models import (
    ConversationContext,
    EvidencePool,
    SessionState,
)
from rag_knowledge.services.evidence_scope import (
    BindingStrength,
    EvidenceScope,
    ProvenancePath,
    ProvenanceSourceType,
    ScopePolicy,
    ScopeResolver,
    SubjectResolution,
)
from rag_knowledge.services.retrieval_scope import RetrievalScope
from rag_knowledge.services.retrieval_strategy import RetrievalStrategy


def test_binding_strength_and_lock_status():
    """测试 BindingStrength 状态机及锁定属性。"""
    unbound_scope = EvidenceScope(
        scope_id="s1",
        root_entities=(),
        binding_strength=BindingStrength.UNBOUND,
    )
    assert not unbound_scope.is_identity_locked
    assert unbound_scope.primary_root is None

    inferred_scope = EvidenceScope(
        scope_id="s2",
        root_entities=("PipelineWebGL",),
        binding_strength=BindingStrength.INFERRED,
    )
    assert not inferred_scope.is_identity_locked
    assert inferred_scope.primary_root == "PipelineWebGL"

    confirmed_scope = EvidenceScope(
        scope_id="s3",
        root_entities=("PipelineWebGL",),
        binding_strength=BindingStrength.CONFIRMED,
    )
    assert confirmed_scope.is_identity_locked

    explicit_scope = EvidenceScope(
        scope_id="s4",
        root_entities=("PipelineWebGL",),
        binding_strength=BindingStrength.EXPLICIT,
    )
    assert explicit_scope.is_identity_locked


def test_scope_resolver_subject_resolution():
    """测试 ScopeResolver 对显式输入、澄清选择和提问中主体的解析。"""
    constraints = {
        "canonical_by_alias": {
            "webgl": "PipelineWebGL",
            "pipelinewebgl": "PipelineWebGL",
            "builder": "PipelineBuilder",
            "pipelinebuilder": "PipelineBuilder",
        },
        "entity_type_by_name": {
            "PipelineWebGL": "Product",
            "PipelineBuilder": "Product",
        },
        "relations": [
            {"source": "PipelineWebGL", "relation_type": "different_from", "target": "PipelineBuilder"},
        ],
    }

    # 1. 显式 entity_name -> EXPLICIT
    subj1 = ScopeResolver.resolve_subject("介绍一下", entity_name="webgl", constraints=constraints)
    assert subj1.binding_strength == BindingStrength.EXPLICIT
    assert subj1.primary_entities == ("PipelineWebGL",)

    # 2. 澄清选择 -> CONFIRMED
    subj2 = ScopeResolver.resolve_subject("介绍一下", clarification_selected="PipelineWebGL（StampTools）", constraints=constraints)
    assert subj2.binding_strength == BindingStrength.CONFIRMED
    assert subj2.primary_entities == ("PipelineWebGL",)

    # 3. 普通文本匹配 -> INFERRED
    subj3 = ScopeResolver.resolve_subject("PipelineWebGL 怎么配置？", constraints=constraints)
    assert subj3.binding_strength == BindingStrength.INFERRED
    assert subj3.primary_entities == ("PipelineWebGL",)

    # 4. V1.6：Scope 不再识别“比较”业务语义，只记录其它显式引用实体。
    subj4 = ScopeResolver.resolve_subject("PipelineWebGL 和 PipelineBuilder 有什么区别？", constraints=constraints)
    assert subj4.primary_entities == ("PipelineWebGL",)
    assert subj4.referenced_entities == ("PipelineBuilder",)

    scope4 = ScopeResolver.resolve(
        "PipelineWebGL 和 PipelineBuilder 有什么区别？",
        constraints=constraints,
    )
    assert scope4.root_entities == ("PipelineWebGL", "PipelineBuilder")
    assert "PipelineWebGL" in scope4.admissible_entities
    assert "PipelineBuilder" in scope4.admissible_entities


def test_scope_resolver_bounded_expansion():
    """测试 ScopeResolver 生成的广义 ProvenancePath 与有界预算。"""
    constraints = {
        "canonical_by_alias": {
            "pipelinewebgl": "PipelineWebGL",
            "servicea": "ServiceA",
            "serviceb": "ServiceB",
            "servicec": "ServiceC",
        },
        "entity_type_by_name": {
            "PipelineWebGL": "Product",
            "ServiceA": "Service",
            "ServiceB": "Service",
            "ServiceC": "Service",
        },
        "different_from": {frozenset({"PipelineWebGL", "PipelineBuilder"})},
        "relations": [
            {"source": "PipelineWebGL", "relation_type": "depends_on", "target": "ServiceA"},
            {"source": "PipelineWebGL", "relation_type": "has_service", "target": "ServiceB"},
            {"source": "PipelineWebGL", "relation_type": "requires", "target": "ServiceC"},
            {"source": "PipelineWebGL", "relation_type": "different_from", "target": "PipelineBuilder"},
        ],
    }

    policy = ScopePolicy(max_hops=1, max_admissible_entities=3)
    scope = ScopeResolver.resolve(
        "PipelineWebGL 依赖什么服务？",
        constraints=constraints,
        policy=policy,
    )

    # 必须包含 root 自身
    assert "PipelineWebGL" in scope.admissible_entities
    # 受 max_admissible_entities=3 限制
    assert len(scope.admissible_entities) <= 3
    # 非对比问题下，different_from 不得默认扩展入 admissible_entities，并应在 excluded_rebindings 中
    assert "PipelineBuilder" not in scope.admissible_entities
    assert "PipelineBuilder" in scope.excluded_rebindings

    # 验证 ProvenancePath
    paths = scope.provenance_paths
    assert any(p.relation_type == "self" and p.root_entity == "PipelineWebGL" for p in paths)
    assert any(p.source_type == ProvenanceSourceType.GRAPH_RELATION.value for p in paths)


def test_retrieval_scope_facade_compatibility():
    """测试 RetrievalScope 外观类与 EvidenceScope 的双向兼容。"""
    scope = RetrievalScope.create(
        "PipelineWebGL 安装说明",
        entity_name="PipelineWebGL",
        doc_category="Manual",
    )
    assert scope.canonical_entity == "PipelineWebGL"
    assert scope.explicit_selection is True
    assert scope.doc_category == "Manual"
    assert scope.evidence_scope is not None
    assert scope.evidence_scope.is_identity_locked is True

    ev = scope.to_evidence_scope()
    assert ev.primary_root == "PipelineWebGL"
    assert "PipelineWebGL" in ev.admissible_entities


def test_retrieval_strategy_filter_by_scope():
    """测试检索策略中的 Structural Eligibility 过滤。"""
    scope = EvidenceScope(
        scope_id="test_scope",
        root_entities=("PipelineWebGL",),
        binding_strength=BindingStrength.EXPLICIT,
        admissible_entities=frozenset({"PipelineWebGL", "ServiceA"}),
        excluded_rebindings=frozenset({"PipelineBuilder"}),
    )

    doc_valid_root = Document(page_content="PipelineWebGL content", metadata={"chunk_id": "c1", "document_entity": "PipelineWebGL"})
    doc_valid_related = Document(page_content="ServiceA content", metadata={"chunk_id": "c2", "document_entity": "ServiceA"})
    doc_excluded_sibling = Document(page_content="PipelineBuilder content", metadata={"chunk_id": "c3", "document_entity": "PipelineBuilder"})
    doc_general_no_entity = Document(page_content="General guide", metadata={"chunk_id": "c4"})

    input_docs = [doc_valid_root, doc_valid_related, doc_excluded_sibling, doc_general_no_entity]
    filtered = RetrievalStrategy._filter_by_scope(input_docs, scope)

    chunk_ids = [d.metadata.get("chunk_id") for d in filtered]
    assert "c1" in chunk_ids
    assert "c2" in chunk_ids
    # locked scope 下，无正式实体归属且未 materialize 的 chunk 不再自动放行。
    assert "c4" not in chunk_ids
    # 歧义兄弟实体必须被过滤
    assert "c3" not in chunk_ids
    assert all(d.metadata.get("scope_id") == "test_scope" for d in filtered)
    assert all(d.metadata.get("scope_root") == "PipelineWebGL" for d in filtered)
    assert all(d.metadata.get("scope_binding_strength") == "explicit" for d in filtered)
    assert all(d.metadata.get("scope_admitted") is True for d in filtered)


def test_evidence_gate_evaluate_rules():
    """测试 Evidence Guard 对广义 Provenance 与对齐证据的校验。"""
    scope = EvidenceScope(
        scope_id="test_gate_scope",
        root_entities=("PipelineWebGL",),
        binding_strength=BindingStrength.EXPLICIT,
        admissible_entities=frozenset({"PipelineWebGL", "ServiceA"}),
        excluded_rebindings=frozenset({"PipelineBuilder"}),
    )

    conv = ConversationContext(
        user_question="PipelineWebGL 怎么使用？",
        session=SessionState(chat_id="s1"),
        head_entity="PipelineWebGL",
        scope=scope,
    )

    # 1. 证据池包含合法实体
    pool = EvidencePool(question_id="q1")
    pool.add_retrieve([
        {
            "content": "PipelineWebGL 是渲染组件",
            "metadata": {
                "chunk_id": "c1",
                "document_entity": "PipelineWebGL",
                "scope_id": "test_gate_scope",
                "scope_admitted": True,
                "scope_admission_reason": "admissible_entity",
                "provenance_source_type": ProvenanceSourceType.DIRECT_ENTITY_CHUNK.value,
            },
        }
    ])
    verdict1 = evaluate_rules(conv, pool)
    assert verdict1["allow_knowledge_answer"] is True

    # 2. 证据池仅包含合法依赖实体
    pool2 = EvidencePool(question_id="q2")
    pool2.add_retrieve([
        {
            "content": "ServiceA 用于后台服务",
            "metadata": {
                "chunk_id": "c2",
                "document_entity": "ServiceA",
                "scope_id": "test_gate_scope",
                "scope_admitted": True,
                "scope_admission_reason": "admissible_entity",
                "provenance_source_type": ProvenanceSourceType.GRAPH_RELATION.value,
                "provenance_path": {"relation_type": "depends_on"},
            },
        }
    ])
    verdict2 = evaluate_rules(conv, pool2)
    assert verdict2["allow_knowledge_answer"] is True

    # 3. 证据池为空
    empty_pool = EvidencePool(question_id="q3")
    verdict3 = evaluate_rules(conv, empty_pool)
    assert verdict3["allow_knowledge_answer"] is False
    assert verdict3["reason"] == "empty_pool"

    # 4. filename/section 等 legacy heuristic 不能单独通过 locked Evidence Guard。
    legacy_pool = EvidencePool(question_id="q4")
    legacy_pool.add_retrieve([
        {
            "content": "PipelineWebGL legacy content",
            "metadata": {
                "chunk_id": "c4",
                "source": "PipelineWebGL用户手册.docx",
                "section_path": "PipelineWebGL > 配置",
            },
        }
    ])
    verdict4 = evaluate_rules(conv, legacy_pool)
    assert verdict4["allow_knowledge_answer"] is False
    assert verdict4["reason"] == "scope_provenance_failed"

    # 5. Evidence Guard 与 ScopeResolver 共用 relation policy，未知关系不能借 graph provenance 放行。
    invalid_relation_pool = EvidencePool(question_id="q5")
    invalid_relation_pool.add_retrieve([
        {
            "content": "ServiceA weak relation",
            "metadata": {
                "chunk_id": "c5",
                "document_entity": "ServiceA",
                "scope_id": "test_gate_scope",
                "scope_admitted": True,
                "scope_admission_reason": "admissible_entity",
                "provenance_source_type": ProvenanceSourceType.GRAPH_RELATION.value,
                "provenance_path": {"relation_type": "mentions"},
            },
        }
    ])
    verdict5 = evaluate_rules(conv, invalid_relation_pool)
    assert verdict5["allow_knowledge_answer"] is False
    assert verdict5["provenance_reason"] == "relation_not_scope_admissible"


def test_bm25_pre_topk_scope_filtering():
    """测试 BM25Store 在 Top-K 收集循环内的前置 Scope 结构准入过滤。"""
    from rag_knowledge.services.bm25_store import BM25Store

    store = BM25Store()
    docs = [
        Document(page_content="PipelineWebGL 渲染 引擎", metadata={"chunk_id": "c1", "document_entity": "PipelineWebGL", "review_status": "approved"}),
        Document(page_content="PipelineBuilder 构建 流水线 引擎", metadata={"chunk_id": "c2", "document_entity": "PipelineBuilder", "review_status": "approved"}),
        Document(page_content="PipelineWebGL 高级 渲染 设置 引擎", metadata={"chunk_id": "c3", "document_entity": "PipelineWebGL", "review_status": "approved"}),
    ]
    store.build_index_from_documents(docs)

    scope = EvidenceScope(
        scope_id="test_scope",
        root_entities=("PipelineWebGL",),
        binding_strength=BindingStrength.EXPLICIT,
        admissible_entities=frozenset({"PipelineWebGL"}),
        excluded_rebindings=frozenset({"PipelineBuilder"}),
    )

    hits = store.search("渲染", top_k=2, scope=scope)
    for h in hits:
        assert h.metadata.get("document_entity") != "PipelineBuilder"
    assert len(hits) == 2
    assert all(h.metadata.get("document_entity") == "PipelineWebGL" for h in hits)


def test_scope_resolver_records_secondary_entity_without_task_semantics():
    constraints = {
        "canonical_by_alias": {
            "pipelinewebgl": "PipelineWebGL",
            "servicea": "ServiceA",
        },
        "entity_type_by_name": {
            "PipelineWebGL": "Product",
            "ServiceA": "Service",
        },
        "relations": [],
    }
    subject = ScopeResolver.resolve_subject(
        "PipelineWebGL 与 ServiceA 配合使用",
        constraints=constraints,
    )
    assert subject.primary_entities == ("PipelineWebGL",)
    assert subject.referenced_entities == ("ServiceA",)


def test_scope_resolver_real_two_hop_expansion():
    constraints = {
        "canonical_by_alias": {
            "pipelinewebgl": "PipelineWebGL",
            "servicea": "ServiceA",
            "serviceb": "ServiceB",
        },
        "entity_type_by_name": {
            "PipelineWebGL": "Product",
            "ServiceA": "Service",
            "ServiceB": "Service",
        },
        "relations": [
            {"source": "PipelineWebGL", "relation_type": "depends_on", "target": "ServiceA"},
            {"source": "ServiceA", "relation_type": "depends_on", "target": "ServiceB"},
        ],
    }
    scope = ScopeResolver.resolve(
        "PipelineWebGL 依赖链是什么？",
        entity_name="PipelineWebGL",
        constraints=constraints,
        policy=ScopePolicy(max_hops=2, max_admissible_entities=8),
    )
    assert "ServiceB" in scope.admissible_entities
    assert any(p.target_entity == "ServiceB" and p.hops == 2 for p in scope.provenance_paths)


def test_vector_filter_contains_locked_scope_before_topk():
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    strategy = object.__new__(RetrievalStrategy)
    strategy._cfg = SimpleNamespace(
        retrieval_top_k=4,
        retrieval_fetch_k=20,
        retrieval_lambda_mult=0.5,
    )
    chroma = MagicMock()
    chroma.as_retriever.return_value.invoke.return_value = []
    strategy._store = MagicMock()
    strategy._store.get_chroma.return_value = chroma
    scope = EvidenceScope(
        scope_id="vector_scope",
        root_entities=("PipelineWebGL",),
        binding_strength=BindingStrength.EXPLICIT,
        admissible_entities=frozenset({"PipelineWebGL", "ServiceA"}),
    )

    strategy._retrieve_vector(
        "渲染设置",
        kb_name="文章附件",
        doc_category=None,
        review_status="approved",
        search_type="similarity",
        top_k=2,
        scope=scope,
    )

    filt = chroma.as_retriever.call_args.kwargs["search_kwargs"]["filter"]
    assert filt == {
        "$and": [
            {"kb_name": "文章附件"},
            {"review_status": "approved"},
            {"document_entity": {"$in": ["PipelineWebGL", "ServiceA"]}},
        ]
    }
    assert chroma.as_retriever.call_args.kwargs["search_kwargs"]["k"] == 2


def test_explicit_selection_does_not_expand_legacy_scope_from_task_wording():
    """V1.6 下旧 Scope 只锁主实体，不再因“区别”等任务措辞扩大全局白名单。"""
    constraints = {
        "canonical_by_alias": {
            "webgl": "PipelineWebGL",
            "pipelinewebgl": "PipelineWebGL",
            "builder": "PipelineBuilder",
            "pipelinebuilder": "PipelineBuilder",
        },
        "entity_type_by_name": {
            "PipelineWebGL": "Product",
            "PipelineBuilder": "Product",
        },
        "different_from": {frozenset({"PipelineWebGL", "PipelineBuilder"})},
        "relations": [
            {"source": "PipelineWebGL", "relation_type": "different_from", "target": "PipelineBuilder"},
        ],
    }

    scope = ScopeResolver.resolve(
        "PipelineWebGL 和 PipelineBuilder 有什么区别？",
        entity_name="PipelineWebGL",
        constraints=constraints,
    )
    assert scope.is_identity_locked is True
    assert scope.primary_root == "PipelineWebGL"
    # 第二实体由 Agent Step 的 ExplorationGrant 授权，旧全局 Scope 不再自动放行。
    assert "PipelineBuilder" not in scope.admissible_entities


def test_query_cache_scope_fingerprint_isolation():
    """测试 QueryCache 基于 scope fingerprint 的缓存隔离机制。"""
    from rag_knowledge.services.query_cache import QueryCache

    scope1 = EvidenceScope(
        scope_id="s1",
        root_entities=("PipelineWebGL",),
        binding_strength=BindingStrength.EXPLICIT,
        admissible_entities=frozenset({"PipelineWebGL"}),
    )
    scope2 = EvidenceScope(
        scope_id="s2",
        root_entities=("PipelineWebGL",),
        binding_strength=BindingStrength.EXPLICIT,
        admissible_entities=frozenset({"PipelineWebGL", "ServiceA"}),
    )

    k1 = QueryCache.make_key(
        rewritten_query="test query",
        kb_name="kb",
        doc_category=None,
        review_status="approved",
        method="hybrid",
        rerank=True,
        web_search=False,
        scope_fingerprint=scope1.fingerprint,
    )
    k2 = QueryCache.make_key(
        rewritten_query="test query",
        kb_name="kb",
        doc_category=None,
        review_status="approved",
        method="hybrid",
        rerank=True,
        web_search=False,
        scope_fingerprint=scope2.fingerprint,
    )
    assert k1 != k2

    materialized_a = EvidenceScope(
        scope_id="s3",
        root_entities=("PipelineWebGL",),
        binding_strength=BindingStrength.EXPLICIT,
        admissible_entities=frozenset({"PipelineWebGL"}),
        materialized_chunk_ids=frozenset({"chunk-a"}),
    )
    materialized_b = EvidenceScope(
        scope_id="s4",
        root_entities=("PipelineWebGL",),
        binding_strength=BindingStrength.EXPLICIT,
        admissible_entities=frozenset({"PipelineWebGL"}),
        materialized_chunk_ids=frozenset({"chunk-b"}),
    )
    assert materialized_a.fingerprint != materialized_b.fingerprint


def test_qa_trace_scope_persistence():
    """测试 QA Trace 中对 EvidenceScope 的序列化记录。"""
    from rag_knowledge.services.qa_trace import serialize_scope

    scope = EvidenceScope(
        scope_id="s123",
        root_entities=("PipelineWebGL",),
        binding_strength=BindingStrength.EXPLICIT,
        admissible_entities=frozenset({"PipelineWebGL", "ServiceA"}),
        excluded_rebindings=frozenset({"PipelineBuilder"}),
        doc_category="StampTools",
    )
    serialized = serialize_scope(scope)
    assert serialized["scope_id"] == "s123"
    assert "PipelineWebGL" in serialized["root_entities"]
    assert "PipelineBuilder" in serialized["excluded_rebindings"]
    assert serialized["binding_strength"] == "explicit"

