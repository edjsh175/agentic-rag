import pytest
from langchain_core.documents import Document

from rag_knowledge.config import Config
from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.evidence_scope import BindingStrength, EvidenceScope
from rag_knowledge.services.graph_retrieval import EntityLinker, GraphContext, GraphExpander, GraphRetriever
from rag_knowledge.services.retrieval_strategy import RetrievalStrategy


@pytest.fixture
def graph_db(isolated_storage):
    isolated_storage(db_name="graph-retrieval.db")
    db = RelationalDB()

    pipeline = db.create_entity("PipelineBuilder", "Tool", doc_category="StampTools")
    product = db.create_entity("StampTools", "Product", doc_category="StampTools")
    service = db.create_entity("\u7ba1\u7ebf\u53d1\u5e03\u670d\u52a1", "Service", doc_category="StampServer")
    config = db.create_entity("PipelinePublishConfig", "ConfigItem", doc_category="StampServer")
    procedure = db.create_entity("PipelineBuilder \u4f7f\u7528\u6d41\u7a0b", "Procedure", doc_category="StampTools")
    step = db.create_entity("\u5de5\u7a0b\u8bbe\u7f6e", "Step", doc_category="StampTools")

    db.create_alias(pipeline, "\u7ba1\u7ebf\u53d1\u5e03\u5de5\u5177", review_status="approved")
    db.create_relation(pipeline, product, "belongs_to", review_status="approved")
    db.create_relation(pipeline, service, "different_from", review_status="approved")
    db.create_relation(pipeline, config, "different_from", review_status="approved")
    db.create_relation(service, config, "uses_config", review_status="approved")
    db.create_relation(pipeline, procedure, "requires", review_status="approved")
    db.create_relation(procedure, step, "has_step", review_status="approved")
    db.create_link(pipeline, "chunk-pipeline", evidence_text="PipelineBuilder")
    db.create_link(step, "chunk-engineering", evidence_text="\u5de5\u7a0b\u8bbe\u7f6e")
    return db


def test_entity_linker_resolves_alias_and_collects_explicit_exclusions(graph_db):
    linked = EntityLinker(graph_db).link("\u7ba1\u7ebf\u53d1\u5e03\u5de5\u5177\u5982\u4f55\u4f7f\u7528\uff1f", "procedure")

    assert len(linked) == 1
    assert linked[0].canonical_name == "PipelineBuilder"
    assert linked[0].entity_type == "Tool"
    assert linked[0].match_method == "alias_exact"
    excluded = {graph_db.get_entity(entity_id)["name"] for entity_id in linked[0].excluded_entity_ids}
    assert excluded == {"\u7ba1\u7ebf\u53d1\u5e03\u670d\u52a1", "PipelinePublishConfig"}


def test_entity_linker_returns_empty_for_ambiguous_same_priority_matches(graph_db, monkeypatch):
    mock_entities = [
        {
            "id": "ent-1",
            "name": "PipelineBuilder",
            "canonical_name": "\u7ba1\u7ebf\u53d1\u5e03\u5de5\u5177",
            "entity_type": "Tool",
            "review_status": "approved",
            "doc_category": "StampTools"
        },
        {
            "id": "ent-2",
            "name": "PipelinePublisher",
            "canonical_name": "\u7ba1\u7ebf\u53d1\u5e03\u5de5\u5177",
            "entity_type": "Tool",
            "review_status": "approved",
            "doc_category": "StampTools"
        }
    ]
    monkeypatch.setattr(graph_db, "list_entities", lambda *args, **kwargs: mock_entities)
    monkeypatch.setattr(graph_db, "list_aliases", lambda *args, **kwargs: [])

    assert EntityLinker(graph_db).link("\u7ba1\u7ebf\u53d1\u5e03\u5de5\u5177\u5982\u4f55\u4f7f\u7528\uff1f", "procedure") == ()


