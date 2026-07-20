import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "freeze_fr10_gold_v4.py"
SPEC = importlib.util.spec_from_file_location("freeze_fr10_gold_v4", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_v4_review_covers_all_candidates_and_excludes_rejected_items():
    candidate = ROOT / "docs/3_待办清单/切块基石治理/已完成-第0B轮-并行准备与预研/评测基线与黄金集/multi_chunk_qa_gold_v4_retrieval_candidate.json"
    final_items, ledger = MODULE.review_candidates(json.loads(candidate.read_text(encoding="utf-8")))

    assert len(ledger) == 90
    assert len(final_items) == 45
    assert {row["decision"] for row in ledger} == {"approved", "revised", "rejected"}
    assert not {item["id"] for item in final_items} & MODULE.REJECTED_IDS
    assert all(item["review_status"] == "approved" for item in final_items)


def test_v4_revisions_correct_the_known_https_port_mapping_question():
    candidate = ROOT / "docs/3_待办清单/切块基石治理/已完成-第0B轮-并行准备与预研/评测基线与黄金集/multi_chunk_qa_gold_v4_retrieval_candidate.json"
    final_items, _ = MODULE.review_candidates(json.loads(candidate.read_text(encoding="utf-8")))
    item = next(item for item in final_items if item["id"] == "mq-108")

    assert item["required_facts"] == ["89：8450", "service nginx restart"]
    assert "HTTPS 端口 8450" in item["ground_truth"]
