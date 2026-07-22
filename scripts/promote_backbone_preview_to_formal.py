#!/usr/bin/env python3
"""Promote product_relation_backbone_preview.json to formal product_relation_backbone.json."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

from rag_knowledge.models.graph_schema import validate_relation

ROOT = Path(__file__).resolve().parents[1]
PREVIEW = ROOT / "data" / "product_relation_backbone_preview.json"
FORMAL = ROOT / "data" / "product_relation_backbone.json"
BACKUP_DIR = ROOT / "data" / "archive" / "backups"


def _relation_ok(source_type: str, relation_type: str, target_type: str) -> bool:
    ok, _ = validate_relation(source_type, relation_type, target_type)
    return ok


def _normalize_relation(
    source: str,
    source_type: str,
    relation_type: str,
    target: str,
    target_type: str,
) -> tuple[str, str, str, str]:
    """Return (source, relation_type, target, remap_action)."""
    if _relation_ok(source_type, relation_type, target_type):
        return source, relation_type, target, "keep"
    if relation_type == "belongs_to":
        if _relation_ok(target_type, "belongs_to", source_type):
            return target, "belongs_to", source, "invert_belongs_to"
        if _relation_ok(source_type, "depends_on", target_type):
            return source, "depends_on", target, "belongs_to_to_depends_on"
        if _relation_ok(source_type, "supports_format", target_type):
            return source, "supports_format", target, "belongs_to_to_supports_format"
        return source, "requires", target, "belongs_to_to_requires"
    return source, "requires", target, f"{relation_type}_to_requires"

# Infer doc_category from canonical name or aliases.
_DOC_CATEGORY_HINTS = {
    "StampGIS Server": "StampServer",
    "StampServer": "StampServer",
    "StampGIS Tools": "StampTools",
    "StampTools": "StampTools",
    "StampWebRTC": "StampWebRTC",
    "云端渲染（WebRTC）": "StampWebRTC",
}


def _infer_doc_category(name: str, aliases: list[str]) -> str:
    for candidate in (name, *aliases):
        if candidate in _DOC_CATEGORY_HINTS:
            return _DOC_CATEGORY_HINTS[candidate]
    return ""


def promote(preview_path: Path, formal_path: Path, *, write: bool) -> dict:
    preview = json.loads(preview_path.read_text(encoding="utf-8"))
    entities_out: list[dict] = []
    seen_names: set[str] = set()

    for item in preview.get("entities") or []:
        name = str(item.get("name") or "").strip()
        entity_type = str(item.get("graph_type") or item.get("entity_type") or "").strip()
        if not name or not entity_type:
            continue
        if name in seen_names:
            continue
        seen_names.add(name)

        aliases_raw = item.get("alias_candidates") or item.get("aliases") or []
        aliases: list[str] = []
        for alias in aliases_raw:
            alias_name = str(alias or "").strip()
            if not alias_name or alias_name == name or alias_name in aliases:
                continue
            aliases.append(alias_name)

        entities_out.append(
            {
                "name": name,
                "entity_type": entity_type,
                "aliases": aliases,
                "doc_category": _infer_doc_category(name, aliases),
            }
        )

    type_by_name = {e["name"]: e["entity_type"] for e in entities_out}
    relations_out: list[dict] = []
    seen_edges: set[tuple[str, str, str]] = set()
    remap_actions: dict[str, int] = {}
    for item in preview.get("relations") or []:
        source = str(item.get("source") or "").strip()
        target = str(item.get("target") or "").strip()
        relation_type = str(item.get("relation_type") or "").strip()
        note = str(item.get("note") or "").strip()
        if not source or not target or not relation_type:
            continue
        source_type = type_by_name.get(source, "")
        target_type = type_by_name.get(target, "")
        source, relation_type, target, action = _normalize_relation(
            source, source_type, relation_type, target, target_type
        )
        remap_actions[action] = remap_actions.get(action, 0) + 1
        if action != "keep":
            note = f"{note} | remap:{action}" if note else f"remap:{action}"
        key = (source, relation_type, target)
        if key in seen_edges:
            continue
        seen_edges.add(key)
        row = {
            "source": source,
            "relation_type": relation_type,
            "target": target,
        }
        if note:
            row["note"] = note
        relations_out.append(row)

    formal = {
        "schema_version": 1,
        "source_ref": str(
            preview.get("source_ref")
            or "docs/3_待办清单/知识图谱语义抽取/已完成-第2.5轮-产品关系主干/产品关系材料/2026-07-16-产品实体分层清单-待业务确认.md"
        ),
        "promoted_from": "product_relation_backbone_preview.json",
        "promoted_at": datetime.now().strftime("%Y-%m-%d"),
        "entities": entities_out,
        "relations": relations_out,
    }

    summary = {
        "preview_path": str(preview_path),
        "formal_path": str(formal_path),
        "entity_count": len(entities_out),
        "relation_count": len(relations_out),
        "remap_actions": remap_actions,
        "write": write,
    }

    if not write:
        summary["formal_preview"] = {
            "schema_version": formal["schema_version"],
            "entity_count": len(entities_out),
            "relation_count": len(relations_out),
            "sample_entities": entities_out[:3],
            "sample_relations": relations_out[:3],
        }
        return summary

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if formal_path.exists():
        backup = BACKUP_DIR / f"product_relation_backbone.pre_replace_{stamp}.json"
        shutil.copy2(formal_path, backup)
        summary["formal_backup"] = str(backup)
    preview_backup = BACKUP_DIR / f"product_relation_backbone_preview.pre_confirm_{stamp}.json"
    shutil.copy2(preview_path, preview_backup)
    summary["preview_backup"] = str(preview_backup)

    formal_path.write_text(
        json.dumps(formal, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    preview["status"] = "confirmed"
    preview["confirmed_at"] = datetime.now().strftime("%Y-%m-%d")
    preview_path.write_text(
        json.dumps(preview, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    summary["preview_status"] = "confirmed"
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview", type=Path, default=PREVIEW)
    parser.add_argument("--formal", type=Path, default=FORMAL)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = promote(args.preview, args.formal, write=args.write)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
