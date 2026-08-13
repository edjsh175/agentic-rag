from __future__ import annotations

import pytest
from langchain_core.documents import Document
from types import SimpleNamespace

from rag_knowledge.services.document_profiles import (
    ChunkPolicy,
    DocumentProfile,
    apply_document_profile,
    get_chunk_policy,
)


def _doc(text: str, path: str, order: int, *, content_type: str = "text") -> Document:
    return Document(
        page_content=text,
        metadata={
            "source": "sample.docx",
            "section_path": path,
            "element_order": order,
            "content_type": content_type,
            "element_id": f"el_{order}",
            "source_element_ids": [f"el_{order}"],
            "source_raw_block_ids": [f"rb_{order:04d}"],
            "source_document_id": "doc-1",
            "source_snapshot_hash": "snapshot-1",
        },
    )


def test_policy_is_validated_and_has_stable_content_id():
    first = get_chunk_policy(DocumentProfile.TECHNICAL_MANUAL)
    second = get_chunk_policy("technical_manual")

    assert first == second
    assert first.policy_id == second.policy_id
    assert first.policy_id.startswith("cp_")
    with pytest.raises(ValueError):
        ChunkPolicy(profile=DocumentProfile.SECTION_BASED, target_max=900, soft_max=800)


def test_profile_policy_can_be_loaded_from_profile_level_config():
    config = SimpleNamespace(
        document_profile_policies={
            "technical_manual": {
                "target_min": 320,
                "target_max": 820,
                "soft_max": 1250,
                "command_follow_max": 1500,
                "table_row_group_max": 500,
            }
        }
    )

    policy = get_chunk_policy("technical_manual", config=config)

    assert policy.target_min == 320
    assert policy.target_max == 820
    assert policy.soft_max == 1250
    assert policy.policy_id != get_chunk_policy("technical_manual").policy_id


def test_section_based_never_merges_sibling_sections():
    chunks = apply_document_profile(
        [
            _doc("A" * 80, "白皮书 > 背景", 1),
            _doc("B" * 80, "白皮书 > 目标", 2),
        ],
        DocumentProfile.SECTION_BASED,
    )

    assert len(chunks) == 2
    assert [c.metadata["section_path"] for c in chunks] == ["白皮书 > 背景", "白皮书 > 目标"]


def test_section_based_splits_single_oversized_body_within_policy_limit():
    policy = ChunkPolicy(
        profile=DocumentProfile.SECTION_BASED,
        target_max=80,
        soft_max=120,
        command_follow_max=150,
    )

    chunks = apply_document_profile(
        [_doc("段落内容" * 100, "白皮书 > 背景", 1)],
        DocumentProfile.SECTION_BASED,
        policy=policy,
    )

    assert len(chunks) > 1
    assert all(len(chunk.page_content) <= policy.soft_max for chunk in chunks)
    assert {chunk.metadata["section_path"] for chunk in chunks} == {"白皮书 > 背景"}


def test_section_based_keeps_atomic_role_separate_and_preserves_role():
    command = _doc("systemctl restart demo", "运维", 2)
    command.metadata["content_role"] = "command"

    chunks = apply_document_profile(
        [
            _doc("前置说明" * 20, "运维", 1),
            command,
            _doc("执行结果" * 20, "运维", 3),
        ],
        DocumentProfile.SECTION_BASED,
    )

    assert [chunk.metadata["content_role"] for chunk in chunks] == [
        "ordinary_body",
        "command",
        "ordinary_body",
    ]


def test_technical_manual_keeps_bounded_short_leaf_merge():
    chunks = apply_document_profile(
        [
            _doc("运行环境。" * 15, "概述 > 运行环境", 1),
            _doc("密钥管理。" * 15, "概述 > 密钥管理", 2),
        ],
        DocumentProfile.TECHNICAL_MANUAL,
    )

    assert len(chunks) == 1
    assert chunks[0].metadata["source_element_ids"] == ["el_1", "el_2"]


def test_technical_manual_policy_drives_actual_merge_thresholds():
    docs = [
        _doc("A" * 150, "概览 > 运行环境", 1),
        _doc("B" * 150, "概览 > 密钥管理", 2),
    ]
    policy = ChunkPolicy(
        profile=DocumentProfile.TECHNICAL_MANUAL,
        target_min=100,
        target_max=800,
        soft_max=1200,
        command_follow_max=1500,
    )

    chunks = apply_document_profile(
        docs,
        DocumentProfile.TECHNICAL_MANUAL,
        policy=policy,
    )

    assert len(chunks) == 2
    assert all(chunk.metadata["chunk_policy_id"] == policy.policy_id for chunk in chunks)


