from rag_knowledge.services.evidence_pack import build_evidence_pack, govern_answer


def _source(index, content, **metadata):
    return {"content": content, "metadata": {"citation_id": index, "source": "manual.docx", **metadata}}


def test_evidence_pack_keeps_chunk_section_document_lineage_and_trim_reason():
    first = _source(1, "tls-listening-port: 5439", section_id="s-1", section_path="部署 > TLS", chunk_id="c-1")
    second = _source(2, "tls-listening-port: 5349", section_id="s-2", section_path="部署 > 端口", chunk_id="c-2")
    pack = build_evidence_pack("存在冲突，请核对。[1]", [first, second], [first])
    assert pack["cited"][0]["chunk_id"] == "c-1"
    assert pack["retrieved_uncited"][0]["drop_reason"] == "budget_trim"
    assert {v["value"] for v in pack["conflicts"][0]["values"]} == {"5439", "5349"}


def test_governance_rejects_uncited_and_downgrades_complete_claim():
    source = _source(1, "步骤一：安装")
    assert "无法给出" in govern_answer("步骤一：安装", "如何安装？", [source])
    governed = govern_answer("完整流程如下：[1]", "完整安装流程是什么？", [source])
    assert "不能据此确认完整流程" in governed


def test_governance_lists_conflicting_configuration_values_with_citations():
    first = _source(1, "tls-listening-port: 5439")
    second = _source(2, "tls-listening-port: 5349")
    governed = govern_answer("使用 5439 [1]。", "端口是多少？", [first, second])
    assert "5439 [1]" in governed
    assert "5349 [2]" in governed
    assert "请核对原文" in governed


def test_evidence_pack_detects_tls_port_conflict_across_config_and_table_text():
    config = _source(1, "tls-listening-port=5349")
    table = _source(2, "网络穿透服务 | TLS端口 | 5439")

    pack = build_evidence_pack("端口冲突，请核对。[1][2]", [config, table], [config, table])

    conflict = next(item for item in pack["conflicts"] if item["key"] == "tls_port")
    assert {value["value"] for value in conflict["values"]} == {"5349", "5439"}
    assert len(conflict["values"]) == 2
