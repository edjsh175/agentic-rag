import pytest
from rag_knowledge.services.query_entity_guard import (
    extract_explicit_entities,
    protect_rewritten_query,
    protect_query_list,
    filter_entity_candidates,
)

def test_extract_explicit_entities():
    # Single entities
    assert extract_explicit_entities("ueModelBuilder呢？") == ["ueModelBuilder"]
    assert extract_explicit_entities("uemodelbuilder呢？") == ["uemodelbuilder"]
    assert extract_explicit_entities("PipelineBuilder 值域映射怎么设置？") == ["PipelineBuilder"]
    assert extract_explicit_entities("pipelinebuilder 值域映射怎么设置？") == ["pipelinebuilder"]
    assert extract_explicit_entities("继续说一下工程设置") == []
    assert extract_explicit_entities("obliqueModelBuilder呢？") == ["obliqueModelBuilder"]
    assert extract_explicit_entities("obliquemodelbuilder呢？") == ["obliquemodelbuilder"]
    
    # Substring overlap but not independently occurring (e.g. ModelBuilder inside UEModelBuilder)
    assert extract_explicit_entities("UEModelBuilder 的使用") == ["UEModelBuilder"]
    
    # Substring overlap and independently occurring (both kept)
    assert extract_explicit_entities("ModelBuilder 和 UEModelBuilder 有什么区别？") == ["ModelBuilder", "UEModelBuilder"]
    assert extract_explicit_entities("UEModelBuilder 和 ObliqueModelBuilder 的区别") == ["UEModelBuilder", "ObliqueModelBuilder"]


def test_protect_rewritten_query():
    # Single entity: ModelBuilder -> UEModelBuilder
    orig = "ueModelBuilder呢？"
    rewritten = "ModelBuilder 的使用方法和流程说明"
    last = "ModelBuilder如何使用？"
    assert protect_rewritten_query(orig, rewritten, last) == "ueModelBuilder 的使用方法和流程说明"
    
    # Single entity: obliqueModelBuilder
    orig = "obliqueModelBuilder呢？"
    rewritten = "ModelBuilder 的使用方法和流程说明"
    last = "ModelBuilder如何使用？"
    assert protect_rewritten_query(orig, rewritten, last) == "obliqueModelBuilder 的使用方法和流程说明"
    
    # Single entity: no entity in rewritten -> pre-pend/replace
    orig = "ueModelBuilder呢？"
    rewritten = "使用方法和流程说明"
    last = "ModelBuilder如何使用？"
    assert protect_rewritten_query(orig, rewritten, last) == "ueModelBuilder 使用方法和流程说明"
    
    # No explicit entity in original query -> keep rewritten as-is
    orig = "继续说一下工程设置"
    rewritten = "ModelBuilder 的工程设置"
    last = "ModelBuilder如何使用？"
    assert protect_rewritten_query(orig, rewritten, last) == "ModelBuilder 的工程设置"
    
    # Multi-entity: both present -> keep rewritten
    orig = "ModelBuilder 和 UEModelBuilder 有什么区别？"
    rewritten = "介绍一下 ModelBuilder 与 UEModelBuilder 之间的区别和联系"
    assert protect_rewritten_query(orig, rewritten) == "介绍一下 ModelBuilder 与 UEModelBuilder 之间的区别和联系"
    
    # Multi-entity: one missing -> fallback to original question
    orig = "ModelBuilder 和 UEModelBuilder 有什么区别？"
    rewritten = "ModelBuilder 的概念和用途说明"
    assert protect_rewritten_query(orig, rewritten) == "ModelBuilder 和 UEModelBuilder 有什么区别？"


def test_protect_query_list():
    orig = "ueModelBuilder呢？"
    queries = [
        "ModelBuilder 工程设置",
        "ModelBuilder 数据设置"
    ]
    last = "ModelBuilder如何使用？"
    protected = protect_query_list(orig, queries, last)
    assert protected == [
        "ueModelBuilder 工程设置",
        "ueModelBuilder 数据设置"
    ]


def test_lowercase_entity_is_protected_in_rewrites():
    orig = "uemodelbuilder 数据设置"
    rewritten = "ModelBuilder 数据设置"
    last = "ModelBuilder如何使用？"
    assert protect_rewritten_query(orig, rewritten, last) == "uemodelbuilder 数据设置"


def test_filter_entity_candidates():
    # Single entity question: discard substring conflict (ModelBuilder)
    orig = "ueModelBuilder呢？"
    candidates = ["ModelBuilder", "UEModelBuilder"]
    assert filter_entity_candidates(orig, candidates) == ["UEModelBuilder"]
    
    # Multi-entity question: keep both
    orig = "ModelBuilder 和 UEModelBuilder 有什么区别？"
    candidates = ["ModelBuilder", "UEModelBuilder"]
    assert sorted(filter_entity_candidates(orig, candidates)) == sorted(["ModelBuilder", "UEModelBuilder"])


def test_filter_keeps_chinese_compound_despite_latin_prefix():
    """UV ⊂ UV展开错误 不应误杀题面中的中文复合 Error 实体。"""
    orig = "出现 UV展开错误时应如何排查？"
    candidates = ["UV展开错误", "PipelineBuilder", "ModelBuilder"]
    kept = filter_entity_candidates(orig, candidates)
    assert "UV展开错误" in kept
    # PipelineBuilder / ModelBuilder 与 UV 无包含关系，仍保留
    assert "PipelineBuilder" in kept


def test_protect_accepts_backbone_alias_equivalents():
    aliases = {
        "StampTools": "StampGIS Tools",
        "StampGIS Tools": "StampGIS Tools",
        "StampServer": "StampGIS Server",
        "StampGIS Server": "StampGIS Server",
    }
    orig = "StampTools 和 StampServer 有什么区别"
    rewritten = "StampGIS Tools 与 StampGIS Server 产品区别"
    assert (
        protect_rewritten_query(orig, rewritten, canonical_by_alias=aliases)
        == rewritten
    )
