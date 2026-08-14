"""SQLite relational database client for storing knowledge graph elements."""
from __future__ import annotations

from datetime import datetime
import json
import logging
import sqlite3
import uuid

from rag_knowledge.config import Config
from rag_knowledge.models.graph_schema import normalize_entity_name

logger = logging.getLogger(__name__)


class RelationalDB:
    """SQLite relational database client (Singleton)."""

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
        logger.info("Initialized relational database: %s", self._db_path)

    def get_schema_version(self) -> int:
        """Return the database schema version from schema_version table."""
        with self._get_conn() as conn:
            try:
                row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
                return row[0] if (row and row[0] is not None) else 0
            except sqlite3.OperationalError:
                return 0

    def _get_conn(self) -> sqlite3.Connection:
        """Create a connection with WAL and Foreign Key constraints."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_tables(self):
        """Run database migrations to initialize/upgrade database schema."""
        with self._get_conn() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_version ("
                "  version INTEGER PRIMARY KEY,"
                "  applied_at TEXT NOT NULL"
                ")"
            )
            row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
            current_version = row[0] if (row and row[0] is not None) else 0

            # Retrofit check: if schema_version is empty but entities table exists
            if current_version == 0:
                has_entities = conn.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='entities'"
                ).fetchone()[0]
                if has_entities:
                    has_batches = conn.execute(
                        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='extraction_batches'"
                    ).fetchone()[0]
                    has_aliases = conn.execute(
                        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='aliases'"
                    ).fetchone()[0]
                    if has_batches:
                        current_version = 3
                    elif has_aliases:
                        current_version = 2
                    else:
                        current_version = 1
                    for v in range(1, current_version + 1):
                        conn.execute(
                            "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
                            (v, self._now()),
                        )

            migrations = {
                1: self._migration_v1,
                2: self._migration_v2,
                3: self._migration_v3,
                4: self._migration_v4,
                5: self._migration_v5,
            }

            for version, migration_func in sorted(migrations.items()):
                if version > current_version:
                    logger.info("Applying database migration version %d", version)
                    migration_func(conn)
                    conn.execute(
                        "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                        (version, self._now()),
                    )
                    logger.info("Successfully applied database migration version %d", version)

    def _migration_v1(self, conn: sqlite3.Connection):
        """V1 legacy schema placeholder."""
        pass

    def _migration_v2(self, conn: sqlite3.Connection):
        """V2 schema: extended columns, aliases, and indexing."""
        # 1. Update entities table columns if missing
        has_entities = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='entities'"
        ).fetchone()[0]
        if has_entities:
            cursor = conn.execute("PRAGMA table_info(entities)")
            existing_cols = {row["name"] for row in cursor.fetchall()}

            expected_entities_cols = {
                "canonical_name": "TEXT",
                "description": "TEXT DEFAULT ''",
                "properties_json": "TEXT DEFAULT '{}'",
                "doc_category": "TEXT DEFAULT ''",
                "confidence": "REAL DEFAULT 1.0",
                "review_status": "TEXT DEFAULT 'approved'",
                "created_by": "TEXT DEFAULT 'system'",
                "created_at": "TEXT",
                "updated_at": "TEXT",
            }
            for col, col_type in expected_entities_cols.items():
                if col not in existing_cols:
                    if col in ("created_at", "updated_at"):
                        conn.execute(f"ALTER TABLE entities ADD COLUMN {col} {col_type} DEFAULT (datetime('now','localtime'))")
                    else:
                        conn.execute(f"ALTER TABLE entities ADD COLUMN {col} {col_type}")

        # 2. Update relations table columns if missing
        has_relations = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='relations'"
        ).fetchone()[0]
        if has_relations:
            cursor = conn.execute("PRAGMA table_info(relations)")
            existing_relations_cols = {row["name"] for row in cursor.fetchall()}

            expected_relations_cols = {
                "properties_json": "TEXT DEFAULT '{}'",
                "confidence": "REAL DEFAULT 1.0",
                "evidence_text": "TEXT DEFAULT ''",
                "source_chunk_id": "TEXT DEFAULT ''",
                "review_status": "TEXT DEFAULT 'approved'",
                "created_by": "TEXT DEFAULT 'system'",
                "created_at": "TEXT",
            }
            for col, col_type in expected_relations_cols.items():
                if col not in existing_relations_cols:
                    if col == "created_at":
                        conn.execute(f"ALTER TABLE relations ADD COLUMN {col} {col_type} DEFAULT (datetime('now','localtime'))")
                    else:
                        conn.execute(f"ALTER TABLE relations ADD COLUMN {col} {col_type}")

        # 3. Update entity_chunk_links columns if missing
        has_links = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='entity_chunk_links'"
        ).fetchone()[0]
        if has_links:
            cursor = conn.execute("PRAGMA table_info(entity_chunk_links)")
            existing_links_cols = {row["name"] for row in cursor.fetchall()}

            expected_links_cols = {
                "link_type": "TEXT DEFAULT 'primary'",
                "section_path": "TEXT DEFAULT ''",
                "page_label": "TEXT DEFAULT ''",
                "evidence_text": "TEXT DEFAULT ''",
                "source": "TEXT DEFAULT ''",
                "created_at": "TEXT",
            }
            for col, col_type in expected_links_cols.items():
                if col not in existing_links_cols:
                    if col == "created_at":
                        conn.execute(f"ALTER TABLE entity_chunk_links ADD COLUMN {col} {col_type} DEFAULT (datetime('now','localtime'))")
                    else:
                        conn.execute(f"ALTER TABLE entity_chunk_links ADD COLUMN {col} {col_type}")

        # 4. Now execute the DDL to create new tables/indices if they don't exist
        from rag_knowledge.models.graph_schema import KG_DDL_V2
        conn.executescript(KG_DDL_V2)

    def _migration_v3(self, conn: sqlite3.Connection):
        """V3 Schema: extraction staging tables."""
        from rag_knowledge.models.graph_schema import KG_DDL_V3_STAGING
        conn.executescript(KG_DDL_V3_STAGING)

    def _migration_v4(self, conn: sqlite3.Connection):
        """V4 Schema: user_feedbacks table for quality control feedback loop."""
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS user_feedbacks ("
            "  id TEXT PRIMARY KEY,"
            "  user_id TEXT NOT NULL,"
            "  query_text TEXT NOT NULL,"
            "  answer_text TEXT NOT NULL,"
            "  referenced_chunk_ids TEXT NOT NULL DEFAULT '[]',"
            "  rating TEXT NOT NULL,"
            "  reason TEXT DEFAULT '',"
            "  trace_id TEXT DEFAULT '',"
            "  created_at TEXT DEFAULT (datetime('now', 'localtime'))"
            ");"
            "CREATE INDEX IF NOT EXISTS idx_user_feedbacks_rating ON user_feedbacks(rating);"
            "CREATE INDEX IF NOT EXISTS idx_user_feedbacks_created_at ON user_feedbacks(created_at);"
        )

    def _migration_v5(self, conn: sqlite3.Connection):
        """V5 Schema: feedback_scope, target_chunk_id, and indexes for fine-grained feedback."""
        cursor = conn.execute("PRAGMA table_info(user_feedbacks)")
        columns = {row["name"] for row in cursor.fetchall()}
        if "feedback_scope" not in columns:
            conn.execute("ALTER TABLE user_feedbacks ADD COLUMN feedback_scope TEXT NOT NULL DEFAULT 'answer'")
        if "target_chunk_id" not in columns:
            conn.execute("ALTER TABLE user_feedbacks ADD COLUMN target_chunk_id TEXT DEFAULT ''")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_user_feedbacks_target_chunk_id ON user_feedbacks(target_chunk_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_user_feedbacks_scope ON user_feedbacks(feedback_scope);")

    @staticmethod
    def _uid() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def _now() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ------------------------------------------------------------------
    # Entity CRUD
    # ------------------------------------------------------------------

    def create_entity(
        self,
        name: str,
        entity_type: str,
        doc_category: str = "",
        canonical_name: str = "",
        description: str = "",
        properties_json: str = "{}",
        confidence: float = 1.0,
        review_status: str = "approved",
        created_by: str = "system",
    ) -> str:
        eid = self._uid()
        now = self._now()
        with self._get_conn() as conn:
            try:
                conn.execute(
                    "INSERT INTO entities (id, name, canonical_name, entity_type, description, properties_json, doc_category, confidence, review_status, created_by, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        eid,
                        name,
                        canonical_name or name,
                        entity_type,
                        description,
                        properties_json,
                        doc_category,
                        confidence,
                        review_status,
                        created_by,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError:
                row = conn.execute("SELECT id FROM entities WHERE name = ?", (name,)).fetchone()
                if row:
                    return str(row["id"])
        return eid

    def get_entity(self, entity_id: str) -> dict | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM entities WHERE id = ?", (entity_id,)).fetchone()
            return dict(row) if row else None

    def get_entity_by_name(self, name: str) -> dict | None:
        norm_name = normalize_entity_name(name)
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM entities WHERE name = ?", (norm_name,)).fetchone()
            return dict(row) if row else None

    def list_entities(self, review_status: str = "") -> list[dict]:
        sql = "SELECT * FROM entities"
        params = []
        if review_status:
            sql += " WHERE review_status = ?"
            params.append(review_status)
        sql += " ORDER BY created_at DESC"
        with self._get_conn() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def update_entity(
        self,
        entity_id: str,
        name: str = "",
        entity_type: str = "",
        doc_category: str = "",
        canonical_name: str = "",
        description: str = "",
        properties_json: str = "",
        confidence: float | None = None,
        review_status: str = "",
    ) -> bool:
        fields = {}
        if name:
            fields["name"] = name
        if entity_type:
            fields["entity_type"] = entity_type
        if doc_category:
            fields["doc_category"] = doc_category
        if canonical_name:
            fields["canonical_name"] = canonical_name
        if description:
            fields["description"] = description
        if properties_json:
            fields["properties_json"] = properties_json
        if confidence is not None:
            fields["confidence"] = confidence
        if review_status:
            fields["review_status"] = review_status
        if not fields:
            return False
        fields["updated_at"] = self._now()
        sets = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [entity_id]
        with self._get_conn() as conn:
            cur = conn.execute(f"UPDATE entities SET {sets} WHERE id = ?", values)
            return cur.rowcount > 0

    def delete_entity(self, entity_id: str) -> bool:
        with self._get_conn() as conn:
            cur = conn.execute("DELETE FROM entities WHERE id = ?", (entity_id,))
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Relation CRUD
    # ------------------------------------------------------------------

    def create_relation(
        self,
        source_id: str,
        target_id: str,
        relation_type: str,
        properties_json: str = "{}",
        confidence: float = 1.0,
        evidence_text: str = "",
        source_chunk_id: str = "",
        review_status: str = "approved",
        created_by: str = "system",
    ) -> str | None:
        rid = self._uid()
        now = self._now()
        with self._get_conn() as conn:
            try:
                conn.execute(
                    "INSERT INTO relations (id, source_entity_id, target_entity_id, relation_type, properties_json, confidence, evidence_text, source_chunk_id, review_status, created_by, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        rid,
                        source_id,
                        target_id,
                        relation_type,
                        properties_json,
                        confidence,
                        evidence_text,
                        source_chunk_id,
                        review_status,
                        created_by,
                        now,
                    ),
                )
            except sqlite3.IntegrityError:
                return None
        logger.info("New relation: %s ->[%s]-> %s", source_id[:8], relation_type, target_id[:8])
        return rid

    def list_relations(self, entity_id: str = "", relation_type: str = "", review_status: str = "") -> list[dict]:
        sql = """SELECT r.id, r.source_entity_id, r.target_entity_id, r.relation_type, r.properties_json,
                        r.confidence, r.evidence_text, r.source_chunk_id, r.review_status, r.created_by, r.created_at,
                        s.name AS source_name, s.entity_type AS source_type,
                        t.name AS target_name, t.entity_type AS target_type
                 FROM relations r
                 JOIN entities s ON r.source_entity_id = s.id
                 JOIN entities t ON r.target_entity_id = t.id
                 WHERE 1=1"""
        params = []
        if entity_id:
            sql += " AND (r.source_entity_id = ? OR r.target_entity_id = ?)"
            params.extend([entity_id, entity_id])
        if relation_type:
            sql += " AND r.relation_type = ?"
            params.append(relation_type)
        if review_status:
            sql += " AND r.review_status = ?"
            params.append(review_status)
        sql += " ORDER BY r.created_at DESC"
        with self._get_conn() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def delete_relation(self, relation_id: str) -> bool:
        with self._get_conn() as conn:
            cur = conn.execute("DELETE FROM relations WHERE id = ?", (relation_id,))
            return cur.rowcount > 0

    def delete_relations_between(self, entity_id_a: str, entity_id_b: str) -> int:
        with self._get_conn() as conn:
            cur = conn.execute(
                "DELETE FROM relations WHERE (source_entity_id = ? AND target_entity_id = ?) "
                "OR (source_entity_id = ? AND target_entity_id = ?)",
                (entity_id_a, entity_id_b, entity_id_b, entity_id_a),
            )
            return cur.rowcount

    def get_relation_by_details(self, source_id: str, target_id: str, relation_type: str) -> dict | None:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM relations WHERE source_entity_id = ? AND target_entity_id = ? AND relation_type = ?",
                (source_id, target_id, relation_type),
            ).fetchone()
            return dict(row) if row else None

    # ------------------------------------------------------------------
    # Entity-Chunk link CRUD
    # ------------------------------------------------------------------

    def create_link(
        self,
        entity_id: str,
        chunk_id: str,
        link_type: str = "primary",
        section_path: str = "",
        page_label: str = "",
        evidence_text: str = "",
        source: str = "",
    ) -> str | None:
        lid = self._uid()
        now = self._now()
        with self._get_conn() as conn:
            try:
                conn.execute(
                    "INSERT INTO entity_chunk_links (id, entity_id, chunk_id, link_type, section_path, page_label, evidence_text, source, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (lid, entity_id, chunk_id, link_type, section_path, page_label, evidence_text, source, now),
                )
            except sqlite3.IntegrityError:
                return None
        return lid

    def list_links(self, entity_id: str = "", chunk_id: str = "") -> list[dict]:
        sql = """SELECT l.*, e.name AS entity_name
                 FROM entity_chunk_links l
                 JOIN entities e ON l.entity_id = e.id
                 WHERE 1=1"""
        params = []
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
        with self._get_conn() as conn:
            cur = conn.execute("DELETE FROM entity_chunk_links WHERE id = ?", (link_id,))
            return cur.rowcount > 0

    def delete_link_by_entity_chunk(self, entity_id: str, chunk_id: str) -> bool:
        with self._get_conn() as conn:
            cur = conn.execute(
                "DELETE FROM entity_chunk_links WHERE entity_id = ? AND chunk_id = ?",
                (entity_id, chunk_id),
            )
            return cur.rowcount > 0

    def get_link_by_entity_chunk(self, entity_id: str, chunk_id: str) -> dict | None:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM entity_chunk_links WHERE entity_id = ? AND chunk_id = ?",
                (entity_id, chunk_id),
            ).fetchone()
            return dict(row) if row else None

    # ------------------------------------------------------------------
    # Alias CRUD
    # ------------------------------------------------------------------

    def create_alias(
        self,
        entity_id: str,
        alias: str,
        confidence: float = 1.0,
        source_chunk_id: str = "",
        evidence_text: str = "",
        review_status: str = "pending",
    ) -> str | None:
        aid = self._uid()
        now = self._now()
        with self._get_conn() as conn:
            try:
                conn.execute(
                    "INSERT INTO aliases (id, entity_id, alias, confidence, source_chunk_id, evidence_text, review_status, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (aid, entity_id, alias, confidence, source_chunk_id, evidence_text, review_status, now),
                )
            except sqlite3.IntegrityError:
                return None
        return aid

    def list_aliases(self, entity_id: str = "") -> list[dict]:
        sql = "SELECT * FROM aliases"
        params = []
        if entity_id:
            sql += " WHERE entity_id = ?"
            params.append(entity_id)
        sql += " ORDER BY created_at DESC"
        with self._get_conn() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def delete_alias(self, alias_id: str) -> bool:
        with self._get_conn() as conn:
            cur = conn.execute("DELETE FROM aliases WHERE id = ?", (alias_id,))
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Batch/Staging Operations (Phase B / Extraction Pipeline)
    # ------------------------------------------------------------------

    def create_extraction_batch(self, mode: str, filters: dict, source_snapshot_hash: str) -> str:
        bid = self._uid()
        now = self._now()
        filters_json = json.dumps(filters, ensure_ascii=False, sort_keys=True)
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO extraction_batches (id, mode, status, source_snapshot_hash, filters_json, created_at) "
                "VALUES (?, ?, 'draft', ?, ?, ?)",
                (bid, mode, source_snapshot_hash, filters_json, now),
            )
        return bid

    def get_extraction_batch(self, batch_id: str) -> dict | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM extraction_batches WHERE id = ?", (batch_id,)).fetchone()
            return dict(row) if row else None

    def list_extraction_batches(self, status: str = "") -> list[dict]:
        with self._get_conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM extraction_batches WHERE status = ? ORDER BY created_at DESC",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM extraction_batches ORDER BY created_at DESC").fetchall()
            return [dict(row) for row in rows]

    def set_extraction_batch_status(self, batch_id: str, status: str, error_text: str = "") -> bool:
        if status == "approved":
            # Check for any remaining pending candidates
            with self._get_conn() as conn:
                pending_count = conn.execute(
                    "SELECT COUNT(*) FROM extraction_candidates WHERE batch_id = ? AND status = 'pending'",
                    (batch_id,),
                ).fetchone()[0]
                if pending_count > 0:
                    raise ValueError("Cannot approve batch with pending candidates")

        now = self._now()
        applied_at = now if status == "applied" else None
        reviewed_at = now if status == "approved" else None
        with self._get_conn() as conn:
            cur = conn.execute(
                "UPDATE extraction_batches SET status = ?, error_text = ?, reviewed_at = COALESCE(?, reviewed_at), applied_at = COALESCE(?, applied_at) WHERE id = ?",
                (status, error_text, reviewed_at, applied_at, batch_id),
            )
            return cur.rowcount > 0

    def update_extraction_batch_stats(self, batch_id: str, stats: dict) -> None:
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE extraction_batches SET stats_json = ? WHERE id = ?",
                (json.dumps(stats, ensure_ascii=False, sort_keys=True), batch_id),
            )

    def add_extraction_candidate(
        self,
        batch_id: str,
        candidate_kind: str,
        fingerprint: str,
        payload: dict,
        source_chunk_id: str = "",
        evidence_text: str = "",
    ) -> str:
        cid = self._uid()
        now = self._now()
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self._get_conn() as conn:
            try:
                conn.execute(
                    "INSERT INTO extraction_candidates (id, batch_id, candidate_kind, fingerprint, payload_json, source_chunk_id, evidence_text, status, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
                    (cid, batch_id, candidate_kind, fingerprint, payload_json, source_chunk_id, evidence_text, now),
                )
            except sqlite3.IntegrityError:
                # Fingerprint collision policy (R4): keep the first-written payload
                # (including confidence/created_by); only merge additional evidences.
                row = conn.execute(
                    "SELECT id, payload_json FROM extraction_candidates WHERE batch_id = ? AND fingerprint = ?",
                    (batch_id, fingerprint),
                ).fetchone()
                if row:
                    existing_id = str(row["id"])
                    existing_payload = json.loads(row["payload_json"] or "{}")

                    # Consolidate evidences in payload
                    existing_evidences = existing_payload.get("evidences", [])
                    # Append if new source chunk isn't already included
                    if not any(ev.get("source_chunk_id") == source_chunk_id for ev in existing_evidences):
                        existing_evidences.append({
                            "source_chunk_id": source_chunk_id,
                            "evidence_text": evidence_text
                        })
                        existing_payload["evidences"] = existing_evidences
                        updated_payload_json = json.dumps(existing_payload, ensure_ascii=False, sort_keys=True)
                        conn.execute(
                            "UPDATE extraction_candidates SET payload_json = ? WHERE id = ?",
                            (updated_payload_json, existing_id),
                        )
                    return existing_id
        return cid

    def list_extraction_candidates(self, batch_id: str = "", status: str = "") -> list[dict]:
        sql = "SELECT * FROM extraction_candidates WHERE 1=1"
        params = []
        if batch_id:
            sql += " AND batch_id = ?"
            params.append(batch_id)
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at ASC"
        with self._get_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
            res = []
            for r in rows:
                d = dict(r)
                d["payload"] = json.loads(d["payload_json"] or "{}")
                res.append(d)
            return res

    def review_extraction_candidates(
        self,
        batch_id: str,
        candidate_ids: list[str],
        status: str,
        rejection_reason: str = "",
    ) -> int:
        if not candidate_ids:
            return 0
        now = self._now()
        reviewed_at = now
        placeholders = ", ".join("?" for _ in candidate_ids)
        with self._get_conn() as conn:
            cur = conn.execute(
                f"UPDATE extraction_candidates SET status = ?, rejection_reason = ?, reviewed_at = ? "
                f"WHERE batch_id = ? AND id IN ({placeholders}) AND status = 'pending'",
                [status, rejection_reason, reviewed_at, batch_id] + list(candidate_ids),
            )
            return cur.rowcount

    # ------------------------------------------------------------------
    # Statistics & Misc
    # ------------------------------------------------------------------

    def stats(self) -> dict:
        """Get database statistics."""
        with self._get_conn() as conn:
            return {
                "entities": conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0],
                "relations": conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0],
                "entity_chunk_links": conn.execute("SELECT COUNT(*) FROM entity_chunk_links").fetchone()[0],
            }

    # ------------------------------------------------------------------
    # User Feedbacks CRUD (Quality Control & Feedback Loop)
    # ------------------------------------------------------------------

    def create_feedback(
        self,
        user_id: str,
        query_text: str,
        answer_text: str,
        referenced_chunk_ids: list[str],
        rating: str,
        reason: str = "",
        trace_id: str = "",
        feedback_scope: str = "answer",
        target_chunk_id: str = "",
    ) -> str:
        fid = self._uid()
        now = self._now()
        chunks_json = json.dumps(referenced_chunk_ids or [], ensure_ascii=False)
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO user_feedbacks (id, user_id, query_text, answer_text, referenced_chunk_ids, rating, reason, trace_id, feedback_scope, target_chunk_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (fid, user_id, query_text, answer_text, chunks_json, rating, reason, trace_id, feedback_scope, target_chunk_id, now),
            )
        return fid

    def get_feedback(self, feedback_id: str) -> dict | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM user_feedbacks WHERE id = ?", (feedback_id,)).fetchone()
            if not row:
                return None
            d = dict(row)
            try:
                d["referenced_chunk_ids"] = json.loads(d.get("referenced_chunk_ids") or "[]")
            except Exception:
                d["referenced_chunk_ids"] = []
            d["feedback_scope"] = d.get("feedback_scope") or "answer"
            d["target_chunk_id"] = d.get("target_chunk_id") or ""
            return d

    def list_feedbacks(self, rating: str = "", limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM user_feedbacks"
        params = []
        if rating:
            sql += " WHERE rating = ?"
            params.append(rating)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._get_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
            res = []
            for r in rows:
                d = dict(r)
                try:
                    d["referenced_chunk_ids"] = json.loads(d.get("referenced_chunk_ids") or "[]")
                except Exception:
                    d["referenced_chunk_ids"] = []
                d["feedback_scope"] = d.get("feedback_scope") or "answer"
                d["target_chunk_id"] = d.get("target_chunk_id") or ""
                res.append(d)
            return res

    def batch_get_chunk_down_scores(
        self,
        chunk_ids: list[str] | None = None,
        half_life_days: float = 30.0,
    ) -> dict[str, float]:
        """Compute effective down scores for specified or all chunks with deduplication & time decay."""
        import math
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT user_id, trace_id, referenced_chunk_ids, target_chunk_id, feedback_scope, created_at "
                "FROM user_feedbacks WHERE rating = 'down'"
            ).fetchall()

        now_dt = datetime.now()
        decay_factor = math.log(2) / max(half_life_days, 1.0)

        # Deduplication map: (user_id, trace_id/hour, chunk_id) -> latest created_at
        seen_down_votes: dict[tuple[str, str, str], datetime] = {}
        target_set = set(str(cid) for cid in chunk_ids) if chunk_ids is not None else None

        for r in rows:
            u_id = str(r["user_id"] or "anonymous")
            t_id = str(r["trace_id"] or "")
            scope = str(r["feedback_scope"] or "answer")
            t_chunk = str(r["target_chunk_id"] or "")
            c_str = str(r["created_at"] or "")
            try:
                created_dt = datetime.fromisoformat(c_str[:19])
            except (ValueError, TypeError):
                created_dt = now_dt

            affected_ids: set[str] = set()
            if scope == "chunk" and t_chunk:
                affected_ids.add(t_chunk)
            else:
                try:
                    ids = json.loads(r["referenced_chunk_ids"] or "[]")
                    if isinstance(ids, list):
                        affected_ids.update(str(cid) for cid in ids)
                except Exception:
                    pass

            for cid in affected_ids:
                if not cid:
                    continue
                if target_set is not None and cid not in target_set:
                    continue
                dedup_key = (u_id, t_id or c_str[:13], cid)
                if dedup_key not in seen_down_votes or created_dt > seen_down_votes[dedup_key]:
                    seen_down_votes[dedup_key] = created_dt

        result_scores: dict[str, float] = {}
        for (u_id, key_id, cid), dt in seen_down_votes.items():
            age_days = max(0.0, (now_dt - dt).total_seconds() / 86400.0)
            weight = math.exp(-decay_factor * age_days)
            result_scores[cid] = result_scores.get(cid, 0.0) + weight

        return {cid: round(score, 3) for cid, score in result_scores.items()}

    def get_chunk_effective_down_score(self, chunk_id: str, half_life_days: float = 30.0) -> float:
        """Calculate effective down score for a single chunk_id with deduplication & time decay."""
        if not chunk_id:
            return 0.0
        scores = self.batch_get_chunk_down_scores([chunk_id], half_life_days=half_life_days)
        return scores.get(chunk_id, 0.0)

    def count_chunk_down_ratings(self, chunk_id: str) -> int:
        """Count total rating='down' for a specific chunk_id in user_feedbacks."""
        if not chunk_id:
            return 0
        scores = self.batch_get_chunk_down_scores([chunk_id])
        return int(round(scores.get(chunk_id, 0.0)))

    def get_7d_feedback_stats(self) -> dict:
        """Get feedback counts (up, down, total) in the last 7 days."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT rating, COUNT(*) as cnt FROM user_feedbacks "
                "WHERE created_at >= datetime('now', '-7 days', 'localtime') "
                "GROUP BY rating"
            ).fetchall()
            stats = {"up": 0, "down": 0, "total": 0}
            for r in rows:
                rating = r["rating"]
                cnt = r["cnt"]
                if rating in stats:
                    stats[rating] = cnt
                stats["total"] += cnt
            return stats
