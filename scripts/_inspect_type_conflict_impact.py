"""Inspect relations on type-conflict entities before retargeting types."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rag_knowledge.models.graph_schema import validate_relation
from rag_knowledge.repository.relational_db import RelationalDB

conflicts = json.loads((ROOT / "data" / "backbone_type_conflicts.json").read_text(encoding="utf-8"))["conflicts"]
db = RelationalDB()
report = []
for c in conflicts:
    ent = db.get_entity_by_name(c["name"])
    rels = db.list_relations(entity_id=ent["id"]) if ent else []
    after_type = c["wanted_type"]
    illegal_after = []
    for r in rels:
        if r["source_name"] == c["name"]:
            st, tt = after_type, r["target_type"]
        else:
            st, tt = r["source_type"], after_type
        ok, reason = validate_relation(st, r["relation_type"], tt)
        if not ok:
            illegal_after.append({
                "id": r["id"],
                "source": r["source_name"],
                "relation_type": r["relation_type"],
                "target": r["target_name"],
                "created_by": r["created_by"],
                "reason": reason,
            })
    report.append({
        "name": c["name"],
        "wanted_type": after_type,
        "relation_count": len(rels),
        "illegal_after_type_change": illegal_after,
        "relations": [
            {
                "source": r["source_name"],
                "relation_type": r["relation_type"],
                "target": r["target_name"],
                "created_by": r["created_by"],
            }
            for r in rels
        ],
    })

out = ROOT / "data" / "backbone_type_conflict_relation_impact.json"
out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps([{
    "name": r["name"],
    "rels": r["relation_count"],
    "illegal_after": len(r["illegal_after_type_change"]),
} for r in report], ensure_ascii=False))
for r in report:
    for item in r["illegal_after_type_change"]:
        print("ILLEGAL", item)
