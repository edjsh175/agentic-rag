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


def test_governance_repairs_uncited_with_subject_context_and_downgrades_complete_claim():
    source = _source(
        1,
        "PipelineBuilder 工程设置说明",
        section_path="PipelineBuilder > 工程设置",
    )
    repaired = govern_answer("PipelineBuilder 属于工具层。", "PipelineBuilder 属于什么", [source])
    assert "PipelineBuilder 属于工具层。" in repaired
    assert "[1]" in repaired
    assert "补充" in repaired
    assert "部分相关内容" not in repaired
    assert "无法给出" not in repaired

    miss_repaired = govern_answer(
        "当前知识库中未查询到相关内容。",
        "介绍一下 StampManager",
        [_source(2, "StampManager 部署步骤", section_path="Stamp服务部署 > StampManager部署")],
    )
    assert "StampManager" in miss_repaired
    assert "[2]" in miss_repaired
    assert "部分相关内容" in miss_repaired
    assert "相关原文要点" in miss_repaired
    assert "部署步骤" in miss_repaired

    thin = (
        "知识库中查到了pipeline的部分相关内容（如PipelineBuilder > 数据规范 > 管线点表），"
        "但未检索到关于「pipeline」的完整说明。[1]"
    )
    enriched = govern_answer(thin, "pipeline", [source])
    assert "相关原文要点" in enriched
    assert "PipelineBuilder 工程设置说明" in enriched

    governed = govern_answer("完整流程如下：[1]", "完整安装流程是什么？", [source])
    assert "不能据此确认完整流程" in governed


def test_governance_keeps_hard_fail_when_context_empty():
    assert "无法给出" in govern_answer("步骤一：安装", "如何安装？", [])


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


def test_governance_rejects_hallucinated_body_when_uncited_and_disconnected():
    source = _source(1, "管线点表字段说明：管点编号、地面高程、特征、附属物设施", section_path="PipelineBuilder > 数据规范 > 管线点表")
    hallucinated_answer = (
        "PipelineBuilder 是一个由 Palantir 开发的可视化数据集成平台，"
        "支持解析 XML、JSON、PDF 数据集并进行 joins 和 unions 操作。"
    )
    result = govern_answer(hallucinated_answer, "pipelinebuilder", [source])
    # The hallucinated Palantir content must be discarded
    assert "Palantir" not in result
    assert "XML" not in result
    # It must fallback to grounded context bullets
    assert "部分相关内容" in result
    assert "相关原文要点" in result
    assert "管线点表" in result
    assert "[1]" in result


def test_verify_grounding_rejects_external_hallucinations_even_with_fake_citation():
    from rag_knowledge.services.evidence_pack import verify_grounding

    source = _source(1, "StampGIS 实现了二三维一体化，融合原生 GIS 引擎与游戏引擎。", section_path="产品概述 > 简介")
    # 模型输出中伪造了 OpenGL ES、STUN/TURN 等未在证据中存在的外部技术
    hallucinated_answer = (
        "StampGIS 基于 OpenGL ES 规范使用 WebGL 进行渲染，并通过 STUN/TURN 服务器支持 WebRTC 实时通信 [1]。"
    )
    verdict = verify_grounding(hallucinated_answer, [source])
    assert not verdict.ok
    assert any("unsupported_latin_term" in r for r in verdict.reasons)


def test_verify_grounding_passes_valid_rewriting():
    from rag_knowledge.services.evidence_pack import verify_grounding

    source = _source(1, "StampServer 默认服务端口为 8080，管理端口为 8081。", section_path="服务部署 > 端口配置")
    valid_answer = "StampServer 的默认服务端口是 8080，而管理端口配置为 8081 [1]。"
    verdict = verify_grounding(valid_answer, [source])
    assert verdict.ok
    assert len(verdict.unsupported_segments) == 0


def test_synthesize_grounded_fallback_produces_deterministic_summary():
    from rag_knowledge.services.evidence_pack import synthesize_grounded_fallback

    source1 = _source(1, "StampServer 服务端口配置为 8080。", section_path="服务部署 > 端口配置")
    source2 = _source(2, "StampTools 支持二三维数据转换与优化。", section_path="产品体系 > 数据工具")
    fallback = synthesize_grounded_fallback([source1, source2], "Stamp 产品配置")
    assert "8080" in fallback
    assert "[1]" in fallback
    assert "二三维数据转换" in fallback
    assert "[2]" in fallback
    assert "根据知识库现有相关文档" in fallback


def test_verify_grounding_strictly_checks_per_citation_scope():
    from rag_knowledge.services.evidence_pack import verify_grounding

    doc1 = _source(1, "WebGL 技术用于前端轻量级渲染展示。", section_path="技术架构 > 渲染")
    doc2 = _source(2, "WebRTC 协议用于音视频实时通信与流媒体传输。", section_path="技术架构 > 通信")

    # 1. 跨文档拼装且伪造 STUN 实体
    fake_relation_answer = "StampGIS 通过 STUN 服务器建立 WebRTC 连接 [2]。"
    verdict1 = verify_grounding(fake_relation_answer, [doc1, doc2])
    assert not verdict1.ok
    assert any("unsupported_latin_term" in r or "unsupported_semantic_relation" in r for r in verdict1.reasons)

    # 2. 无引用的技术断言
    uncited_answer = "WebGL 和 WebRTC 属于完全不同的技术路线。"
    verdict2 = verify_grounding(uncited_answer, [doc1, doc2])
    assert not verdict2.ok
    assert any("missing_citation" in r or "missing_all_citations" in r for r in verdict2.reasons)

    # 3. 严格基于具体引用的合法事实陈述
    valid_answer = "系统前端采用 WebGL 技术进行轻量级渲染展示 [1]，并通过 WebRTC 协议实现音视频实时通信 [2]。"
    verdict3 = verify_grounding(valid_answer, [doc1, doc2])
    assert verdict3.ok


def test_verify_grounding_rejects_relationship_fabricated_from_separate_chunks():
    from rag_knowledge.services.evidence_pack import verify_grounding

    doc1 = _source(1, "WebGL 技术用于前端轻量级渲染展示。")
    doc2 = _source(2, "WebRTC 协议用于音视频实时通信与流媒体传输。")

    fabricated = "WebGL 和 WebRTC 属于不同技术路线，分别用于图形渲染和实时通信。[1][2]"
    assert not verify_grounding(fabricated, [doc1, doc2]).ok

    separately_cited = "WebGL 用于前端轻量级渲染展示。[1] WebRTC 用于音视频实时通信。[2]"
    assert verify_grounding(separately_cited, [doc1, doc2]).ok
