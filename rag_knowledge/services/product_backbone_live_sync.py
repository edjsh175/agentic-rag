"""Export live product-backbone facts into product_relation_backbone.json."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from rag_knowledge.repository.relational_db import RelationalDB

logger = logging.getLogger(__name__)

SEED_CREATED_BY_PREFIX = "seed:product_backbone"
DEFAULT_BACKBONE_PATH = Path(__file__).resolve().parents[2] / "data" / "product_relation_backbone.json"


def _is_seed_created_by(value: str | None) -> bool:
    return str(value or "").startswith(SEED_CREATED_BY_PREFIX)


class ProductBackboneLiveSyncService:
    """Keep official backbone JSON aligned with live SQLite (formal admin edits)."""

    def __init__(self, db: RelationalDB | None = None, path: str | Path | None = None):
        self.db = db or RelationalDB()
        self.path = Path(path) if path else DEFAULT_BACKBONE_PATH

    def load_existing(self) -> dict:
        if not self.path.is_file():
            return {
                "schema_version": 1,
                "source_ref": "",
                "entities": [],
                "relations": [],
            }
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("product_relation_backbone root must be an object")
        return data

    def backbone_entity_names(self, existing: dict | None = None) -> set[str]:
        existing = existing if existing is not None else self.load_existing()
        names: set[str] = set()
        for item in existing.get("entities") or []:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                if name:
                    names.add(name)
        with self.db._get_conn() as conn:
            for row in conn.execute(
                "SELECT name FROM entities "
                "WHERE review_status = 'approved' AND created_by LIKE ?",
                (f"{SEED_CREATED_BY_PREFIX}%",),
            ):
                name = str(row["name"] or "").strip()
                if name:
                    names.add(name)
            for row in conn.execute(
                """
                SELECT s.name AS source_name, t.name AS target_name
                FROM relations r
                JOIN entities s ON s.id = r.source_entity_id
                JOIN entities t ON t.id = r.target_entity_id
                WHERE r.review_status = 'approved' AND r.created_by LIKE ?
                """,
                (f"{SEED_CREATED_BY_PREFIX}%",),
            ):
                for key in ("source_name", "target_name"):
                    name = str(row[key] or "").strip()
                    if name:
                        names.add(name)
        return names

    def entity_is_backbone(self, entity: dict | None, *, names: set[str] | None = None) -> bool:
        if not entity:
            return False
        if _is_seed_created_by(entity.get("created_by")):
            return True
        name = str(entity.get("name") or "").strip()
        if not name:
            return False
        names = names if names is not None else self.backbone_entity_names()
        return name in names

    def should_sync_for_entities(self, entity_ids: list[str] | tuple[str, ...] | None) -> bool:
        if not entity_ids:
            return False
        names = self.backbone_entity_names()
        for entity_id in entity_ids:
            entity = self.db.get_entity(str(entity_id))
            if self.entity_is_backbone(entity, names=names):
                return True
        return False

    def should_sync_for_relation(self, relation_id: str | None = None, *, relation: dict | None = None) -> bool:
        row = relation
        if row is None and relation_id:
            with self.db._get_conn() as conn:
                found = conn.execute("SELECT * FROM relations WHERE id = ?", (relation_id,)).fetchone()
                row = dict(found) if found else None
        if not row:
            # Deleted relation: sync if JSON still has backbone content (safe full rewrite).
            return self.path.is_file()
        if _is_seed_created_by(row.get("created_by")):
            return True
        source = self.db.get_entity(row.get("source_entity_id") or "")
        target = self.db.get_entity(row.get("target_entity_id") or "")
        names = self.backbone_entity_names()
        return self.entity_is_backbone(source, names=names) and self.entity_is_backbone(target, names=names)

    def build_payload(self) -> dict:
        existing = self.load_existing()
        names = self.backbone_entity_names(existing)
        old_notes = {
            (
                str(item.get("source") or "").strip(),
                str(item.get("relation_type") or "").strip(),
                str(item.get("target") or "").strip(),
            ): str(item.get("note") or "").strip()
            for item in (existing.get("relations") or [])
            if isinstance(item, dict)
        }
        old_entity_meta = {
            str(item.get("name") or "").strip(): item
            for item in (existing.get("entities") or [])
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        }

        with self.db._get_conn() as conn:
            seed_rel_rows = conn.execute(
                """
                SELECT s.name AS source_name, t.name AS target_name, r.relation_type, r.created_by
                FROM relations r
                JOIN entities s ON s.id = r.source_entity_id
                JOIN entities t ON t.id = r.target_entity_id
                WHERE r.review_status = 'approved' AND r.created_by LIKE ?
                ORDER BY s.name, r.relation_type, t.name
                """,
                (f"{SEED_CREATED_BY_PREFIX}%",),
            ).fetchall()
            admin_rel_rows = []
            if names:
                placeholders = ",".join("?" for _ in names)
                name_list = sorted(names)
                admin_rel_rows = conn.execute(
                    f"""
                    SELECT s.name AS source_name, t.name AS target_name, r.relation_type, r.created_by
                    FROM relations r
                    JOIN entities s ON s.id = r.source_entity_id
                    JOIN entities t ON t.id = r.target_entity_id
                    WHERE r.review_status = 'approved'
                      AND r.created_by = 'admin'
                      AND s.name IN ({placeholders})
                      AND t.name IN ({placeholders})
                    ORDER BY s.name, r.relation_type, t.name
                    """,
                    (*name_list, *name_list),
                ).fetchall()

        relation_keys: dict[tuple[str, str, str], dict] = {}
        for row in list(seed_rel_rows) + list(admin_rel_rows):
            source = str(row["source_name"] or "").strip()
            target = str(row["target_name"] or "").strip()
            relation_type = str(row["relation_type"] or "").strip()
            if not source or not target or not relation_type:
                continue
            names.add(source)
            names.add(target)
            key = (source, relation_type, target)
            item = {
                "source": source,
                "relation_type": relation_type,
                "target": target,
            }
            note = old_notes.get(key) or ""
            if note:
                item["note"] = note
            elif str(row["created_by"] or "") == "admin":
                item["note"] = "synced_from_live:admin"
            relation_keys[key] = item

        entities_out: list[dict] = []
        for name in sorted(names):
            entity = self.db.get_entity_by_name(name)
            if not entity or entity.get("review_status") != "approved":
                continue
            aliases = [
                str(alias.get("alias") or "").strip()
                for alias in self.db.list_aliases(entity["id"])
                if alias.get("review_status") == "approved" and str(alias.get("alias") or "").strip()
            ]
            # Keep stable unique alias order.
            seen_alias: set[str] = set()
            alias_list: list[str] = []
            for alias in aliases:
                key = alias.casefold()
                if key in seen_alias:
                    continue
                seen_alias.add(key)
                alias_list.append(alias)
            old = old_entity_meta.get(name) or {}
            entities_out.append(
                {
                    "name": name,
                    "entity_type": str(entity.get("entity_type") or old.get("entity_type") or "").strip(),
                    "aliases": alias_list,
                    "doc_category": str(entity.get("doc_category") or old.get("doc_category") or "").strip(),
                }
            )

        relations_out = [relation_keys[key] for key in sorted(relation_keys.keys())]
        payload = {
            "schema_version": 1,
            "source_ref": str(existing.get("source_ref") or ""),
            "promoted_from": str(existing.get("promoted_from") or ""),
            "promoted_at": str(existing.get("promoted_at") or ""),
            "synced_from_live_at": datetime.now(timezone.utc).isoformat(),
            "entities": entities_out,
            "relations": relations_out,
        }
        return payload

    def write_payload(self, payload: dict) -> dict:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self.path)
        summary = {
            "path": str(self.path),
            "entities": len(payload.get("entities") or []),
            "relations": len(payload.get("relations") or []),
            "synced_from_live_at": payload.get("synced_from_live_at"),
        }
        logger.info(
            "product_backbone_live_sync wrote %s | entities=%d relations=%d",
            self.path,
            summary["entities"],
            summary["relations"],
        )
        return summary

    def export_from_live(self) -> dict:
        payload = self.build_payload()
        return self.write_payload(payload)

    def sync_after_entity_change(self, *entity_ids: str) -> dict | None:
        ids = [str(item) for item in entity_ids if item]
        if not self.should_sync_for_entities(ids):
            return None
        return self.export_from_live()

    def sync_after_relation_change(
        self,
        relation_id: str | None = None,
        *,
        relation_before: dict | None = None,
        source_id: str | None = None,
        target_id: str | None = None,
    ) -> dict | None:
        if relation_before is not None:
            if self.should_sync_for_relation(relation=relation_before):
                return self.export_from_live()
        if relation_id and self.should_sync_for_relation(relation_id=relation_id):
            return self.export_from_live()
        # create path: relation exists; also allow endpoint-based check
        if source_id or target_id:
            if self.should_sync_for_entities([x for x in (source_id, target_id) if x]):
                # only when BOTH ends are backbone
                names = self.backbone_entity_names()
                source = self.db.get_entity(source_id or "") if source_id else None
                target = self.db.get_entity(target_id or "") if target_id else None
                if self.entity_is_backbone(source, names=names) and self.entity_is_backbone(target, names=names):
                    return self.export_from_live()
        return None

    def sync_after_alias_change(self, entity_id: str) -> dict | None:
        return self.sync_after_entity_change(entity_id)
