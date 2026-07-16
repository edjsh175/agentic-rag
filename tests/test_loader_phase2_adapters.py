from pathlib import Path
import pytest
import fitz

from rag_knowledge.services.loader import FileLoader, FileLoadResult


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


def test_load_with_decisions_for_queued_and_media_files(tmp_path: Path, isolated_storage):
    isolated_storage()
    loader = FileLoader()

    # Legacy .doc file
    doc_path = tmp_path / "legacy.doc"
    doc_path.write_bytes(b"doc content")
    res_doc = loader.load_with_decisions(str(doc_path))
    assert isinstance(res_doc, FileLoadResult)
    assert len(res_doc.chunks) == 0
    assert len(res_doc.decisions) == 1
    assert res_doc.decisions[0].reason_code == "LEGACY_DOC_REQUIRES_CONVERSION"
    assert res_doc.decisions[0].status == "queued"

    # Image file
    img_path = tmp_path / "image.png"
    img_path.write_bytes(b"png content")
    res_img = loader.load_with_decisions(str(img_path))
    assert len(res_img.chunks) == 0
    assert len(res_img.decisions) == 1
    assert res_img.decisions[0].reason_code == "MEDIA_PROCESSING_DEFERRED"

    # Video file
    vid_path = tmp_path / "video.mp4"
    vid_path.write_bytes(b"mp4 content")
    res_vid = loader.load_with_decisions(str(vid_path))
    assert len(res_vid.chunks) == 0
    assert len(res_vid.decisions) == 1
    assert res_vid.decisions[0].reason_code == "MEDIA_PROCESSING_DEFERRED"


def test_load_with_decisions_partial_pdf(tmp_path: Path, isolated_storage):
    isolated_storage()
    loader = FileLoader()

    path = tmp_path / "partial.pdf"
    pdf = fitz.open()
    # page 1 has text
    page1 = pdf.new_page()
    page1.insert_text((72, 72), "This is text.")
    # page 2 is blank
    pdf.new_page()
    pdf.save(path)
    pdf.close()

    res = loader.load_with_decisions(str(path))
    assert len(res.chunks) == 1
    assert len(res.decisions) == 1
    assert res.decisions[0].reason_code == "PDF_PAGE_REQUIRES_OCR"