def test_graph_expander_uses_two_hops_for_procedure_and_collects_evidence(graph_db):
    linked = EntityLinker(graph_db).link("\u7ba1\u7ebf\u53d1\u5e03\u5de5\u5177\u5982\u4f55\u4f7f\u7528\uff1f", "procedure")
    context = GraphExpander(graph_db).expand(linked, "procedure")

    names = {graph_db.get_entity(entity_id)["name"] for entity_id in context.expanded_entity_ids}
    assert {"PipelineBuilder", "PipelineBuilder \u4f7f\u7528\u6d41\u7a0b", "\u5de5\u7a0b\u8bbe\u7f6e"} <= names
    assert context.chunk_ids == ("chunk-pipeline", "chunk-engineering")
    assert "PipelineBuilder" in context.retrieval_queries
    assert "\u5de5\u7a0b\u8bbe\u7f6e" in context.retrieval_queries
    assert context.fallback_reason is None


def test_graph_expander_returns_fallback_for_empty_links(graph_db):
    context = GraphExpander(graph_db).expand((), "definition")

    assert context.chunk_ids == ()
    assert context.fallback_reason == "no_linked_entity"


def test_config_question_links_service_and_expands_its_config(graph_db):
    linked = EntityLinker(graph_db).link("管线发布服务如何配置？", "config")
    context = GraphExpander(graph_db).expand(linked, "config")

    assert linked[0].canonical_name == "管线发布服务"
    names = {graph_db.get_entity(entity_id)["name"] for entity_id in context.expanded_entity_ids}
    assert "PipelinePublishConfig" in names


def test_graph_retriever_locked_scope_bypasses_query_rebinding(graph_db, monkeypatch):
    retriever = GraphRetriever(graph_db, store=object(), chunk_index_lookup=object())
    scope = EvidenceScope(
        scope_id="locked-pipeline",
        root_entities=("PipelineBuilder",),
        binding_strength=BindingStrength.EXPLICIT,
        admissible_entities=frozenset({"PipelineBuilder"}),
    )
    captured = {}

    def fail_link_queries(*args, **kwargs):
        pytest.fail("locked scope must not run lexical link_queries")

    def capture_expand(linked, intent, question=None):
        captured["linked"] = linked
        return GraphContext(linked_entities=tuple(linked), fallback_reason="no_evidence")

    monkeypatch.setattr(retriever.linker, "link_queries", fail_link_queries)
    monkeypatch.setattr(retriever.expander, "expand", capture_expand)

    context, docs = retriever.retrieve(
        "管线发布服务怎么配置？",
        "definition",
        scope=scope,
    )

    assert docs == []
    assert context.fallback_reason == "no_evidence"
    assert [item.canonical_name for item in captured["linked"]] == ["PipelineBuilder"]
    assert captured["linked"][0].match_method == "scope_root"


def test_locked_graph_chunk_uses_entity_chunk_link_as_formal_scope_provenance(graph_db):
    class Collection:
        def get(self, ids, include):
            assert "chunk-pipeline" in ids
            return {
                "ids": ["chunk-pipeline"],
                "documents": ["PipelineBuilder 内容"],
                "metadatas": [{"review_status": "approved"}],
            }

    store = type("Store", (), {"get_chroma": lambda self: type("Chroma", (), {"_collection": Collection()})()})()
    retriever = GraphRetriever(graph_db, store=store)
    scope = EvidenceScope(
        scope_id="locked-pipeline",
        root_entities=("PipelineBuilder",),
        binding_strength=BindingStrength.EXPLICIT,
        admissible_entities=frozenset({"PipelineBuilder"}),
    )

    _context, docs = retriever.retrieve(
        "管线发布服务怎么配置？",
        "definition",
        scope=scope,
    )

    assert len(docs) == 1
    assert docs[0].metadata["scope_entity"] == "PipelineBuilder"
    assert docs[0].metadata["graph_provenance_link_id"]
    admitted = RetrievalStrategy._filter_by_scope(docs, scope)
    assert len(admitted) == 1
    assert admitted[0].metadata["scope_admitted"] is True


