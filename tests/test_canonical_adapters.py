import zipfile
from pathlib import Path
import fitz
import pytest

from rag_knowledge.services.canonical_adapters import load_canonical_result, load_canonical_documents


def test_pdf_adapter_handles_textless_pages_and_partial_text(tmp_path: Path):
    path = tmp_path / "test_doc.pdf"
    pdf = fitz.open()

    # Page 1: Has text
    page1 = pdf.new_page()
    page1.insert_text((72, 72), "This is page 1 text.")

    # Page 2: Textless
    pdf.new_page()

    # Page 3: Has text and embedded image
    page3 = pdf.new_page()
    page3.insert_text((72, 72), "This is page 3 text.")
    # Add a mock image
    rect = fitz.Rect(100, 100, 200, 200)
    pix = fitz.Pixmap(fitz.csRGB, (0, 0, 10, 10), 1)
    page3.insert_image(rect, pixmap=pix)

    pdf.save(path)
    pdf.close()

    result = load_canonical_result(path)

    # Assertions
    # Page 1 and Page 3 text should be merged because they belong to the same section
    assert len(result.documents) == 1
    doc = result.documents[0]
    assert "This is page 1 text." in doc.page_content
    assert "This is page 3 text." in doc.page_content
    assert doc.metadata["page_start"] == 1
    assert doc.metadata["page_end"] == 3
    assert "OCR" not in doc.page_content

    # Verify decisions
    # Page 2 should have PDF_PAGE_REQUIRES_OCR
    ocr_dec = next(d for d in result.decisions if d.reason_code == "PDF_PAGE_REQUIRES_OCR")
    assert ocr_dec.locator == "page:2"

    # Page 3 should have EMBEDDED_MEDIA_PROCESSING_DEFERRED
    media_dec = next(d for d in result.decisions if d.reason_code == "EMBEDDED_MEDIA_PROCESSING_DEFERRED")
    assert media_dec.locator == "page:3"


def test_html_adapter_cleans_nav_and_returns_boundaries(tmp_path: Path):
    path = tmp_path / "article.html"
    path.write_text(
        "<html><nav>导航栏目</nav><script>console.log(1);</script><h1>部署指南</h1>"
        "<p>正文说明</p><h2>验证</h2><p>检查结果</p>"
        "<pre>code content</pre><table><tr><td>cell</td></tr></table>"
        "<img src='a.jpg' /></html>",
        encoding="utf-8",
    )

    result = load_canonical_result(path)

    assert len(result.documents) == 4 # Heading 1/2, pre, table
    assert [doc.metadata["section_path"] for doc in result.documents] == [
        "部署指南", "部署指南 > 验证", "部署指南 > 验证", "部署指南 > 验证"
    ]
    # Verify media decision
    assert len(result.decisions) == 1
    assert result.decisions[0].reason_code == "EMBEDDED_MEDIA_PROCESSING_DEFERRED"
    assert result.decisions[0].locator == "html:media:1"


