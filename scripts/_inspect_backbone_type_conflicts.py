"""Inspect entity type conflicts blocking product backbone apply."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rag_knowledge.repository.relational_db import RelationalDB

BATCH = "a1c75bec-09ef-4420-be9a-e229add62c7b"
formal = json.loads((ROOT / "data" / "product_relation_backbone.json").read_text(encoding="utf-8"))
wanted = {e["name"]: e["entity_type"] for e in formal["entities"]}

db = RelationalDB()
conflicts = []
for name, etype in sorted(wanted.items()):
    existing = db.get_entity_by_name(name)
    if not existing:
        continue
    if existing.get("entity_type") != etype:
        conflicts.append({
            "name": name,
            "existing_type": existing.get("entity_type"),
            "existing_created_by": existing.get("created_by"),
            "existing_id": existing.get("id"),
            "wanted_type": etype,
        })

out = ROOT / "data" / "backbone_type_conflicts.json"
out.write_text(json.dumps({"count": len(conflicts), "conflicts": conflicts}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("conflicts", len(conflicts))
for c in conflicts:
    print(c["name"], c["existing_type"], "->", c["wanted_type"], c["existing_created_by"])
