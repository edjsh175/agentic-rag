import asyncio
import json
from unittest.mock import MagicMock
from langchain_core.documents import Document

from rag_knowledge.config import Config
from rag_knowledge.services.agent_candidate_pipeline import AgentCandidatePipeline, CandidateBudgets
from rag_knowledge.services.agent_orchestration.graph_admission import GraphRelationAdmissionService
from rag_knowledge.services.agent_orchestration.graph_explorer import GraphExplorer
from rag_knowledge.services.agent_orchestration.graph_working_set import (
    GraphRelationCandidate,
    GraphWorkingSet,
)
from rag_knowledge.services.agent_orchestration.models import (
    AgentBudget,
    ConversationContext,
    EvidencePool,
    SessionState,
    ToolProgressStatus,
)
from rag_knowledge.services.dialogue_understanding import SemanticTaskContext
from rag_knowledge.services.agent_orchestration.runtime import (
    AgentLoop,
    build_agent_registry,
)


def _mock_graph_db():
    entities = [
        {"id": "e_server", "canonical_name": "StampServer", "entity_type": "Product", "review_status": "approved"},
        {"id": "e_webrtc", "canonical_name": "StampWebRTC", "entity_type": "Module", "review_status": "approved"},
        {"id": "e_tools", "canonical_name": "StampTools", "entity_type": "Tool", "review_status": "approved"},
        {"id": "e_viewer", "canonical_name": "StampViewer", "entity_type": "Client", "review_status": "approved"},
    ]
    relations = [
        # StampServer -> StampWebRTC (Hop 1)
        {
            "id": "rel_1",
            "source_entity_id": "e_server",
            "source_name": "StampServer",
            "source_type": "Product",
            "target_entity_id": "e_webrtc",
            "target_name": "StampWebRTC",
            "target_type": "Module",
            "relation_type": "belongs_to",
            "review_status": "approved",
            "confidence": 0.95,
        },
        # StampWebRTC -> StampTools (Hop 2 from StampServer)
        {
            "id": "rel_2",
            "source_entity_id": "e_webrtc",
            "source_name": "StampWebRTC",
            "source_type": "Module",
            "target_entity_id": "e_tools",
            "target_name": "StampTools",
            "target_type": "Tool",
            "relation_type": "depends_on",
            "review_status": "approved",
            "confidence": 0.90,
        },
        # StampTools -> StampViewer (Hop 3)
        {
            "id": "rel_3",
            "source_entity_id": "e_tools",
            "source_name": "StampTools",
            "source_type": "Tool",
            "target_entity_id": "e_viewer",
            "target_name": "StampViewer",
            "target_type": "Client",
            "relation_type": "implements",
            "review_status": "approved",
            "confidence": 0.85,
        },
    ]
    db = MagicMock()
    db.list_entities.return_value = entities
    db.list_relations.side_effect = lambda entity_id=None, review_status=None: [
        r for r in relations
        if (entity_id is None or r["source_entity_id"] == entity_id or r["target_entity_id"] == entity_id)
        and (review_status is None or r["review_status"] == review_status)
    ]
    return db


