"""Test suite for Entity Identity Subsystem and PRD invariants."""
import pytest
from dataclasses import dataclass
from sqlite3 import Connection

from rag_knowledge.services.entity_identity import EntityIdentityService, IdentityOutcome, normalize_identity_key
from rag_knowledge.services.graph_governance import is_safe_review_candidate
from rag_knowledge.services.graph_extraction.pipeline import GraphCandidateApplier
from rag_knowledge.repository.relational_db import RelationalDB


@dataclass
class FakeDB:
    entities: list[dict]
    aliases: list[dict]

    def list_entities(self):
        return self.entities

    def list_aliases(self):
        return self.aliases


def test_invariant_8_normalize_identity_key_consistency():
    """Invariance 8: Comparison key handles full-width parentheses, spaces, and casefold."""
    assert normalize_identity_key(" StampGIS  Tools（测试） ") == "stampgis tools(测试)"
    assert normalize_identity_key("StampTools") == "stamptools"
    assert normalize_identity_key("STAMPTOOLS") == "stamptools"


def test_invariant_1_and_3_identity_service_resolves_catalog_and_db_aliases():
    """Invariants 1 & 3: Catalog and DB aliases resolve correctly and conclusions take effect."""
    db = FakeDB(
        entities=[{"id": "e1", "name": "StampTools", "entity_type": "Product"}],
        aliases=[{"entity_id": "e1", "alias": "Stamp Tool"}],
    )
    service = EntityIdentityService(db)

    # 1. Exact DB match -> bind
    d1 = service.resolve("StampTools", "Product")
    assert d1.outcome == IdentityOutcome.BIND
    assert d1.target_entity_id == "e1"

    # 2. DB alias match -> alias_of
    d2 = service.resolve("Stamp Tool", "Product")
    assert d2.outcome == IdentityOutcome.ALIAS_OF
    assert d2.target_entity_id == "e1"

    # 3. Catalog alias match (StampGIS Tools -> StampTools) -> alias_of
    d3 = service.resolve("StampGIS Tools", "Product")
    assert d3.outcome == IdentityOutcome.ALIAS_OF
    assert d3.canonical_name == "StampTools"

    # 4. Type conflict (name not in catalog) -> conflict
    db_conflict = FakeDB(
        entities=[{"id": "e2", "name": "CustomExporter", "entity_type": "Procedure"}],
        aliases=[],
    )
    d4 = EntityIdentityService(db_conflict).resolve("CustomExporter", "Step")
    assert d4.outcome == IdentityOutcome.CONFLICT
    assert d4.diagnostics[0].code == "type_conflict"


def test_invariant_3_is_safe_review_candidate_blocks_non_new():
    """Invariant 3: is_safe_review_candidate blocks bind, alias_of, conflict, diagnostic candidates."""
    for act in ["bind", "alias_of", "conflict", "alias", "reuse", "diagnostic"]:
        item = {
            "candidate_kind": "entity",
            "payload": {
                "name": "StampGIS Tools",
                "entity_type": "Product",
                "evidence_text": "evidence",
                "resolution_action": act,
            },
        }
        assert not is_safe_review_candidate(item)

    # New valid entity candidate is safe
    safe_item = {
        "candidate_kind": "entity",
        "payload": {
            "name": "BrandNewProduct",
            "entity_type": "Product",
            "evidence_text": "evidence",
            "resolution_action": "new",
        },
    }
    assert is_safe_review_candidate(safe_item)


def test_invariant_2_applier_does_not_insert_duplicate_entity_for_alias(isolated_storage):
    """Invariant 2: Apply MUST NOT insert a new entity if name is an alias of existing entity."""
    isolated_storage()
    db = RelationalDB()

    applier = GraphCandidateApplier(db)

    with db._get_conn() as conn:
        # Create base canonical entity
        e1_id = applier._entity(conn, {"name": "StampTools", "entity_type": "Product"})

        # Add alias
        applier._alias(conn, {"entity_name": "StampTools", "alias": "StampGIS Tools"})

        # Verify initial entities count
        cnt1 = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        assert cnt1 == 1

        # Attempt to apply alias as an entity candidate
        e2_id = applier._entity(conn, {"name": "StampGIS Tools", "entity_type": "Product"})
        assert e2_id == e1_id

        # Verify entities count did NOT increase!
        cnt2 = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        assert cnt2 == 1


