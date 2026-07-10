import json
import shutil
from pathlib import Path

import pytest
from langchain_core.documents import Document

from rag_knowledge.config import Config
from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.graph_intent_scoring import GraphIntentFactProvider
from rag_knowledge.services.retrieval_intent import (
    RetrievalIntentPolicy,
    RetrievalIntentPlan,
    RetrievalIntentResolver,
    load_intent_policies,
    load_legacy_intent_profiles,
    score_graph_doc,
    score_legacy_doc,
    section_matches_expected,
)
from tests.fixtures.pipeline_graph_facts import seed_pipeline_table_graph

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write_policies(path: Path):
    policies = [
        {
            "id": "widget_publish",
            "entity_ref": "WidgetBuilder",
            "intent_terms": ["发布", "publish"],
            "query_hints": ["发布流程", "成果上传"],
            "preferred_doc_categories": ["WidgetTools"],
            "fallback_doc_categories": ["WidgetServer"],
            "candidate_min_k": 9,
        }
    ]
    path.write_text(json.dumps(policies, ensure_ascii=False), encoding="utf-8")


def _write_legacy_profiles(path: Path):
    profiles = [
        {
            "id": "widget_publish",
            "entity_aliases": ["WidgetBuilder"],
            "intent_terms": ["发布", "publish"],
            "recall_terms": ["发布流程", "成果上传"],
            "section_families": [["WidgetBuilder > 发布", "WidgetBuilder > 发布流程"]],
            "preferred_sources": ["WidgetTools"],
            "fallback_sources": ["WidgetServer"],
            "sibling_penalty_groups": [["发布流程", "发布服务"]],
            "candidate_min_k": 9,
        }
    ]
    path.write_text(json.dumps(profiles, ensure_ascii=False), encoding="utf-8")


def _policy_resolver(tmp_path: Path) -> RetrievalIntentResolver:
    policies_path = tmp_path / "policies.json"
    _write_policies(policies_path)
    return RetrievalIntentResolver(load_intent_policies(policies_path))


def _migration_resolver(tmp_path: Path) -> RetrievalIntentResolver:
    legacy_path = tmp_path / "legacy.json"
    _write_legacy_profiles(legacy_path)
    return RetrievalIntentResolver.for_migration(legacy_path=legacy_path)


@pytest.fixture
def widget_graph_db(isolated_storage):
    isolated_storage(
        db_name="widget-intent.db",
        data_dir_name="widget-intent-data",
    )
    db = RelationalDB()
    widget = db.create_entity("WidgetBuilder", "Tool", doc_category="WidgetTools", review_status="approved")
    db.create_alias(widget, "WidgetBuilder", review_status="approved")
    publish_section = db.create_entity(
        "WidgetBuilder_发布",
        "Section",
        properties_json=json.dumps({"section_path": "WidgetBuilder > 发布"}, ensure_ascii=False),
        review_status="approved",
    )
    flow_section = db.create_entity(
        "WidgetBuilder_发布流程",
        "Section",
        properties_json=json.dumps({"section_path": "WidgetBuilder > 发布流程"}, ensure_ascii=False),
        review_status="approved",
    )
    service = db.create_entity("发布服务", "Service", review_status="approved")
    db.create_relation(widget, publish_section, "defined_in", review_status="approved")
    db.create_relation(widget, flow_section, "defined_in", review_status="approved")
    db.create_relation(widget, service, "different_from", review_status="approved")
    return db


def test_default_resolver_does_not_load_legacy_profiles():
    resolver = RetrievalIntentResolver.default()
    assert resolver._legacy_by_id == {}


def test_for_migration_resolver_loads_legacy_explicitly(tmp_path):
    legacy_path = tmp_path / "legacy.json"
    _write_legacy_profiles(legacy_path)
    resolver = RetrievalIntentResolver.for_migration(legacy_path=legacy_path)
    assert "widget_publish" in resolver._legacy_by_id


def test_load_policies_validates_required_id(tmp_path):
    path = tmp_path / "policies.json"
    _write_policies(path)

    policies = load_intent_policies(path)

    assert policies[0].id == "widget_publish"
    assert policies[0].candidate_min_k == 9


def test_load_policies_rejects_legacy_fact_fields(tmp_path):
    path = tmp_path / "policies.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "bad",
                    "entity_ref": "WidgetBuilder",
                    "entity_aliases": ["WidgetBuilder"],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="entity_aliases"):
        load_intent_policies(path)


def test_load_policies_requires_query_match_terms(tmp_path):
    path = tmp_path / "policies.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "source_only",
                    "preferred_doc_categories": ["WidgetTools"],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="entity_ref or intent_terms"):
        load_intent_policies(path)


def test_query_matching_and_recall_expansion_are_policy_driven(tmp_path):
    resolver = _policy_resolver(tmp_path)

    plan = resolver.resolve("WidgetBuilder 如何发布成果", top_k=4)

    assert [policy.id for policy in plan.policies] == ["widget_publish"]
    assert plan.expand_query("WidgetBuilder 如何发布成果") == "WidgetBuilder 如何发布成果 发布流程 成果上传"
    assert plan.effective_top_k(4) == 9


def test_refine_from_graph_selects_policy_by_entity_ref(tmp_path):
    resolver = _policy_resolver(tmp_path)

    plan = resolver.resolve("发布流程")
    refined = resolver.refine_from_graph(plan, canonical_names=("WidgetBuilder",))

    assert [policy.id for policy in refined.policies] == ["widget_publish"]
    assert refined.graph_entity_refs == ("WidgetBuilder",)