def test_procedure_does_not_cross_top_level_steps_and_attaches_command():
    chunks = apply_document_profile(
        [
            _doc("1. 安装服务", "部署", 1),
            _doc("执行以下命令", "部署", 2),
            _doc("systemctl enable demo", "部署", 3),
            _doc("2. 验证服务", "部署", 4),
            _doc("检查服务状态", "部署", 5),
        ],
        DocumentProfile.PROCEDURE,
    )

    assert len(chunks) == 2
    assert "systemctl enable demo" in chunks[0].page_content
    assert chunks[0].metadata["content_role"] == "step"
    assert chunks[0].metadata["related_element_ids"] == ["el_2", "el_3"]
    assert "2. 验证服务" in chunks[1].page_content


def test_procedure_honors_canonical_step_role_without_number_text():
    first = _doc("安装服务", "部署", 1)
    second = _doc("验证服务", "部署", 2)
    first.metadata["content_role"] = "step"
    second.metadata["content_role"] = "step"

    chunks = apply_document_profile([first, second], DocumentProfile.PROCEDURE)

    assert len(chunks) == 2


def test_procedure_splits_oversized_step_and_repeats_step_title():
    policy = ChunkPolicy(
        profile=DocumentProfile.PROCEDURE,
        target_max=100,
        soft_max=140,
        command_follow_max=180,
    )
    chunks = apply_document_profile(
        [
            _doc("1. 安装服务", "部署", 1),
            _doc("1.1 准备环境\n" + "准备说明" * 60, "部署", 2),
            _doc("systemctl enable demo", "部署", 3),
        ],
        DocumentProfile.PROCEDURE,
        policy=policy,
    )

    assert len(chunks) > 1
    assert all(chunk.page_content.startswith("1. 安装服务") for chunk in chunks)
    assert all(len(chunk.page_content) <= policy.soft_max for chunk in chunks)


def test_api_doc_never_crosses_endpoints_and_preserves_subroles():
    chunks = apply_document_profile(
        [
            _doc("GET /v1/users", "用户接口", 1),
            _doc("请求参数：page", "用户接口 > 请求参数", 2),
            _doc("响应参数：items", "用户接口 > 响应参数", 3),
            _doc("POST /v1/users", "用户接口", 4),
            _doc("请求示例：{}", "用户接口 > 示例", 5),
        ],
        DocumentProfile.API_DOC,
    )

    assert len(chunks) == 2
    assert chunks[0].metadata["content_role"] == "api_endpoint"
    assert chunks[0].metadata["endpoint"] == "GET /v1/users"
    assert chunks[0].metadata["related_element_ids"] == ["el_2", "el_3"]
    assert chunks[1].metadata["endpoint"] == "POST /v1/users"


def test_api_doc_splits_oversized_endpoint_by_subrole_and_repeats_endpoint():
    policy = ChunkPolicy(
        profile=DocumentProfile.API_DOC,
        target_max=100,
        soft_max=140,
        command_follow_max=180,
    )
    chunks = apply_document_profile(
        [
            _doc("GET /v1/users", "用户接口", 1),
            _doc("请求参数\n" + "page 参数说明" * 30, "用户接口 > 请求参数", 2),
            _doc("响应参数\n" + "items 字段说明" * 30, "用户接口 > 响应参数", 3),
        ],
        DocumentProfile.API_DOC,
        policy=policy,
    )

    assert len(chunks) > 1
    assert all(chunk.page_content.startswith("GET /v1/users") for chunk in chunks)
    assert all(chunk.metadata["endpoint"] == "GET /v1/users" for chunk in chunks)
    assert all(len(chunk.page_content) <= policy.soft_max for chunk in chunks)
    assert {chunk.metadata["content_role"] for chunk in chunks} >= {"api_request", "api_response"}


def test_api_doc_keeps_stamputil_signature_params_and_sample_atomic():
    sample = """StampUtil.createElementLineParams(params);

参数：
params={
linewidth:线宽度
linecolor: 边线颜色
}

代码示例：
let paramsObj = await StampUtil.createElementLineParams({
  guid: guid,
  name: "PolylineSearch",
  linewidth: 3,
  linecolor: "0xffff0000",
});
StampUtil.showHighlightObj(paramsObj.guid);
"""
    chunks = apply_document_profile(
        [_doc(sample, "标绘模块 > 创建折线", 1)],
        DocumentProfile.API_DOC,
    )

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.metadata["endpoint"] == "StampUtil.createElementLineParams"
    assert chunk.metadata["api_name"] == "createElementLineParams"
    assert chunk.metadata["content_role"] == "api_endpoint"
    assert chunk.metadata["content_type"] == "code"
    assert "linewidth" in chunk.page_content
    assert "linecolor" in chunk.page_content
    assert "代码示例" in chunk.page_content
    assert "showHighlightObj" in chunk.page_content


def test_api_doc_splits_multiple_stamputil_signatures_in_one_section():
    text = """StampUtil.transToSphr(params);

参数：x,y

StampUtil.transToXy(params);

参数：lon,lat
"""
    chunks = apply_document_profile(
        [_doc(text, "坐标值转换", 1)],
        DocumentProfile.API_DOC,
    )

    assert len(chunks) == 2
    assert [c.metadata["api_name"] for c in chunks] == ["transToSphr", "transToXy"]
    assert chunks[0].metadata["endpoint"] == "StampUtil.transToSphr"
    assert "transToXy" not in chunks[0].page_content
    assert "transToSphr" not in chunks[1].page_content


