from rag_knowledge.services.answer_finalizer import AnswerFinalizer
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


def test_governance_discards_uncited_body_and_downgrades_complete_claim():
    source = _source(
        1,
        "PipelineBuilder 工程设置说明",
        section_path="PipelineBuilder > 工程设置",
    )
    repaired = govern_answer("PipelineBuilder 属于工具层。", "PipelineBuilder 属于什么", [source])
    assert "PipelineBuilder 属于工具层。" not in repaired
    assert "[1]" in repaired
    assert "部分相关内容" in repaired
    assert "相关原文要点" in repaired

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


def test_answer_finalizer_strict_mode_blocks_hallucinated_candidate():
    source = _source(
        1,
        "StampGIS 实现了二三维一体化，融合原生 GIS 引擎与游戏引擎。",
        section_path="产品概述 > 简介",
    )
    candidate = "StampGIS 基于 OpenGL ES 规范使用 WebGL 进行渲染 [1]。"

    finalized = AnswerFinalizer().finalize(
        candidate,
        "StampGIS 使用什么技术？",
        [source],
        allow_general_knowledge=False,
    )

    assert "OpenGL ES" not in finalized.answer
    assert "StampGIS 实现了二三维一体化" in finalized.answer
    assert finalized.grounding["verdict"] == "fail"
    assert finalized.grounding["final_mode"] == "deterministic_fallback"
    assert finalized.grounding["fallback_used"] is True


def test_answer_finalizer_strict_mode_publishes_verified_candidate():
    source = _source(
        1,
        "StampServer 默认服务端口为 8080，管理端口为 8081。",
        section_path="服务部署 > 端口配置",
    )
    candidate = "StampServer 的默认服务端口是 8080，而管理端口配置为 8081 [1]。"

    finalized = AnswerFinalizer().finalize(
        candidate,
        "StampServer 的端口是什么？",
        [source],
        allow_general_knowledge=False,
    )

    assert finalized.answer == candidate
    assert finalized.grounding["verdict"] == "pass"
    assert finalized.grounding["fallback_used"] is False


def test_answer_finalizer_retries_once_before_fallback():
    source = _source(
        1,
        "StampServer 默认服务端口为 8080，管理端口为 8081。",
        section_path="服务部署 > 端口配置",
    )
    candidate = "StampServer 使用 React 管理 8080 端口 [1]。"
    retried = "StampServer 的默认服务端口为 8080，管理端口为 8081 [1]。"
    calls = []

    def retry(verdict):
        calls.append(verdict)
        return retried

    finalized = AnswerFinalizer().finalize(
        candidate,
        "StampServer 的端口是什么？",
        [source],
        allow_general_knowledge=False,
        retry_candidate=retry,
    )

    assert finalized.answer == retried
    assert len(calls) == 1
    assert finalized.grounding["final_mode"] == "grounded_retry"
    assert finalized.grounding["candidate_attempts"] == 2
    assert [item["verdict"] for item in finalized.grounding["attempts"]] == ["fail", "pass"]


def test_answer_finalizer_retry_failure_fails_closed_to_deterministic_fallback():
    source = _source(
        1,
        "StampServer 默认服务端口为 8080。",
        section_path="服务部署 > 端口配置",
    )
    candidate = "StampServer 使用 React 管理端口 [1]。"

    def retry(_verdict):
        raise RuntimeError("provider unavailable")

    finalized = AnswerFinalizer().finalize(
        candidate,
        "StampServer 的端口是什么？",
        [source],
        allow_general_knowledge=False,
        retry_candidate=retry,
    )

    assert "React" not in finalized.answer
    assert "8080" in finalized.answer
    assert finalized.grounding["final_mode"] == "deterministic_fallback"
    assert finalized.grounding["fallback_used"] is True
    assert finalized.grounding["attempts"][-1]["verdict"] == "error"
    assert any("grounded_retry_error" in reason for reason in finalized.grounding["reasons"])


def test_answer_finalizer_mixed_mode_keeps_uncited_general_knowledge_explicit():
    source = _source(1, "StampServer 默认服务端口为 8080。")
    finalized = AnswerFinalizer().finalize(
        "React 是一种前端技术。",
        "StampServer 和常见前端技术分别是什么？",
        [source],
        allow_general_knowledge=True,
    )

    assert "8080" in finalized.answer
    assert "## 通用知识补充" in finalized.answer
    assert "React 是一种前端技术" in finalized.answer
    assert "不属于知识库检索证据" in finalized.answer
    assert finalized.grounding["policy"] == "mixed"
    assert finalized.grounding["final_mode"] == "mixed_relabel"


