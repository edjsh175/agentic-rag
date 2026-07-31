import pytest
from rag_knowledge.models.graph_schema import EntityType
from rag_knowledge.services.function_area_classifier import FunctionAreaClassifier
from rag_knowledge.services.graph_extraction import SectionPathExtractor, EntityCandidate, ExtractionResult
from rag_knowledge.services.graph_extraction.llm_extractor import LLMGraphExtractor


def test_function_area_classifier():
    classifier = FunctionAreaClassifier()
    assert classifier.classify("数据管理") == "function_area"
    assert classifier.classify("工程设置") == "function_area"
    assert classifier.classify("服务部署") == "function_area"
    assert classifier.classify("快速开始") == "section"
    assert classifier.classify("目录") == "section"
    assert classifier.classify("附录") == "section"
    assert classifier.classify("一些随机后缀指南") == "section"
    assert classifier.classify("未命名节点") == "ambiguous"


def test_section_path_extractor_creates_function_area():
    chunk = {
        "chunk_id": "c100",
        "content": "材质映射具体说明...",
        "metadata": {
            "source": "StampTools用户手册.docx",
            "doc_category": "StampTools",
            "section_path": "PipelineBuilder > 数据管理 > 材质映射",
        },
    }
    result = SectionPathExtractor().extract(chunk)

    # FunctionArea PipelineBuilder::数据管理 should be created
    fa_entity = result.entity("PipelineBuilder::数据管理")
    assert fa_entity is not None
    assert fa_entity.entity_type == "FunctionArea"
    assert fa_entity.properties.get("display_name") == "数据管理"

    # Relation PipelineBuilder::数据管理 belongs_to PipelineBuilder
    assert result.has_relation("PipelineBuilder::数据管理", "belongs_to", "PipelineBuilder")

    # Document and Section graph should also exist
    assert result.entity("StampTools用户手册.docx").entity_type == "Document"


def test_llm_extractor_rejects_llm_created_function_area(isolated_storage):
    isolated_storage(db_name="llm_fa.db", data_dir_name="llm_fa_data", chroma_name="llm_fa_chroma")
    extractor = LLMGraphExtractor()
    raw_data = {
        "entities": [
            {
                "name": "非法功能区",
                "entity_type": "FunctionArea",
                "confidence": 0.9,
                "evidence_text": "材质映射具体说明...",
            },
            {
                "name": "合法流程",
                "entity_type": "Procedure",
                "confidence": 0.9,
                "evidence_text": "材质映射具体说明...",
            },
        ],
        "relations": [],
    }

    res = ExtractionResult()
    extractor._validate_and_normalize(
        raw_data, "chunk_1", "StampTools", res, "材质映射具体说明...", "PipelineBuilder > 数据管理"
    )

    # FunctionArea entity created by LLM should be rejected
    assert res.entity("非法功能区") is None
    # Diagnostic should be recorded
    assert any(d.code == "function_area_readonly" for d in res.diagnostics)
    # Valid procedure should be accepted
    assert res.entity("合法流程") is not None
