#!/usr/bin/env python3
"""One-shot: export live product-backbone facts into product_relation_backbone.json."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--path",
        default=str(ROOT / "data" / "product_relation_backbone.json"),
        help="Target official backbone JSON path",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print summary without writing")
    parser.add_argument("--json", action="store_true", help="Print machine-readable summary")
    args = parser.parse_args(argv)

    from rag_knowledge.repository.relational_db import RelationalDB
    from rag_knowledge.services.product_backbone_live_sync import ProductBackboneLiveSyncService
    from rag_knowledge.services.safe_rebuild import check_backbone_integrity
    from rag_knowledge.services.backbone_guard import load_backbone_constraints

    service = ProductBackboneLiveSyncService(db=RelationalDB(), path=args.path)
    payload = service.build_payload()
    summary = {
        "path": str(service.path),
        "entities": len(payload.get("entities") or []),
        "relations": len(payload.get("relations") or []),
        "synced_from_live_at": payload.get("synced_from_live_at"),
        "dry_run": bool(args.dry_run),
    }
    if not args.dry_run:
        summary = service.write_payload(payload)
        constraints = load_backbone_constraints(Path(args.path))
        integrity = check_backbone_integrity(RelationalDB(), constraints)
        summary["integrity"] = integrity
        if not integrity.get("complete"):
            if args.json:
                print(json.dumps(summary, ensure_ascii=False, indent=2))
            else:
                print(summary)
            return 1
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