def test_answer_finalizer_mixed_mode_strips_fake_kb_citation_from_general_section():
    source = _source(1, "StampServer 默认服务端口为 8080。")
    finalized = AnswerFinalizer().finalize(
        "React 是 StampServer 的官方前端框架 [1]。",
        "StampServer 使用什么前端框架？",
        [source],
        allow_general_knowledge=True,
    )

    general = finalized.answer.split("## 通用知识补充", 1)[1]
    assert "React" in general
    assert "[1]" not in general
    assert "8080" in finalized.answer.split("## 通用知识补充", 1)[0]


def test_answer_finalizer_mixed_mode_preserves_valid_kb_part_and_separates_general_part():
    source = _source(1, "StampServer 默认服务端口为 8080。")
    candidate = (
        "StampServer 默认服务端口为 8080 [1]。\n\n"
        "## 通用知识补充\nReact 常用于 Web 前端开发。"
    )
    finalized = AnswerFinalizer().finalize(
        candidate,
        "StampServer 的端口和前端技术是什么？",
        [source],
        allow_general_knowledge=True,
    )

    kb_part, general = finalized.answer.split("## 通用知识补充", 1)
    assert "8080 [1]" in kb_part
    assert "React 常用于 Web 前端开发" in general
    assert "[1]" not in general
    assert finalized.grounding["final_mode"] == "mixed_separated"
    assert finalized.grounding["fallback_used"] is False


def test_answer_finalizer_mixed_mode_keeps_fully_grounded_answer_unchanged():
    source = _source(1, "StampServer 默认服务端口为 8080。")
    candidate = "StampServer 默认服务端口为 8080 [1]。"
    finalized = AnswerFinalizer().finalize(
        candidate,
        "StampServer 默认端口是什么？",
        [source],
        allow_general_knowledge=True,
    )

    assert finalized.answer == candidate
    assert finalized.grounding["policy"] == "mixed"
    assert finalized.grounding["verdict"] == "pass"


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


def test_verify_grounding_rejects_capability_polarity_flip():
    from rag_knowledge.services.evidence_pack import verify_grounding

    source = _source(1, "PipelineBuilder 不支持离线发布。")
    verdict = verify_grounding("PipelineBuilder 支持离线发布 [1]。", [source])

    assert not verdict.ok
    assert "unsupported_semantic_operator" in verdict.reasons
    assert any("positive_capability" in segment for segment in verdict.unsupported_segments)


def test_verify_grounding_rejects_negative_capability_invented_from_positive_evidence():
    from rag_knowledge.services.evidence_pack import verify_grounding

    source = _source(1, "PipelineBuilder 支持离线发布。")
    verdict = verify_grounding("PipelineBuilder 不支持离线发布 [1]。", [source])

    assert not verdict.ok
    assert "unsupported_semantic_operator" in verdict.reasons
    assert any("negative_capability" in segment for segment in verdict.unsupported_segments)


def test_verify_grounding_rejects_sameness_claim_when_evidence_only_says_difference():
    from rag_knowledge.services.evidence_pack import verify_grounding

    source = _source(1, "WebGL 与 WebRTC 属于不同产品线，接口规范相互独立。")
    verdict = verify_grounding("WebGL 与 WebRTC 属于同一技术路线 [1]。", [source])

    assert not verdict.ok
    assert "unsupported_semantic_operator" in verdict.reasons
    assert any("sameness" in segment for segment in verdict.unsupported_segments)


def test_verify_grounding_rejects_dependency_relation_not_stated_by_evidence():
    from rag_knowledge.services.evidence_pack import verify_grounding

    source = _source(
        1,
        "StampWebGL 与 WebRTC 分属不同产品线；StampWebGL 提供 CreateElementLine 接口。",
    )
    verdict = verify_grounding(
        "StampWebGL 基于 WebRTC 实现 CreateElementLine [1]。",
        [source],
    )

    assert not verdict.ok
    assert "unsupported_semantic_operator" in verdict.reasons
    assert any("dependency" in segment for segment in verdict.unsupported_segments)


def test_verify_grounding_accepts_semantic_operator_when_evidence_has_same_relation():
    from rag_knowledge.services.evidence_pack import verify_grounding

    source = _source(1, "StampTools 支持二三维数据转换。")
    verdict = verify_grounding("StampTools 支持二三维数据转换 [1]。", [source])

    assert verdict.ok