def test_api_doc_recognizes_factory_create_signature():
    text = """earth.Factory.CreateElementLine(params);

参数：
lineColor
lineWidth
"""
    chunks = apply_document_profile(
        [_doc(text, "创建折线", 1)],
        DocumentProfile.API_DOC,
    )
    assert len(chunks) == 1
    assert chunks[0].metadata["endpoint"] == "earth.Factory.CreateElementLine"
    assert chunks[0].metadata["api_name"] == "CreateElementLine"


def test_table_doc_uses_explicit_element_relationships_and_repeats_header():
    policy = ChunkPolicy(
        profile=DocumentProfile.TABLE_DOC,
        target_max=80,
        soft_max=120,
        table_row_group_max=80,
    )
    table = "| 字段 | 类型 | 说明 |\n| --- | --- | --- |\n" + "\n".join(
        f"| field_{i} | varchar | 说明{i} |" for i in range(8)
    )
    chunks = apply_document_profile(
        [
            _doc("表 1：用户表", "数据库", 1),
            _doc(table, "数据库", 2, content_type="table"),
            _doc("字段补充说明", "数据库", 3),
        ],
        DocumentProfile.TABLE_DOC,
        policy=policy,
    )

    table_chunks = [c for c in chunks if c.metadata["content_role"] == "table"]
    assert len(table_chunks) > 1
    assert all(c.page_content.startswith("| 字段 | 类型 | 说明 |") for c in table_chunks)
    assert all(c.metadata["related_element_ids"] == ["el_1", "el_3"] for c in table_chunks)
    assert all(c.metadata["related_table_ids"] == [] for c in table_chunks)
    assert chunks[0].metadata["content_role"] == "table_title"
    assert chunks[0].metadata["related_element_ids"] == ["el_2"]
    assert chunks[-1].metadata["content_role"] == "table_context"
    assert chunks[-1].metadata["related_element_ids"] == ["el_2"]


def test_excel_adapter_assigns_sheet_section_and_complete_lineage(tmp_path):
    from openpyxl import Workbook
    from rag_knowledge.services.loader import FileLoader

    path = tmp_path / "config.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "系统配置"
    sheet.append(["配置项", "默认值"])
    sheet.append(["host", "127.0.0.1"])
    workbook.save(path)
    workbook.close()

    docs = FileLoader._load_excel(object.__new__(FileLoader), str(path))

    assert len(docs) == 1
    meta = docs[0].metadata
    assert meta["section_path"] == "系统配置"
    assert meta["content_role"] == "table"
    assert meta["source_element_ids"]
    assert meta["source_raw_block_ids"]
    assert meta["source_document_id"]
    assert meta["source_snapshot_hash"]


def test_record_list_splits_records_and_never_combines_them():
    chunks = apply_document_profile(
        [
            _doc("1. 登录失败\n补充：仅首次登录出现\n2. 地图空白\n补充：刷新后恢复", "问题清单", 1),
        ],
        DocumentProfile.RECORD_LIST,
    )

    assert len(chunks) == 2
    assert [c.metadata["content_role"] for c in chunks] == ["record", "record"]
    assert "首次登录" in chunks[0].page_content
    assert "地图空白" not in chunks[0].page_content
    assert "刷新后恢复" in chunks[1].page_content


def test_record_list_splits_oversized_record_and_repeats_record_title():
    policy = ChunkPolicy(
        profile=DocumentProfile.RECORD_LIST,
        target_max=90,
        soft_max=130,
        command_follow_max=160,
    )
    chunks = apply_document_profile(
        [_doc("1. 登录失败\n" + "问题补充说明" * 80, "问题清单", 1)],
        DocumentProfile.RECORD_LIST,
        policy=policy,
    )

    assert len(chunks) > 1
    assert all(chunk.page_content.startswith("1. 登录失败") for chunk in chunks)
    assert all(len(chunk.page_content) <= policy.soft_max for chunk in chunks)


@pytest.mark.parametrize("profile", list(DocumentProfile))
def test_common_finalizer_writes_profile_policy_identity_and_adjacency(profile):
    chunks = apply_document_profile([_doc("有效正文" * 20, "章节", 1)], profile)

    assert chunks
    meta = chunks[0].metadata
    assert meta["document_profile"] == profile.value
    assert meta["chunk_policy_id"].startswith("cp_")
    assert meta["content_role"]
    assert isinstance(meta["related_element_ids"], list)
    assert meta["section_id"].startswith("sec_")
    assert meta["chunk_uid"].startswith("chk_")
    assert meta["prev_chunk_id"] is None
    assert meta["next_chunk_id"] is None
