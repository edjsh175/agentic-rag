"""Phase 2.5 verify: audit + quality + Task81 gate."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
py = str(ROOT / "venv" / "Scripts" / "python.exe")
env = os.environ.copy()
env["PYTHONIOENCODING"] = "utf-8"
env["PYTHONUTF8"] = "1"
env["PYTHONPATH"] = str(ROOT)

OUT = ROOT / "data" / "backbone_phase2_verify.json"


def run_capture(args: list[str], out_file: Path) -> tuple[int, str]:
    with out_file.open("wb") as fh:
        proc = subprocess.run(args, cwd=str(ROOT), stdout=fh, stderr=subprocess.PIPE, env=env)
    text = out_file.read_text(encoding="utf-8", errors="replace")
    err = (proc.stderr or b"").decode("utf-8", errors="replace")
    return proc.returncode, text if text.strip() else err


def main() -> int:
    summary: dict = {}

    code, text = run_capture(
        [py, str(ROOT / "run_graph_build.py"), "audit",
         "--output-json", "data/graph_audit_post_backbone_replace.json",
         "--output-md", "data/graph_audit_post_backbone_replace.md"],
        ROOT / "data" / "_audit_stdout.json",
    )
    summary["audit_exit"] = code
    try:
        summary["audit"] = json.loads(text)
    except Exception:
        summary["audit_raw"] = text[:2000]
    print("audit", code)

    code, text = run_capture(
        [py, str(ROOT / "run_graph_build.py"), "quality", "--graph", "--profile", "full"],
        ROOT / "data" / "_quality_stdout.json",
    )
    summary["quality_exit"] = code
    try:
        summary["quality"] = json.loads(text)
    except Exception:
        summary["quality_raw"] = text[:3000]
    print("quality", code)

    code, text = run_capture(
        [py, str(ROOT / "scripts" / "validate_task81_graph_gate.py"), "--json"],
        ROOT / "data" / "_task81_stdout.json",
    )
    summary["task81_exit"] = code
    try:
        summary["task81"] = json.loads(text)
    except Exception:
        summary["task81_raw"] = text[:3000]
    print("task81", code, summary.get("task81", {}).get("status") or summary.get("task81", {}).get("verdict"))

    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote", OUT)
    # Gate should be PASS; audit/quality may warn but shouldn't block unless task81 blocked
    task81 = summary.get("task81") or {}
    status = str(task81.get("status") or task81.get("verdict") or task81.get("result") or "")
    if code != 0 and "PASS" not in status.upper():
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
