#!/usr/bin/env python3
"""A1 acceptance: backbone anchor layer + optional /admin/qa-debug full path."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("NO_PROXY", "192.168.10.158,127.0.0.1,localhost,::1")

# Fixed set from 执行PRD §4.1 / §5
CASES = [
    {
        "id": "sm-intro",
        "question": "介绍一下 StampManager",
        "expect_canonical": ["StampManager"],
        "kind": "product_intro",
    },
    {
        "id": "sm-alias",
        "question": "Stamp管理中心是做什么的",
        "expect_canonical": ["StampManager"],
        "kind": "product_intro",
    },
    {
        "id": "tools-vs-server",
        "question": "StampTools 和 StampServer 有什么区别",
        "expect_canonical": ["StampGIS Tools", "StampGIS Server"],
        "kind": "compare",
        # Soft/alias should map both surfaces onto backbone canonicals.
        "must_include": ["StampGIS Tools", "StampGIS Server"],
    },
    {
        "id": "pipeline-belongs",
        "question": "PipelineBuilder 属于什么",
        "expect_canonical": ["PipelineBuilder"],
        "kind": "tool_belongs",
        "forbid_canonical": ["PipelineWebGL"],
    },
    {
        "id": "oral-pipeline-what",
        "question": "管线工具是什么",
        "expect_canonical": ["PipelineBuilder"],
        "kind": "oral",
        "forbid_canonical": ["PipelineWebGL"],
    },
    {
        "id": "oral-pipeline-how",
        "question": "怎么用管线工具",
        "expect_canonical": ["PipelineBuilder"],
        "kind": "oral",
        "forbid_canonical": ["PipelineWebGL"],
    },
]


def _classify_anchor(case: dict, soft_hits: list[str], canonical: list[str]) -> str | None:
    forbid = set(case.get("forbid_canonical") or [])
    expect = set(case.get("expect_canonical") or [])
    if forbid.intersection(canonical) and not (expect & set(canonical)):
        if case.get("kind") == "oral" and not soft_hits:
            return "alias_gap"
        if soft_hits and expect & set(soft_hits):
            return "helper_unstable"
        return "wrong_anchor"
    must = case.get("must_include") or []
    if must and not all(m in canonical for m in must):
        if case.get("kind") == "oral" and not soft_hits:
            return "alias_gap"
        return "wrong_anchor"
    if case.get("require_any"):
        ok = bool(expect & set(canonical))
    else:
        ok = all(e in canonical for e in expect) if expect else True
    if ok:
        return None
    if case.get("kind") == "oral" and not soft_hits:
        return "alias_gap"
    if soft_hits and expect & set(soft_hits) and not (expect & set(canonical)):
        return "helper_unstable"
    return "wrong_anchor"


def _source_hint(docs: list) -> list[dict]:
    out = []
    for d in (docs or [])[:5]:
        if not isinstance(d, dict):
            continue
        meta = d.get("metadata") or {}
        raw_name = (
            meta.get("file_name")
            or meta.get("source")
            or d.get("file_name")
            or d.get("source")
            or ""
        )
        file_name = Path(str(raw_name)).name if raw_name else ""
        out.append(
            {
                "source": str(meta.get("source") or raw_name or ""),
                "section_title": str(meta.get("section_title") or ""),
                "section_path": str(meta.get("section_path") or ""),
                "file_name": file_name,
                "content_preview": str(d.get("content") or "")[:160],
            }
        )
    return out


def _classify_retrieve(case: dict, canonical: list[str], sources: list[dict], answer: str) -> str | None:
    answer = answer or ""
    if "当前知识库中未查询到相关内容" in answer and not sources:
        return "empty_context"
    if "没有可验证的引用证据" in answer and not sources:
        return "retrieve_miss"
    if not sources:
        return "retrieve_miss" if answer.strip() else "empty_context"

    needles = [c.casefold() for c in canonical if c]
    for e in case.get("expect_canonical") or []:
        if e.casefold() not in needles:
            needles.append(e.casefold())
    # Surface aliases commonly appearing in manuals
    surface = {
        "StampGIS Tools": ["stamptools", "stamp tools"],
        "StampGIS Server": ["stampserver", "stamp server"],
        "PipelineBuilder": ["pipelinebuilder", "管线工具", "piplinebuilder"],
        "StampManager": ["stampmanager", "stamp管理中心"],
    }
    for e in case.get("expect_canonical") or []:
        for alt in surface.get(e, []):
            if alt not in needles:
                needles.append(alt)

    blob = " ".join(
        f"{s.get('file_name','')} {s.get('section_path','')} {s.get('section_title','')} "
        f"{s.get('source','')} {s.get('content_preview','')}"
        for s in sources
    ).casefold()
    blob = blob + " " + answer.casefold()

    if needles and not any(n in blob for n in needles):
        if case.get("kind") == "compare":
            alts = ["stamptools", "stampserver", "stampgis"]
            if any(a in blob for a in alts):
                return None
        return "retrieve_miss"
    forbid = [x.casefold() for x in (case.get("forbid_canonical") or [])]
    expect_hit = any(n in blob for n in needles) if needles else True
    if forbid and any(f in blob for f in forbid) and not expect_hit:
        return "retrieve_miss"
    return None


def run_anchor_layer():
    from rag_knowledge.services.backbone_guard import soft_match_backbone_entities
    from rag_knowledge.services.graph_query_rewrite import GraphQueryRewriter

    rewriter = GraphQueryRewriter()
    rows = []
    for case in CASES:
        q = case["question"]
        t0 = time.perf_counter()
        soft = soft_match_backbone_entities(q)
        anchor = rewriter.anchor_from_backbone(q)
        ms = int((time.perf_counter() - t0) * 1000)
        canonical = list(anchor.canonical_entities)
        queries = [getattr(rq, "text", str(rq)) for rq in (anchor.retrieval_queries or ())]
        reason = _classify_anchor(case, soft, canonical)
        rows.append(
            {
                "id": case["id"],
                "question": q,
                "kind": case["kind"],
                "soft_hits": soft,
                "canonical": canonical,
                "avoid": list(anchor.avoid or ()),
                "primary_intent": anchor.primary_intent,
                "anchored_queries": list(anchor.anchored_queries or ()),
                "retrieval_queries": queries,
                "relation_summary_preview": (anchor.relation_summary or "")[:240],
                "latency_ms": ms,
                "anchor_status": "fail" if reason else "pass",
                "fail_reason": reason,
            }
        )
        print(
            f"[anchor] {case['id']}: {rows[-1]['anchor_status']}"
            f" soft={soft} canonical={canonical} reason={reason} ({ms}ms)"
        )
    return rows


def run_query_layer(base_url: str, timeout: float = 180.0):
    import httpx

    rows = []
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout) as client:
        health = client.get("/health")
        health.raise_for_status()
        for case in CASES:
            q = case["question"]
            t0 = time.perf_counter()
            resp = client.post("/admin/qa-debug", json={"question": q, "thinking": False})
            ms = int((time.perf_counter() - t0) * 1000)
            if resp.status_code >= 400:
                rows.append(
                    {
                        "id": case["id"],
                        "question": q,
                        "query_status": "fail",
                        "fail_reason": "empty_context",
                        "http_status": resp.status_code,
                        "error": resp.text[:500],
                        "latency_ms": ms,
                    }
                )
                print(f"[query] {case['id']}: fail http={resp.status_code} ({ms}ms)")
                continue
            data = resp.json()
            sources = _source_hint(data.get("source_documents") or [])
            answer = data.get("answer") or ""
            # evidence_chain is citation pack (no plan); use case expect for source checks
            canonical = list(case.get("expect_canonical") or [])
            reason = _classify_retrieve(case, canonical, sources, answer)
            rows.append(
                {
                    "id": case["id"],
                    "question": q,
                    "query_status": "fail" if reason else "pass",
                    "fail_reason": reason,
                    "canonical_from_evidence": canonical,
                    "sources": sources,
                    "answer_preview": answer[:280].replace("\n", " "),
                    "trace_id": data.get("trace_id"),
                    "latency_ms": ms,
                }
            )
            print(
                f"[query] {case['id']}: {rows[-1]['query_status']}"
                f" sources={len(sources)} reason={reason} ({ms}ms)"
            )
    return rows


def merge_report(anchor_rows, query_rows):
    by_q = {r["id"]: r for r in (query_rows or [])}
    merged = []
    for a in anchor_rows:
        q = by_q.get(a["id"])
        overall = "pass"
        reasons = []
        if a.get("anchor_status") != "pass":
            overall = "fail"
            if a.get("fail_reason"):
                reasons.append(a["fail_reason"])
        if q is not None and q.get("query_status") != "pass":
            overall = "fail"
            if q.get("fail_reason"):
                reasons.append(q["fail_reason"])
        merged.append(
            {
                **a,
                "query": q,
                "overall_status": overall,
                "overall_reasons": reasons,
            }
        )
    return merged


def to_markdown(merged: list[dict], meta: dict) -> str:
    lines = [
        "# 主干锚定固定题集验收台账",
        "",
        f"- 生成时间：`{meta.get('generated_at')}`",
        f"- 配置：`graph_retrieval.enabled={meta.get('enabled')}`，"
        f"`query_rewrite_enabled={meta.get('query_rewrite_enabled')}`",
        f"- helper：`{meta.get('helper')}` @ `{meta.get('ollama')}`",
        f"- query 层：`{meta.get('query_mode')}`",
        "",
        "## 汇总",
        "",
        f"- 题数：{len(merged)}",
        f"- pass：{sum(1 for r in merged if r['overall_status']=='pass')}",
        f"- fail：{sum(1 for r in merged if r['overall_status']!='pass')}",
        "",
        "## 逐题",
        "",
    ]
    for r in merged:
        lines.append(f"### {r['id']} — {r['overall_status']}")
        lines.append("")
        lines.append(f"- 问法：{r['question']}")
        lines.append(f"- soft_hits：`{r.get('soft_hits')}`")
        lines.append(f"- canonical：`{r.get('canonical')}`")
        lines.append(f"- avoid：`{r.get('avoid')}`")
        lines.append(f"- anchored_queries：`{r.get('anchored_queries')}`")
        lines.append(f"- relation_summary：{r.get('relation_summary_preview') or '(empty)'}")
        lines.append(f"- anchor：{r.get('anchor_status')} / {r.get('fail_reason')}")
        q = r.get("query")
        if q:
            lines.append(f"- query：{q.get('query_status')} / {q.get('fail_reason')} ({q.get('latency_ms')}ms)")
            srcs = q.get("sources") or []
            if srcs:
                lines.append("- top sources：")
                for s in srcs:
                    lines.append(
                        f"  - `{s.get('file_name')}` | {s.get('section_path') or s.get('section_title')}"
                    )
            lines.append(f"- answer_preview：{q.get('answer_preview')}")
        else:
            lines.append("- query：skipped")
        if r.get("overall_reasons"):
            lines.append("- 失败分类：`" + "` / `".join(r["overall_reasons"]) + "`")
        lines.append("")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:10605")
    parser.add_argument("--skip-query", action="store_true")
    parser.add_argument(
        "--out-dir",
        default=str(
            ROOT
            / "docs/3_待办清单/知识图谱语义抽取/进行中-主干锚定检索与关系可答"
        ),
    )
    args = parser.parse_args()

    from datetime import datetime, timezone

    from rag_knowledge.config import Config

    cfg = Config()
    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "enabled": bool(cfg.graph_retrieval.enabled),
        "query_rewrite_enabled": bool(cfg.graph_retrieval.query_rewrite_enabled),
        "helper": cfg.helper_llm_model,
        "ollama": cfg.ollama_base_url,
        "query_mode": "skipped" if args.skip_query else f"POST {args.base_url}/admin/qa-debug",
    }

    print("== anchor layer ==")
    anchor_rows = run_anchor_layer()
    query_rows = []
    if not args.skip_query:
        print("== query layer ==")
        try:
            query_rows = run_query_layer(args.base_url)
        except Exception as exc:
            print(f"[query] unavailable: {exc}")
            meta["query_mode"] = f"failed: {exc}"
    merged = merge_report(anchor_rows, query_rows)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "A1-固定题集验收台账.json"
    md_path = out_dir / "A1-固定题集验收台账.md"
    payload = {"meta": meta, "cases": merged}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(to_markdown(merged, meta), encoding="utf-8")
    print(f"wrote {json_path}")
    print(f"wrote {md_path}")
    fails = [r for r in merged if r["overall_status"] != "pass"]
    sys.exit(1 if fails and query_rows else (1 if any(r["anchor_status"] != "pass" for r in merged) else 0))


if __name__ == "__main__":
    main()
