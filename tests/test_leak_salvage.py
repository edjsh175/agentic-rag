"""Leak salvage: rule-gated second LLM extract for missed business leaves."""
from __future__ import annotations

from rag_knowledge.services.graph_extraction import (
    EntityCandidate,
    ExtractionDiagnostic,
    ExtractionResult,
    RelationCandidate,
)
from rag_knowledge.services.graph_extraction.leak_salvage import (
    assess_leak_risk,
    build_salvage_note,
    merge_salvage_result,
    section_depth,
    section_leaf,
)


def test_section_leaf_and_depth():
    path = "PipelineBuilder > 值域映射 > 材质映射"
    assert section_leaf(path) == "材质映射"
    assert section_depth(path) == 3


def test_assess_keyword_leak_when_no_business_entities():
    chunk = {
        "content": "材质映射功能用于将模型材质名称映射为标准编码",
        "metadata": {"section_path": "PipelineBuilder > 值域映射 > 材质映射"},
    }
    empty = ExtractionResult()
    assert assess_leak_risk(chunk, empty) == "keyword_suggests_business_entity"


def test_assess_deep_section_without_keyword():
    chunk = {
        "content": "示意图如下所示。",
        "metadata": {
            "section_path": "PipelineBuilder > 值域映射 > 一般管线：二维示意图"
        },
    }
    assert assess_leak_risk(chunk, ExtractionResult()) == "deep_section_no_business_entity"


def test_assess_skips_when_business_already_present():
    chunk = {
        "content": "材质映射",
        "metadata": {"section_path": "PipelineBuilder > 值域映射 > 材质映射"},
    }
    got = ExtractionResult(
        entities=[EntityCandidate(name="材质映射", entity_type="Procedure")]
    )
    assert assess_leak_risk(chunk, got) is None


def test_build_salvage_note_mentions_leaf():
    note = build_salvage_note(
        "keyword_suggests_business_entity",
        {"metadata": {"section_path": "A > B > 材质映射"}},
    )
    assert "材质映射" in note
    assert "Salvage Pass" in note


def test_merge_salvage_adds_only_new_and_tags():
    primary = ExtractionResult(
        entities=[EntityCandidate(name="已有", entity_type="Section")],
        relations=[
            RelationCandidate("已有", "defined_in", "Doc"),
        ],
    )
    salvage = ExtractionResult(
        entities=[
            EntityCandidate(name="已有", entity_type="Section"),
            EntityCandidate(name="材质映射", entity_type="Procedure"),
        ],
        relations=[
            RelationCandidate("已有", "defined_in", "Doc"),
            RelationCandidate("材质映射", "belongs_to", "PipelineBuilder"),
        ],
        diagnostics=[ExtractionDiagnostic(code="x", message="m", chunk_id="c1")],
    )
    merged, e_add, r_add = merge_salvage_result(primary, salvage)
    assert e_add == 1
    assert r_add == 1
    proc = next(e for e in merged.entities if e.name == "材质映射")
    assert (proc.properties or {}).get("created_by") == "llm:leak_salvage"
    assert merged.has_relation("材质映射", "belongs_to", "PipelineBuilder")


def test_llm_extractor_appends_salvage_note(isolated_storage):
    isolated_storage()
    from rag_knowledge.services.graph_extraction.llm_extractor import LLMGraphExtractor

    ext = LLMGraphExtractor(backbone_constraints={"entity_type_by_name": {}, "relations": []})
    prompt = ext.build_prompt(
        doc_category="StampTools",
        section_path="A > B",
        content="hello",
        salvage_note="\n# Salvage Pass\nfocus",
    )
    assert "# Salvage Pass" in prompt
    assert "hello" in prompt