def test_graph_retriever_fetches_evidence_documents_and_marks_channel(graph_db):
    class Collection:
        def get(self, ids, include):
            assert ids == ["chunk-pipeline", "chunk-engineering"]
            return {
                "ids": ids,
                "documents": ["PipelineBuilder 内容", "工程设置内容"],
                "metadatas": [{"chunk_id": ids[0]}, {"chunk_id": ids[1]}],
            }

    store = type("Store", (), {"get_chroma": lambda self: type("Chroma", (), {"_collection": Collection()})()})()
    retriever = GraphRetriever(graph_db, store=store)

    context, docs = retriever.retrieve("管线发布工具如何使用？", "procedure")

    assert context.fallback_reason is None
    assert [doc.metadata["chunk_id"] for doc in docs] == ["chunk-pipeline", "chunk-engineering"]
    assert all(doc.metadata["retrieval_channel"] == "graph" for doc in docs)


def test_graph_retriever_fuses_graph_channel_with_weight_and_deduplication(graph_db):
    standard = [
        Document(page_content="wrong", metadata={"chunk_id": "wrong"}),
        Document(page_content="pipeline", metadata={"chunk_id": "chunk-pipeline"}),
    ]
    graph = [Document(page_content="pipeline", metadata={"chunk_id": "chunk-pipeline"})]

    fused = GraphRetriever.fuse(standard, graph, top_k=2, graph_weight=1.25)

    # Text top1 is protected; dual-channel pipeline still fills remaining seats.
    assert [doc.metadata["chunk_id"] for doc in fused] == ["wrong", "chunk-pipeline"]
    assert fused[1].metadata["matched_query_kinds"] == ["retrieval", "graph"]


def test_fuse_caps_graph_only_slots_and_protects_text_top1(graph_db):
    standard = [
        Document(page_content="text-top", metadata={"chunk_id": "text-1"}),
        Document(page_content="text-2", metadata={"chunk_id": "text-2"}),
    ]
    graph = [
        Document(page_content="g1", metadata={"chunk_id": "graph-1"}),
        Document(page_content="g2", metadata={"chunk_id": "graph-2"}),
        Document(page_content="g3", metadata={"chunk_id": "graph-3"}),
    ]

    fused = GraphRetriever.fuse(standard, graph, top_k=4, graph_weight=5.0)

    ids = [doc.metadata["chunk_id"] for doc in fused]
    assert ids[0] == "text-1"
    assert "text-2" in ids
    graph_only = [cid for cid in ids if cid.startswith("graph-")]
    assert len(graph_only) == 1


def test_fuse_graph_only_cap_can_be_disabled(graph_db):
    standard = [Document(page_content="text-top", metadata={"chunk_id": "text-1"})]
    graph = [
        Document(page_content="g1", metadata={"chunk_id": "graph-1"}),
        Document(page_content="g2", metadata={"chunk_id": "graph-2"}),
    ]

    fused = GraphRetriever.fuse(
        standard,
        graph,
        top_k=3,
        graph_weight=5.0,
        max_graph_only_slots=2,
        protect_text_top1=True,
    )
    ids = [doc.metadata["chunk_id"] for doc in fused]
    assert ids[0] == "text-1"
    assert len([cid for cid in ids if cid.startswith("graph-")]) == 2


def test_graph_retriever_soft_downweights_excluded_chunks(graph_db):
    docs = [
        Document(page_content="service", metadata={"chunk_id": "service-config"}),
        Document(page_content="tool", metadata={"chunk_id": "tool-guide"}),
    ]

    fused = GraphRetriever.fuse(
        docs,
        [],
        top_k=2,
        excluded_chunk_ids=("service-config",),
    )

    assert [doc.metadata["chunk_id"] for doc in fused] == ["tool-guide", "service-config"]


def test_graph_retriever_falls_back_when_database_query_fails(graph_db, monkeypatch):
    monkeypatch.setattr(graph_db, "list_entities", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("db down")))
    retriever = GraphRetriever(graph_db, store=object())

    context, docs = retriever.retrieve("管线发布工具如何使用？", "procedure")

    assert docs == []
    assert context.fallback_reason == "graph_query_failed"


