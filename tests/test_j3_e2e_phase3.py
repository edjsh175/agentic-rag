"""Phase 3 (PRD V1.8+) offline tests: J3 scoring, prompt contract, escape reject."""
from __future__ import annotations

import pytest

from rag_knowledge.services.rag import RagChain

from scripts.run_j3_e2e_acceptance import (
    api_present,
    forbidden_present,
    has_pasteable_code_block,
    params_present,
    score_j3_answer,
)


class TestJ3Scoring:
    def test_good_answer_passes_all_gates(self):
        answer = (
            "适用产品：StampWebRTC（StampUtil）\n\n"
            "```js\n"
            "const params = { linecolor: 0xffff0000, linewidth: 3 };\n"
            "await StampUtil.createElementLineParams(params);\n"
            "```\n\n"
            "参数说明：linecolor 为边线颜色，linewidth 为线宽度。"
        )
        case = {
            "expected_apis": ["createElementLineParams"],
            "must_params": ["linecolor", "linewidth"],
            "forbidden": ["Canvas", "PipelineBuilder"],
        }
        score = score_j3_answer(answer, case)
        assert score["pass"] is True
        assert score["missing_apis"] == []
        assert score["missing_params"] == []
        assert score["forbidden_hits"] == []

    def test_missing_api_and_param_fail(self):
        answer = "```js\nctx.lineWidth = 3;\n```"
        case = {
            "expected_apis": ["createElementLineParams"],
            "must_params": ["linecolor"],
            "forbidden": [],
        }
        score = score_j3_answer(answer, case)
        assert score["pass"] is False
        assert "createElementLineParams" in score["missing_apis"]
        assert "linecolor" in score["missing_params"]

    def test_forbidden_product_fails(self):
        answer = (
            "```js\nStampUtil.createElementLineParams({linecolor: 1, linewidth: 3});\n```"
            "\nPipelineWebGL 说明见用户手册。"
        )
        case = {
            "expected_apis": ["createElementLineParams"],
            "must_params": ["linecolor"],
            "forbidden": ["PipelineWebGL", "Canvas"],
        }
        score = score_j3_answer(answer, case)
        assert score["pass"] is False
        assert "PipelineWebGL" in score["forbidden_hits"]

    def test_no_fenced_code_block_fails_g3_3(self):
        assert has_pasteable_code_block("写一行：StampUtil.createElementLineParams()") is False
        assert has_pasteable_code_block("```js\na();\nb();\n```") is True

    def test_helpers_basic(self):
        assert api_present("调用 createElementLineParams", "createElementLineParams")
        assert params_present("linecolor 与 linewidth", ["linecolor", "linewidth"])
        assert forbidden_present("Canvas 绘制", ["Canvas"])


class TestJ3PromptContract:
    def test_j3_job_injects_contract_section(self):
        msgs = RagChain._build_messages(
            "写一段创建折线的代码",
            "context",
            job="j3",
            allow_general_knowledge=False,
        )
        system = msgs[0]["content"]
        assert "二次开发代码示例输出契约（J3）" in system
        assert "禁止编造 context 中未出现的产品私有 API" in system

    def test_non_j3_job_has_no_contract_section(self):
        msgs = RagChain._build_messages(
            "StampWebRTC 是什么",
            "context",
            job="j1",
            allow_general_knowledge=False,
        )
        assert "二次开发代码示例输出契约（J3）" not in msgs[0]["content"]


class TestJ3UnclearReject:
    def test_unclear_j3_short_rejects(self):
        from rag_knowledge.services.sdk_code_job import (
            resolve_job,
            should_skip_backbone_guess,
        )

        chain = object.__new__(RagChain)
        decision = resolve_job("写一段创建折线的代码，线颜色设为红色、线宽为 3。")
        assert should_skip_backbone_guess(decision) is True

        rejected = RagChain._j3_clarify_reject_if_needed(
            chain,
            "写一段创建折线的代码，线颜色设为红色、线宽为 3。",
        )
        assert rejected is not None
        answer = rejected["answer"]
        assert "StampWebRTC" in answer and "StampWebGL" in answer
        assert "Pipeline" not in answer
        assert rejected["source_documents"] == []

    def test_selected_entity_bypasses_reject(self):
        chain = object.__new__(RagChain)
        rejected = RagChain._j3_clarify_reject_if_needed(
            chain,
            "写一段创建折线的代码，线颜色设为红色、线宽为 3。",
            entity_name="StampWebRTC",
        )
        assert rejected is None

    def test_named_product_bypasses_reject(self):
        chain = object.__new__(RagChain)
        rejected = RagChain._j3_clarify_reject_if_needed(
            chain,
            "StampWebRTC 写一段创建折线的代码，线颜色红色",
        )
        assert rejected is None

    def test_non_j3_question_bypasses_reject(self):
        chain = object.__new__(RagChain)
        rejected = RagChain._j3_clarify_reject_if_needed(chain, "PipelineBuilder 如何新建工程")
        assert rejected is None

    def test_explorer_selection_bypasses_reject(self):
        from rag_knowledge.services.sdk_code_job import EXPLORER_OPS_SENTINEL

        chain = object.__new__(RagChain)
        rejected = RagChain._j3_clarify_reject_if_needed(
            chain,
            "写一段创建折线的代码",
            entity_name=EXPLORER_OPS_SENTINEL,
        )
        assert rejected is None


@pytest.fixture(autouse=True)
def _isolated(isolated_storage):
    isolated_storage(
        db_name="j3-e2e.db",
        chroma_name="j3-e2e-chroma",
        data_dir_name="j3-e2e-data",
    )


@pytest.fixture(autouse=True)
def _no_trace(monkeypatch):
    monkeypatch.setenv("QA_TRACE_ENABLED", "false")