def test_invariant_6_relation_endpoint_alias_resolution(isolated_storage):
    """Invariant 6: Relation endpoints using alias resolve to canonical entity."""
    isolated_storage()
    db = RelationalDB()
    applier = GraphCandidateApplier(db)

    with db._get_conn() as conn:
        # Create canonical entity StampTools and child tool PipelineBuilder
        applier._entity(conn, {"name": "StampTools", "entity_type": "Product"})
        applier._entity(conn, {"name": "PipelineBuilder", "entity_type": "Tool"})

        # Create relation where target_name uses alias StampGIS Tools
        rel_id = applier._relation(conn, {
            "source_name": "PipelineBuilder",
            "relation_type": "belongs_to",
            "target_name": "StampGIS Tools",  # Alias of StampTools via DomainCatalog
        })
        assert rel_id

        # Verify relation connects to StampTools
        rel_row = conn.execute("SELECT target_entity_id FROM relations WHERE id = ?", (rel_id,)).fetchone()
        target_entity = conn.execute("SELECT name FROM entities WHERE id = ?", (rel_row[0],)).fetchone()
        assert target_entity[0] == "StampTools"


class FakeArbiter:
    def __init__(self, verdict: str, confidence: float):
        self.verdict = verdict
        self.confidence = confidence

    def arbitrate(self, candidate_name: str, entity_type: str, target_name: str, target_type: str):
        return (self.verdict, self.confidence)


def test_llm_identity_arbiter_resolves_uncertain_pairs():
    """LLM Identity Arbiter resolves uncertain candidate pairs when enabled."""
    db = FakeDB(entities=[{"id": "e1", "name": "StampTools", "entity_type": "Product"}], aliases=[])

    # 1. Arbiter says "same" with high confidence -> alias_of
    service_same = EntityIdentityService(db, arbiter=FakeArbiter("same", 0.9))
    decision_same = service_same.resolve("StampTools Suite", "Product")
    assert decision_same.outcome == IdentityOutcome.ALIAS_OF
    assert decision_same.target_entity_id == "e1"

    # 2. Arbiter says "different" with high confidence -> new
    service_diff = EntityIdentityService(db, arbiter=FakeArbiter("different", 0.95))
    decision_diff = service_diff.resolve("StampTools Suite", "Product")
    assert decision_diff.outcome == IdentityOutcome.NEW

    # 3. Arbiter says "unsure" or low confidence -> uncertain (requires manual review, forbidden to approve-all)
    service_unsure = EntityIdentityService(db, arbiter=FakeArbiter("unsure", 0.5))
    decision_unsure = service_unsure.resolve("StampTools Suite", "Product")
    assert decision_unsure.outcome == IdentityOutcome.UNCERTAIN


def test_entity_resolution_service_wires_arbiter_and_batch_context():
    """Extract-path wrapper must pass arbiter + batch context into identity service."""
    from rag_knowledge.services.entity_resolution import EntityResolutionService
    from rag_knowledge.services.graph_extraction import EntityCandidate

    db = FakeDB(entities=[{"id": "e1", "name": "StampTools", "entity_type": "Product"}], aliases=[])
    resolver = EntityResolutionService(db, arbiter=FakeArbiter("same", 0.95))
    result = resolver.resolve(EntityCandidate("Stamp GIS Tools", "Product"))
    assert result.action == "alias"
    assert result.outcome == IdentityOutcome.ALIAS_OF
    assert result.canonical_name == "StampTools"
    assert result.target_id == "e1"

    # Batch context forwarded
    empty_db = FakeDB([], [])
    resolver2 = EntityResolutionService(empty_db)
    batch_result = resolver2.resolve(
        EntityCandidate("StampTools", "Product"),
        batch_type_index={"stamptools": "Product"},
        batch_entity_ids={"stamptools": "cand_1"},
        batch_display_names={"stamptools": "StampTools"},
    )
    assert batch_result.action == "reuse"
    assert batch_result.target_id == "cand_1"


def test_pipeline_stages_alias_when_identity_folds(isolated_storage):
    """GraphBuilder helper stages alias candidate after identity fold."""
    isolated_storage()
    db = RelationalDB()
    from rag_knowledge.services.graph_extraction.pipeline import GraphBuilder

    batch_id = db.create_extraction_batch("full", {}, "snap-test")
    builder = GraphBuilder(db=db, chunk_source=lambda: [])
    candidate_ids = {k: set() for k in ("entity", "relation", "field", "link", "diagnostic", "alias")}
    payload = {
        "name": "Stamp GIS Tools",
        "entity_type": "Product",
        "identity_canonical": "StampTools",
        "source_chunk_id": "c1",
        "evidence_text": "Stamp GIS Tools",
        "confidence": 0.9,
    }
    alias_id = builder._stage_identity_alias_candidate(
        batch_id,
        payload,
        {},
        candidate_ids,
        created_by="llm:entity_resolver",
    )
    assert alias_id
    assert alias_id in candidate_ids["alias"]
    rows = db.list_extraction_candidates(batch_id, "")
    aliases = [r for r in rows if r["candidate_kind"] == "alias"]
    assert len(aliases) == 1
    assert aliases[0]["payload"]["entity_name"] == "StampTools"
    assert aliases[0]["payload"]["alias"] == "Stamp GIS Tools"
    assert aliases[0]["payload"]["created_by"] == "llm:entity_resolver"