def test_pptx_adapter_slide_sections_tables_and_charts(tmp_path: Path):
    path = tmp_path / "slides.pptx"
    # Create mock pptx zip file structure
    # slide1 has text shapes and relationships
    slide_xml = (
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<p:cSld><p:spTree>'
        '<p:sp>'
        '<p:nvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>'
        '<p:txBody><a:p><a:r><a:t>Slide Title</a:t></a:r></a:p></p:txBody>'
        '</p:sp>'
        '<p:sp>'
        '<p:spPr><a:xfrm><a:off x="100" y="200"/></a:xfrm></p:spPr>'
        '<p:txBody><a:p><a:pPr lstLvl="1"/><a:r><a:t>List Item</a:t></a:r></a:p></p:txBody>'
        '</p:sp>'
        '<a:tbl><a:tr><a:tc><a:t>table cell</a:t></a:tc></a:tr></a:tbl>'
        '</p:spTree></p:cSld></p:sld>'
    )

    rels_xml = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide" Target="../notesSlides/notesSlide1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/image1.png"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart" Target="../charts/chart1.xml"/>'
        '</Relationships>'
    )

    notes_xml = (
        '<p:notes xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>Speaker Notes</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld>'
        '</p:notes>'
    )

    chart_xml = (
        '<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart">'
        '<c:chart><c:title><c:tx><c:rich><a:p xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<a:r><a:t>Chart Title</a:t></a:r></a:p></c:rich></c:tx></c:title></c:chart>'
        '</c:chartSpace>'
    )

    with zipfile.ZipFile(path, "w") as z:
        z.writestr("ppt/slides/slide1.xml", slide_xml)
        z.writestr("ppt/slides/_rels/slide1.xml.rels", rels_xml)
        z.writestr("ppt/notesSlides/notesSlide1.xml", notes_xml)
        z.writestr("ppt/charts/chart1.xml", chart_xml)

    result = load_canonical_result(path)

    # 3 docs: slide body (which includes notes), chart, and table
    assert len(result.documents) == 3

    body_doc = next(doc for doc in result.documents if doc.metadata["content_role"] == "ordinary_body")
    assert body_doc.metadata["section_path"] == "Slide Title"
    assert "List Item" in body_doc.page_content
    assert "Speaker Notes" in body_doc.page_content

    table_doc = next(doc for doc in result.documents if doc.metadata["content_role"] == "table")
    assert "| table cell |" in table_doc.page_content

    chart_doc = next(doc for doc in result.documents if doc.metadata["content_role"] == "chart")
    assert "Chart Title" in chart_doc.page_content

    # 1 decision (image)
    assert len(result.decisions) == 1
    assert result.decisions[0].reason_code == "EMBEDDED_MEDIA_PROCESSING_DEFERRED"
    assert result.decisions[0].locator == "slide:1:image1.png"


def test_sql_adapter_splits_safely(tmp_path: Path):
    path = tmp_path / "queries.sql"
    sql = (
        "CREATE TABLE users (id int);\n"
        "CREATE FUNCTION get_name() RETURNS text AS $$\n"
        "BEGIN\n"
        "  RETURN 'Name; Test';\n"
        "END;\n"
        "$$ LANGUAGE plpgsql;\n"
        "SELECT * FROM users WHERE email = 'a;b';"
    )
    path.write_text(sql, encoding="utf-8")

    result = load_canonical_result(path)
    assert len(result.documents) == 3
    assert result.documents[0].metadata["section_path"] == "TABLE users"
    assert result.documents[1].metadata["section_path"] == "FUNCTION get_name"
    assert result.documents[2].metadata["section_path"] == "SQL 3"


def test_config_and_xml_adapters(tmp_path: Path):
    # Test INI config
    ini_path = tmp_path / "server.ini"
    ini_path.write_text(
        "global_var=1\n"
        "[server]\n"
        "port=8080\n"
        "[database]\n"
        "host=localhost",
        encoding="utf-8",
    )
    ini_res = load_canonical_result(ini_path)
    assert len(ini_res.documents) == 3
    assert [d.metadata["section_path"] for d in ini_res.documents] == ["global", "server", "database"]

    # Test XML
    xml_path = tmp_path / "config.xml"
    xml_path.write_text(
        "<config name='app'>"
        "  <service id='s1'>data1</service>"
        "  <service name='s2'>data2</service>"
        "</config>",
        encoding="utf-8",
    )
    xml_res = load_canonical_result(xml_path)
    assert len(xml_res.documents) == 2
    assert [d.metadata["section_path"] for d in xml_res.documents] == ["app > s1", "app > s2"]

    # Test Corrupted XML
    bad_xml_path = tmp_path / "bad.xml"
    bad_xml_path.write_text("<config><service>", encoding="utf-8")
    bad_res = load_canonical_result(bad_xml_path)
    assert len(bad_res.documents) == 0
    assert len(bad_res.decisions) == 1
    assert bad_res.decisions[0].reason_code == "FORMAT_PARSE_FAILED"
