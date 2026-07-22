"""Phase 2 orchestration: cleanup obsolete seed → stage → split review → apply."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
py = str(ROOT / "venv" / "Scripts" / "python.exe")
env = os.environ.copy()
env["PYTHONIOENCODING"] = "utf-8"
env["PYTHONUTF8"] = "1"
env["PYTHONPATH"] = str(ROOT)


def run(args: list[str], *, out_path: Path | None = None) -> dict | str:
    if out_path is None:
        proc = subprocess.run(args, cwd=str(ROOT), capture_output=True, env=env)
        stdout = (proc.stdout or b"").decode("utf-8", errors="replace")
        stderr = (proc.stderr or b"").decode("utf-8", errors="replace")
        if proc.returncode != 0:
            raise RuntimeError(f"cmd failed ({proc.returncode}): {' '.join(args)}\n{stderr}\n{stdout}")
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            return stdout
    with out_path.open("wb") as fh:
        proc = subprocess.run(args, cwd=str(ROOT), stdout=fh, stderr=subprocess.PIPE, env=env)
    stderr = (proc.stderr or b"").decode("utf-8", errors="replace")
    text = out_path.read_text(encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(f"cmd failed ({proc.returncode}): {' '.join(args)}\n{stderr}\n{text[:2000]}")
    return json.loads(text)


def main() -> int:
    summary: dict = {}

    # 1) resolve db
    db = subprocess.run(
        [py, "-c", "from rag_knowledge.services.graph_governance import resolve_db_path; print(resolve_db_path())"],
        cwd=str(ROOT),
        capture_output=True,
        env=env,
        text=True,
    )
    if db.returncode != 0:
        raise RuntimeError(db.stderr)
    db_path = db.stdout.strip()
    summary["db_path"] = db_path

    # 2) cleanup with backup
    cleanup_out = ROOT / "data" / "backbone_cleanup_apply_out.json"
    cleanup = run(
        [py, str(ROOT / "scripts" / "cleanup_obsolete_product_backbone_seed.py"), "--apply", "--backup", "--json"],
        out_path=cleanup_out,
    )
    summary["cleanup"] = {
        "backup_path": cleanup.get("backup_path"),
        "report_path": cleanup.get("report_path"),
        "to_delete_count": cleanup["diff"]["to_delete_count"],
        "to_keep_count": cleanup["diff"]["to_keep_count"],
        "to_add_count": cleanup["diff"]["to_add_count"],
        "deleted_relations": cleanup["cleanup"]["deleted_relations"],
        "deleted_entity_count": cleanup["cleanup"]["deleted_entity_count"],
    }
    backup_path = cleanup["backup_path"]
    print("cleanup_ok", json.dumps(summary["cleanup"], ensure_ascii=False))

    # 3) stage
    stage_out = ROOT / "data" / "backbone_stage_out.json"
    stage = run(
        [
            py,
            str(ROOT / "sync_product_backbone_to_graph.py"),
            "--stage",
            "--confirm-db-path",
            db_path,
            "--json",
        ],
        out_path=stage_out,
    )
    batch_id = stage["batch_id"]
    summary["batch_id"] = batch_id
    summary["stage_stats"] = stage.get("stats")
    print("stage_ok", batch_id, stage.get("stats"))

    # 4) split review
    for kind in ("entity", "alias", "relation"):
        review_out = ROOT / "data" / f"backbone_review_{kind}_out.json"
        # review CLI may not emit JSON; capture text
        with review_out.open("wb") as fh:
            proc = subprocess.run(
                [
                    py,
                    str(ROOT / "run_graph_build.py"),
                    "review",
                    "--batch",
                    batch_id,
                    "--approve-kind",
                    kind,
                ],
                cwd=str(ROOT),
                stdout=fh,
                stderr=subprocess.PIPE,
                env=env,
            )
        text = review_out.read_text(encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            raise RuntimeError(
                f"review {kind} failed: {(proc.stderr or b'').decode('utf-8', errors='replace')}\n{text}"
            )
        summary[f"review_{kind}"] = text.strip()[:500]
        print("review_ok", kind, text.strip()[:200])

    # 5) apply
    apply_out = ROOT / "data" / "backbone_apply_out.json"
    with apply_out.open("wb") as fh:
        proc = subprocess.run(
            [
                py,
                str(ROOT / "run_graph_build.py"),
                "apply",
                "--batch",
                batch_id,
                "--confirm-db-path",
                db_path,
                "--confirm-batch",
                batch_id,
                "--confirm-backup",
                backup_path,
            ],
            cwd=str(ROOT),
            stdout=fh,
            stderr=subprocess.PIPE,
            env=env,
        )
    apply_text = apply_out.read_text(encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(
            f"apply failed: {(proc.stderr or b'').decode('utf-8', errors='replace')}\n{apply_text}"
        )
    summary["apply"] = apply_text.strip()[:2000]
    print("apply_ok", apply_text.strip()[:500])

    out = ROOT / "data" / "backbone_phase2_summary.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("summary", out)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        (ROOT / "data" / "backbone_phase2_error.txt").write_text(str(exc), encoding="utf-8")
        print("ERROR", exc)
        raise SystemExit(1)
