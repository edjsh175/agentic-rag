import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
py = str(ROOT / "venv" / "Scripts" / "python.exe")
out = ROOT / "data" / "backbone_dry_run_out.json"

env = os.environ.copy()
env["PYTHONIOENCODING"] = "utf-8"
env["PYTHONUTF8"] = "1"
env["PYTHONPATH"] = str(ROOT)

with out.open("wb") as fh:
    proc = subprocess.run(
        [py, str(ROOT / "sync_product_backbone_to_graph.py"), "--dry-run", "--json"],
        cwd=str(ROOT),
        stdout=fh,
        stderr=subprocess.PIPE,
        env=env,
    )

print("exit", proc.returncode)
stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
if stderr:
    print("stderr:", stderr[:3000])

raw = out.read_bytes()
text = raw.decode("utf-8")
data = json.loads(text)
print(
    "entities", data.get("entity_count"),
    "aliases", data.get("alias_count"),
    "relations", data.get("relation_count"),
    "diags", data.get("diagnostic_count"),
)
for item in (data.get("diagnostics") or [])[:50]:
    print(item.get("code"), item.get("message"))
sys.exit(proc.returncode)
