import json
import shutil
from pathlib import Path

import pytest
from langchain_core.documents import Document

from rag_knowledge.config import Config
from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.graph_intent_scoring import GraphIntentFactProvider
from rag_knowledge.services.retrieval_intent import (
    RetrievalIntentResolver,
    load_intent_policies,
    load_legacy_intent_profiles,
    score_graph_doc,
    score_legacy_doc,
)
from tests.fixtures.pipeline_graph_facts import seed_pipeline_table_graph
from tests.fixtures.pipeline_graph_facts_production import seed_production_pipeline_graph

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def pipeline_graph(isolated_storage):
    cfg, _, _, data_dir = isolated_storage(
        db_name="equiv-graph.db",
        data_dir_name="equiv-graph-data",
    )
    policies_src = PROJECT_ROOT / "data" / "retrieval_intent_policies.json"
    shutil.copy2(policies_src, data_dir / "retrieval_intent_policies.json")
    db = RelationalDB()
    seed_pipeline_table_graph(db)
    return db, data_dir


def _legacy_profile(profile_id: str):
    for profile in load_legacy_intent_profiles(
        PROJECT_ROOT / "data/migrations/retrieval_intent_profiles_v1.json"
    ):
        if profile.id == profile_id:
            return profile
    raise AssertionError(profile_id)


def _policy(profile_id: str):
    for policy in load_intent_policies(PROJECT_ROOT / "data/retrieval_intent_policies.json"):
        if policy.id == profile_id:
            return policy
    raise AssertionError(profile_id)


@pytest.mark.parametrize(
    "profile_id,query,doc",
    [
        (
            "pipeline_point_table",
            "PipelineBuilder 管线点表字段要求",
            Document(
                page_content="# PipelineBuilder > 数据规范 > 点数据结构\n\n管点编号 地面高程",
                metadata={
                    "section_path": "PipelineBuilder > 数据规范 > 点数据结构",
                    "doc_category": "StampTools",
                    "source": "StampTools用户手册.docx",
                },
            ),
        ),
        (
            "dom_builder_publish",
            "DOMBuilder 如何发布影像",
            Document(
                page_content="# TerrainBuilder > DOMBuilder > 发布影像\n\nDOMBuilder 编译完成后可发布影像成果。",
                metadata={
                    "section_path": "TerrainBuilder > DOMBuilder > 发布影像",
                    "source": "StampTools用户手册.docx",
                    "doc_category": "StampTools",
                },
            ),
        ),
    ],
)
def test_legacy_and_graph_scores_match(pipeline_graph, profile_id, query, doc):
    db, _ = pipeline_graph
    legacy = _legacy_profile(profile_id)
    policy = _policy(profile_id)
    facts = GraphIntentFactProvider(db).load_one(policy.entity_ref)

    assert score_legacy_doc(query, legacy, doc) == score_graph_doc(policy, facts, doc)


def test_graph_provider_loads_has_field(pipeline_graph):
    db, _ = pipeline_graph
    facts = GraphIntentFactProvider(db).load_one("管线点表")
    assert "管点编号" in facts.field_names
    assert "地面高程" in facts.field_names


@pytest.fixture
def production_pipeline_graph(isolated_storage):
    cfg, _, _, data_dir = isolated_storage(
        db_name="equiv-prod-graph.db",
        data_dir_name="equiv-prod-graph-data",
    )
    policies_src = PROJECT_ROOT / "data" / "retrieval_intent_policies.json"
    shutil.copy2(policies_src, data_dir / "retrieval_intent_policies.json")
    db = RelationalDB()
    seed_production_pipeline_graph(db)
    return db, data_dir


def test_production_legacy_and_graph_scores_match(production_pipeline_graph):
    db, _ = production_pipeline_graph
    legacy = _legacy_profile("pipeline_point_table")
    policy = _policy("pipeline_point_table")
    facts = GraphIntentFactProvider(db).load_one(policy.entity_ref)
    doc = Document(
        page_content="# PipelineBuilder > 数据规范 > 点数据结构\n\n管点编号 地面高程",
        metadata={
            "section_path": "PipelineBuilder > 数据规范 > 点数据结构",
            "doc_category": "StampTools",
            "source": "StampTools用户手册.docx",
        },
    )
    query = "PipelineBuilder 管线点表字段要求"
    assert score_legacy_doc(query, legacy, doc) == score_graph_doc(policy, facts, doc)


def test_production_graph_provider_loads_scoped_fields(production_pipeline_graph):
    db, _ = production_pipeline_graph
    facts = GraphIntentFactProvider(db).load_one("管线点表")
    assert "管线点表.管点编号" in facts.field_names
    assert "管线点表.地面高程" in facts.field_names