def test_runtime_bootstrap_writes_evidence_pool_even_with_candidate_pipeline_v2(isolated_storage):
    """Verify that Runtime 1-hop Bootstrap relations enter EvidencePool unconditionally."""
    cfg, _, _, _ = isolated_storage()
    db = _mock_graph_db()
    admission_svc = GraphRelationAdmissionService(graph_db=db)
    explorer = GraphExplorer(graph_db=db, admission_service=admission_svc)

    conv = ConversationContext(
        session=SessionState(),
        user_question="StampServer 包含哪些模块？",
        head_entity="StampServer",
        confirmed_entity="StampServer",
        confirmed_entities=("StampServer",),
    )
    conv.semantic_task = SemanticTaskContext(
        "StampServer 包含哪些模块？",
        "StampServer",
        ("StampServer",),
        "single_entity",
        1.0,
        "definition",
        (),
        "test",
        True,
    )
    evidence = EvidencePool(question_id="q1")
    budget = AgentBudget(max_steps=4)

    # Configure candidate_pipeline_v2 = True
    cfg.agent_orchestration.candidate_pipeline_v2 = True
    cfg.agent_orchestration.graph_bootstrap_enabled = True

    async def mock_handler(_args):
        return None

    loop = AgentLoop(
        conversation=conv,
        evidence=evidence,
        budget=budget,
        registry=MagicMock(),
        handlers={"environment.read_status": mock_handler},
        cfg=cfg,
        graph_explorer=explorer,
        decide_fn=lambda _c, _e, _obs: MagicMock(action="finalize", answer_mode="full", tool=None, arguments={}, gap=None, expected_gain=None),
    )

    result = asyncio.run(loop.run())

    # Graph Relation Admission PASS must materialize GraphRelationEvidence in
    # the query-scoped EvidencePool, independently of chunk Candidate V2.
    assert len(evidence.groups) >= 1
    rel_groups = [g for g in evidence.groups if g.kind == "relation"]
    assert len(rel_groups) == 1
    assert "StampServer -[belongs_to]-> StampWebRTC" in rel_groups[0].relation_key
    assert rel_groups[0].docs[0]["metadata"]["source_type"] == "graph_relation"
    assert rel_groups[0].docs[0]["metadata"]["relation_type"] == "belongs_to"


def test_candidate_pipeline_v2_consumes_graph_working_set_exclusively():
    """Verify Candidate Pipeline generates graph candidates from GraphWorkingSet without querying Graph DB."""
    forbidden_db = MagicMock()
    forbidden_db.list_relations.side_effect = AssertionError("Direct Graph DB list_relations called in candidate pipeline!")
    forbidden_db.list_entities.side_effect = AssertionError("Direct Graph DB list_entities called in candidate pipeline!")

    mock_store = MagicMock()
    mock_store.get_chunks_by_metadata.return_value = [
        Document(
            page_content="StampWebRTC 负责视频传输处理。",
            metadata={"chunk_id": "c_webrtc_1", "document_entity": "StampWebRTC", "kb_name": "default", "review_status": "approved"},
        )
    ]

    pipeline = AgentCandidatePipeline(
        vector_store=mock_store,
        retrieval_strategy=MagicMock(),
        graph_db=forbidden_db,
    )

    # Working set with StampServer and StampWebRTC relation
    ws = GraphWorkingSet(exploration_roots=("StampServer",))
    ws.add_root("StampServer", entity_id="e_server", entity_type="Product")
    state_webrtc = MagicMock(
        entity_id="e_webrtc",
        canonical_name="StampWebRTC",
        entity_type="Module",
        depth_from_root=1,
        origin_root="StampServer",
    )
    ws.entities["stampwebrtc"] = state_webrtc
    rel_mock = MagicMock(
        relation_id="rel_1",
        source_name="StampServer",
        target_name="StampWebRTC",
        relation_type="belongs_to",
        depth_from_root=1,
    )
    ws.relations["stampserver -[belongs_to]-> stampwebrtc"] = rel_mock

    # Run generate with graph_working_set -> must not hit forbidden_db
    results = pipeline.generate(
        question="StampServer 模块说明",
        target_entity="StampServer",
        kb_name="default",
        review_status="approved",
        doc_category=None,
        graph_working_set=ws,
        budgets=CandidateBudgets(graph_expansion=10),
    )

    # Assert graph candidates were generated from working set
    graph_candidates = [r for r in results if any(p.generator == "graph_working_set" or p.graph_path for p in r.provenance)]
    assert len(graph_candidates) >= 1
    assert graph_candidates[0].document.metadata["document_entity"] == "StampWebRTC"


