"""Normalize historical mojibake graph text to the canonical Chinese labels."""
from __future__ import annotations

import sqlite3

from rag_knowledge.repository.relational_db import RelationalDB


MOJIBAKE_TEXT_MAP: dict[str, str] = {
    "姝ｆ枃": "正文",
    "鏁版嵁瑙勮寖": "数据规范",
    "鏁版嵁缁撴瀯": "数据结构",
    "鏁版嵁鏍煎紡": "数据格式",
    "鏈嶅姟閰嶇疆": "服务配置",
    "閰嶇疆": "配置",
    "Stamp鏈嶅姟閮ㄧ讲": "Stamp服务部署",
    "绠＄嚎鍙戝竷鏈嶅姟": "管线发布服务",
    "绠＄嚎鍙戝竷宸ュ叿": "管线发布工具",
    "绠＄嚎鐐硅〃": "管线点表",
    "绠＄偣缂栧彿": "管点编号",
    "鍦伴潰楂樼▼": "地面高程",
    "鍦拌〃楂樼▼": "地表高程",
    "瀛楁鍚?": "字段名",
    "瀛楁鍚嶇О": "字段名称",
    "璇存槑": "说明",
    "鎻忚堪": "描述",
    "鍗曚綅": "单位",
    "鍊煎煙": "值域",
    "鍙栧€艰寖鍥?": "取值范围",
    "蹇呭～": "必填",
    "鏄?": "是",
    "蹇呰": "必要",
    "蹇呰鎬?": "必要性",
    "蹇呰瀛楁": "必要字段",
    "鍔熻兘妯″潡": "功能模块",
    "鏁版嵁鏂囦欢": "数据文件",
    "閰嶇疆椤?": "配置项",
    "API鎺ュ彛": "API接口",
    "渚濊禆": "依赖",
    "鍖呭惈": "包含",
    "琚娇鐢ㄤ簬": "被使用于",
    "骞崇骇": "平级",
    "涓昏鎻忚堪": "主要描述",
    "闂存帴鎻愬強": "间接提及",
    "鏂囩珷闄勪欢": "文章附件",
    "宸插彂甯冩枃绔?": "已发布文章",
}


