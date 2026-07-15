from pathlib import Path

import pytest

from rag_knowledge.services.loader import FileLoader


def test_loader_routes_html_through_canonical_adapter_and_profile(tmp_path: Path, isolated_storage):
    isolated_storage()
    path = tmp_path / "api.html"
    path.write_text("<h1>GET /v1/users</h1><p>请求参数：page</p>", encoding="utf-8")
    loader = FileLoader()

    chunks, category = loader.load(str(path), document_profile="api_doc")

    assert category == "text"
    assert chunks[0].metadata["document_profile"] == "api_doc"
    assert chunks[0].metadata["content_role"] == "api_endpoint"


def test_pptx_is_detected_as_text_document():
    assert FileLoader.detect_category("concepts.pptx") == "text"


def test_legacy_doc_is_sent_to_conversion_queue_not_flat_extracted(tmp_path: Path, isolated_storage):
    isolated_storage()
    path = tmp_path / "legacy.doc"
    path.write_bytes(b"not-a-real-doc")
    loader = FileLoader()

    with pytest.raises(ValueError, match="LEGACY_DOC_REQUIRES_CONVERSION"):
        loader.load(str(path))
