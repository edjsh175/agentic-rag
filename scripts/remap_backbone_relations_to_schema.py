"""Normalize product_relation_backbone.json relations to pass schema validation.

Deterministic remaps (no silent drop):
- belongs_to with valid inverted pair → flip endpoints
- belongs_to Tool/Service peer → depends_on (if valid) else requires
- other illegal belongs_to → requires (unrestricted)
- keep note; append remap annotation
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from rag_knowledge.models.graph_schema import validate_relation

ROOT = Path(__file__).resolve().parents[1]
FORMAL = ROOT / "data" / "product_relation_backbone.json"
OUT_REPORT = ROOT / "data" / "backbone_relation_remap_report.json"


def _ok(st: str, rel: str, tt: str) -> bool:
    ok, _ = validate_relation(st, rel, tt)
    return ok


def remap_edge(source: str, st: str, rel: str, target: str, tt: str) -> tuple[str, str, str, str]:
    """Return (source, relation_type, target, action)."""
    if _ok(st, rel, tt):
        return source, rel, target, "keep"

    if rel == "belongs_to":
        # Ownership direction is child → parent; invert when that becomes legal.
        if _ok(tt, "belongs_to", st):
            return target, "belongs_to", source, "invert_belongs_to"
        if _ok(st, "depends_on", tt):
            return source, "depends_on", target, "belongs_to_to_depends_on"
        if _ok(st, "supports_format", tt):
            return source, "supports_format", target, "belongs_to_to_supports_format"
        # Format/Module etc.: requires is unrestricted
        return source, "requires", target, "belongs_to_to_requires"

    # Non-belongs_to illegal: try requires
    if _ok(st, "requires", tt) or True:  # requires unrestricted
        return source, "requires", target, f"{rel}_to_requires"
    return source, rel, target, "unfixed"


def main() -> int:
    data = json.loads(FORMAL.read_text(encoding="utf-8"))
    etype = {e["name"]: e["entity_type"] for e in data.get("entities") or []}
    actions = Counter()
    remapped: list[dict] = []
    new_relations: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    for item in data.get("relations") or []:
        source = item["source"]
        target = item["target"]
        rel = item["relation_type"]
        note = str(item.get("note") or "").strip()
        st = etype.get(source, "")
        tt = etype.get(target, "")
        ns, nrel, nt, action = remap_edge(source, st, rel, target, tt)
        actions[action] += 1
        if action != "keep":
            remapped.append({
                "from": {"source": source, "relation_type": rel, "target": target, "types": [st, tt]},
                "to": {"source": ns, "relation_type": nrel, "target": nt},
                "action": action,
            })
            if note:
                note = f"{note} | remap:{action}"
            else:
                note = f"remap:{action}"
        key = (ns, nrel, nt)
        if key in seen:
            actions["dedupe_skip"] += 1
            continue
        seen.add(key)
        row = {"source": ns, "relation_type": nrel, "target": nt}
        if note:
            row["note"] = note
        new_relations.append(row)

    data["relations"] = new_relations
    FORMAL.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Validate all
    remaining = []
    for item in new_relations:
        st = etype[item["source"]]
        tt = etype[item["target"]]
        ok, reason = validate_relation(st, item["relation_type"], tt)
        if not ok:
            remaining.append({"edge": item, "reason": reason})

    report = {
        "relation_count": len(new_relations),
        "actions": dict(actions),
        "remapped_count": len(remapped),
        "remaining_illegal": remaining,
        "sample_remapped": remapped[:20],
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"relation_count": len(new_relations), "actions": dict(actions), "remaining": len(remaining)}, ensure_ascii=False))
    return 0 if not remaining else 1


if __name__ == "__main__":
    raise SystemExit(main())
