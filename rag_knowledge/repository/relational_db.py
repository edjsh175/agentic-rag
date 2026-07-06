"""
关系数据库操作层 —— SQLite 三张表

MVP 极速版仅保留 3 张表：
  - entities:           实体表
  - relations:          关系边表
  - entity_chunk_links: 实体-知识块关联表

使用 sqlite3 标准库，单机部署，后续可迁移到 PostgreSQL。
"""
import uuid
import sqlite3
import logging
from datetime import datetime
from rag_knowledge.config import Config

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# 建表 DDL
# ------------------------------------------------------------------

DDL = """
CREATE TABLE IF NOT EXISTS entities (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    entity_type TEXT NOT NULL CHECK(entity_type IN ('功能模块','数据文件','配置项','API接口')),
    doc_category TEXT,
    created_by  TEXT NOT NULL DEFAULT 'system',
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS relations (
    id            TEXT PRIMARY KEY,
    source_id     TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    target_id     TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL CHECK(relation_type IN ('依赖','被使用于','包含','平级')),
    created_by    TEXT NOT NULL DEFAULT 'system',
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(source_id, target_id, relation_type)
);

CREATE TABLE IF NOT EXISTS entity_chunk_links (
    id        TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    chunk_id  TEXT NOT NULL,
    link_type TEXT NOT NULL CHECK(link_type IN ('主要描述','间接提及')),
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(entity_id, chunk_id)
);

CREATE INDEX IF NOT EXISTS idx_entities_type     ON entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_entities_category ON entities(doc_category);
CREATE INDEX IF NOT EXISTS idx_relations_source  ON relations(source_id);
CREATE INDEX IF NOT EXISTS idx_relations_target  ON relations(target_id);
CREATE INDEX IF NOT EXISTS idx_links_entity      ON entity_chunk_links(entity_id);
CREATE INDEX IF NOT EXISTS idx_links_chunk       ON entity_chunk_links(chunk_id);
"""