def test_graph_builder_injects_arbiter_when_entity_resolve_enabled(isolated_storage, monkeypatch):
    """When entity resolve is enabled, EntityResolutionService receives a live arbiter."""
    isolated_storage()
    from rag_knowledge.services.graph_extraction import pipeline as pipeline_mod
    from rag_knowledge.services.graph_extraction.pipeline import GraphBuilder
    import rag_knowledge.config as config_mod

    captured = {}

    class CapturingResolution(pipeline_mod.EntityResolutionService):
        def __init__(self, db, *, identity_service=None, arbiter=None, type_arbiter=None):
            captured["arbiter"] = arbiter
            super().__init__(
                db, identity_service=identity_service, arbiter=arbiter, type_arbiter=type_arbiter
            )

    monkeypatch.setattr(pipeline_mod, "EntityResolutionService", CapturingResolution)
    monkeypatch.setattr(pipeline_mod, "assert_ollama_reachable", lambda **kwargs: None)

    class FakeCfg:
        class graph_extraction_llm:
            enabled = False
            entity_resolve_enabled = True
            relation_direction_resolve_enabled = False
            entity_type_resolve_enabled = False
            relation_type_resolve_enabled = False
            relation_belonging_resolve_enabled = False
            leak_salvage_enabled = False
            provider = "ollama"
            api_key_env = ""
            prompt_version = "v4"
            extractor_version = "v1"
            rate_limit_delay = 0.0
            temperature = 0.0
            max_retries = 1
            entity_resolve_min_confidence = 0.80
            relation_direction_min_confidence = 0.80
            entity_type_resolve_min_confidence = 0.80
            relation_type_min_confidence = 0.80
            relation_belonging_min_confidence = 0.80
            leak_salvage_enabled = False

        ollama_base_url = "http://127.0.0.1:11434"

        def graph_llm_endpoint(self):
            return self.ollama_base_url

        @property
        def graph_extraction_endpoint(self):
            from rag_knowledge.config import GraphLLMExtractorConfig

            return GraphLLMExtractorConfig().as_endpoint()

    monkeypatch.setattr(config_mod, "Config", FakeCfg)

    db = RelationalDB()
    builder = GraphBuilder(
        db=db,
        chunk_source=lambda: [
            {
                "chunk_id": "c1",
                "content": "hello",
                "metadata": {"doc_category": "其他", "section_path": "", "source": "t.md"},
            }
        ],
    )
    builder.build_full(force_rebuild=True, include_entity_resolve=True, limit=1)
    assert captured.get("arbiter") is not None
    assert getattr(captured["arbiter"], "use_graph_endpoint", False) is True



def test_batch_context_resolves_in_batch_entities():
    """batch_type_index and batch_entity_ids resolve in-batch candidate entities."""
    service = EntityIdentityService(FakeDB([], []))
    batch_type_index = {"stamptools": "Product"}
    batch_entity_ids = {"stamptools": "cand_123"}

    decision = service.resolve("StampTools", "Product", batch_type_index=batch_type_index, batch_entity_ids=batch_entity_ids)
    assert decision.outcome == IdentityOutcome.BIND
    assert decision.target_entity_id == "cand_123"


def test_different_from_hard_constraint_short_circuit():
    """different_from constraint short-circuits to CONFLICT and bypasses LLM arbiter."""
    # Setup backbone constraints with different_from
    db = FakeDB(entities=[{"id": "e1", "name": "StampGIS", "entity_type": "Product"}], aliases=[])
    service = EntityIdentityService(db)

    # Manually inject different_from pair
    service.backbone_constraints = {"different_from": [["StampGIS", "StampServer"]]}

    decision = service.resolve("StampServer", "Product")
    assert decision.outcome == IdentityOutcome.CONFLICT
    assert decision.diagnostics[0].code == "different_from_violation"


