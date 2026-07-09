import json
from pathlib import Path

from langchain_core.documents import Document

from rag_knowledge.services.retrieval_intent import (
    RetrievalIntentResolver,
    load_intent_profiles,
    section_matches_expected,
)


def _write_profiles(path: Path):
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


def test_load_profiles_validates_required_id(tmp_path):
    path = tmp_path / "profiles.json"
    _write_profiles(path)

    profiles = load_intent_profiles(path)

    assert profiles[0].id == "widget_publish"
    assert profiles[0].candidate_min_k == 9


def test_load_profiles_requires_query_match_terms(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text(
        json.dumps(
            [
                {
                    "id": "source_only",
                    "preferred_sources": ["WidgetTools"],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    try:
        load_intent_profiles(path)
    except ValueError as exc:
        assert "entity_aliases or intent_terms" in str(exc)
    else:
        raise AssertionError("Expected invalid profile to be rejected")


def test_query_matching_and_recall_expansion_are_profile_driven(tmp_path):
    path = tmp_path / "profiles.json"
    _write_profiles(path)
    resolver = RetrievalIntentResolver(load_intent_profiles(path))

    plan = resolver.resolve("WidgetBuilder 如何发布成果", top_k=4)

    assert [profile.id for profile in plan.profiles] == ["widget_publish"]
    assert plan.expand_query("WidgetBuilder 如何发布成果") == "WidgetBuilder 如何发布成果 发布流程 成果上传"
    assert plan.effective_top_k(4) == 9


def test_generic_preferred_and_fallback_source_scoring(tmp_path):
    path = tmp_path / "profiles.json"
    _write_profiles(path)
    resolver = RetrievalIntentResolver(load_intent_profiles(path))
    plan = resolver.resolve("WidgetBuilder 如何发布成果", top_k=4)
    docs = [
        Document(
            page_content="# WidgetBuilder > 发布服务\n\nWidgetServer 发布服务配置。",
            metadata={
                "chunk_id": "service",
                "quality_score": 0.05,
                "source": "WidgetServer用户手册.docx",
                "section_path": "WidgetBuilder > 发布服务",
            },
        ),
        Document(
            page_content="# WidgetBuilder > 发布流程\n\nWidgetBuilder 发布成果。",
            metadata={
                "chunk_id": "tool",
                "quality_score": 0.035,
                "source": "WidgetTools用户手册.docx",
                "section_path": "WidgetBuilder > 发布流程",
            },
        ),
    ]

    ranked = plan.apply_quality_scores(docs)

    assert ranked[0].metadata["chunk_id"] == "tool"
    assert ranked[0].metadata["intent_profile_boost"] > 0
    assert ranked[1].metadata["intent_profile_penalty"] > 0


def test_preferred_source_requires_content_anchor(tmp_path):
    path = tmp_path / "profiles.json"
    _write_profiles(path)
    resolver = RetrievalIntentResolver(load_intent_profiles(path))
    plan = resolver.resolve("WidgetBuilder 如何发布成果", top_k=4)
    docs = [
        Document(
            page_content="# 通用安装说明\n\n这里只讲环境准备，不讲成果流程。",
            metadata={
                "chunk_id": "source_only_preferred",
                "quality_score": 0.05,
                "source": "WidgetTools用户手册.docx",
                "section_path": "其他章节 > 安装说明",
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

    ranked = plan.apply_quality_scores(docs)

    assert ranked[0].metadata["chunk_id"] == "target_flow"
    assert "intent_profile_boost" not in ranked[1].metadata


def test_query_without_intent_match_stays_unprofiled(tmp_path):
    path = tmp_path / "profiles.json"
    _write_profiles(path)
    resolver = RetrievalIntentResolver(load_intent_profiles(path))

    plan = resolver.resolve("WidgetBuilder 安装环境要求", top_k=4)

    assert plan.profiles == ()
    docs = [
        Document(page_content="A", metadata={"chunk_id": "a", "quality_score": 0.03}),
        Document(page_content="B", metadata={"chunk_id": "b", "quality_score": 0.02}),
    ]
    ranked = plan.apply_quality_scores(docs)
    assert [doc.metadata["chunk_id"] for doc in ranked] == ["a", "b"]
    assert all("intent_profile_boost" not in doc.metadata for doc in ranked)


def test_section_family_matching_comes_from_profiles(tmp_path):
    path = tmp_path / "profiles.json"
    _write_profiles(path)
    profiles = load_intent_profiles(path)

    assert section_matches_expected(
        "WidgetBuilder > 发布流程",
        "WidgetBuilder > 发布",
        profiles,
    )


def test_sibling_penalty_is_profile_driven_for_synthetic_entity(tmp_path):
    path = tmp_path / "profiles.json"
    _write_profiles(path)
    resolver = RetrievalIntentResolver(load_intent_profiles(path))
    plan = resolver.resolve("WidgetBuilder 如何发布成果", top_k=4)
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

    ranked = plan.apply_quality_scores(docs)

    assert ranked[0].metadata["chunk_id"] == "target_flow"
