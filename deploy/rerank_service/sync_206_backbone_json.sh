#!/bin/bash
# Backup then replace product backbone JSON on 206 with uploaded local copies.
set -euo pipefail
TS=$(date +%Y%m%d_%H%M%S)
DATA=/data/rag_python/data
SRC=/tmp/backbone_sync_local
mkdir -p "$DATA/archive/backups"

for f in product_relation_backbone.json product_relation_backbone_preview.json; do
  if [[ -f "$DATA/$f" ]]; then
    cp -a "$DATA/$f" "$DATA/archive/backups/${f}.pre_sync_${TS}"
    echo "backed up $f"
  fi
  cp -a "$SRC/$f" "$DATA/$f"
  echo "installed $f bytes=$(wc -c < "$DATA/$f")"
done

python3 <<'PY'
import json
from pathlib import Path
for name in [
    "product_relation_backbone.json",
    "product_relation_backbone_preview.json",
]:
    p = Path("/data/rag_python/data") / name
    d = json.loads(p.read_text(encoding="utf-8"))
    print(f"{name}: entities={len(d.get('entities',[]))} relations={len(d.get('relations',[]))}")
PY

# Preview API goes through container; restart not strictly required for JSON file read each request,
# but touch logs for audit.
echo "JSON sync done. Formal SQLite seed NOT modified by this script."