def test_llm_identity_arbiter_parses_json_and_markdown_fences():
    """LLMIdentityArbiter correctly parses raw JSON strings and markdown-fenced ```json code blocks."""
    from rag_knowledge.services.entity_identity import LLMIdentityArbiter

    class FakeLLMClient:
        def __init__(self, response_text: str):
            self.response_text = response_text

        def invoke(self, prompt: str):
            return self.response_text

    # 1. Raw JSON string
    arbiter1 = LLMIdentityArbiter(FakeLLMClient('{"verdict": "same", "confidence": 0.95}'))
    verdict1, conf1 = arbiter1.arbitrate("Stamp GIS Tools", "Product", "StampTools", "Product")
    assert verdict1 == "same"
    assert conf1 == 0.95

    # 2. Markdown fenced JSON block
    fenced = "```json\n{\n  \"verdict\": \"different\",\n  \"confidence\": 0.88\n}\n```"
    arbiter2 = LLMIdentityArbiter(FakeLLMClient(fenced))
    verdict2, conf2 = arbiter2.arbitrate("StampGIS", "Product", "StampServer", "Product")
    assert verdict2 == "different"
    assert conf2 == 0.88


def test_alias_of_relation_edge_redirected_to_aliases_table(isolated_storage):
    """Invariant 5: alias_of relation edges are redirected to aliases table."""
    isolated_storage()
    db = RelationalDB()
    applier = GraphCandidateApplier(db)

    with db._get_conn() as conn:
        applier._entity(conn, {"name": "StampTools", "entity_type": "Product"})

        # Apply alias_of relation candidate
        res_id = applier._relation(conn, {
            "source_name": "StampGIS Tools",
            "relation_type": "alias_of",
            "target_name": "StampTools",
        })
        assert res_id

        # Verify no alias_of edge in relations table
        rel_cnt = conn.execute("SELECT COUNT(*) FROM relations WHERE relation_type = 'alias_of'").fetchone()[0]
        assert rel_cnt == 0

        # Verify alias row in aliases table
        alias_row = conn.execute("SELECT alias FROM aliases WHERE entity_id = (SELECT id FROM entities WHERE name='StampTools')").fetchone()
        assert alias_row[0] == "StampGIS Tools"


def test_cascade_rejected_endpoint_relations_cleans_orphaned_relations(isolated_storage):
    """Invariant 7: Cascade relation rejection cleans orphaned relations when entity is rejected."""
    from rag_knowledge.services.graph_governance import cascade_rejected_endpoint_relations

    isolated_storage()
    db = RelationalDB()

    # Create extraction batch
    batch_id = db.create_extraction_batch("full", {}, "hash123")

    # Add entity & relation candidates
    e1 = db.add_extraction_candidate(batch_id, "entity", "fp1", {"name": "StampTools", "entity_type": "Product"}, "c1", "ev")
    e2 = db.add_extraction_candidate(batch_id, "entity", "fp2", {"name": "OrphanEntity", "entity_type": "Tool"}, "c1", "ev")
    r1 = db.add_extraction_candidate(batch_id, "relation", "fp3", {"source_name": "OrphanEntity", "relation_type": "belongs_to", "target_name": "StampTools"}, "c1", "ev")

    # Approve e1 and r1, reject e2
    db.review_extraction_candidates(batch_id, [e1, r1], "approved")
    db.review_extraction_candidates(batch_id, [e2], "rejected")

    # Run cascade
    cascaded = cascade_rejected_endpoint_relations(db, batch_id)
    assert cascaded == 1

    # Relation r1 should now be rejected
    r1_item = next(c for c in db.list_extraction_candidates(batch_id) if c["id"] == r1)
    assert r1_item["status"] == "rejected"
    assert "auto-cascade" in r1_item["rejection_reason"]


class FakeTypeArbiter:
    def __init__(self, verdict: str = "prefer_existing", confidence: float = 0.95):
        self.verdict = verdict
        self.confidence = confidence
        self.calls = []

    def arbitrate(self, name, candidate_type, existing_name, existing_type, **kwargs):
        self.calls.append((name, candidate_type, existing_name, existing_type, kwargs))
        return (self.verdict, self.confidence)


def test_type_arbiter_prefer_existing_binds():
    db = FakeDB(
        entities=[{"id": "e1", "name": "导出模型", "entity_type": "Procedure"}],
        aliases=[],
    )
    arbiter = FakeTypeArbiter(verdict="prefer_existing", confidence=0.92)
    service = EntityIdentityService(db, type_arbiter=arbiter)
    decision = service.resolve("导出模型", "Step", evidence_text="导出模型流程")
    assert decision.outcome == IdentityOutcome.BIND
    assert decision.resolved_type == "Procedure"
    assert decision.target_entity_id == "e1"
    assert any(d.code == "type_arbiter_prefer_existing" for d in decision.diagnostics)
    assert arbiter.calls


