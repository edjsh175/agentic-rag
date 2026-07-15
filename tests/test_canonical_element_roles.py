from rag_knowledge.models.structured_document import (
    CanonicalDocumentElement,
    canonical_element_to_document,
)
from rag_knowledge.services.unstructured_loader import _ElementCollector, infer_content_role


def test_role_inference_is_structure_based_not_filename_based():
    assert infer_content_role("1. 安装服务", numbered=True) == "step"
    assert infer_content_role("systemctl enable demo") == "command"
    assert infer_content_role("GET /v1/users") == "api_endpoint"
    assert infer_content_role("请求参数") == "api_request"
    assert infer_content_role("响应示例") == "api_response"
    assert infer_content_role("表 2：用户表") == "table_title"
    assert infer_content_role("普通说明") == "ordinary_body"


def test_canonical_conversion_preserves_role_and_explicit_relations():
    element = CanonicalDocumentElement(
        element_type="table_context",
        section_path=["数据库", "用户表"],
        content_markdown="字段说明",
        searchable_text="字段说明",
        source="schema.docx",
        element_id="el_context",
        content_role="table_context",
        related_element_ids=["el_table"],
    )

    doc = canonical_element_to_document(element)

    assert doc.metadata["content_role"] == "table_context"
    assert doc.metadata["related_element_ids"] == ["el_table"]


def test_collector_establishes_table_title_context_relations_before_final_chunks():
    collector = _ElementCollector("schema.docx", document_key="doc-1")
    collector.handle_heading("用户表", 1)
    collector.handle_text("表 1：用户信息", raw_block_id="rb_1", content_role="table_title")
    collector.handle_table("| 字段 | 类型 |\n| --- | --- |\n| id | int |", raw_block_id="rb_2")
    collector.handle_text("id 为主键", raw_block_id="rb_3", content_role="table_context")

    title, table, context = collector.finish()

    assert title.content_role == "table_title"
    assert title.related_element_ids == [table.element_id]
    assert table.content_role == "table"
    assert table.related_element_ids == [title.element_id, context.element_id]
    assert context.content_role == "table_context"
    assert context.related_element_ids == [table.element_id]


def test_collector_keeps_step_and_command_as_separate_canonical_elements():
    collector = _ElementCollector("deploy.docx", document_key="doc-1")
    collector.handle_text("1. 安装服务", raw_block_id="rb_1", content_role="step")
    collector.handle_text("执行以下命令", raw_block_id="rb_2")
    collector.handle_text("systemctl enable demo", raw_block_id="rb_3", content_role="command")
    collector.handle_text("2. 验证服务", raw_block_id="rb_4", content_role="step")

    elements = collector.finish()

    assert [element.content_role for element in elements] == ["step", "command", "step"]
    assert elements[0].source_raw_block_ids == ["rb_1", "rb_2"]
    assert elements[1].source_raw_block_ids == ["rb_3"]
