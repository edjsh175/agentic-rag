# -*- coding: utf-8 -*-
from __future__ import annotations

from rag_knowledge.services.graph_extraction import (
    EntityCandidate,
    ExtractionResult,
    RelationCandidate,
)
from rag_knowledge.services.graph_extraction.exemplar_pack import (
    clear_exemplar_cache,
    format_exemplars_for_prompt,
    load_universal_pack,
)
from rag_knowledge.services.graph_extraction.leaf_fallback import apply_leaf_rule_fallback


def test_universal_pack_injected_for_all_categories():
    clear_exemplar_cache()
    pack = load_universal_pack()
    assert pack.get("pack_id") == "pattern_universal_v1"
    for category in ("StampTools", "StampServer", "StampWebRTC", "博客", ""):
        text = format_exemplars_for_prompt(category)
        assert "uni-proc-under-tool" in text
        assert "uni-deploy-proc-command" in text
        assert "{Tool}" in text or "Universal navigational" in text
        assert text != "(none)"


def test_stamptools_still_appends_category_pack():
    clear_exemplar_cache()
    text = format_exemplars_for_prompt("StampTools")
    assert "uni-proc-under-tool" in text
    assert "st-proc-new-project" in text


def test_leaf_fallback_keeps_all_when_llm_empty():
    rule = ExtractionResult(
        entities=[EntityCandidate("新建工程", "Procedure", "StampTools")],
        relations=[RelationCandidate("ObliqueModelBuilder", "has_procedure", "新建工程")],
    )
    out = apply_leaf_rule_fallback(rule, ExtractionResult())
    assert out.entity("新建工程") is not None
    assert out.has_relation("ObliqueModelBuilder", "has_procedure", "新建工程")


def test_leaf_fallback_drops_duplicates_covered_by_llm():
    rule = ExtractionResult(
        entities=[
            EntityCandidate("新建工程", "Procedure", "StampTools"),
            EntityCandidate("Las", "Format", "StampTools"),
        ],
        relations=[
            RelationCandidate("ObliqueModelBuilder", "has_procedure", "新建工程"),
            RelationCandidate("点云数据处理工具", "supports_format", "Las"),
        ],
    )
    llm = ExtractionResult(
        entities=[EntityCandidate("新建工程", "Procedure", "StampTools")],
        relations=[RelationCandidate("ObliqueModelBuilder", "has_procedure", "新建工程")],
    )
    out = apply_leaf_rule_fallback(rule, llm)
    assert out.entity("新建工程") is None
    assert out.entity("Las") is not None
    assert not out.has_relation("ObliqueModelBuilder", "has_procedure", "新建工程")
    assert out.has_relation("点云数据处理工具", "supports_format", "Las")
