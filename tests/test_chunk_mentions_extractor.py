"""
Unit tests for ChunkMentionsExtractor.
"""
import pytest
from rag_knowledge.services.graph_extraction.chunk_mentions_extractor import (
    ChunkMentionsExtractor,
    FORBIDDEN_ENTITY_TYPES,
    ALLOWED_ENTITY_TYPES,
)


def test_chunk_mentions_forbidden_types():
    """Ensure Hub/Backbone entity types are strictly forbidden."""
    extractor = ChunkMentionsExtractor()
    chunk_text = "PipelineBuilder 支持材质映射与数据管理功能，属于 Product 核心组件。"

    candidates = [
        {"id": "e1", "name": "PipelineBuilder", "entity_type": "Tool"},
        {"id": "e2", "name": "Product", "entity_type": "Product"},
        {"id": "e3", "name": "数据管理", "entity_type": "FunctionArea"},
        {"id": "e4", "name": "材质映射", "entity_type": "Feature"},
    ]

    mentions = extractor.extract_mentions("c1", chunk_text, candidates)
    entity_names = [m["entity_name"] for m in mentions]

    # PipelineBuilder (Tool), Product (Product), 数据管理 (FunctionArea) MUST be filtered out
    assert "PipelineBuilder" not in entity_names
    assert "Product" not in entity_names
    assert "数据管理" not in entity_names
    
    # 材质映射 (Feature) MUST be allowed
    assert "材质映射" in entity_names
    assert len(mentions) == 1
    assert mentions[0]["link_type"] == "mentions"


def test_chunk_mentions_quota_limit():
    """Ensure text chunks enforce max mentions quota (e.g. 10)."""
    extractor = ChunkMentionsExtractor(text_quota=3)
    chunk_text = "支持 Feature1, Feature2, Feature3, Feature4, Feature5 五种特性。"

    candidates = [
        {"id": f"e{i}", "name": f"Feature{i}", "entity_type": "Feature"}
        for i in range(1, 6)
    ]

    # Non-table chunk: should be capped at quota=3
    mentions_text = extractor.extract_mentions("c2", chunk_text, candidates, is_table=False)
    assert len(mentions_text) == 3

    # Table chunk: should bypass 3 quota
    mentions_table = extractor.extract_mentions("c2", chunk_text, candidates, is_table=True)
    assert len(mentions_table) == 5


def test_chunk_mentions_evidence_extraction():
    """Ensure snippet extraction contains matched entity name."""
    extractor = ChunkMentionsExtractor()
    chunk_text = "在高级设置中，端口 6379 的 Redis 配置项包含 ArcConfig 参数。"

    candidates = [
        {"id": "e10", "name": "ArcConfig", "entity_type": "ConfigItem"},
    ]

    mentions = extractor.extract_mentions("c3", chunk_text, candidates)
    assert len(mentions) == 1
    assert "ArcConfig" in mentions[0]["evidence_text"]