def test_generic_preferred_and_fallback_source_scoring(tmp_path, widget_graph_db):
    resolver = _policy_resolver(tmp_path)
    plan = resolver.resolve("WidgetBuilder 如何发布成果", top_k=4)
    provider = GraphIntentFactProvider(widget_graph_db)
    docs = [
        Document(
            page_content="# WidgetBuilder > 发布服务\n\nWidgetServer 发布服务配置。",
            metadata={
                "chunk_id": "service",
                "quality_score": 0.05,
                "source": "WidgetServer用户手册.docx",
                "section_path": "WidgetBuilder > 发布服务",
                "doc_category": "WidgetServer",
            },
        ),
        Document(
            page_content="# WidgetBuilder > 发布流程\n\nWidgetBuilder 发布成果。",
            metadata={
                "chunk_id": "tool",
                "quality_score": 0.035,
                "source": "WidgetTools用户手册.docx",
                "section_path": "WidgetBuilder > 发布流程",
                "doc_category": "WidgetTools",
            },
        ),
    ]

    ranked = plan.apply_quality_scores(docs, fact_provider=provider)

    assert ranked[0].metadata["chunk_id"] == "tool"
    assert ranked[0].metadata["intent_profile_boost"] > 0
    assert ranked[1].metadata["intent_profile_penalty"] > 0


def test_preferred_source_requires_content_anchor(tmp_path, widget_graph_db):
    resolver = _policy_resolver(tmp_path)
    plan = resolver.resolve("WidgetBuilder 如何发布成果", top_k=4)
    provider = GraphIntentFactProvider(widget_graph_db)
    docs = [
        Document(
            page_content="# 通用安装说明\n\n这里只讲环境准备，不讲成果流程。",
            metadata={
                "chunk_id": "source_only_preferred",
                "quality_score": 0.05,
                "source": "WidgetTools用户手册.docx",
                "section_path": "其他章节 > 安装说明",
                "doc_category": "WidgetTools",
            },
        ),
        Document(
            page_content="# WidgetBuilder > 发布流程\n\nWidgetBuilder 发布成果。",
            metadata={
                "chunk_id": "target_flow",
                "quality_score": 0.045,
                "source": "OtherManual.docx",
                "section_path": "WidgetBuilder > 发布流程",
            },
        ),
    ]

    ranked = plan.apply_quality_scores(docs, fact_provider=provider)

    assert ranked[0].metadata["chunk_id"] == "target_flow"
    assert "intent_profile_boost" not in ranked[1].metadata


def test_query_without_intent_match_stays_unprofiled(tmp_path, widget_graph_db):
    resolver = _policy_resolver(tmp_path)

    plan = resolver.resolve("WidgetBuilder 安装环境要求", top_k=4)

    assert plan.policies == ()
    docs = [
        Document(page_content="A", metadata={"chunk_id": "a", "quality_score": 0.03}),
        Document(page_content="B", metadata={"chunk_id": "b", "quality_score": 0.02}),
    ]
    ranked = plan.apply_quality_scores(docs, fact_provider=GraphIntentFactProvider(widget_graph_db))
    assert [doc.metadata["chunk_id"] for doc in ranked] == ["a", "b"]
    assert all("intent_profile_boost" not in doc.metadata for doc in ranked)


def test_section_family_matching_uses_graph_provider(widget_graph_db):
    provider = GraphIntentFactProvider(widget_graph_db)

    assert section_matches_expected(
        "WidgetBuilder > 发布流程",
        "WidgetBuilder > 发布",
        fact_provider=provider,
        entity_ref="WidgetBuilder",
    )


def test_sibling_penalty_uses_graph_facts(tmp_path, widget_graph_db):
    resolver = _policy_resolver(tmp_path)
    plan = resolver.resolve("WidgetBuilder 如何发布成果", top_k=4)
    provider = GraphIntentFactProvider(widget_graph_db)
    docs = [
        Document(
            page_content="# WidgetBuilder > 发布服务\n\nWidgetBuilder 发布服务。",
            metadata={
                "chunk_id": "sibling_service",
                "quality_score": 0.06,
                "source": "WidgetTools用户手册.docx",
                "section_path": "WidgetBuilder > 发布服务",
            },
        ),
        Document(
            page_content="# WidgetBuilder > 发布流程\n\nWidgetBuilder 发布流程。",
            metadata={
                "chunk_id": "target_flow",
                "quality_score": 0.035,
                "source": "WidgetTools用户手册.docx",
                "section_path": "WidgetBuilder > 发布流程",
            },
        ),
    ]

    ranked = plan.apply_quality_scores(docs, fact_provider=provider)

    assert ranked[0].metadata["chunk_id"] == "target_flow"


def _legacy_profile():
    return load_legacy_intent_profiles(
        PROJECT_ROOT / "data/migrations/retrieval_intent_profiles_v1.json"
    )[0]


def test_legacy_and_graph_scoring_match_for_widget_doc(tmp_path, widget_graph_db):
    legacy = _migration_resolver(tmp_path)._legacy_by_id["widget_publish"]
    policy = _policy_resolver(tmp_path).resolve("WidgetBuilder 如何发布成果").policies[0]
    facts = GraphIntentFactProvider(widget_graph_db).load_one("WidgetBuilder")
    doc = Document(
        page_content="# WidgetBuilder > 发布流程\n\nWidgetBuilder 发布成果。",
        metadata={
            "chunk_id": "tool",
            "source": "WidgetTools用户手册.docx",
            "section_path": "WidgetBuilder > 发布流程",
            "doc_category": "WidgetTools",
        },
    )

    legacy_score = score_legacy_doc("", legacy, doc)
    graph_score = score_graph_doc(policy, facts, doc)

    assert legacy_score == graph_score
