from rag_knowledge.services.evidence_pack import (
    build_evidence_pack,
    citation_ids,
    cited_sources,
)


def _source(index, content, **metadata):
    return {
        "content": content,
        "metadata": {
            "citation_id": index,
            "source": "manual.docx",
            **metadata,
        },
    }


def test_evidence_pack_keeps_chunk_section_document_lineage_and_trim_reason():
    first = _source(
        1,
        "tls-listening-port: 5439",
        section_id="s-1",
        section_path="部署 > TLS",
        chunk_id="c-1",
    )
    second = _source(
        2,
        "tls-listening-port: 5349",
        section_id="s-2",
        section_path="部署 > 端口",
        chunk_id="c-2",
    )
    pack = build_evidence_pack("存在冲突，请核对。[1]", [first, second], [first])

    assert pack["cited"][0]["chunk_id"] == "c-1"
    assert pack["retrieved_uncited"][0]["drop_reason"] == "budget_trim"
    assert {v["value"] for v in pack["conflicts"][0]["values"]} == {
        "5439",
        "5349",
    }


def test_evidence_pack_detects_tls_port_conflict_across_config_and_table_text():
    config = _source(1, "tls-listening-port=5349")
    table = _source(2, "网络穿透服务 | TLS端口 | 5439")

    pack = build_evidence_pack(
        "端口冲突，请核对。[1][2]",
        [config, table],
        [config, table],
    )

    conflict = next(item for item in pack["conflicts"] if item["key"] == "tls_port")
    assert {value["value"] for value in conflict["values"]} == {"5349", "5439"}
    assert len(conflict["values"]) == 2


def test_citation_ids_extraction():
    assert citation_ids("支持发布 [1]，也支持管理 (2)。[3]") == {1, 2, 3}
    assert citation_ids("无引用文本") == set()


def test_cited_sources_filtering():
    doc1 = _source(1, "doc 1")
    doc2 = _source(2, "doc 2")
    doc3 = _source(3, "doc 3")
    cited = cited_sources("仅引用了 [1] 和 [3]。", [doc1, doc2, doc3])
    assert len(cited) == 2
    assert [d["metadata"]["citation_id"] for d in cited] == [1, 3]
