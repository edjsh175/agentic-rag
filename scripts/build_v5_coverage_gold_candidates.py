#!/usr/bin/env python3
"""Generate ~100 v5 coverage gold candidates from the coverage matrix + live chunks.

Read-only against Chroma. Does not mutate v4.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BASE = ROOT / "docs/3_待办清单/切块基石治理/已完成-第0B轮-并行准备与预研/评测基线与黄金集"
DEFAULT_MATRIX = BASE / "v5_coverage_matrix.json"
DEFAULT_OUT = BASE / "multi_chunk_qa_gold_v5_coverage_candidate.json"
DEFAULT_MANIFEST = BASE / "multi_chunk_qa_gold_v5_coverage.candidate.manifest.json"
DEFAULT_CHECKLIST = BASE / "multi_chunk_qa_gold_v5_coverage.review_checklist.md"

_CMD_RE = re.compile(
    r"(?m)^\s*(?:\$\s*)?((?:systemctl|umount|mount|chmod|chown|mkdir|cp|mv|rm|yum|dnf|apt|nginx|"
    r"firewall-cmd|semanage|restorecon|xfs_repair|df|free|top|ps|curl|wget|tar|unzip)\b[^\n]{0,120})"
)
_PATH_RE = re.compile(r"(/(?:etc|data|var|opt|boot|home|usr)[A-Za-z0-9_./-]{2,80})")
_PORT_RE = re.compile(r"(?i)(?:port|端口)\s*(?:=|:|：|为|是)?\s*(\d{2,5})")
_IPV4_RE = re.compile(r"\b(?:https?://)?(?:\d{1,3}\.){3}\d{1,3}(?::\d{2,5})?(?:/[^\s]*)?")
_FIELD_RE = re.compile(r"(?m)^\s*([A-Za-z][A-Za-z0-9_]{2,40}|[\u4e00-\u9fff]{2,20})\s*(?:[:：|=])\s*([^\n]{1,60})")
_STEP_RE = re.compile(r"(?m)^\s*(?:[（(]?[0-9一二三四五六七八九十]+[）).、．]|[-*•])\s*([^\n]{6,80})")
_TABLE_HINT = re.compile(r"(?m)^\s*\|.+\|\s*$")


def _load_chunks() -> dict[str, dict[str, Any]]:
    from rag_knowledge.repository.vector_store import VectorStore

    raw = VectorStore().get_chunk_stats_source()
    out: dict[str, dict[str, Any]] = {}
    for chunk_id, document, metadata in zip(
        raw.get("ids") or [], raw.get("documents") or [], raw.get("metadatas") or []
    ):
        out[str(chunk_id)] = {
            "id": str(chunk_id),
            "document": str(document or ""),
            "metadata": dict(metadata or {}),
        }
    return out


def _clean_fact(text: str, limit: int = 80) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip(" .;；,，")
    return value[:limit]


def _extract_facts(text: str) -> list[dict[str, str]]:
    facts: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(kind: str, value: str) -> None:
        fact = _clean_fact(value)
        if len(fact) < 2:
            return
        # Only keep facts that appear verbatim in the chunk (freeze gate).
        if fact not in text:
            # Prefer longest verbatim token from the candidate.
            parts = [p for p in re.split(r"[\s=：:|,，；;]+", fact) if len(p) >= 4]
            parts.sort(key=len, reverse=True)
            chosen = next((p for p in parts if p in text), "")
            if not chosen:
                return
            fact = chosen
        key = fact.casefold()
        if key in seen:
            return
        seen.add(key)
        facts.append({"kind": kind, "value": fact})

    for match in _CMD_RE.findall(text):
        add("command", match)
    for match in _PATH_RE.findall(text):
        add("path", match)
    for match in _PORT_RE.findall(text):
        # Keep bare port only if the digits appear in text (always true),
        # but prefer a short surrounding phrase when available.
        add("port", match)
    for match in _IPV4_RE.findall(text):
        add("url", match)
    for key, value in _FIELD_RE.findall(text):
        # Prefer the label token (more stable than long descriptions).
        add("field", key)
        add("field", value)
    for match in _STEP_RE.findall(text):
        add("step", match)

    # Also harvest distinctive Latin / path-like tokens directly from text.
    for token in re.findall(r"(?:/[A-Za-z0-9_./-]{4,}|[A-Za-z][A-Za-z0-9_.-]{5,})", text):
        if len(token) > 60:
            continue
        add("token", token)
    return facts


def _pick_category(text: str, facts: list[dict[str, str]], section_key: str) -> str:
    if _TABLE_HINT.search(text) or "表" in section_key or "字段" in section_key:
        return "table"
    kinds = {f["kind"] for f in facts}
    if "command" in kinds or "step" in kinds or "安装" in section_key or "部署" in section_key:
        return "procedure"
    return "fact"


def _question_for(slot: dict[str, Any], facts: list[dict[str, str]], category: str, text: str) -> tuple[str, str, list[str], str]:
    source_name = Path(slot["source"]).stem
    section = slot.get("section_key") or slot.get("section_path_sample") or "相关章节"
    primary = facts[0]["value"] if facts else ""
    answerability = "full" if facts else "partial"

    if category == "procedure" and facts:
        cmd_or_step = next((f["value"] for f in facts if f["kind"] in {"command", "step"}), primary)
        question = f"在「{section}」中，执行或配置时需要用到哪条关键命令/步骤？"
        if any(f["kind"] == "command" for f in facts):
            question = f"根据文档「{section}」，相关操作应执行什么命令？"
        ground = f"文档在「{section}」给出：{cmd_or_step}。"
        required = [f for f in [cmd_or_step] + [x["value"] for x in facts[1:6]] if f in text]
        required = list(dict.fromkeys(required))[:4]
        if not required:
            answerability = "partial"
            m = re.search(r"[A-Za-z][A-Za-z0-9_.-]{5,}|[\u4e00-\u9fff]{3,}", text)
            required = [m.group(0)] if m else []
        return question, ground, required, answerability

    if category == "table" and facts:
        field = next((f["value"] for f in facts if f["kind"] == "field"), primary)
        question = f"「{section}」中关于字段/表项有哪些关键取值说明？"
        ground = f"文档在「{section}」写明：{field}。"
        required = [f for f in [field] + [x["value"] for x in facts[1:6]] if f in text]
        required = list(dict.fromkeys(required))[:4]
        return question, ground, required, answerability

    # fact default
    if primary:
        question = f"关于「{section}」，文档中与 {source_name} 相关的关键配置/事实是什么？"
        # Prefer more specific question when we have path/port/url
        for fact in facts:
            if fact["kind"] == "path":
                question = f"文档「{section}」提到的关键路径是什么？"
                break
            if fact["kind"] == "port":
                question = f"文档「{section}」中给出的端口是多少？"
                break
            if fact["kind"] == "url":
                question = f"文档「{section}」中给出的访问地址是什么？"
                break
        ground = f"文档在「{section}」给出：{primary}。"
        required = [f for f in [primary] + [x["value"] for x in facts[1:6]] if f in text]
        required = list(dict.fromkeys(required))[:4]
        return question, ground, required, answerability

    # Weak fallback: use section title + short excerpt tokens that exist in text
    excerpt = re.sub(r"\s+", " ", text.strip())[:120]
    tokens = [
        t
        for t in re.findall(r"[A-Za-z][A-Za-z0-9_.-]{3,}|[\u4e00-\u9fff]{2,}", excerpt)
        if t not in {"文档", "配置", "说明"} and t in text
    ]
    required = tokens[:3] or ([section] if section in text else [])
    if not required:
        # last resort: first 8+ char latin/cjk run in text
        m = re.search(r"[A-Za-z][A-Za-z0-9_.-]{7,}|[\u4e00-\u9fff]{4,}", text)
        required = [m.group(0)] if m else ["（无稳定事实词）"]
        answerability = "partial"
    question = f"「{section}」章节主要说明了什么内容？"
    ground = f"文档「{section}」相关原文包含：{'、'.join(required)}。"
    return question, ground, required, answerability


def _oral_variant(item: dict[str, Any], slot: dict[str, Any]) -> dict[str, Any] | None:
    """Optional oral rewrite-regression twin (<=10% of set)."""
    source = Path(slot["source"]).name
    if "PipelineBuilder" in source or "管线" in (slot.get("section_key") or ""):
        twin = dict(item)
        twin["id"] = item["id"] + "-oral"
        twin["question"] = "管线工具这一节主要讲什么？" if "Pipeline" in source else item["question"].replace("文档", "手册里")
        twin["notes"] = "oral rewrite regression twin; same anchors"
        twin["category"] = item["category"]
        return twin
    if "StampManager" in source:
        twin = dict(item)
        twin["id"] = item["id"] + "-oral"
        twin["question"] = "Stamp管理中心相关部署要注意什么？"
        twin["notes"] = "oral rewrite regression twin; same anchors"
        return twin
    return None


def build_candidates(matrix: dict[str, Any], chunks: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    category_budget = {"fact": 0, "procedure": 0, "table": 0}
    oral_budget = max(1, len(matrix.get("slots") or []) // 12)

    for idx, slot in enumerate(matrix.get("slots") or [], start=1):
        chunk = chunks.get(str(slot.get("best_chunk_id") or ""))
        if not chunk:
            # fallback: skip empty
            continue
        text = chunk["document"]
        facts = _extract_facts(text)
        category = _pick_category(text, facts, slot.get("section_key") or "")
        # Soft balance categories toward 50/35/15
        if category == "fact" and category_budget["fact"] > 55 and facts:
            category = "procedure" if any(f["kind"] in {"command", "step"} for f in facts) else category
        if category == "procedure" and category_budget["procedure"] > 40 and facts:
            if any(f["kind"] == "field" for f in facts):
                category = "table"
        question, ground, required, answerability = _question_for(slot, facts, category, text)
        if not required or any(fact not in text for fact in required):
            # Skip unverifiable drafts rather than emit weak items.
            continue
        section_ids = slot.get("section_ids") or []
        anchor: dict[str, Any] = {"source": slot["source"]}
        if section_ids:
            anchor["section_id"] = section_ids[0]
        else:
            sample = str(slot.get("section_path_sample") or slot.get("section_key") or "").strip()
            if sample and sample != "(no_section)":
                # Use a short stable contains needle
                parts = [p.strip() for p in sample.split(">") if p.strip()]
                anchor["section_path_contains"] = parts[-1] if parts else sample

        item = {
            "id": f"cv5-{idx:03d}",
            "question": question,
            "answerability": answerability,
            "ground_truth": ground,
            "required_facts": required,
            "evidence_anchors": [anchor],
            "required_section_ids": section_ids[:3],
            "forbidden_claims": [],
            "notes": f"coverage slot {slot.get('slot_id')}; chunk={slot.get('best_chunk_id')}",
            "category": category,
            "source_snapshot_hash": str(matrix.get("corpus_snapshot_hash") or "")[:16],
            "evaluation_scope": "fr10_retrieval",
            "review_status": "pending",
            "review_basis": "v5 automated draft from coverage matrix",
            "slot_id": slot.get("slot_id"),
        }
        items.append(item)
        category_budget[category] = category_budget.get(category, 0) + 1

        if oral_budget > 0:
            twin = _oral_variant(item, slot)
            if twin is not None:
                items.append(twin)
                oral_budget -= 1
                category_budget[category] = category_budget.get(category, 0) + 1

    # Trim to ~100-110 if oral twins pushed over
    if len(items) > 110:
        items = items[:110]
    return items


def build_checklist(items: list[dict[str, Any]]) -> str:
    lines = [
        "# v5 覆盖黄金集审核清单",
        "",
        "审核要点：锚点 chunk 是否存在；`required_facts` 是否可从原文直接推出；题干是否过宽。",
        "",
        f"- 候选题数：{len(items)}",
        "",
        "| id | category | answerability | question | required_facts |",
        "|---|---|---|---|---|",
    ]
    for item in items:
        facts = "；".join(item.get("required_facts") or [])
        q = str(item.get("question") or "").replace("|", "/")
        lines.append(
            f"| {item.get('id')} | {item.get('category')} | {item.get('answerability')} | {q} | {facts} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build v5 coverage gold candidates")
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--checklist", type=Path, default=DEFAULT_CHECKLIST)
    args = parser.parse_args(argv)

    matrix = json.loads(args.matrix.read_text(encoding="utf-8"))
    chunks = _load_chunks()
    items = build_candidates(matrix, chunks)
    payload = json.dumps(items, ensure_ascii=False, indent=2) + "\n"
    args.out.write_text(payload, encoding="utf-8")
    args.checklist.write_text(build_checklist(items), encoding="utf-8")

    cat_counts: dict[str, int] = {}
    for item in items:
        cat = str(item.get("category") or "")
        cat_counts[cat] = cat_counts.get(cat, 0) + 1

    manifest = {
        "gold_version": "v5-coverage-candidate",
        "status": "not_frozen",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "matrix": args.matrix.name,
        "corpus_snapshot_hash": matrix.get("corpus_snapshot_hash"),
        "candidate_count": len(items),
        "category_counts": cat_counts,
        "candidate_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "scope_policy": {
            "fr10_retrieval": "coverage text questions for rewrite/filter tuning; v4 remains frozen"
        },
        "freeze_requirement": "Auto-verify required_facts against live anchors, then freeze verified items; optional human signoff can follow.",
    }
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "candidate_count": len(items),
                "category_counts": cat_counts,
                "out": str(args.out),
                "checklist": str(args.checklist),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
