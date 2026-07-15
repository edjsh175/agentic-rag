from pathlib import Path
import zipfile

import fitz

from rag_knowledge.services.canonical_adapters import load_canonical_documents


def test_html_adapter_removes_chrome_and_restores_heading_hierarchy(tmp_path: Path):
    path = tmp_path / "article.html"
    path.write_text(
        "<html><nav>导航</nav><script>bad()</script><h1>部署指南</h1>"
        "<p>正文说明</p><h2>验证</h2><p>检查结果</p></html>",
        encoding="utf-8",
    )

    docs = load_canonical_documents(path)

    assert [doc.metadata["section_path"] for doc in docs] == ["部署指南", "部署指南 > 验证"]
    assert "导航" not in "".join(doc.page_content for doc in docs)
    assert "bad()" not in "".join(doc.page_content for doc in docs)


def test_pptx_adapter_uses_slide_title_as_section_and_keeps_notes(tmp_path: Path):
    path = tmp_path / "concepts.pptx"
    slide = '<p:sld xmlns:p="p" xmlns:a="a"><a:t>三维基本概念</a:t><a:t>坐标系统说明</a:t></p:sld>'
    notes = '<p:notes xmlns:p="p" xmlns:a="a"><a:t>讲者备注</a:t></p:notes>'
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ppt/slides/slide1.xml", slide)
        archive.writestr("ppt/notesSlides/notesSlide1.xml", notes)

    docs = load_canonical_documents(path)

    assert len(docs) == 1
    assert docs[0].metadata["section_path"] == "三维基本概念"
    assert "坐标系统说明" in docs[0].page_content
    assert "讲者备注" in docs[0].page_content
    assert docs[0].metadata["content_role"] == "ordinary_body"


def test_sql_and_config_adapters_create_atomic_sections(tmp_path: Path):
    sql = tmp_path / "route.sql"
    sql.write_text("CREATE TABLE roads(id int);\nCREATE FUNCTION route() RETURNS void AS $$ x $$ LANGUAGE sql;", encoding="utf-8")
    config = tmp_path / "server.ini"
    config.write_text("[server]\nport=8080\n[database]\nhost=localhost", encoding="utf-8")

    sql_docs = load_canonical_documents(sql)
    config_docs = load_canonical_documents(config)

    assert len(sql_docs) == 2
    assert all(doc.metadata["content_role"] == "code" for doc in sql_docs)
    assert [doc.metadata["section_path"] for doc in config_docs] == ["server", "database"]
    assert all(doc.metadata["content_role"] == "code" for doc in config_docs)


def test_pdf_adapter_marks_textless_pages_for_ocr_instead_of_plain_text_gate(tmp_path: Path):
    path = tmp_path / "scan.pdf"
    pdf = fitz.open()
    pdf.new_page()
    pdf.save(path)
    pdf.close()

    docs = load_canonical_documents(path)

    assert len(docs) == 1
    assert docs[0].metadata["content_role"] == "ocr_required"
    assert docs[0].metadata["ocr_status"] == "required"
    assert docs[0].metadata["page_number"] == 1
