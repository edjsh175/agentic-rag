"""Promote (schema-normalize) + dry-run validate product backbone."""
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

# Re-promote with schema remap
promote = subprocess.run(
    [py, str(ROOT / "scripts" / "promote_backbone_preview_to_formal.py"), "--write", "--json"],
    cwd=str(ROOT),
    capture_output=True,
    env=env,
)
promote_out = (promote.stdout or b"").decode("utf-8", errors="replace")
(ROOT / "data" / "backbone_promote_out.json").write_text(promote_out, encoding="utf-8")
print("promote_exit", promote.returncode)
print(promote_out[:1500])
if promote.returncode != 0:
    print((promote.stderr or b"").decode("utf-8", errors="replace")[:2000])
    sys.exit(promote.returncode)

# Dry-run
out = ROOT / "data" / "backbone_dry_run_out.json"
with out.open("wb") as fh:
    dry = subprocess.run(
        [py, str(ROOT / "sync_product_backbone_to_graph.py"), "--dry-run", "--json"],
        cwd=str(ROOT),
        stdout=fh,
        stderr=subprocess.PIPE,
        env=env,
    )
print("dry_exit", dry.returncode)
if dry.stderr:
    print("dry_stderr", dry.stderr.decode("utf-8", errors="replace")[:2000])

data = json.loads(out.read_text(encoding="utf-8"))
summary = {
    "entity_count": data.get("entity_count"),
    "alias_count": data.get("alias_count"),
    "relation_count": data.get("relation_count"),
    "diagnostic_count": data.get("diagnostic_count"),
    "diagnostics": data.get("diagnostics") or [],
}
(ROOT / "data" / "backbone_dry_run_summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print(json.dumps({k: summary[k] for k in summary if k != "diagnostics"}, ensure_ascii=False))
for item in summary["diagnostics"][:20]:
    print(item.get("code"), item.get("message"))
sys.exit(0 if summary["diagnostic_count"] == 0 else 1)