def test_graph_evidence_pointing_to_rejected_chunk_is_filtered(graph_db):
    class Collection:
        def get(self, ids, include):
            return {
                "ids": ids,
                "documents": ["PipelineBuilder 内容"],
                "metadatas": [{"chunk_id": ids[0], "review_status": "rejected"}],
            }

    store = type("Store", (), {"get_chroma": lambda self: type("Chroma", (), {"_collection": Collection()})()})()
    retriever = GraphRetriever(graph_db, store=store)

    context, docs = retriever.retrieve("管线发布工具如何使用？", "procedure", review_status="approved")

    assert docs == []
    assert context.fallback_reason == "graph_evidence_filtered"


def test_graph_evidence_with_mismatched_doc_category_is_filtered(graph_db):
    class Collection:
        def get(self, ids, include):
            return {
                "ids": ids,
                "documents": ["PipelineBuilder 内容"],
                "metadatas": [{"chunk_id": ids[0], "doc_category": "StampServer"}],
            }

    store = type("Store", (), {"get_chroma": lambda self: type("Chroma", (), {"_collection": Collection()})()})()
    retriever = GraphRetriever(graph_db, store=store)

    context, docs = retriever.retrieve("管线发布工具如何使用？", "procedure", doc_category="StampTools")

    assert docs == []
    assert context.fallback_reason == "graph_evidence_filtered"


def test_graph_evidence_with_kb_name_filtering(graph_db, monkeypatch):
    class Collection:
        def get(self, ids, include):
            return {
                "ids": ids,
                "documents": ["PipelineBuilder 内容"],
                "metadatas": [{"chunk_id": ids[0]}],
            }

    store = type("Store", (), {"get_chroma": lambda self: type("Chroma", (), {"_collection": Collection()})()})()

    class LookupStub:
        def by_chunk_id(self, chunk_id: str) -> dict:
            if chunk_id == "chunk-pipeline":
                return {"kb_name": "文章附件"}
            return {}

    retriever = GraphRetriever(graph_db, store=store, chunk_index_lookup=LookupStub())

    # With correct kb_name
    context, docs = retriever.retrieve("管线发布工具如何使用？", "procedure", kb_name="文章附件")
    assert len(docs) == 1

    # With mismatched kb_name
    context, docs = retriever.retrieve("管线发布工具如何使用？", "procedure", kb_name="other_kb")
    assert docs == []
    assert context.fallback_reason == "graph_evidence_filtered"


def test_graph_evidence_prefers_chroma_kb_name_over_index(graph_db):
    class Collection:
        def get(self, ids, include):
            return {
                "ids": ids,
                "documents": ["PipelineBuilder 内容"],
                "metadatas": [{"chunk_id": ids[0], "kb_name": "文章附件"}],
            }

    store = type("Store", (), {"get_chroma": lambda self: type("Chroma", (), {"_collection": Collection()})()})()

    class LookupStub:
        def by_chunk_id(self, chunk_id: str) -> dict:
            return {"kb_name": "other_kb"}

    retriever = GraphRetriever(graph_db, store=store, chunk_index_lookup=LookupStub())
    _, docs = retriever.retrieve("管线发布工具如何使用？", "procedure", kb_name="文章附件")
    assert len(docs) == 1


def test_followup_question_links_via_contextualized_query(graph_db):
    from rag_knowledge.services.query_contextualizer import RetrievalQuery
    queries = [
        RetrievalQuery("它怎么配置？", "original", 1.0),
        RetrievalQuery("PipelineBuilder怎么配置？", "standalone", 0.8)
    ]
    linked = EntityLinker(graph_db).link_queries(queries, "config")
    assert len(linked) == 1
    assert linked[0].canonical_name == "PipelineBuilder"


def test_explicit_tier_uses_original_question_string_matching(graph_db):
    from rag_knowledge.services.query_contextualizer import RetrievalQuery

    queries = [
        RetrievalQuery("  PipelineBuilder   怎么配置？  ", "standalone", 0.8),
    ]

    linked = EntityLinker(graph_db).link_queries(
        queries,
        "config",
        original_question="pipelinebuilder怎么配置？",
    )

    assert len(linked) == 1
    assert linked[0].canonical_name == "PipelineBuilder"
    assert linked[0].confidence >= 0.98


