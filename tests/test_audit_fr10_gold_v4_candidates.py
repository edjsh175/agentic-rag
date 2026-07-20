import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_fr10_gold_v4_candidates.py"
SPEC = importlib.util.spec_from_file_location("audit_fr10_gold_v4_candidates", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_audit_requires_source_and_section_constraints():
    anchor = {"source": "manual.docx", "section_id": "sec-1", "section_path_contains": "部署"}

    assert MODULE._anchor_matches({"source": "manual.docx", "section_id": "sec-1", "section_path": "安装 > 部署"}, anchor)
    assert not MODULE._anchor_matches({"source": "manual.docx", "section_id": "sec-2", "section_path": "安装 > 部署"}, anchor)


def test_audit_marks_direct_fact_miss_for_manual_review():
    item = {
        "id": "mq-test",
        "category": "fact",
        "question": "测试问题",
        "ground_truth": "测试答案",
        "required_facts": ["实际端口 或 备用端口", "不存在的词"],
        "evidence_anchors": [{"source": "manual.docx", "section_path_contains": "部署"}],
    }
    chunks = [{
        "id": "chunk-1",
        "document": "部署时使用备用端口 5349。",
        "metadata": {"source": "manual.docx", "section_path": "部署"},
    }]

    row = MODULE.audit_item(item, chunks)

    assert row["review_state"] == "needs_fact_review"
    assert row["fact_checks"][0]["direct_match"] is True
    assert row["facts_not_directly_found"] == ["不存在的词"]


def test_audit_marks_approved_item_verified_after_direct_evidence_check():
    item = {
        "id": "mq-test",
        "category": "fact",
        "question": "测试问题",
        "ground_truth": "测试答案",
        "required_facts": ["端口"],
        "review_status": "approved",
        "evidence_anchors": [{"source": "manual.docx", "section_path_contains": "部署"}],
    }
    chunks = [{"id": "chunk-1", "document": "部署端口为 443。", "metadata": {"source": "manual.docx", "section_path": "部署"}}]

    row = MODULE.audit_item(item, chunks)

    assert row["review_state"] == "verified_after_review"
