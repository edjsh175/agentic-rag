"""Unit tests for QualityService, 8 core metrics, and automated feedback loop."""
import pytest
from unittest.mock import MagicMock

from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.quality_service import (
    QualityService,
    compute_simhash,
    simhash_similarity,
)


def test_simhash_computation_and_similarity():
    text_a = "StampServer 基础配置说明与接口文档说明"
    text_b = "StampServer 基础配置说明与接口文档说明！"
    text_c = "完全不同的关于某些水果和蔬菜的分类说明"

    sh_a = compute_simhash(text_a)
    sh_b = compute_simhash(text_b)
    sh_c = compute_simhash(text_c)

    sim_ab = simhash_similarity(sh_a, sh_b)
    sim_ac = simhash_similarity(sh_a, sh_c)

    assert sim_ab > 0.90
    assert sim_ac < 0.80


def test_user_feedbacks_db_crud(isolated_storage):
    isolated_storage()
    db = RelationalDB()

    fid = db.create_feedback(
        user_id="test_user_1",
        query_text="什么是 StampServer？",
        answer_text="StampServer 是服务端软件...",
        referenced_chunk_ids=["chunk_test_101", "chunk_test_102"],
        rating="down",
        reason="过时错误",
        trace_id="trace_test_001",
    )
    assert bool(fid)

    fb = db.get_feedback(fid)
    assert fb is not None
    assert fb["user_id"] == "test_user_1"
    assert fb["rating"] == "down"
    assert "chunk_test_101" in fb["referenced_chunk_ids"]

    down_count = db.count_chunk_down_ratings("chunk_test_101")
    assert down_count >= 1


def test_feedback_loop_triggers_pending_reset_at_threshold(isolated_storage):
    isolated_storage()
    db = RelationalDB()

    mock_store = MagicMock()
    mock_admin = MagicMock()

    mock_store.get_chunk_stats_source.return_value = {
        "ids": ["c1", "c2"],
        "documents": ["doc1", "doc2"],
        "metadatas": [
            {"review_status": "approved", "source": "file1.txt"},
            {"review_status": "approved", "source": "file2.txt"},
        ],
    }

    qs = QualityService(store=mock_store, db=db, chunk_admin=mock_admin)

    # 1st down vote (soft penalization zone)
    res1 = qs.process_user_feedback(
        user_id="user_a",
        query_text="q1",
        answer_text="a1",
        referenced_chunk_ids=["c_loop_target"],
        rating="down",
        reason="答非所问",
        trace_id="t1",
    )
    assert len(res1["triggered_chunks"]) == 0
    mock_admin.update_chunk.assert_not_called()

    # 2nd down vote from another trace (still in soft penalization zone, down_score ~ 2.0)
    res2 = qs.process_user_feedback(
        user_id="user_b",
        query_text="q2",
        answer_text="a2",
        referenced_chunk_ids=["c_loop_target"],
        rating="down",
        reason="内容有误",
        trace_id="t2",
    )
    assert len(res2["triggered_chunks"]) == 0
    mock_admin.update_chunk.assert_not_called()

    # 3rd down vote from 3rd trace (reaches threshold >= 3.0 => triggers hard fallback pending)
    res3 = qs.process_user_feedback(
        user_id="user_c",
        query_text="q3",
        answer_text="a3",
        referenced_chunk_ids=["c_loop_target"],
        rating="down",
        reason="严重错误",
        trace_id="t3",
    )
    assert len(res3["triggered_chunks"]) == 1
    assert res3["triggered_chunks"][0]["chunk_id"] == "c_loop_target"
    assert res3["triggered_chunks"][0]["down_score"] >= 3.0

    mock_admin.update_chunk.assert_called_once()
    call_args = mock_admin.update_chunk.call_args
    assert call_args.kwargs["chunk_id"] == "c_loop_target"
    assert call_args.kwargs["changes"]["review_status"] == "pending"


def test_single_chunk_feedback_and_deduplication(isolated_storage):
    isolated_storage()
    db = RelationalDB()

    mock_store = MagicMock()
    mock_admin = MagicMock()
    qs = QualityService(store=mock_store, db=db, chunk_admin=mock_admin)

    # Submit chunk-level feedback
    res = qs.process_user_feedback(
        user_id="user_x",
        query_text="特定问题",
        answer_text="回答文本",
        referenced_chunk_ids=["c10", "c20"],
        rating="down",
        reason="该段落过时",
        trace_id="trace_x",
        feedback_scope="chunk",
        target_chunk_id="c10",
    )
    assert res["rating"] == "down"

    # Verify score applies specifically to c10, not c20
    score_c10 = db.get_chunk_effective_down_score("c10")
    score_c20 = db.get_chunk_effective_down_score("c20")
    assert abs(score_c10 - 1.0) < 0.05
    assert score_c20 == 0.0

    # Repeat exact same down vote by user_x on same trace_x (deduplication check)
    qs.process_user_feedback(
        user_id="user_x",
        query_text="特定问题",
        answer_text="回答文本",
        referenced_chunk_ids=["c10"],
        rating="down",
        reason="再次点击",
        trace_id="trace_x",
        feedback_scope="chunk",
        target_chunk_id="c10",
    )
    score_c10_dedup = db.get_chunk_effective_down_score("c10")
    assert abs(score_c10_dedup - 1.0) < 0.05


def test_get_dashboard_data_structure(isolated_storage):
    isolated_storage()
    db = RelationalDB()

    mock_store = MagicMock()
    mock_store.get_chunk_stats_source.return_value = {
        "ids": ["c1", "c2", "c3"],
        "documents": ["Standard doc 1", "Standard doc 2", "Standard doc 3"],
        "metadatas": [
            {"review_status": "approved", "source": "f1.pdf"},
            {"review_status": "pending", "source": "f2.pdf"},
            {"review_status": "approved", "source": "f3.pdf"},
        ],
    }

    qs = QualityService(store=mock_store, db=db)
    data = qs.get_dashboard_data()

    assert "metrics" in data
    assert "alerts" in data

    metrics = data["metrics"]
    assert metrics["total_chunks"] == 3
    assert abs(metrics["approved_ratio"] - 0.667) < 0.01
    assert metrics["pending_chunks"] == 1
    assert "isolated_entities" in metrics
    assert "isolated_chunks" in metrics
    assert "duplicate_ratio" in metrics
    assert "no_result_ratio_7d" in metrics
    assert "satisfaction_ratio_7d" in metrics