def test_flexible_whitespace_links_https_config(graph_db):
    """题面「HTTPS 配置」应命中实体「HTTPS配置」。"""
    graph_db.create_entity("HTTPS配置", "Procedure", doc_category="StampServer")
    graph_db.create_entity("私有CA配置", "Procedure", doc_category="StampServer")
    linked = EntityLinker(graph_db).link(
        "HTTPS 配置与私有 CA 配置之间怎么衔接？",
        "procedure",
    )
    names = {item.canonical_name for item in linked}
    assert "HTTPS配置" in names
    assert "私有CA配置" in names


def test_leaf_error_preferred_over_backbone_rewrite_product(graph_db):
    """原题 Exact 叶子优先于 rewrite query 里的宽 Tool；排错题丢掉非原题宽实体。"""
    from rag_knowledge.services.query_contextualizer import RetrievalQuery

    graph_db.create_entity("UV展开错误", "Error", doc_category="StampTools")
    queries = [
        RetrievalQuery("UV 排查", "planner_stage", 0.5),
        RetrievalQuery("PipelineBuilder 错误排查", "graph_rewrite", 0.9),
    ]
    linked = EntityLinker(graph_db).link_queries(
        queries,
        "troubleshooting",
        original_question="出现 UV展开错误时应如何排查？",
    )
    names = [item.canonical_name for item in linked]
    assert names[0] == "UV展开错误"
    assert "UV展开错误" in names
    assert "PipelineBuilder" not in names


def test_product_links_ranked_by_question_tokens(graph_db):
    """Product 多链接时，按问题词（部署）优先排序 evidence。"""
    product = graph_db.create_entity("StampServer", "Product", doc_category="StampServer")
    graph_db.create_link(product, "chk-os", evidence_text="操作系统安装")
    graph_db.create_link(product, "chk-deploy", evidence_text="应用系统部署 > WebRTC部署")
    graph_db.create_link(product, "chk-ssh", evidence_text="SSH设置")
    linked = EntityLinker(graph_db).link(
        "StampServer 产品概述里覆盖哪些部署与运维能力？",
        "definition",
    )
    ctx = GraphExpander(graph_db).expand(
        linked,
        "definition",
        question="StampServer 产品概述里覆盖哪些部署与运维能力？",
    )
    assert ctx.chunk_ids
    assert ctx.chunk_ids[0] == "chk-deploy"


def test_multi_entity_links_product_and_environment(graph_db):
    """非 comparison 也允许多实体：Product + EnvironmentComponent。"""
    graph_db.create_entity("StampWebRTC", "Product", doc_category="StampWebRTC")
    graph_db.create_entity("Win11", "EnvironmentComponent", doc_category="StampWebRTC")
    graph_db.create_entity("Edge", "EnvironmentComponent", doc_category="StampWebRTC")
    linked = EntityLinker(graph_db).link(
        "StampWebRTC 对 Win11 与 Edge 浏览器环境有什么要求？",
        "dependency",
    )
    names = {item.canonical_name for item in linked}
    assert {"Win11", "Edge", "StampWebRTC"} <= names
    assert len(linked) == 3


def test_command_stem_links_run_local(graph_db):
    """题面 run_local 应命中 Command 实体 run_local_1.bat。"""
    graph_db.create_entity("StampWebRTC", "Product", doc_category="StampWebRTC")
    graph_db.create_entity("run_local_1.bat", "Command", doc_category="StampWebRTC")
    linked = EntityLinker(graph_db).link(
        "StampWebRTC 本地启动常用哪些 run_local 脚本？",
        "procedure",
    )
    names = {item.canonical_name for item in linked}
    assert "run_local_1.bat" in names


