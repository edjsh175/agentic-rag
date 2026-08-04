"""Evidence span match + deterministic repair."""
from __future__ import annotations

from rag_knowledge.services.graph_extraction.evidence_span import (
    evidence_matches,
    normalize_for_evidence_match,
    repair_evidence_span,
)


def test_normalize_folds_fullwidth_and_whitespace():
    assert normalize_for_evidence_match("A（B）") == "A(B)"
    assert normalize_for_evidence_match("执行　systemctl") == "执行 systemctl"


def test_exact_and_normalized_match():
    content = "执行　systemctl restart redis（生产）"
    assert evidence_matches("执行 systemctl restart redis(生产)", content)
    repaired = repair_evidence_span(
        "执行 systemctl restart redis(生产)", content
    )
    assert repaired is not None
    assert repaired.method == "normalized"
    assert "systemctl restart redis" in repaired.text
    assert "（生产）" in repaired.text or "(生产)" in repaired.text


def test_fuzzy_repairs_near_paraphrase():
    content = "材质映射功能用于将模型材质名称映射为标准编码。"
    # LLM dropped one character / slight paraphrase still near the span.
    evidence = "材质映射功能用于将模型材质名称映射为标准编码"
    repaired = repair_evidence_span(evidence + "。", content)
    assert repaired is not None
    assert repaired.method in {"exact", "normalized"}

    # Mild paraphrase: insert filler word that still fuzzy-matches the window.
    paraphrased = "材质映射功能 用于将模型材质名称映射为标准编码"
    repaired2 = repair_evidence_span(paraphrased, content)
    assert repaired2 is not None
    assert "材质映射" in repaired2.text


def test_fuzzy_rejects_unrelated():
    content = "执行 systemctl restart redis（生产）"
    assert repair_evidence_span("完全不相关的句子内容啊", content) is None
    assert not evidence_matches("完全不相关", content)


def test_section_path_fallback():
    content = "示意图如下。"
    section = "PipelineBuilder > 值域映射 > 材质映射"
    repaired = repair_evidence_span("PipelineBuilder > 值域映射 > 材质映射", content, section)
    assert repaired is not None
    assert repaired.method == "exact"
    assert repaired.text == section


def test_name_anchor_when_paraphrase_misses():
    content = "前文省略。材质映射功能用于将模型材质名称映射为标准编码。后文省略。"
    evidence = "该功能可以把材质名映射成编码"  # paraphrase, no fuzzy hit expected
    assert repair_evidence_span(evidence, content) is None
    repaired = repair_evidence_span(evidence, content, anchor="材质映射")
    assert repaired is not None
    assert repaired.method == "name_anchor"
    assert "材质映射功能" in repaired.text