def normalize_graph_text(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return text
    mapped = MOJIBAKE_TEXT_MAP.get(text)
    if mapped:
        return mapped
    if "." in text:
        return ".".join(normalize_graph_text(part) for part in text.split("."))
    return text


class GraphTextMigration:
    """Repair mojibake graph labels in-place and merge duplicates safely."""

    def __init__(self, db: RelationalDB | None = None):
        self.db = db or RelationalDB()

    def apply(self) -> dict[str, int | bool]:
        stats: dict[str, int | bool] = {
            "entities_renamed": 0,
            "entities_merged": 0,
            "aliases_renamed": 0,
            "fields_renamed": 0,
            "ok": False,
        }
        with self.db._get_conn() as conn:
            self._rename_entities(conn, stats)
            self._rename_aliases(conn, stats)
            self._rename_fields(conn, stats)
        stats["ok"] = True
        return stats

    def _rename_entities(self, conn: sqlite3.Connection, stats: dict[str, int | bool]) -> None:
        rows = conn.execute(
            "SELECT id, name, canonical_name FROM entities ORDER BY created_at ASC, id ASC"
        ).fetchall()
        for row in rows:
            current = conn.execute("SELECT id, name, canonical_name FROM entities WHERE id = ?", (row["id"],)).fetchone()
            if current is None:
                continue
            desired_name = normalize_graph_text(current["name"])
            desired_canonical = normalize_graph_text(current["canonical_name"] or desired_name)
            if desired_name != current["name"]:
                existing = conn.execute(
                    "SELECT id FROM entities WHERE name = ? AND id != ?",
                    (desired_name, current["id"]),
                ).fetchone()
                if existing:
                    self._merge_entity(conn, str(current["id"]), str(existing["id"]), stats)
                    current = conn.execute("SELECT id, name, canonical_name FROM entities WHERE id = ?", (existing["id"],)).fetchone()
                    desired_canonical = normalize_graph_text((current["canonical_name"] if current else "") or desired_name)
                else:
                    conn.execute(
                        "UPDATE entities SET name = ?, canonical_name = ?, updated_at = ? WHERE id = ?",
                        (desired_name, desired_canonical, self.db._now(), current["id"]),
                    )
                    stats["entities_renamed"] += 1
                    current = conn.execute("SELECT id, name, canonical_name FROM entities WHERE id = ?", (current["id"],)).fetchone()
            if current and desired_canonical != (current["canonical_name"] or ""):
                conn.execute(
                    "UPDATE entities SET canonical_name = ?, updated_at = ? WHERE id = ?",
                    (desired_canonical, self.db._now(), current["id"]),
                )

    def _rename_aliases(self, conn: sqlite3.Connection, stats: dict[str, int | bool]) -> None:
        rows = conn.execute("SELECT id, entity_id, alias FROM aliases ORDER BY created_at ASC, id ASC").fetchall()
        for row in rows:
            desired = normalize_graph_text(row["alias"])
            if desired == row["alias"]:
                continue
            existing = conn.execute(
                "SELECT id FROM aliases WHERE entity_id = ? AND alias = ? AND id != ?",
                (row["entity_id"], desired, row["id"]),
            ).fetchone()
            if existing:
                conn.execute("DELETE FROM aliases WHERE id = ?", (row["id"],))
            else:
                conn.execute("UPDATE aliases SET alias = ? WHERE id = ?", (desired, row["id"]))
            stats["aliases_renamed"] += 1

    def _rename_fields(self, conn: sqlite3.Connection, stats: dict[str, int | bool]) -> None:
        rows = conn.execute(
            "SELECT id, table_entity_id, field_name, field_entity_id FROM fields ORDER BY created_at ASC, id ASC"
        ).fetchall()
        for row in rows:
            desired = normalize_graph_text(row["field_name"])
            if desired == row["field_name"]:
                continue
            existing = conn.execute(
                "SELECT id FROM fields WHERE table_entity_id = ? AND field_name = ? AND id != ?",
                (row["table_entity_id"], desired, row["id"]),
            ).fetchone()
            if existing:
                conn.execute("DELETE FROM fields WHERE id = ?", (row["id"],))
            else:
                conn.execute("UPDATE fields SET field_name = ? WHERE id = ?", (desired, row["id"]))
            stats["fields_renamed"] += 1

    def _merge_entity(self, conn: sqlite3.Connection, source_id: str, target_id: str,
                      stats: dict[str, int | bool]) -> None:
        if source_id == target_id:
            return
        self._move_aliases(conn, source_id, target_id, stats)
        self._move_links(conn, source_id, target_id)
        self._move_relations(conn, source_id, target_id)
        self._move_fields(conn, source_id, target_id)
        self._move_procedures(conn, source_id, target_id)
        conn.execute("DELETE FROM entities WHERE id = ?", (source_id,))
        stats["entities_merged"] += 1

    def _move_aliases(self, conn: sqlite3.Connection, source_id: str, target_id: str,
                      stats: dict[str, int | bool]) -> None:
        rows = conn.execute("SELECT * FROM aliases WHERE entity_id = ?", (source_id,)).fetchall()
        for row in rows:
            desired = normalize_graph_text(row["alias"])
            existing = conn.execute(
                "SELECT id FROM aliases WHERE entity_id = ? AND alias = ?",
                (target_id, desired),
            ).fetchone()
            if existing:
                conn.execute("DELETE FROM aliases WHERE id = ?", (row["id"],))
                if desired != row["alias"]:
                    stats["aliases_renamed"] += 1
                continue
            conn.execute(
                "UPDATE aliases SET entity_id = ?, alias = ? WHERE id = ?",
                (target_id, desired, row["id"]),
            )
            if desired != row["alias"]:
                stats["aliases_renamed"] += 1

    @staticmethod
    def _move_links(conn: sqlite3.Connection, source_id: str, target_id: str) -> None:
        rows = conn.execute("SELECT id, chunk_id FROM entity_chunk_links WHERE entity_id = ?", (source_id,)).fetchall()
        for row in rows:
            existing = conn.execute(
                "SELECT id FROM entity_chunk_links WHERE entity_id = ? AND chunk_id = ?",
                (target_id, row["chunk_id"]),
            ).fetchone()
            if existing:
                conn.execute("DELETE FROM entity_chunk_links WHERE id = ?", (row["id"],))
            else:
                conn.execute(
                    "UPDATE entity_chunk_links SET entity_id = ? WHERE id = ?",
                    (target_id, row["id"]),
                )

    @staticmethod
    def _move_relations(conn: sqlite3.Connection, source_id: str, target_id: str) -> None:
        rows = conn.execute(
            "SELECT id, source_entity_id, target_entity_id, relation_type FROM relations "
            "WHERE source_entity_id = ? OR target_entity_id = ?",
            (source_id, source_id),
        ).fetchall()
        for row in rows:
            new_source = target_id if row["source_entity_id"] == source_id else row["source_entity_id"]
            new_target = target_id if row["target_entity_id"] == source_id else row["target_entity_id"]
            if new_source == new_target:
                conn.execute("DELETE FROM relations WHERE id = ?", (row["id"],))
                continue
            existing = conn.execute(
                "SELECT id FROM relations WHERE source_entity_id = ? AND target_entity_id = ? AND relation_type = ? AND id != ?",
                (new_source, new_target, row["relation_type"], row["id"]),
            ).fetchone()
            if existing:
                conn.execute("DELETE FROM relations WHERE id = ?", (row["id"],))
            else:
                conn.execute(
                    "UPDATE relations SET source_entity_id = ?, target_entity_id = ? WHERE id = ?",
                    (new_source, new_target, row["id"]),
                )

    def _move_fields(self, conn: sqlite3.Connection, source_id: str, target_id: str) -> None:
        table_rows = conn.execute(
            "SELECT * FROM fields WHERE table_entity_id = ? ORDER BY created_at ASC, id ASC",
            (source_id,),
        ).fetchall()
        for row in table_rows:
            desired_name = normalize_graph_text(row["field_name"])
            existing = conn.execute(
                "SELECT id FROM fields WHERE table_entity_id = ? AND field_name = ? AND id != ?",
                (target_id, desired_name, row["id"]),
            ).fetchone()
            if existing:
                conn.execute("DELETE FROM fields WHERE id = ?", (row["id"],))
            else:
                conn.execute(
                    "UPDATE fields SET table_entity_id = ?, field_name = ? WHERE id = ?",
                    (target_id, desired_name, row["id"]),
                )
        conn.execute(
            "UPDATE fields SET field_entity_id = ? WHERE field_entity_id = ?",
            (target_id, source_id),
        )

    @staticmethod
    def _move_procedures(conn: sqlite3.Connection, source_id: str, target_id: str) -> None:
        conn.execute("UPDATE procedures SET entity_id = ? WHERE entity_id = ?", (target_id, source_id))
