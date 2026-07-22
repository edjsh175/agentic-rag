"""Round 3: dry-run then execute rebuild-safe --include-llm, then verify/archive."""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import run_graph_build
from rag_knowledge.config import Config
from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.graph_governance import resolve_db_path
from rag_knowledge.services.ollama_health import assert_ollama_reachable


def main() -> int:
    db = RelationalDB()
    db_path = str(resolve_db_path())
    summary: dict = {"db_path": db_path, "started_at": datetime.now().isoformat(timespec="seconds")}

    dry_path = ROOT / "data" / "rebuild_safe_dry_run_pre_round3.json"
    if not dry_path.exists():
        print("=== dry-run ===", flush=True)
        code = run_graph_build.main(
            [
                "rebuild-safe",
                "--dry-run",
                "--output-json",
                "data/rebuild_safe_dry_run_pre_round3.json",
                "--output-md",
                "data/rebuild_safe_dry_run_pre_round3.md",
            ],
            db=db,
        )
        if code != 0:
            raise RuntimeError(f"dry-run exited {code}")
    dry = json.loads(dry_path.read_text(encoding="utf-8"))
    summary["dry_run"] = {
        "preserved_by_source": dry.get("preserved_by_source"),
        "backbone_integrity": dry.get("backbone_integrity"),
    }
    if not (dry.get("backbone_integrity") or {}).get("complete"):
        raise RuntimeError("dry-run backbone_integrity incomplete; refuse execute")
    print("dry-run ready backbone", dry.get("backbone_integrity"), flush=True)

    configured = Config().ollama_base_url
    summary["configured_ollama"] = configured
    # Hard fail: do not stub empty LLM responses.
    assert_ollama_reachable(base_url=configured, timeout=8.0)
    summary["llm_mode"] = "live"
    print("ollama live", configured, flush=True)

    print("=== execute include-llm ===", flush=True)
    code = run_graph_build.main(
        [
            "rebuild-safe",
            "--execute",
            "--include-llm",
            "--confirm-db-path",
            db_path,
            "--backup-dir",
            "data/backups",
            "--output-json",
            "data/rebuild_safe_execute_round3.json",
            "--output-md",
            "data/rebuild_safe_execute_round3.md",
        ],
        db=db,
    )
    if code != 0:
        raise RuntimeError(f"execute exited {code}")
    exe = json.loads((ROOT / "data" / "rebuild_safe_execute_round3.json").read_text(encoding="utf-8"))
    summary["execute"] = {
        "backup_path": exe.get("backup_path"),
        "batch_id": (exe.get("extract") or {}).get("batch_id"),
        "extract_stats": (exe.get("extract") or {}).get("stats"),
        "review": exe.get("review"),
        "apply": {
            "batch_id": (exe.get("apply") or {}).get("batch_id") if exe.get("apply") else None,
            "counts_before": (exe.get("apply") or {}).get("counts_before") if exe.get("apply") else None,
            "counts_after": (exe.get("apply") or {}).get("counts_after") if exe.get("apply") else None,
        },
        "backbone_after": exe.get("backbone_after"),
        "before_after": exe.get("before_after"),
        "cleanup": exe.get("cleanup"),
    }
    print("execute done", json.dumps(summary["execute"], ensure_ascii=False)[:1000], flush=True)

    print("=== post verify ===", flush=True)
    run_graph_build.main(
        [
            "audit",
            "--output-json",
            "data/graph_audit_post_round3.json",
            "--output-md",
            "data/graph_audit_post_round3.md",
        ],
        db=db,
    )
    try:
        qcode = run_graph_build.main(["quality", "--graph", "--profile", "full"], db=db)
    except Exception as exc:
        qcode = -1
        summary["quality_error"] = str(exc)
    summary["quality_exit"] = qcode

    import subprocess

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    gate_out = ROOT / "data" / "task81_post_round3.json"
    with gate_out.open("wb") as fh:
        proc = subprocess.run(
            [
                str(ROOT / "venv" / "Scripts" / "python.exe"),
                str(ROOT / "scripts" / "validate_task81_graph_gate.py"),
                "--json",
            ],
            cwd=str(ROOT),
            stdout=fh,
            stderr=subprocess.PIPE,
            env=env,
        )
    summary["task81_exit"] = proc.returncode
    try:
        summary["task81"] = json.loads(gate_out.read_text(encoding="utf-8"))
    except Exception:
        summary["task81_raw"] = gate_out.read_text(encoding="utf-8", errors="replace")[:2000]

    archive = ROOT / "data" / "archive" / "rebuild_reports"
    archive.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for name in (
        "rebuild_safe_dry_run_pre_round3.json",
        "rebuild_safe_dry_run_pre_round3.md",
        "rebuild_safe_execute_round3.json",
        "rebuild_safe_execute_round3.md",
        "graph_audit_post_round3.json",
        "graph_audit_post_round3.md",
        "manual_graph_facts_pre_round3.json",
    ):
        src = ROOT / "data" / name
        if src.exists():
            shutil.copy2(src, archive / f"{stamp}_{name}")
    summary["archive_dir"] = str(archive)
    summary["archive_stamp"] = stamp
    summary["finished_at"] = datetime.now().isoformat(timespec="seconds")

    out = ROOT / "data" / "round3_orchestration_summary.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("summary", out, flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        (ROOT / "data" / "round3_orchestration_error.txt").write_text(str(exc), encoding="utf-8")
        print("ERROR", exc, flush=True)
        raise SystemExit(1)
