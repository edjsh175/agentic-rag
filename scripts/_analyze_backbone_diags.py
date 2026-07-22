import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
data = json.loads((ROOT / "data" / "backbone_dry_run_out.json").read_text(encoding="utf-8"))
print("codes", Counter(d.get("code") for d in data.get("diagnostics") or []))
print("keys0", list((data.get("diagnostics") or [{}])[0].keys()))
print("sample", json.dumps((data.get("diagnostics") or [None])[0], ensure_ascii=False)[:800])

# Also inspect formal JSON relation type pairs by entity types
formal = json.loads((ROOT / "data" / "product_relation_backbone.json").read_text(encoding="utf-8"))
etype = {e["name"]: e.get("entity_type") for e in formal.get("entities") or []}
pair_counts = Counter()
for r in formal.get("relations") or []:
    st = etype.get(r["source"], "?")
    tt = etype.get(r["target"], "?")
    pair_counts[(r.get("relation_type"), st, tt)] += 1
print("formal relation type pairs:")
for k, v in sorted(pair_counts.items(), key=lambda x: (-x[1], x[0])):
    print(v, k)