def test_command_stem_keeps_one_when_numbered_scripts_tie(graph_db):
    """run_local_1/2 同词干同 span 时保留其一，而不是全部丢弃。"""
    graph_db.create_entity("StampWebRTC", "Product", doc_category="StampWebRTC")
    graph_db.create_entity("run_local_1.bat", "Command", doc_category="StampWebRTC")
    graph_db.create_entity("run_local_2.bat", "Command", doc_category="StampWebRTC")
    linked = EntityLinker(graph_db).link(
        "StampWebRTC 本地启动常用哪些 run_local 脚本？",
        "procedure",
    )
    names = {item.canonical_name for item in linked}
    assert names & {"run_local_1.bat", "run_local_2.bat"}
    assert "StampWebRTC" in names


def test_revision_token_changes_when_aliases_or_links_change(graph_db):
    retriever = GraphRetriever(graph_db, store=object())
    r1 = retriever.revision()
    
    # 增加别名
    graph_db.create_alias(graph_db.get_entity_by_name("PipelineBuilder")["id"], "new_alias", review_status="approved")
    r2 = retriever.revision()
    assert r1 != r2
    
    # 增加证据 chunk 链接
    graph_db.create_link(graph_db.get_entity_by_name("PipelineBuilder")["id"], "new_chunk")
    r3 = retriever.revision()
    assert r2 != r3


def test_full_chain_pipeline_integration(isolated_storage, monkeypatch):
    # 1. Isolate database
    from rag_knowledge.config import Config
    from rag_knowledge.services.graph_extraction import GraphBuilder, GraphCandidateApplier
    from rag_knowledge.services.rag import RagChain
    
    cfg, _, _, _ = isolated_storage(db_name="integration-full.db")
    monkeypatch.setattr(cfg.graph_retrieval, "enabled", True)

    db = RelationalDB()
    
    # 2. Add raw chunks to mock source
    from tests.test_graph_extraction import chunk
    chunks = [
        chunk(
            chunk_id="c-tool",
            content="PipelineBuilder is a tool that belongs_to StampTools.",
            source="manual.docx",
            doc_category="StampTools",
            section_path="PipelineBuilder > 说明",
            content_type="text"
        ),
        chunk(
            chunk_id="c-config",
            content="PipelinePublishConfig /opt/config/pipeline.xml",
            source="server.docx",
            doc_category="StampServer",
            section_path="PipelineBuilder > 服务配置",
            content_type="code"
        )
    ]
    
    # Run Phase B extraction
    batch = GraphBuilder(db=db, chunk_source=lambda: chunks).build_full()
    
    # Approve and apply extraction candidates to relational graph
    ids = [item["id"] for item in db.list_extraction_candidates(batch.batch_id)]
    db.review_extraction_candidates(batch.batch_id, ids, "approved")
    db.set_extraction_batch_status(batch.batch_id, "approved")
    GraphCandidateApplier(db).apply(batch.batch_id)
    
    # Verify entities exist
    tool_ent = db.get_entity_by_name("PipelineBuilder")
    config_ent = db.get_entity_by_name("PipelinePublishConfig")
    assert tool_ent and config_ent
    assert tool_ent["entity_type"] == "Tool"
    assert config_ent["entity_type"] == "ConfigItem"
    db.create_relation(
        tool_ent["id"],
        config_ent["id"],
        "different_from",
        created_by="test:disambiguation",
    )
    
    # 3. Setup mock vector store
    class MockChromaCollection:
        def get(self, ids, include):
            ret_ids = []
            ret_docs = []
            ret_metas = []
            for cid in ids:
                for c in chunks:
                    if c["chunk_id"] == cid:
                        ret_ids.append(c["chunk_id"])
                        ret_docs.append(c["content"])
                        ret_metas.append({
                            "chunk_id": c["chunk_id"],
                            "source": c["metadata"]["source"],
                            "doc_category": c["metadata"]["doc_category"],
                            "review_status": "approved"
                        })
            return {
                "ids": ret_ids,
                "documents": ret_docs,
                "metadatas": ret_metas
            }
            
    class MockChroma:
        def __init__(self):
            self._collection = MockChromaCollection()
            
        def get(self, *args, **kwargs):
            return {"ids": [], "documents": [], "metadatas": []}
            
        def as_retriever(self, *args, **kwargs):
            class MockRetriever:
                def invoke(self, query):
                    return [Document(
                        page_content="PipelineBuilder is a tool that belongs_to StampTools.",
                        metadata={
                            "chunk_id": "c-tool",
                            "source": "manual.docx",
                            "doc_category": "StampTools",
                            "review_status": "approved"
                        }
                    )]
                def get_relevant_documents(self, query):
                    return self.invoke(query)
            return MockRetriever()
            
    from rag_knowledge.repository.vector_store import VectorStore
    mock_vs = type("Store", (), {
        "get_chroma": lambda self: MockChroma()
    })()
    monkeypatch.setattr(VectorStore, "_instance", mock_vs)
    
    # 4. Run RAG query and assert fused config chunk is retrieved
    chain = RagChain()
    
    class MockLLM:
        def invoke(self, messages, *args, **kwargs):
            from langchain_core.messages import AIMessage
            return AIMessage(content="Mock Answer referencing PipelinePublishConfig [1] [2]")
            
    monkeypatch.setattr(chain, "_build_llm", lambda model=None: MockLLM())
    
    # Mock query planner to return deterministic config retrieval plan
    from rag_knowledge.services.query_contextualizer import RetrievalQuery
    from rag_knowledge.services.query_planner import RetrievalPlan
    mock_plan = RetrievalPlan(
        intent="config",
        queries=[RetrievalQuery("PipelineBuilder配置在哪？", "standalone", 1.0)],
        top_k=5,
        candidate_k=15,
        enable_rerank=True,
        expand_neighbors=False,
        confidence=1.0
    )
    monkeypatch.setattr(chain._query_planner, "plan", lambda *args, **kwargs: mock_plan)
    
    resp = chain.query("PipelineBuilder配置在哪？")
    retrieved_ids = {doc.get("metadata", {}).get("chunk_id") for doc in resp["source_documents"]}
    assert "c-tool" in retrieved_ids
    assert "c-config" not in retrieved_ids


