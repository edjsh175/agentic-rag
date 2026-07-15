"""Unit tests for FR-10 multi-evidence rule metrics."""
from __future__ import annotations

from rag_knowledge.evaluation.multi_evidence_metrics import (
    evidence_anchor_recall,
    score_answer,
    summarize_scores,
)


def test_full_answer_passes_when_facts_present():
    item = {
        "id": "t1",
        "category": "fact",
        "answerability": "full",
        "required_facts": ["umount -f /dev/mapper/rl-var", "xfs_repair"],
        "forbidden_claims": [],
        "evidence_anchors": [],
    }
    answer = "先执行 umount -f /dev/mapper/rl-var，再运行 xfs_repair。"
    scored = score_answer(item, answer)
    assert scored["passed"] is True
    assert scored["completeness"] == 1.0


def test_forbidden_claim_fails():
    item = {
        "id": "t2",
        "answerability": "partial",
        "required_facts": ["证据不足"],
        "forbidden_claims": ["完整流程如下"],
        "evidence_anchors": [],
    }
    answer = "完整流程如下：……另外证据不足。"
    scored = score_answer(item, answer)
    assert scored["passed"] is False
    assert scored["forbidden_triggered"]


def test_conflict_requires_multi_value_and_hint():
    item = {
        "id": "t3",
        "answerability": "conflict",
        "required_facts": ["5439", "5349", "冲突提示"],
        "forbidden_claims": ["仅存在唯一端口"],
        "evidence_anchors": [],
    }
    weak = score_answer(item, "端口是 5439。")
    assert weak["passed"] is False

    strong = score_answer(
        item,
        "端口表写 5439，正文写 5349，存在冲突，请核对来源后再配置。",
    )
    assert strong["passed"] is True


def test_none_requires_refusal():
    item = {
        "id": "t4",
        "answerability": "none",
        "required_facts": ["未查询到"],
        "forbidden_claims": ["编造端口"],
        "evidence_anchors": [],
    }
    scored = score_answer(item, "当前知识库中未查询到相关内容。")
    assert scored["passed"] is True


def test_evidence_anchor_recall_by_source_and_section():
    anchors = [{"source": "StampWebRTC用户手册.docx", "section_path_contains": "Turnserver"}]
    sources = [
        {"source": "StampWebRTC用户手册.docx", "section_path": "部署 > Turnserver 配置"},
    ]
    result = evidence_anchor_recall(sources, anchors)
    assert result["score"] == 1.0


def test_summarize_scores_buckets():
    rows = [
        score_answer(
            {
                "id": "a",
                "category": "fact",
                "answerability": "full",
                "required_facts": ["a"],
                "forbidden_claims": [],
                "evidence_anchors": [],
            },
            "a",
        ),
        score_answer(
            {
                "id": "b",
                "category": "none",
                "answerability": "none",
                "required_facts": [],
                "forbidden_claims": [],
                "evidence_anchors": [],
            },
            "hello",
        ),
    ]
    summary = summarize_scores(rows)
    assert summary["total"] == 2
    assert "fact" in summary["by_category"]
    assert "none" in summary["by_answerability"]
