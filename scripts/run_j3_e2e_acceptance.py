"""Phase 3 (PRD V1.8+) J3 end-to-end acceptance: clarify-selected generation scoring.

Gates (PRD §六 Phase 3):
  G3-1 API    — the answer mentions the manual's expected API name(s)
  G3-2 参数    — key parameter names appear (meaning not obviously wrong is judged
                 by the scorer's absence of contradiction markers; full semantic
                 check stays human)
  G3-3 形态    — a complete pasteable fenced code block exists; forbidden
                 products/tokens (Canvas / Pipeline*) do not appear

Usage (local Ollama acceptance path; stop the backend first):
  $env:RAG_CONFIG="config-local.ini"
  $env:PYTHONPATH=(Get-Location).Path
  .\\venv\\Scripts\\python.exe scripts/run_j3_e2e_acceptance.py            # all cases
  .\\venv\\Scripts\\python.exe scripts/run_j3_e2e_acceptance.py --limit 2  # smoke
  .\\venv\\Scripts\\python.exe scripts/run_j3_e2e_acceptance.py --scoring-only  # no LLM

Output: data/eval_j3_e2e_<ts>.json (same schema as other eval artifacts).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# PRD §七 J3 题集（选定后生成；clarify 门禁由 G0-C / G0-C2 单测覆盖）
J3_CASES: list[dict] = [
    {
        "id": "j3-line-style",
        "question": "写一段创建折线的代码，线颜色设为红色、线宽为 3。",
        "entity_name": "StampWebRTC",
        "expected_apis": ["createElementLineParams"],
        "must_params": ["linecolor", "linewidth"],
        "forbidden": ["Canvas", "PipelineBuilder", "PipelineWebGL"],
    },
    {
        "id": "j3-line-style-webgl",
        "question": "写一段创建折线的代码，线颜色设为红色、线宽为 3。",
        "entity_name": "StampWebGL",
        "expected_apis": ["CreateElementLine"],
        "must_params": ["lineColor", "lineWidth"],
        "forbidden": ["PipelineBuilder", "PipelineWebGL"],
    },
    {
        "id": "j3-polygon-fill",
        "question": "写一段创建多边形并设置填充色的代码。",
        "entity_name": "StampWebRTC",
        "expected_apis": ["createElementPolygonParams"],
        "must_params": ["fillcolor"],
        "forbidden": ["PipelineBuilder"],
    },
    {
        "id": "j3-stamputil-vue",
        "question": "Vue3 项目中如何引入 StampUtil 并初始化地图？",
        "entity_name": "StampWebRTC",
        "expected_apis": ["StampUtil"],
        "must_params": ["引入", "初始化"],
        "forbidden": ["PipelineBuilder"],
    },
]


def _fenced_code_blocks(answer: str) -> list[str]:
    return re.findall(r"```[^\n]*\n(.*?)```", answer or "", re.S)


def has_pasteable_code_block(answer: str) -> bool:
    """G3-3: at least one fenced code block containing non-trivial code lines."""
    for block in _fenced_code_blocks(answer):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if len(lines) >= 2:
            return True
    return False


def api_present(answer: str, api: str) -> bool:
    if not api:
        return True
    return re.search(re.escape(api), answer or "", re.I) is not None


def params_present(answer: str, params: list[str]) -> bool:
    return all(
        re.search(re.escape(p), answer or "", re.I) is not None for p in (params or [])
    )


def forbidden_present(answer: str, forbidden: list[str]) -> bool:
    return any(
        re.search(rf"\b{re.escape(t)}\b", answer or "", re.I) for t in (forbidden or [])
    )


def score_j3_answer(answer: str, case: dict) -> dict:
    """Pure scoring shared by the CLI and unit tests (G3-1/2/3)."""
    expected_apis = list(case.get("expected_apis") or [])
    must_params = list(case.get("must_params") or [])
    forbidden = list(case.get("forbidden") or [])
    g3_1 = all(api_present(answer, api) for api in expected_apis)
    g3_2 = params_present(answer, must_params)
    g3_3_code = has_pasteable_code_block(answer)
    g3_3_clean = not forbidden_present(answer, forbidden)
    return {
        "g3_1_api": g3_1,
        "g3_2_params": g3_2,
        "g3_3_code_block": g3_3_code,
        "g3_3_no_forbidden": g3_3_clean,
        "pass": bool(g3_1 and g3_2 and g3_3_code and g3_3_clean),
        "missing_apis": [a for a in expected_apis if not api_present(answer, a)],
        "missing_params": [p for p in must_params if not params_present(answer, [p])],
        "forbidden_hits": [t for t in forbidden if forbidden_present(answer, [t])],
    }


async def run_case(chain, case: dict, llm_model: str | None = None) -> dict:
    result = await chain.aquery(
        case["question"],
        entity_name=case["entity_name"],
        llm_model=llm_model,
        allow_general_knowledge=False,
    )
    answer = str(result.get("answer") or "")
    score = score_j3_answer(answer, case)
    return {
        "id": case["id"],
        "question": case["question"],
        "entity_name": case["entity_name"],
        "llm_model": llm_model,
        "answer_preview": answer[:600],
        "answer_len": len(answer),
        **score,
    }


async def run_all(cases: list[dict], llm_model: str | None = None) -> list[dict]:
    from rag_knowledge.services.rag import RagChain

    chain = RagChain()
    out = []
    for case in cases:
        try:
            out.append(await run_case(chain, case, llm_model=llm_model))
        except Exception as exc:  # noqa: BLE001 — per-case isolation
            out.append(
                {
                    "id": case["id"],
                    "question": case["question"],
                    "error": f"{type(exc).__name__}: {exc}",
                    "pass": False,
                }
            )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="J3 end-to-end acceptance (Phase 3)")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 题（冒烟）")
    parser.add_argument("--llm-model", default=None, help="生成模型（如 qwen3:30b）；缺省用配置默认")
    parser.add_argument("--scoring-only", action="store_true", help="只验证评分函数，不调 LLM")
    args = parser.parse_args()

    if args.scoring_only:
        sample = {
            "id": "sample",
            "expected_apis": ["createElementLineParams"],
            "must_params": ["linecolor"],
            "forbidden": ["Canvas"],
        }
        ok = score_j3_answer(
            "```js\nStampUtil.createElementLineParams({linecolor: 0xffff0000});\n```",
            sample,
        )
        bad = score_j3_answer(
            "Canvas 绘制折线", sample,
        )
        print("scoring self-check ok:", ok, "\nscoring self-check bad:", bad)
        return 0

    cases = J3_CASES if not args.limit else J3_CASES[: args.limit]
    results = asyncio.run(run_all(cases, llm_model=args.llm_model))

    passed = sum(1 for r in results if r.get("pass"))
    stamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    payload = {
        "generated_at": stamp,
        "config": "RAG_CONFIG=" + str(Path(__file__).resolve().parents[1] / "config-local.ini"),
        "cases": results,
        "summary": {
            "pass": passed,
            "total": len(results),
            "gates": {
                "g3_1_api": sum(1 for r in results if r.get("g3_1_api")),
                "g3_2_params": sum(1 for r in results if r.get("g3_2_params")),
                "g3_3_code_block": sum(1 for r in results if r.get("g3_3_code_block")),
                "g3_3_no_forbidden": sum(1 for r in results if r.get("g3_3_no_forbidden")),
            },
        },
    }
    out_path = ROOT / "data" / f"eval_j3_e2e_{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    for r in results:
        flag = "PASS" if r.get("pass") else "FAIL"
        extra = (
            f" apis={r.get('missing_apis')} params={r.get('missing_params')} "
            f"forbidden={r.get('forbidden_hits')}"
            if not r.get("pass")
            else ""
        )
        print(f"  [{flag}] {r.get('id')}{extra}")
    print(f"结果已写入: {out_path}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
