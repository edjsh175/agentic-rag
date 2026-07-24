#!/usr/bin/env python3
"""A/B harness: anchor chunk filter interference control (T1–T4)."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("NO_PROXY", "192.168.10.158,127.0.0.1,localhost,::1")

DEFAULT_OUT = (
    ROOT
    / "docs/3_待办清单/知识图谱语义抽取/已完成-锚点约束Chunk过滤与干扰对照实验/fixtures"
)

CASES = [
    {
        "id": "T1",
        "question": "怎么用管线工具",
        "expect_canonical": ["PipelineBuilder"],
        "require_positive": True,
    },
    {
        "id": "T2",
        "question": "PipelineBuilder 怎么用",
        "expect_canonical": ["PipelineBuilder"],
        "require_positive": True,
    },
    {
        "id": "T3",
        "question": "管线工具发布要配什么",
        "expect_canonical": ["PipelineBuilder"],
        "require_positive": True,
    },
    {
        "id": "T4",
        "question": "管线更新服务是做什么的",
        "expect_canonical": [],
        "require_positive": False,
    },
]

POSITIVE_SOURCE_NEEDLES = ("stamptools", "stamp tools")
POSITIVE_SECTION_NEEDLES = ("pipelinebuilder",)
INTERFERENCE_RULES = (
    {
        "id": "server_project_config",
        "source_needles": ("stampserver",),
        "section_needles": ("工程配置", "运维管理配置"),
        "require_source_and_section": True,
    },
    {
        "id": "pipeline_update_service",
        "source_needles": ("2ca727efa70847b49f0f67528544d210",),
        "section_needles": ("管线更新服务",),
        "require_source_and_section": False,
        "section_only_ok": True,
    },
)


def _source_hint(docs: list) -> list[dict]:
    out = []
    for d in docs or []:
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
                "section_title": str(meta.get("section_title") or d.get("section_title") or ""),
                "section_path": str(meta.get("section_path") or d.get("section_path") or ""),
                "file_name": file_name,
                "doc_category": str(meta.get("doc_category") or d.get("doc_category") or ""),
                "content_preview": str(d.get("content") or d.get("page_content") or "")[:160],
            }
        )
    return out


def _is_positive(src: dict) -> bool:
    blob = f"{src.get('file_name','')} {src.get('source','')}".casefold()
    section = f"{src.get('section_path','')} {src.get('section_title','')}".casefold()
    source_ok = any(n in blob for n in POSITIVE_SOURCE_NEEDLES)
    section_ok = any(n in section for n in POSITIVE_SECTION_NEEDLES)
    return source_ok and section_ok


def _interference_hits(sources: list[dict]) -> list[str]:
    hits = []
    for rule in INTERFERENCE_RULES:
        for src in sources:
            blob = f"{src.get('file_name','')} {src.get('source','')}".casefold()
            section = f"{src.get('section_path','')} {src.get('section_title','')}"
            section_fold = section.casefold()
            src_hit = any(n in blob for n in rule["source_needles"])
            sec_hit = any(n.casefold() in section_fold for n in rule["section_needles"])
            if rule.get("section_only_ok"):
                if sec_hit:
                    hits.append(rule["id"])
                    break
                continue
            if rule.get("require_source_and_section"):
                if src_hit and sec_hit:
                    hits.append(rule["id"])
                    break
                continue
            if src_hit or sec_hit:
                hits.append(rule["id"])
                break
    return hits


def _judge(case: dict, canonical: list[str], cited: list[dict], context_docs: list[dict], answer: str) -> dict:
    expect = list(case.get("expect_canonical") or [])
    anchor_ok = True
    if expect:
        anchor_ok = all(e in canonical for e in expect)

    positive_cited = any(_is_positive(s) for s in cited)
    positive_context = any(_is_positive(s) for s in context_docs)
    interference_cited = _interference_hits(cited)
    interference_context = _interference_hits(context_docs)
    empty = ("当前知识库中未查询到相关内容" in (answer or "")) or (
        "没有可验证的引用证据" in (answer or "") and not cited
    )

    fail_reasons = []
    if case.get("require_positive"):
        if not anchor_ok:
            fail_reasons.append("wrong_anchor")
        if not (positive_cited or positive_context):
            fail_reasons.append("positive_miss")
        if interference_cited:
            fail_reasons.append("interference_cited:" + ",".join(interference_cited))
    return {
        "anchor_ok": anchor_ok,
        "positive_cited": positive_cited,
        "positive_context": positive_context,
        "interference_cited": interference_cited,
        "interference_context": interference_context,
        "empty_or_ungrounded": empty,
        "pass": not fail_reasons,
        "fail_reasons": fail_reasons,
    }


def run_condition(
    chain,
    *,
    filter_enabled: bool,
    graph_allowlist_enabled: bool,
    use_full_query: bool,
    condition_label: str,
) -> dict:
    from rag_knowledge.services.backbone_guard import soft_match_backbone_entities
    from rag_knowledge.services.graph_query_rewrite import GraphQueryRewriter

    chain._graph_cfg.anchor_chunk_filter_enabled = bool(filter_enabled)
    chain._graph_cfg.anchor_graph_chunk_enabled = bool(graph_allowlist_enabled)
    rewriter = GraphQueryRewriter()
    cases_out = []
    for case in CASES:
        q = case["question"]
        soft = soft_match_backbone_entities(q)
        anchor = rewriter.anchor_from_backbone(q)
        canonical = list(anchor.canonical_entities)

        t0 = time.perf_counter()
        diagnostics: dict = {}
        context_docs: list[dict] = []
        cited: list[dict] = []
        answer = ""
        if use_full_query:
            result = chain.query(q, history=None, thinking=False)
            answer = result.get("answer") or ""
            cited = _source_hint(result.get("source_documents") or [])
            source_docs, _ = chain.retrieve_for_evaluation(q, diagnostics=diagnostics)
            context_docs = _source_hint(source_docs)
        else:
            source_docs, _ = chain.retrieve_for_evaluation(q, diagnostics=diagnostics)
            context_docs = _source_hint(source_docs)
            cited = context_docs[:3]
        ms = int((time.perf_counter() - t0) * 1000)
        judgment = _judge(case, canonical, cited, context_docs, answer)
        row = {
            "id": case["id"],
            "question": q,
            "soft_hits": soft,
            "canonical": canonical,
            "latency_ms": ms,
            "cited": cited,
            "context_docs": context_docs[:8],
            "answer_preview": (answer or "")[:280].replace("\n", " "),
            **judgment,
        }
        cases_out.append(row)
        print(
            f"[{condition_label}] {case['id']}: "
            f"{'pass' if judgment['pass'] else 'fail'} "
            f"canonical={canonical} pos_cited={judgment['positive_cited']} "
            f"interf={judgment['interference_cited']} ({ms}ms)"
        )
    return {
        "condition": condition_label,
        "anchor_chunk_filter_enabled": bool(filter_enabled),
        "anchor_graph_chunk_enabled": bool(graph_allowlist_enabled),
        "cases": cases_out,
    }


def summarize(payload: dict) -> dict:
    scored = [c for c in payload["cases"] if c["id"] in {"T1", "T2", "T3"}]
    n = len(scored) or 1
    return {
        "t123_pass": sum(1 for c in scored if c["pass"]),
        "t123_positive_rate": sum(1 for c in scored if c["positive_cited"] or c["positive_context"]) / n,
        "t123_interference_cited_rate": sum(1 for c in scored if c["interference_cited"]) / n,
        "t123_empty_rate": sum(1 for c in scored if c["empty_or_ungrounded"]) / n,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--retrieve-only", action="store_true", help="Skip answer LLM; judge on context")
    parser.add_argument("--only", choices=["A", "B", "C", "both", "all"], default="both")
    args = parser.parse_args()

    from rag_knowledge.config import Config
    from rag_knowledge.services.rag import RagChain

    cfg = Config()
    chain = RagChain()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "graph_retrieval_enabled": bool(cfg.graph_retrieval.enabled),
        "query_rewrite_enabled": bool(cfg.graph_retrieval.query_rewrite_enabled),
        "mode": "retrieve_only" if args.retrieve_only else "full_query",
    }

    run_map = {
        "A": dict(filter_enabled=False, graph_allowlist_enabled=False, label="A"),
        "B": dict(filter_enabled=True, graph_allowlist_enabled=False, label="B"),
        "C": dict(filter_enabled=True, graph_allowlist_enabled=True, label="C"),
    }
    if args.only == "both":
        selected = ["A", "B"]
    elif args.only == "all":
        selected = ["A", "B", "C"]
    else:
        selected = [args.only]

    results = {}
    for key in selected:
        spec = run_map[key]
        payload = run_condition(
            chain,
            filter_enabled=spec["filter_enabled"],
            graph_allowlist_enabled=spec["graph_allowlist_enabled"],
            use_full_query=not args.retrieve_only,
            condition_label=spec["label"],
        )
        payload["summary"] = summarize(payload)
        results[key] = payload
        path = out_dir / f"condition_{key}.json"
        path.write_text(
            json.dumps({"meta": meta, **payload}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"wrote {path}")

    if len(results) >= 2:
        compare = {"meta": meta}
        for key, payload in results.items():
            compare[f"{key}_summary"] = payload["summary"]
            print(key, payload["summary"])
        path = out_dir / "condition_AB_compare.json"
        path.write_text(json.dumps(compare, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