def test_verify_grounding_rejects_operator_borrowed_from_different_chunk():
    from rag_knowledge.services.evidence_pack import verify_grounding

    doc1 = _source(1, "WebGL 与 WebRTC 均出现在浏览器端产品说明中。")
    doc2 = _source(2, "ActiveX 与 CloudRender 属于不同产品线。")
    verdict = verify_grounding("WebGL 与 WebRTC 属于不同产品线 [1][2]。", [doc1, doc2])

    assert not verdict.ok
    assert "unsupported_semantic_operator" in verdict.reasons
    assert any("difference" in segment for segment in verdict.unsupported_segments)


def test_verify_grounding_rejects_positive_relation_borrowed_from_other_clause_in_same_chunk():
    from rag_knowledge.services.evidence_pack import verify_grounding

    source = _source(
        1,
        "PipelineBuilder 支持在线发布，但是不支持离线发布。",
    )
    verdict = verify_grounding("PipelineBuilder 支持离线发布 [1]。", [source])

    assert not verdict.ok
    assert "unsupported_semantic_operator" in verdict.reasons
    assert any("positive_capability" in segment for segment in verdict.unsupported_segments)


def test_verify_grounding_rejects_reversed_directional_relations():
    from rag_knowledge.services.evidence_pack import verify_grounding

    cases = [
        ("PipelineBuilder 依赖 StampServer。", "StampServer 依赖 PipelineBuilder [1]。"),
        ("PipelineBuilder 属于 StampTools。", "StampTools 属于 PipelineBuilder [1]。"),
        ("PipelineBuilder 性能高于 PipelineWebGL。", "PipelineWebGL 性能高于 PipelineBuilder [1]。"),
        ("必须先启动 DataServer，再启动 StampServer。", "必须先启动 StampServer，再启动 DataServer [1]。"),
    ]

    for evidence, answer in cases:
        verdict = verify_grounding(answer, [_source(1, evidence)])
        assert not verdict.ok, (evidence, answer)
        assert "unsupported_directional_relation" in verdict.reasons


def test_verify_grounding_accepts_equivalent_inverse_comparison_wording():
    from rag_knowledge.services.evidence_pack import verify_grounding

    source = _source(1, "PipelineBuilder 性能高于 PipelineWebGL。")
    verdict = verify_grounding("PipelineWebGL 性能低于 PipelineBuilder [1]。", [source])

    assert verdict.ok


def test_verify_grounding_rejects_reversed_causality():
    from rag_knowledge.services.evidence_pack import verify_grounding

    source = _source(1, "配置缺失会导致 PipelineBuilder 启动失败。")
    verdict = verify_grounding("PipelineBuilder 启动失败会导致配置缺失 [1]。", [source])

    assert not verdict.ok
    assert "unsupported_causal_direction" in verdict.reasons


def test_verify_grounding_rejects_erased_condition_scope_but_keeps_conditioned_claim():
    from rag_knowledge.services.evidence_pack import verify_grounding

    source = _source(1, "启用离线模式时，StampServer 支持本地缓存。")
    erased = verify_grounding("StampServer 支持本地缓存 [1]。", [source])
    preserved = verify_grounding("启用离线模式时，StampServer 支持本地缓存 [1]。", [source])

    assert not erased.ok
    assert "unsupported_condition_scope" in erased.reasons
    assert preserved.ok


def test_verify_grounding_rejects_chinese_direction_and_scope_reversals():
    from rag_knowledge.services.evidence_pack import verify_grounding

    rejected_cases = [
        ("数据服务依赖缓存服务。", "缓存服务依赖数据服务 [1]。"),
        ("数据管理归属功能区。", "功能区归属数据管理 [1]。"),
        ("数据服务性能高于缓存服务。", "缓存服务性能高于数据服务 [1]。"),
        ("工具集包含数据处理模块。", "数据处理模块包含工具集 [1]。"),
        ("客户端调用数据服务。", "数据服务调用客户端 [1]。"),
        ("数据库启动完成后，服务端再启动。", "服务端启动完成后，数据库再启动 [1]。"),
        ("启用离线模式时，服务端支持本地缓存。", "服务端支持本地缓存 [1]。"),
        ("开启兼容模式后，客户端可以导入旧工程。", "客户端可以导入旧工程 [1]。"),
    ]

    for evidence, answer in rejected_cases:
        verdict = verify_grounding(answer, [_source(1, evidence)])
        assert not verdict.ok, (evidence, answer)


def test_verify_grounding_accepts_chinese_inverse_comparison_equivalence():
    from rag_knowledge.services.evidence_pack import verify_grounding

    source = _source(1, "数据服务性能高于缓存服务。")
    verdict = verify_grounding("缓存服务性能低于数据服务 [1]。", [source])

    assert verdict.ok


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
