from pathlib import Path

from scripts.inventory_document_profiles import build_inventory, support_for_suffix


def test_support_matrix_has_explicit_status_or_reason_for_every_suffix():
    assert support_for_suffix(".docx") == ("phase1_supported", "")
    assert support_for_suffix(".pptx") == ("phase2_supported", "")
    assert support_for_suffix(".doc") == ("manual_queue", "LEGACY_DOC_REQUIRES_CONVERSION")
    assert support_for_suffix(".jar") == ("excluded", "DEPENDENCY_ASSET")
    assert support_for_suffix(".rar") == ("excluded", "ARCHIVE_ASSET")
    assert support_for_suffix(".unknown") == ("excluded", "UNSUPPORTED_EXTENSION")


def test_inventory_covers_every_file_and_only_returns_supported_profiles(tmp_path: Path):
    (tmp_path / "手册.docx").write_bytes(b"PK")
    (tmp_path / "接口说明.md").write_text("# GET /v1/users\n请求参数", encoding="utf-8")
    (tmp_path / "三维数据发布服务接口.docx").write_bytes(b"PK")
    (tmp_path / "数据库密码修改流程.docx").write_bytes(b"PK")
    (tmp_path / "asset.jar").write_bytes(b"jar")

    rows = build_inventory(tmp_path)

    assert {row["path"] for row in rows} == {
        "asset.jar", "手册.docx", "接口说明.md", "三维数据发布服务接口.docx", "数据库密码修改流程.docx"
    }
    jar = next(row for row in rows if row["path"] == "asset.jar")
    assert jar["recommended_profile"] == ""
    assert jar["reason_code"] == "DEPENDENCY_ASSET"
    api = next(row for row in rows if row["path"] == "接口说明.md")
    assert api["recommended_profile"] == "api_doc"
    assert api["structure_features"]["http_endpoint_count"] == 1
    assert next(row for row in rows if row["path"] == "三维数据发布服务接口.docx")["recommended_profile"] == "api_doc"
    assert next(row for row in rows if row["path"] == "数据库密码修改流程.docx")["recommended_profile"] == "procedure"
