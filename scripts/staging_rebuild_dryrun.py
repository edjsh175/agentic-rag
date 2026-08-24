"""
Staging Full Rebuild Dry-Run (read-write staging only, no commit-swap).

Steps:
  1. Create a forked staging collection in the LIVE chroma_db (different collection name).
  2. Scan all watch_directory files using the real loader with Phase 2 adapters.
  3. Validate consistency (index vs Chroma).
  4. Verify Phase 2 files (PDF/PPTX/HTML/SQL/Config) produced chunks or correct decisions.
  5. Run FR-10 --mode retrieval against the staging collection.
  6. Print Go/No-Go verdict.
  7. Drop the staging collection (cleanup).

Does NOT call _commit_swap, so live production is never touched.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path.cwd().resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ------------------------------------------------------------------ imports
from rag_knowledge.config import Config
from rag_knowledge.repository.vector_store import VectorStore
from rag_knowledge.services.scanner import DirectoryScanner
from rag_knowledge.services.knowledge_base_consistency import KnowledgeBaseConsistencyService
from rag_knowledge.evaluation.multi_evidence_metrics import score_answer, summarize_scores
from rag_knowledge.services.document_support import classify_suffix

GOLD_PATH = ROOT / "docs/04_已完成归档/01_文档解析与切块/已完成-第0B轮-并行准备与预研/评测基线与黄金集/multi_chunk_qa_gold_v2.json"
REPORT_DIR = ROOT / "docs/04_已完成归档/01_文档解析与切块/已完成-第0C轮-切块合并与隔离验证/首次隔离验证"

# Phase 2 extensions we want to validate
PHASE2_EXTS = {".pdf", ".pptx", ".html", ".htm", ".sql", ".cnf", ".conf", ".cfg", ".ini", ".xml"}


def _verify_phase2_decisions(index_data: dict, decision_data: dict) -> dict:
    """Verify Phase 2 files appear in index (queued→indexed) or decisions (excluded)."""
    files_section = index_data.get("files", {})
    decisions = decision_data.get("decisions", {})

    phase2_indexed = []
    phase2_excluded = []
    phase2_missing = []

    watch_dir = ROOT / "watch_directory"
    for fpath in watch_dir.rglob("*"):
        if not fpath.is_file():
            continue
        suffix = fpath.suffix.lower()
        if suffix not in PHASE2_EXTS:
            continue
        rel = str(fpath.relative_to(watch_dir)).replace("\\", "/")
        # Check if it's in index
        found_in_index = any(
            info.get("file_name") == fpath.name or
            str(info.get("file_path", "")).endswith(fpath.name)
            for info in files_section.values()
        )
        found_in_decisions = any(
            d.get("file_name") == fpath.name or
            str(d.get("file_path", "")).endswith(fpath.name)
            for d in decisions.values()
        )
        disposition = classify_suffix(suffix)
        if disposition.action == "excluded":
            if found_in_decisions:
                phase2_excluded.append(rel)
            else:
                phase2_missing.append(rel)
        else:
            if found_in_index:
                phase2_indexed.append(rel)
            elif found_in_decisions:
                # queued but not indexed yet → treat as covered
                phase2_excluded.append(f"[queued-decision] {rel}")
            else:
                phase2_missing.append(rel)

    return {
        "indexed": phase2_indexed,
        "excluded_or_decision": phase2_excluded,
        "missing": phase2_missing,
        "gate_passed": len(phase2_missing) == 0,
    }


def _run_retrieval_eval(staging_collection: str) -> dict:
    """Run FR-10 retrieval against the staging collection."""
    original_name = os.environ.get("VECTOR_STORE_COLLECTION_NAME", "")
    os.environ["VECTOR_STORE_COLLECTION_NAME"] = staging_collection
    Config._instance = None  # force reload with new collection name

    try:
        from rag_knowledge.services.rag import RagChain
        rag = RagChain()

        items = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
        rows = []
        for i, item in enumerate(items):
            question = str(item.get("question") or "")
            try:
                docs, _ctx = rag._retrieve(
                    question,
                    review_status="approved",
                    method="similarity",  # BM25 is built on live collection, not staging; force vector-only
                )
                sources = []
                for doc in docs or []:
                    meta = doc.get("metadata") or {}
                    sources.append({
                        "source": meta.get("source", ""),
                        "section_path": meta.get("section_path", ""),
                        "chunk_id": meta.get("chunk_id", ""),
                        "content": doc.get("content", ""),
                    })
                proxy = "\n".join(s["content"] for s in sources)
                scored = score_answer(item, proxy, sources=sources)
                scored["retrieved_count"] = len(sources)
            except Exception as exc:
                logger.warning("retrieve failed for %s: %s", item.get("id"), exc)
                scored = score_answer(item, "", sources=[])
                scored["error"] = str(exc)
            rows.append(scored)
            if (i + 1) % 10 == 0:
                logger.info("FR-10 progress %d/%d", i + 1, len(items))
    finally:
        # Always restore original env + singleton
        if original_name:
            os.environ["VECTOR_STORE_COLLECTION_NAME"] = original_name
        else:
            os.environ.pop("VECTOR_STORE_COLLECTION_NAME", None)
        Config._instance = None

    return summarize_scores(rows), rows



def main() -> int:
    cfg = Config()
    live_store = VectorStore()
    data_dir = Path(cfg.data_dir)
    live_index = data_dir / "file_index.json"
    live_decision = data_dir / "ingestion_decisions.json"

    op_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    staging_name = f"{cfg.collection_name}__staging_dryrun__{op_id}"

    work_dir = data_dir / "rebuild_dryrun" / op_id
    work_dir.mkdir(parents=True, exist_ok=True)
    staging_index = work_dir / "file_index.json"
    staging_decision = work_dir / "ingestion_decisions.json"

    # Copy live index/decisions as starting point
    if live_index.exists():
        import shutil
        shutil.copy2(live_index, staging_index)
    else:
        staging_index.write_text(json.dumps({"version": 1, "files": {}}), encoding="utf-8")

    if live_decision.exists():
        import shutil
        shutil.copy2(live_decision, staging_decision)
    else:
        staging_decision.write_text(json.dumps({"version": 1, "decisions": {}}), encoding="utf-8")

    logger.info("=== Staging Dry-Run: collection=%s ===", staging_name)
    staged_store = live_store.fork(staging_name)

    # Build staging scanner
    scanner = DirectoryScanner(
        cfg=cfg,
        store=staged_store,
        index_path=staging_index,
        decision_path=staging_decision,
        refresh_retrieval=False,
        new_chunk_review_status="approved",
    )
    scanner.reset_index(preserve_doc_categories=False)

    logger.info("Scanning watch_directory into staging collection...")
    result = scanner.scan()
    logger.info(
        "Scan complete: new=%d queued=%d excluded=%d errors=%d",
        result["new_files"], result["queued_files"], result["excluded_files"], result["errors"]
    )

    if result["errors"]:
        logger.error("Staging scan had %d errors — aborting.", result["errors"])
        return 1

    # Consistency check
    logger.info("Running consistency check...")
    index_data = json.loads(staging_index.read_text(encoding="utf-8"))
    chunk_snapshot = staged_store.get_chunk_stats_source()
    try:
        KnowledgeBaseConsistencyService(
            index_data=index_data,
            chunk_snapshot=chunk_snapshot,
        ).assert_consistent()
        logger.info("Consistency check PASSED.")
        consistency_ok = True
    except Exception as exc:
        logger.warning("Consistency check FAILED: %s", exc)
        consistency_ok = False

    # Phase 2 verification
    logger.info("Verifying Phase 2 file coverage...")
    decision_data = json.loads(staging_decision.read_text(encoding="utf-8"))
    p2_result = _verify_phase2_decisions(index_data, decision_data)
    logger.info(
        "Phase2: indexed=%d excluded/decision=%d missing=%d gate=%s",
        len(p2_result["indexed"]),
        len(p2_result["excluded_or_decision"]),
        len(p2_result["missing"]),
        p2_result["gate_passed"],
    )
    if p2_result["missing"]:
        for f in p2_result["missing"]:
            logger.warning("  MISSING Phase2 file: %s", f)

    staging_chunk_count = staged_store.count()
    logger.info("Staging collection chunk count: %d", staging_chunk_count)

    # FR-10 retrieval against staging
    logger.info("=== Running FR-10 retrieval against staging collection ===")
    summary, rows = _run_retrieval_eval(staging_name)
    logger.info("=== Running same-mode FR-10 baseline against live collection ===")
    live_summary, _live_rows = _run_retrieval_eval(cfg.collection_name)

    pass_rate = summary.get("pass_rate", 0.0)
    completeness = summary.get("mean_completeness", 0.0)
    evidence_recall = summary.get("mean_evidence_recall", 0.0)
    logger.info(
        "FR-10 staging: pass_rate=%.2f%% completeness=%.2f%% evidence_recall=%.2f%%",
        pass_rate * 100, completeness * 100, evidence_recall * 100,
    )

    # Write report
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "retrieval",
        "target": "staging_dryrun",
        "staging_collection": staging_name,
        "staging_chunk_count": staging_chunk_count,
        "gold_path": str(GOLD_PATH),
        "consistency_ok": consistency_ok,
        "phase2_verification": p2_result,
        "live_similarity_baseline": live_summary,
        "summary": summary,
        "results": rows,
    }
    json_path = REPORT_DIR / "fr10_staging_dryrun_report.json"
    md_path = REPORT_DIR / "fr10_staging_dryrun_report.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Build markdown
    md_lines = [
        "# FR-10 Staging Dry-Run Report",
        "",
        f"- generated_at: `{payload['generated_at']}`",
        f"- mode: `retrieval` (staging collection, not live)",
        f"- staging_chunk_count: **{staging_chunk_count}**",
        f"- consistency_ok: **{consistency_ok}**",
        f"- Phase2 indexed: **{len(p2_result['indexed'])}**  |  excluded/decision: **{len(p2_result['excluded_or_decision'])}**  |  missing: **{len(p2_result['missing'])}**",
        f"- pass_rate: **{pass_rate:.2%}**",
        f"- mean_completeness: **{completeness:.2%}**",
        f"- mean_evidence_recall: **{evidence_recall:.2%}**",
        f"- live_similarity_pass_rate: **{live_summary.get('pass_rate', 0.0):.2%}**",
        f"- live_similarity_mean_completeness: **{live_summary.get('mean_completeness', 0.0):.2%}**",
        f"- live_similarity_mean_evidence_recall: **{live_summary.get('mean_evidence_recall', 0.0):.2%}**",
        "",
        "## Go / No-Go",
        "",
    ]
    compared_metrics = ("pass_rate", "mean_completeness", "mean_evidence_recall")
    fr10_ok = all(
        summary.get(metric, 0.0) + 1e-12 >= live_summary.get(metric, 0.0)
        for metric in compared_metrics
    )
    staging_gate_ok = consistency_ok and p2_result["gate_passed"] and fr10_ok
    reasons = []
    if not consistency_ok:
        reasons.append("consistency check failed")
    if not p2_result["gate_passed"]:
        reasons.append(f"Phase2 missing files: {p2_result['missing']}")
    if not fr10_ok:
        reasons.append(
            "FR-10 similarity metrics regressed against the same-mode live baseline"
        )
    if staging_gate_ok:
        md_lines.append("**✅ STAGING DRY-RUN PASSED** — all gates cleared. Ready for cold backup + stop-service + /rebuild.")
    else:
        md_lines.append("**❌ STAGING DRY-RUN FAILED**")
        for r in reasons:
            md_lines.append(f"- {r}")
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    logger.info("Reports written: %s  %s", json_path, md_path)

    # Cleanup staging collection
    logger.info("Dropping staging collection %s ...", staging_name)
    try:
        staged_store._get_store().delete_collection()
        logger.info("Staging collection dropped.")
    except Exception as exc:
        logger.warning("Could not drop staging collection (manual cleanup may be needed): %s", exc)

    logger.info(
        "=== Staging Dry-Run verdict: %s ===",
        "PASSED" if staging_gate_ok else "FAILED"
    )
    return 0 if staging_gate_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
