"""Tests for graph-backed intent scoring."""
from __future__ import annotations

import pytest
from langchain_core.documents import Document

from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.graph_intent_scoring import GraphIntentFactProvider
from rag_knowledge.services.retrieval_intent import RetrievalIntentPolicy
from tests.fixtures.pipeline_graph_facts import seed_pipeline_table_graph
from tests.fixtures.pipeline_graph_facts_production import seed_production_pipeline_graph


@pytest.fixture
def production_graph_db(isolated_storage):
    isolated_storage(db_name="graph-intent-prod.db", data_dir_name="graph-intent-prod-data")
    db = RelationalDB()
    seed_production_pipeline_graph(db)
    return db


@pytest.fixture
def graph_db(isolated_storage):
    isolated_storage(db_name="graph-intent-scoring.db", data_dir_name="graph-intent-scoring-data")
    db = RelationalDB()
    seed_pipeline_table_graph(db)
    return db


def test_provider_load_many_is_bounded(graph_db):
    provider = GraphIntentFactProvider(graph_db)
    facts = provider.load_many(["管线点表", "管线线表", "管线面表", "DOMBuilder"])
    assert set(facts) == {"管线点表", "管线线表", "管线面表", "DOMBuilder"}
    assert "管点编号" in facts["管线点表"].field_names


def test_production_scoped_field_leaf_match(production_graph_db):
    from rag_knowledge.services.graph_intent_scoring import build_match_signals

    provider = GraphIntentFactProvider(production_graph_db)
    facts = provider.load_one("管线点表")
    policy = RetrievalIntentPolicy(id="pipeline_point_table", entity_ref="管线点表")
    doc = Document(page_content="管点编号 说明", metadata={})
    signals = build_match_signals(policy, facts, doc)
    assert signals.field_hit is True


def test_production_alias_section_path_derived(production_graph_db):
    from rag_knowledge.services.graph_intent_scoring import build_match_signals

    provider = GraphIntentFactProvider(production_graph_db)
    facts = provider.load_one("管线点表")
    assert "PipelineBuilder > 数据规范 > 点数据结构" in facts.section_paths
    policy = RetrievalIntentPolicy(id="pipeline_point_table", entity_ref="管线点表")
    doc = Document(
        page_content="",
        metadata={"section_path": "PipelineBuilder > 数据规范 > 点数据结构"},
    )
    signals = build_match_signals(policy, facts, doc)
    assert signals.section_hit is True


def test_wrong_parent_section_path_no_hit(production_graph_db):
    from rag_knowledge.services.graph_intent_scoring import build_match_signals

    provider = GraphIntentFactProvider(production_graph_db)
    facts = provider.load_one("管线点表")
    policy = RetrievalIntentPolicy(id="pipeline_point_table", entity_ref="管线点表")
    doc = Document(
        page_content="",
        metadata={"section_path": "OtherBuilder > 数据规范 > 点数据结构"},
    )
    signals = build_match_signals(policy, facts, doc)
    assert signals.section_hit is False


def test_pending_entity_not_loaded(graph_db):
    pending_id = graph_db.create_entity("PendingTable", "DataTable", review_status="pending")
    graph_db.create_alias(pending_id, "待审别名", review_status="approved")
    provider = GraphIntentFactProvider(graph_db)
    assert provider.load_one("PendingTable") is None


def test_section_and_field_hits_apply_bonus(graph_db):
    provider = GraphIntentFactProvider(graph_db)
    policy = RetrievalIntentPolicy(
        id="pipeline_point_table",
        entity_ref="管线点表",
        preferred_doc_categories=("StampTools",),
    )
    facts = provider.load_one("管线点表")
    doc = Document(
        page_content="管点编号 地面高程",
        metadata={
            "section_path": "PipelineBuilder > 数据规范 > 点数据结构",
            "doc_category": "StampTools",
            "source": "StampTools用户手册.docx",
        },
    )
    from rag_knowledge.services.graph_intent_scoring import build_match_signals, score_signals

    signals = build_match_signals(policy, facts, doc)
    bonus, penalty = score_signals(signals)
    assert bonus > 0
    assert penalty == 0
