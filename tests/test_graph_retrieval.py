import pytest
from langchain_core.documents import Document

from rag_knowledge.config import Config
from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.graph_retrieval import EntityLinker, GraphExpander, GraphRetriever


@pytest.fixture
def graph_db(isolated_storage):
    isolated_storage(db_name="graph-retrieval.db")
    db = RelationalDB()

    pipeline = db.create_entity("PipelineBuilder", "Tool", doc_category="StampTools")
    product = db.create_entity("StampTools", "Product", doc_category="StampTools")
    service = db.create_entity("管线发布服务", "Service", doc_category="StampServer")
    config = db.create_entity("PipelinePublishConfig", "ConfigItem", doc_category="StampServer")
    procedure = db.create_entity("PipelineBuilder 使用流程", "Procedure", doc_category="StampTools")
    step = db.create_entity("工程设置", "Step", doc_category="StampTools")

    db.create_alias(pipeline, "管线发布工具", review_status="approved")
    db.create_relation(pipeline, product, "belongs_to", review_status="approved")
    db.create_relation(pipeline, service, "different_from", review_status="approved")
    db.create_relation(pipeline, config, "different_from", review_status="approved")
    db.create_relation(service, config, "uses_config", review_status="approved")
    db.create_relation(pipeline, procedure, "requires", review_status="approved")
    db.create_relation(procedure, step, "has_step", review_status="approved")
    db.create_link(pipeline, "chunk-pipeline", evidence_text="PipelineBuilder")
    db.create_link(step, "chunk-engineering", evidence_text="工程设置")
    return db


def test_entity_linker_resolves_alias_and_collects_explicit_exclusions(graph_db):
    linked = EntityLinker(graph_db).link("管线发布工具如何使用？", "procedure")

    assert len(linked) == 1
    assert linked[0].canonical_name == "PipelineBuilder"
    assert linked[0].entity_type == "Tool"
    assert linked[0].match_method == "alias_exact"
    excluded = {graph_db.get_entity(entity_id)["name"] for entity_id in linked[0].excluded_entity_ids}
    assert excluded == {"管线发布服务", "PipelinePublishConfig"}


def test_entity_linker_returns_empty_for_ambiguous_same_priority_matches(graph_db):
    other = graph_db.create_entity("PipelinePublisher", "Tool", doc_category="StampTools")
    graph_db.create_alias(other, "管线发布工具", review_status="approved")

    assert EntityLinker(graph_db).link("管线发布工具如何使用？", "procedure") == ()


def test_graph_expander_uses_two_hops_for_procedure_and_collects_evidence(graph_db):
    linked = EntityLinker(graph_db).link("管线发布工具如何使用？", "procedure")
    context = GraphExpander(graph_db).expand(linked, "procedure")

    names = {graph_db.get_entity(entity_id)["name"] for entity_id in context.expanded_entity_ids}
    assert {"PipelineBuilder", "PipelineBuilder 使用流程", "工程设置"} <= names
    assert context.chunk_ids == ("chunk-pipeline", "chunk-engineering")
    assert "PipelineBuilder" in context.retrieval_queries
    assert "工程设置" in context.retrieval_queries
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

    assert [doc.metadata["chunk_id"] for doc in fused] == ["chunk-pipeline", "wrong"]
    assert fused[0].metadata["matched_query_kinds"] == ["retrieval", "graph"]


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
    retriever = GraphRetriever(graph_db, store=store)

    from rag_knowledge.services.chunk_admin import ChunkAdminService
    monkeypatch.setattr(ChunkAdminService, "_file_lookup", lambda self: {"chunk-pipeline": {"kb_name": "文章附件"}})

    # With correct kb_name
    context, docs = retriever.retrieve("管线发布工具如何使用？", "procedure", kb_name="文章附件")
    assert len(docs) == 1

    # With mismatched kb_name
    context, docs = retriever.retrieve("管线发布工具如何使用？", "procedure", kb_name="other_kb")
    assert docs == []
    assert context.fallback_reason == "graph_evidence_filtered"


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
