import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_fr10_gold_v4_candidates.py"
SPEC = importlib.util.spec_from_file_location("build_fr10_gold_v4_candidates", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_v32_partitions_into_fixed_v4_candidate_scopes():
    gold = ROOT / "docs/04_已完成归档/01_文档解析与切块/已完成-第0B轮-并行准备与预研/评测基线与黄金集/multi_chunk_qa_gold_v3_2.json"
    groups = MODULE.partition_gold(json.loads(gold.read_text(encoding="utf-8")))

    assert {name: len(rows) for name, rows in groups.items()} == {"retrieval": 90, "governance": 20}
    assert {row["evaluation_scope"] for row in groups["retrieval"]} == {"fr10_retrieval"}
    assert {row["evaluation_scope"] for row in groups["governance"]} == {"answer_governance"}
    assert "mq-108" in {row["id"] for row in groups["retrieval"]}


def test_v4_candidate_manifest_remains_explicitly_unfrozen():
    parent = {"gold_sha256": "parent-hash", "corpus_snapshot_hash": "snapshot-hash"}
    groups = {"retrieval": [{}] * 90, "governance": [{}] * 20}

    manifest = MODULE.build_manifest(groups, parent, ["mq-026"])

    assert manifest["status"] == "not_frozen"
    assert manifest["counts"] == {"retrieval": 90, "governance": 20}
    assert manifest["excluded_items"]["ids"] == ["mq-026"]
    assert "manually approved" in manifest["freeze_requirement"]