def test_type_arbiter_prefer_candidate_on_batch_updates_type():
    arbiter = FakeTypeArbiter(verdict="prefer_candidate", confidence=0.91)
    service = EntityIdentityService(FakeDB([], []), type_arbiter=arbiter)
    decision = service.resolve(
        "导出模型",
        "Procedure",
        batch_type_index={"导出模型": "Step"},
        batch_entity_ids={"导出模型": "c-batch-1"},
        batch_display_names={"导出模型": "导出模型"},
        evidence_text="完整导出模型流程",
    )
    assert decision.outcome == IdentityOutcome.BIND
    assert decision.resolved_type == "Procedure"
    assert any(d.code == "type_arbiter_prefer_candidate" for d in decision.diagnostics)


def test_type_arbiter_prefer_candidate_on_db_stays_conflict_for_review():
    db = FakeDB(
        entities=[{"id": "e1", "name": "导出模型", "entity_type": "Step"}],
        aliases=[],
    )
    arbiter = FakeTypeArbiter(verdict="prefer_candidate", confidence=0.93)
    service = EntityIdentityService(db, type_arbiter=arbiter)
    decision = service.resolve("导出模型", "Procedure")
    assert decision.outcome == IdentityOutcome.CONFLICT
    codes = {d.code for d in decision.diagnostics}
    assert "type_arbiter_prefer_candidate_review" in codes
    assert "type_conflict" in codes


def test_catalog_type_coerced_without_conflict():
    """Catalog gold type coerces mis-typed candidate and binds to DB."""
    db = FakeDB(
        entities=[{"id": "e1", "name": "StampTools", "entity_type": "Product"}],
        aliases=[],
    )
    # StampGIS Tools → StampTools Product via catalog; candidate wrongly says Tool
    service = EntityIdentityService(db)
    decision = service.resolve("StampGIS Tools", "Tool")
    assert decision.outcome == IdentityOutcome.ALIAS_OF
    assert decision.canonical_name == "StampTools"
    assert any(d.code == "type_coerced_to_catalog" for d in decision.diagnostics)


def test_graph_builder_injects_type_arbiter_when_enabled(isolated_storage, monkeypatch):
    isolated_storage()
    from rag_knowledge.services.graph_extraction import pipeline as pipeline_mod
    from rag_knowledge.services.graph_extraction.pipeline import GraphBuilder
    import rag_knowledge.config as config_mod

    captured = {}

    class CapturingResolution(pipeline_mod.EntityResolutionService):
        def __init__(self, db, *, identity_service=None, arbiter=None, type_arbiter=None):
            captured["type_arbiter"] = type_arbiter
            super().__init__(
                db, identity_service=identity_service, arbiter=arbiter, type_arbiter=type_arbiter
            )

    monkeypatch.setattr(pipeline_mod, "EntityResolutionService", CapturingResolution)
    monkeypatch.setattr(pipeline_mod, "assert_ollama_reachable", lambda **kwargs: None)

    class FakeCfg:
        class graph_extraction_llm:
            enabled = False
            entity_resolve_enabled = False
            relation_direction_resolve_enabled = False
            entity_type_resolve_enabled = True
            relation_type_resolve_enabled = False
            relation_belonging_resolve_enabled = False
            provider = "ollama"
            api_key_env = ""
            prompt_version = "v4"
            extractor_version = "v1"
            rate_limit_delay = 0.0
            temperature = 0.0
            max_retries = 1
            entity_resolve_min_confidence = 0.80
            relation_direction_min_confidence = 0.80
            entity_type_resolve_min_confidence = 0.80
            relation_type_min_confidence = 0.80
            relation_belonging_min_confidence = 0.80
            leak_salvage_enabled = False

        ollama_base_url = "http://127.0.0.1:11434"

        def graph_llm_endpoint(self):
            return self.ollama_base_url

        @property
        def graph_extraction_endpoint(self):
            from rag_knowledge.config import GraphLLMExtractorConfig

            return GraphLLMExtractorConfig().as_endpoint()

    monkeypatch.setattr(config_mod, "Config", FakeCfg)

    builder = GraphBuilder(
        db=RelationalDB(),
        chunk_source=lambda: [
            {
                "chunk_id": "c1",
                "content": "hello",
                "metadata": {"doc_category": "其他", "section_path": "", "source": "t.md"},
            }
        ],
    )
    builder.build_full(force_rebuild=True, include_entity_type_resolve=True, limit=1)
    assert captured.get("type_arbiter") is not None
    assert getattr(captured["type_arbiter"], "use_graph_endpoint", False) is True