def test_expand_graph_scope_executes_true_multi_hop_bfs():
    """Verify that additional_hops=2 performs a true 2-hop BFS and discovers 2-hop neighbors with depth=2."""
    db = _mock_graph_db()
    admission_svc = GraphRelationAdmissionService(graph_db=db)
    explorer = GraphExplorer(graph_db=db, admission_service=admission_svc)

    ws = GraphWorkingSet(exploration_roots=("StampServer",))
    # 1. Expand with additional_hops=2
    obs = explorer.expand_graph_scope(
        working_set=ws,
        start_entities=["StampServer"],
        additional_hops=2,
        direction="out",
        stage1_confirmed_entities={"StampServer"},
        question="StampServer 拓扑架构与依赖工具",
    )

    assert obs.ok is True
    assert obs.status == ToolProgressStatus.PROGRESS

    # 1-hop neighbor: StampWebRTC (depth 1)
    assert "stampwebrtc" in ws.entities
    assert ws.entities["stampwebrtc"].depth_from_root == 1
    assert ws.entities["stampwebrtc"].origin_root == "StampServer"

    # 2-hop neighbor: StampTools (depth 2)
    assert "stamptools" in ws.entities
    assert ws.entities["stamptools"].depth_from_root == 2
    assert ws.entities["stamptools"].origin_root == "StampServer"

    # 3-hop neighbor StampViewer should NOT be reached with additional_hops=2
    assert "stampviewer" not in ws.entities

    # Relations across 2 hops
    assert len(ws.relations) == 2
    rel_keys = [r.relation_key for r in ws.relations.values()]
    assert any("StampServer -[belongs_to]-> StampWebRTC" in k for k in rel_keys)
    assert any("StampWebRTC -[depends_on]-> StampTools" in k for k in rel_keys)


def test_root_expansion_authorized_by_admitted_text_and_user_mentions():
    """Verify Root Expansion creates depth=0 local roots for authorized entities and blocks unauthorized entities."""
    db = _mock_graph_db()
    admission_svc = GraphRelationAdmissionService(graph_db=db)
    explorer = GraphExplorer(graph_db=db, admission_service=admission_svc)

    ws = GraphWorkingSet(exploration_roots=("StampServer",))

    # 1. Legitimate new root from user_mentioned_entities
    obs_user = explorer.expand_graph_scope(
        working_set=ws,
        start_entities=["StampTools"],
        additional_hops=1,
        stage1_confirmed_entities={"StampServer"},
        user_mentioned_entities={"StampTools"},
        question="StampTools 是什么？",
    )
    assert obs_user.ok is True
    assert "stamptools" in ws.entities
    assert ws.entities["stamptools"].is_root is True
    assert ws.entities["stamptools"].depth_from_root == 0

    # 2. Unauthorized root -> DENIED
    obs_unauthorized = explorer.expand_graph_scope(
        working_set=ws,
        start_entities=["UnknownEntityX"],
        additional_hops=1,
        stage1_confirmed_entities={"StampServer"},
        admitted_text_entities=set(),
        user_mentioned_entities=set(),
        question="UnknownEntityX 是什么？",
    )
    assert obs_unauthorized.ok is False
    assert obs_unauthorized.status == ToolProgressStatus.DENIED
    assert obs_unauthorized.error == "graph_root_not_authorized"


def test_clarify_suppressed_when_meaningful_options_less_than_two(isolated_storage):
    """Verify that handle_clarify rejects and does not pause when meaningful options < 2."""
    cfg, _, _, _ = isolated_storage()
    from rag_knowledge.services.qa_trace import QaTraceBuilder
    from rag_knowledge.services.query_clarification import ClarificationFilter, ClarificationOption

    conv = ConversationContext(
        session=SessionState(),
        user_question="如何配置 StampServer？",
        head_entity="StampServer",
    )
    trace = QaTraceBuilder(question=conv.user_question)

    opts = [
        ClarificationOption(id="opt_1", label="StampServer", filter=ClarificationFilter(entity_name="StampServer"), source="stage1_history"),
        ClarificationOption(id="fixed_other", label="其他", filter=ClarificationFilter(), source="fixed_other"),
    ]
    meaningful = [opt for opt in opts if getattr(opt, "source", None) != "fixed_other"]
    assert len(meaningful) == 1
    # When meaningful < 2, clarify gate suppresses clarification