def test_belongs_to_product_expansion_limit(graph_db):
    # 1. Create a section under StampTools (Product)
    product = graph_db.get_entity_by_name("StampTools")
    product_section = graph_db.create_entity("StampTools 运行环境", "Section", doc_category="StampTools")
    graph_db.create_relation(product["id"], product_section, "defined_in", review_status="approved")
    graph_db.create_link(product_section, "chunk-product-env", evidence_text="运行环境")

    # 2. Run linker & expander
    linker = EntityLinker(graph_db)
    linked = linker.link("管线发布工具如何使用？", "procedure")
    assert len(linked) == 1
    assert linked[0].canonical_name == "PipelineBuilder"

    expander = GraphExpander(graph_db)
    context = expander.expand(linked, "procedure")

    # 3. Assert that StampTools is visited, but product_section is not (due to child -> Product expansion limit)
    assert product["id"] in context.expanded_entity_ids
    assert product_section not in context.expanded_entity_ids
    assert "chunk-product-env" not in context.chunk_ids


def test_fuse_strict_exclusion_on_regression(graph_db):
    # 1. Create a link for the excluded entity "管线发布服务"
    service = graph_db.get_entity_by_name("管线发布服务")
    graph_db.create_link(service["id"], "chunk-service-deploy", evidence_text="部署管线发布服务")

    # 2. Retrieve with a question that does NOT mention "管线发布服务"
    class Collection:
        def get(self, ids, include):
            return {
                "ids": ids,
                "documents": ["PipelineBuilder 内容", "Service 内容"],
                "metadatas": [{"chunk_id": "chunk-pipeline", "doc_category": "StampTools"}, {"chunk_id": "chunk-service-deploy", "doc_category": "StampServer"}],
            }
    mock_store = type("Store", (), {"get_chroma": lambda self: type("Chroma", (), {"_collection": Collection()})()})()
    retriever = GraphRetriever(graph_db, store=mock_store)
    context, _ = retriever.retrieve("管线发布工具如何使用？", "procedure")

    assert context.guard is not None
    assert context.guard.strict_exclusion is True
    assert context.guard.question_mentions_excluded is False
    assert "chunk-service-deploy" in context.guard.excluded_chunk_ids

    # 3. Call fuse and verify strict exclusion skips the chunk
    standard = [
        Document(page_content="PipelineBuilder data settings", metadata={"chunk_id": "chunk-pipeline"}),
        Document(page_content="Service deployment config", metadata={"chunk_id": "chunk-service-deploy"}),
    ]
    fused = GraphRetriever.fuse(standard, [], top_k=2, graph_guard=context.guard)
    assert [doc.metadata["chunk_id"] for doc in fused] == ["chunk-pipeline"]