class RelationalDB:
    """SQLite 关系数据库封装（单例）"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        cfg = Config()
        self._db_path = str(cfg.relational_db_path)
        self._init_tables()
        self._initialized = True
        logger.info("关系数据库已初始化: %s", self._db_path)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        """获取数据库连接（WAL 模式 + 外键约束）"""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_tables(self):
        """创建三张表（IF NOT EXISTS）"""
        with self._get_conn() as conn:
            conn.executescript(DDL)

    @staticmethod
    def _uid() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def _now() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ------------------------------------------------------------------
    # 实体 CRUD
    # ------------------------------------------------------------------

    def create_entity(self, name: str, entity_type: str, doc_category: str = "",
                      created_by: str = "system") -> str:
        """
        创建实体，返回实体 ID。同名实体已存在时返回已有 ID（不抛异常）。

        参数：
          name:         实体名称（唯一）
          entity_type:  功能模块 / 数据文件 / 配置项 / API接口
          doc_category: 所属文档分类（可选）
          created_by:   system / admin
        """
        eid = self._uid()
        now = self._now()
        with self._get_conn() as conn:
            try:
                conn.execute(
                    "INSERT INTO entities (id, name, entity_type, doc_category, created_by, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (eid, name, entity_type, doc_category or None, created_by, now),
                )
            except sqlite3.IntegrityError:
                # 同名已存在 → 返回已有 ID
                row = conn.execute(
                    "SELECT id FROM entities WHERE name = ?", (name,)
                ).fetchone()
                if row:
                    return row["id"]
                raise
        logger.info("新建实体: %s (%s)", name, entity_type)
        return eid

    def list_entities(self, doc_category: str = "", entity_type: str = "") -> list[dict]:
        """查询实体列表，支持按分类和类型筛选"""
        sql = "SELECT * FROM entities WHERE 1=1"
        params: list = []
        if doc_category:
            sql += " AND doc_category = ?"
            params.append(doc_category)
        if entity_type:
            sql += " AND entity_type = ?"
            params.append(entity_type)
        sql += " ORDER BY created_at DESC"

        with self._get_conn() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def get_entity(self, entity_id: str) -> dict | None:
        """获取单个实体详情"""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM entities WHERE id = ?", (entity_id,)).fetchone()
        return dict(row) if row else None

    def get_entity_by_name(self, name: str) -> dict | None:
        """按名称查询实体"""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM entities WHERE name = ?", (name,)).fetchone()
        return dict(row) if row else None

    def update_entity(self, entity_id: str, name: str = "", entity_type: str = "",
                      doc_category: str = "") -> bool:
        """更新实体属性，只更新传入的非空字段"""
        fields: dict = {}
        if name:
            fields["name"] = name
        if entity_type:
            fields["entity_type"] = entity_type
        if doc_category:
            fields["doc_category"] = doc_category
        if not fields:
            return False

        sets = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [entity_id]
        with self._get_conn() as conn:
            conn.execute(f"UPDATE entities SET {sets} WHERE id = ?", values)
        return True

    def delete_entity(self, entity_id: str) -> bool:
        """删除实体（级联删除关联的关系和链接）"""
        with self._get_conn() as conn:
            cur = conn.execute("DELETE FROM entities WHERE id = ?", (entity_id,))
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # 关系 CRUD
    # ------------------------------------------------------------------

    def create_relation(self, source_id: str, target_id: str, relation_type: str,
                        created_by: str = "system") -> str | None:
        """
        创建实体间关系，返回关系 ID。已存在相同关系时返回 None。
        """
        rid = self._uid()
        now = self._now()
        with self._get_conn() as conn:
            try:
                conn.execute(
                    "INSERT INTO relations (id, source_id, target_id, relation_type, created_by, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (rid, source_id, target_id, relation_type, created_by, now),
                )
            except sqlite3.IntegrityError:
                return None
        logger.info("新建关系: %s →[%s]→ %s", source_id[:8], relation_type, target_id[:8])
        return rid

    def list_relations(self, entity_id: str = "") -> list[dict]:
        """查询关系列表，可按实体 ID 筛选"""
        if entity_id:
            sql = """SELECT r.*, s.name AS source_name, t.name AS target_name
                     FROM relations r
                     JOIN entities s ON r.source_id = s.id
                     JOIN entities t ON r.target_id = t.id
                     WHERE r.source_id = ? OR r.target_id = ?
                     ORDER BY r.created_at DESC"""
            with self._get_conn() as conn:
                return [dict(row) for row in conn.execute(sql, (entity_id, entity_id)).fetchall()]
        else:
            sql = """SELECT r.*, s.name AS source_name, t.name AS target_name
                     FROM relations r
                     JOIN entities s ON r.source_id = s.id
                     JOIN entities t ON r.target_id = t.id
                     ORDER BY r.created_at DESC"""
            with self._get_conn() as conn:
                return [dict(row) for row in conn.execute(sql).fetchall()]

    def delete_relation(self, relation_id: str) -> bool:
        """删除关系"""
        with self._get_conn() as conn:
            cur = conn.execute("DELETE FROM relations WHERE id = ?", (relation_id,))
            return cur.rowcount > 0

    def delete_relations_between(self, entity_id_a: str, entity_id_b: str) -> int:
        """删除两个实体之间的所有关系，返回删除数量"""
        with self._get_conn() as conn:
            cur = conn.execute(
                "DELETE FROM relations WHERE (source_id = ? AND target_id = ?) "
                "OR (source_id = ? AND target_id = ?)",
                (entity_id_a, entity_id_b, entity_id_b, entity_id_a),
            )
            return cur.rowcount

    # ------------------------------------------------------------------
    # 实体-知识块关联 CRUD
    # ------------------------------------------------------------------

    def create_link(self, entity_id: str, chunk_id: str, link_type: str = "主要描述") -> str | None:
        """
        关联实体与知识块，返回链接 ID。已存在相同关联时返回 None。
        """
        lid = self._uid()
        now = self._now()
        with self._get_conn() as conn:
            try:
                conn.execute(
                    "INSERT INTO entity_chunk_links (id, entity_id, chunk_id, link_type, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (lid, entity_id, chunk_id, link_type, now),
                )
            except sqlite3.IntegrityError:
                return None
        return lid

    def list_links(self, entity_id: str = "", chunk_id: str = "") -> list[dict]:
        """查询实体-知识块关联，支持按实体或 chunk 筛选"""
        sql = """SELECT l.*, e.name AS entity_name
                 FROM entity_chunk_links l
                 JOIN entities e ON l.entity_id = e.id
                 WHERE 1=1"""
        params: list = []
        if entity_id:
            sql += " AND l.entity_id = ?"
            params.append(entity_id)
        if chunk_id:
            sql += " AND l.chunk_id = ?"
            params.append(chunk_id)
        sql += " ORDER BY l.created_at DESC"

        with self._get_conn() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def delete_link(self, link_id: str) -> bool:
        """删除关联"""
        with self._get_conn() as conn:
            cur = conn.execute("DELETE FROM entity_chunk_links WHERE id = ?", (link_id,))
            return cur.rowcount > 0

    def delete_link_by_entity_chunk(self, entity_id: str, chunk_id: str) -> bool:
        """删除实体与知识块的特定关联"""
        with self._get_conn() as conn:
            cur = conn.execute(
                "DELETE FROM entity_chunk_links WHERE entity_id = ? AND chunk_id = ?",
                (entity_id, chunk_id),
            )
            return cur.rowcount > 0

    def get_link_by_entity_chunk(self, entity_id: str, chunk_id: str) -> dict | None:
        """获取特定实体与知识块的关联"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM entity_chunk_links WHERE entity_id = ? AND chunk_id = ?",
                (entity_id, chunk_id),
            ).fetchone()
            return dict(row) if row else None

    def get_relation_by_details(self, source_id: str, target_id: str, relation_type: str) -> dict | None:
        """按源、目标和关系类型查找关系"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM relations WHERE source_id = ? AND target_id = ? AND relation_type = ?",
                (source_id, target_id, relation_type),
            ).fetchone()
            return dict(row) if row else None

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------

    def stats(self) -> dict:
        """获取三张表的基本统计信息"""
        with self._get_conn() as conn:
            return {
                "entities": conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0],
                "relations": conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0],
                "entity_chunk_links": conn.execute("SELECT COUNT(*) FROM entity_chunk_links").fetchone()[0],
            }