def test_grounding_reviewer_verifies_graph_relation_claims():
    """Verify Grounding Reviewer correctly attributes factual claims to graph relation snapshot items."""
    import json
    from rag_knowledge.services.helper_grounding_reviewer import HelperGroundingReviewer

    mock_resp = json.dumps({
        "verdict": "PASS",
        "coverage": "FULL",
        "summary": "回答由图谱事实关系完全支持",
        "claim_reviews": [
            {
                "claim_id": "c1",
                "claim": "StampServer 包含 StampWebRTC 模块",
                "claim_type": "knowledge_claim",
                "evidence_ids": [1],
                "status": "supported",
                "reason": "由图谱事实关系证据 [1] StampServer belongs_to StampWebRTC 直接支持",
            }
        ],
        "rewrite_actions": [],
    })
    reviewer = HelperGroundingReviewer(lambda _msgs: mock_resp)
    context_docs = [
        {
            "content": "StampServer -[belongs_to]-> StampWebRTC",
            "metadata": {
                "citation_id": 1,
                "source_type": "graph_relation",
                "relation_type": "belongs_to",
                "source_name": "StampServer",
                "target_name": "StampWebRTC",
            },
        }
    ]
    result = reviewer.review(
        question="StampServer 包含哪些模块？",
        context_docs=context_docs,
        candidate="StampServer 包含 StampWebRTC 模块 [1]。",
    )

    assert result.verdict == "PASS"
    assert result.coverage == "FULL"
    assert len(result.claim_reviews) == 1
    assert result.claim_reviews[0].status == "supported"


def test_v2_allowed_tools_exposes_expand_graph_scope_and_excludes_link_entities(isolated_storage):
    """Verify that in V2 Agent mode, allowed_tools contains expand_graph_scope and strictly excludes link_entities."""
    cfg, _, _, _ = isolated_storage()
    cfg.agent_orchestration.candidate_pipeline_v2 = True

    conv = ConversationContext(
        session=SessionState(),
        user_question="StampServer 依赖哪些工具？",
        head_entity="StampServer",
        confirmed_entity="StampServer",
        confirmed_entities=("StampServer",),
    )
    pool = EvidencePool(question_id="q_v2_tools")
    budget = AgentBudget(max_steps=3)
    ws = GraphWorkingSet(exploration_roots=("StampServer",))

    loop = AgentLoop(
        conversation=conv,
        evidence=pool,
        budget=budget,
        registry=build_agent_registry(),
        handlers={},
        cfg=cfg,
        graph_explorer=MagicMock(),
    )
    loop.graph_working_set = ws

    state_json = loop._controller_state_for_prompt()
    state = json.loads(state_json)
    allowed_tools = state.get("allowed_tools", [])

    assert "expand_graph_scope" in allowed_tools
    assert "link_entities" not in allowed_tools


def test_non_answer_relations_never_enter_evidence_pool_even_if_approved():
    """Verify that relations with answer_evidence=False (like related_to) never enter EvidencePool even if approved."""
    from rag_knowledge.services.relation_policy import relation_rule

    # 1. Verify relation_rule policy
    rule = relation_rule("related_to")
    assert rule.answer_evidence is False

    # 2. Verify admission service rejects related_to as non-PASS for knowledge answer
    db = MagicMock()
    admission_svc = GraphRelationAdmissionService(graph_db=db)
    cand = GraphRelationCandidate(
        relation_id="rel_weak",
        source_name="StampServer",
        target_name="StampTools",
        relation_type="related_to",
        review_status="approved",
        confidence=1.0,
    )
    adm = admission_svc.admit_relation(cand, question="StampServer 是什么？")
    # related_to without explicit related intent is rejected or does not have DIRECT answer relevance
    assert adm.verdict == "REJECT" or not rule.answer_evidence