def test_fuse_no_strict_exclusion_on_comparison(graph_db):
    # 1. Create a link for the excluded entity "管线发布服务"
    service = graph_db.get_entity_by_name("管线发布服务")
    graph_db.create_link(service["id"], "chunk-service-deploy", evidence_text="部署管线发布服务")

    # 2. Retrieve with a comparison question or question mentioning the excluded name
    class Collection:
        def get(self, ids, include):
            return {
                "ids": ids,
                "documents": ["PipelineBuilder 内容", "Service 内容"],
                "metadatas": [{"chunk_id": "chunk-pipeline", "doc_category": "StampTools"}, {"chunk_id": "chunk-service-deploy", "doc_category": "StampServer"}],
            }
    mock_store = type("Store", (), {"get_chroma": lambda self: type("Chroma", (), {"_collection": Collection()})()})()
    retriever = GraphRetriever(graph_db, store=mock_store)
    context, _ = retriever.retrieve("管线发布工具和管线发布服务有什么区别？", "comparison")

    assert context.guard is not None
    assert context.guard.strict_exclusion is False
    assert context.guard.question_mentions_excluded is True

    # 3. Call fuse and verify the chunk is NOT skipped
    standard = [
        Document(page_content="PipelineBuilder data settings", metadata={"chunk_id": "chunk-pipeline"}),
        Document(page_content="Service deployment config", metadata={"chunk_id": "chunk-service-deploy"}),
    ]
    fused = GraphRetriever.fuse(standard, [], top_k=2, graph_guard=context.guard)
    assert "chunk-service-deploy" in [doc.metadata["chunk_id"] for doc in fused]


def test_product_own_links_excluded_from_graph_chunks(graph_db):
    product = graph_db.get_entity_by_name("StampTools")
    graph_db.create_link(product["id"], "chunk-product-overview", evidence_text="工具概述")

    linker = EntityLinker(graph_db)
    linked = linker.link("管线发布工具如何使用？", "procedure")
    assert len(linked) == 1
    assert linked[0].canonical_name == "PipelineBuilder"

    expander = GraphExpander(graph_db)
    context = expander.expand(linked, "procedure")

    assert product["id"] in context.expanded_entity_ids
    assert "chunk-product-overview" not in context.chunk_ids
    assert "StampTools" not in context.retrieval_queries


def test_product_link_ranking_prefers_service_overview_evidence():
    links = [
        {"chunk_id": "c-os", "evidence_text": "操作系统安装 > 创建虚拟机"},
        {"chunk_id": "c-svc", "evidence_text": "Stamp服务部署 > 管线查询服务"},
        {"chunk_id": "c-ops", "evidence_text": "Stamp服务部署 > Nginx代理设置 > 运维代理设置"},
    ]
    tokens = GraphExpander._question_rank_tokens(
        "StampServer 产品概述里覆盖哪些部署与运维能力？"
    )
    ranked = GraphExpander._rank_links_by_question(links, tokens)
    assert ranked[0]["chunk_id"] in {"c-svc", "c-ops"}
    assert ranked[-1]["chunk_id"] == "c-os"


def test_rag_chain_fuse_graph_docs_skips_when_graph_docs_missing():
    from langchain_core.documents import Document

    from rag_knowledge.services.rag import RagChain

    docs = [Document(page_content="pipeline", metadata={"chunk_id": "pipeline"})]
    chain = RagChain.__new__(RagChain)
    fused = chain._fuse_graph_docs(
        docs,
        None,
        top_k=4,
        graph_weight=1.25,
        excluded_chunk_ids=(),
        graph_guard=None,
    )
    assert fused is docs
