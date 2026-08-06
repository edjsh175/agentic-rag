"""Entity Identity Subsystem — Single authority for entity identity, resolution, and alias folding."""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from rag_knowledge.models.graph_schema import normalize_entity_name
from rag_knowledge.services.domain_catalog import DomainCatalogLoader
from rag_knowledge.services.backbone_guard import load_backbone_constraints


def normalize_identity_key(name: str) -> str:
    """
    Unified comparison key for entity identity lookup across all modules.
    Replaces full-width parentheses, strips whitespace, and casefolds.
    """
    if not name:
        return ""
    name = name.strip().replace("（", "(").replace("）", ")")
    name = re.sub(r"\s+", " ", name)
    return name.casefold()


def norm_compact(name: str) -> str:
    """Strip all non-alphanumeric and non-chinese characters for compact similarity check."""
    return re.sub(r"[^a-zA-Z0-9\u4e00-\u9fa5]", "", normalize_identity_key(name))


class IdentityOutcome:
    BIND = "bind"
    ALIAS_OF = "alias_of"
    NEW = "new"
    CONFLICT = "conflict"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class IdentityDiagnostic:
    code: str
    message: str


@dataclass(frozen=True)
class IdentityDecision:
    outcome: str  # bind, alias_of, new, conflict, uncertain
    canonical_name: str = ""
    target_entity_id: str = ""
    resolved_type: str = ""  # when set, caller should rewrite candidate entity_type
    diagnostics: list[IdentityDiagnostic] = field(default_factory=list)


class IdentityArbiterProtocol(Protocol):
    def arbitrate(self, candidate_name: str, entity_type: str, target_name: str, target_type: str) -> tuple[str, float]:
        ...


class TypeArbiterProtocol(Protocol):
    def arbitrate(
        self,
        name: str,
        candidate_type: str,
        existing_name: str,
        existing_type: str,
        *,
        evidence_text: str = "",
        source: str = "db",
    ) -> tuple[str, float]:
        """Return (prefer_existing|prefer_candidate|different|unsure, confidence)."""
        ...


class EntityIdentityService:
    """
    Single authority for entity identity decisions.
    Enforces layered lookup order:
    1. Hard constraints (different_from) against DB & in-batch entities (short-circuit, bypass LLM)
    2. Official Catalog / Backbone aliases
    3. Formal DB graph (entities & aliases)
    4. In-batch accepted entities
    5. Near-variant / Substring collision -> Uncertain / LLM Arbiter
    Type mismatches: catalog coerces; DB/batch may use optional TypeArbiter.
    """

    def __init__(
        self,
        db: Any = None,
        catalog: DomainCatalogLoader | None = None,
        arbiter: IdentityArbiterProtocol | None = None,
        type_arbiter: TypeArbiterProtocol | None = None,
    ):
        self.db = db
        self.catalog = catalog or DomainCatalogLoader()
        self.arbiter = arbiter
        self.type_arbiter = type_arbiter
        self.backbone_constraints = load_backbone_constraints()

    def _min_type_confidence(self) -> float:
        try:
            from rag_knowledge.config import Config

            return float(Config().graph_extraction_llm.entity_type_resolve_min_confidence)
        except Exception:
            return 0.80

    def _type_mismatch_decision(
        self,
        *,
        name: str,
        candidate_type: str,
        existing_name: str,
        existing_type: str,
        existing_id: str = "",
        match_as: str = "bind",
        source: str = "db",
        evidence_text: str = "",
        conflict_message: str = "",
        extra_diagnostics: list[IdentityDiagnostic] | None = None,
    ) -> IdentityDecision:
        """Resolve same-identity different-type: optional LLM, else conflict."""
        diags = list(extra_diagnostics or [])
        msg = conflict_message or (
            f"type conflict for '{name}': existing={existing_type} != candidate={candidate_type}"
        )
        if self.type_arbiter:
            verdict, confidence = self.type_arbiter.arbitrate(
                name,
                candidate_type,
                existing_name,
                existing_type,
                evidence_text=evidence_text,
                source=source,
            )
            min_conf = self._min_type_confidence()
            if verdict == "prefer_existing" and confidence >= min_conf:
                outcome = (
                    IdentityOutcome.ALIAS_OF if match_as == "alias_of" else IdentityOutcome.BIND
                )
                diags.append(
                    IdentityDiagnostic(
                        "type_arbiter_prefer_existing",
                        f"type arbiter kept existing type={existing_type} for '{existing_name}' "
                        f"(rejected candidate type={candidate_type}, conf={confidence:.2f})",
                    )
                )
                return IdentityDecision(
                    outcome=outcome,
                    canonical_name=existing_name,
                    target_entity_id=existing_id,
                    resolved_type=existing_type,
                    diagnostics=diags,
                )
            if verdict == "prefer_candidate" and confidence >= min_conf:
                if source == "batch":
                    diags.append(
                        IdentityDiagnostic(
                            "type_arbiter_prefer_candidate",
                            f"type arbiter updated batch type to {candidate_type} for '{name}' "
                            f"(was {existing_type}, conf={confidence:.2f})",
                        )
                    )
                    return IdentityDecision(
                        outcome=IdentityOutcome.BIND,
                        canonical_name=existing_name or name,
                        target_entity_id=existing_id,
                        resolved_type=candidate_type,
                        diagnostics=diags,
                    )
                # Formal DB / catalog-linked DB: do not mutate type during extract.
                diags.append(
                    IdentityDiagnostic(
                        "type_arbiter_prefer_candidate_review",
                        f"type arbiter prefers candidate type={candidate_type} over "
                        f"existing={existing_type} for '{existing_name}'; needs review "
                        f"(conf={confidence:.2f})",
                    )
                )
                diags.append(IdentityDiagnostic("type_conflict", msg))
                return IdentityDecision(
                    outcome=IdentityOutcome.CONFLICT,
                    canonical_name=existing_name,
                    target_entity_id=existing_id,
                    diagnostics=diags,
                )
            if verdict == "different" and confidence >= min_conf:
                diags.append(
                    IdentityDiagnostic(
                        "type_arbiter_different_entities",
                        f"'{name}' ({candidate_type}) treated as distinct from "
                        f"'{existing_name}' ({existing_type}); same key cannot split automatically",
                    )
                )
                diags.append(IdentityDiagnostic("type_conflict", msg))
                return IdentityDecision(
                    outcome=IdentityOutcome.CONFLICT,
                    canonical_name=existing_name,
                    target_entity_id=existing_id,
                    diagnostics=diags,
                )
            diags.append(
                IdentityDiagnostic(
                    "type_arbiter_unsure",
                    f"type arbiter unsure for '{name}': {existing_type} vs {candidate_type} "
                    f"(conf={confidence:.2f})",
                )
            )
        diags.append(IdentityDiagnostic("type_conflict", msg))
        return IdentityDecision(
            outcome=IdentityOutcome.CONFLICT,
            canonical_name=existing_name,
            target_entity_id=existing_id,
            diagnostics=diags,
        )

    def resolve(
        self,
        name: str,
        entity_type: str,
        *,
        batch_type_index: dict[str, str] | None = None,
        batch_entity_ids: dict[str, str] | None = None,
        batch_display_names: dict[str, str] | None = None,
        evidence_text: str = "",
    ) -> IdentityDecision:
        norm_key = normalize_identity_key(name)
        if not norm_key:
            return IdentityDecision(
                outcome=IdentityOutcome.CONFLICT,
                diagnostics=[IdentityDiagnostic("empty_name", "entity name cannot be empty")],
            )

        clean_name = normalize_entity_name(name)
        entities = self.db.list_entities() if self.db and hasattr(self.db, "list_entities") else []
        min_arbiter_conf = 0.8
        try:
            from rag_knowledge.config import Config

            min_arbiter_conf = float(Config().graph_extraction_llm.entity_resolve_min_confidence)
        except Exception:
            pass

        # 1. Check hard constraints (different_from) against catalog target, DB entities & batch entities
        diff_pairs = self.backbone_constraints.get("different_from", [])
        for entity in entities:
            e_name = entity.get("name") or ""
            e_norm = normalize_identity_key(e_name)
            for pair in diff_pairs:
                if len(pair) >= 2:
                    d1, d2 = normalize_identity_key(pair[0]), normalize_identity_key(pair[1])
                    if (norm_key == d1 and e_norm == d2) or (norm_key == d2 and e_norm == d1):
                        return IdentityDecision(
                            outcome=IdentityOutcome.CONFLICT,
                            canonical_name=e_name,
                            diagnostics=[
                                IdentityDiagnostic(
                                    "different_from_violation",
                                    f"'{name}' is marked different_from '{e_name}' in backbone constraints",
                                )
                            ],
                        )

        if batch_display_names:
            for b_key, b_display in batch_display_names.items():
                b_norm = normalize_identity_key(b_display) or b_key
                for pair in diff_pairs:
                    if len(pair) >= 2:
                        d1, d2 = normalize_identity_key(pair[0]), normalize_identity_key(pair[1])
                        if (norm_key == d1 and b_norm == d2) or (norm_key == d2 and b_norm == d1):
                            return IdentityDecision(
                                outcome=IdentityOutcome.CONFLICT,
                                canonical_name=b_display,
                                diagnostics=[
                                    IdentityDiagnostic(
                                        "different_from_violation",
                                        f"'{name}' is marked different_from '{b_display}' in backbone constraints",
                                    )
                                ],
                            )

        # 2. Official Catalog / Backbone alias resolution
        catalog_resolved = self.catalog.resolve(clean_name)
        canonical_from_catalog = catalog_resolved[0] if catalog_resolved else clean_name
        catalog_type = catalog_resolved[1] if catalog_resolved else None

        effective_type = entity_type
        coerce_diags: list[IdentityDiagnostic] = []
        if catalog_type and catalog_type != entity_type:
            # Catalog is gold for known names: coerce type and continue (no LLM).
            coerce_diags.append(
                IdentityDiagnostic(
                    "type_coerced_to_catalog",
                    f"catalog coerced type for '{name}': {entity_type} → {catalog_type}",
                )
            )
            effective_type = catalog_type

        target_canonical = canonical_from_catalog
        target_norm = normalize_identity_key(target_canonical)

        # 3. Check Formal DB graph (entities table & aliases table)
        # 3a. Direct entity name match in DB
        for entity in entities:
            e_name = entity.get("name") or ""
            e_norm = normalize_identity_key(e_name)
            e_id = str(entity.get("id") or "")
            e_type = str(entity.get("entity_type") or "")

            if e_norm == norm_key:
                if e_type == effective_type:
                    return IdentityDecision(
                        outcome=IdentityOutcome.BIND,
                        canonical_name=e_name,
                        target_entity_id=e_id,
                        resolved_type=e_type if coerce_diags else "",
                        diagnostics=list(coerce_diags),
                    )
                return self._type_mismatch_decision(
                    name=name,
                    candidate_type=effective_type,
                    existing_name=e_name,
                    existing_type=e_type,
                    existing_id=e_id,
                    match_as="bind",
                    source="db",
                    evidence_text=evidence_text,
                    conflict_message=(
                        f"type conflict for '{name}': db entity '{e_name}' "
                        f"type={e_type} != candidate={effective_type}"
                    ),
                    extra_diagnostics=coerce_diags,
                )

        # 3b. Check DB aliases table
        aliases = self.db.list_aliases() if self.db and hasattr(self.db, "list_aliases") else []
        for alias in aliases:
            a_text = alias.get("alias") or ""
            if normalize_identity_key(a_text) == norm_key:
                e_id = str(alias.get("entity_id") or "")
                matching_entity = next((e for e in entities if str(e.get("id") or "") == e_id), None)
                if matching_entity:
                    e_name = matching_entity.get("name") or ""
                    e_type = matching_entity.get("entity_type") or ""
                    if e_type == effective_type:
                        return IdentityDecision(
                            outcome=(
                                IdentityOutcome.ALIAS_OF
                                if norm_key != normalize_identity_key(e_name)
                                else IdentityOutcome.BIND
                            ),
                            canonical_name=e_name,
                            target_entity_id=e_id,
                            resolved_type=e_type if coerce_diags else "",
                            diagnostics=list(coerce_diags),
                        )
                    return self._type_mismatch_decision(
                        name=name,
                        candidate_type=effective_type,
                        existing_name=e_name,
                        existing_type=e_type,
                        existing_id=e_id,
                        match_as="alias_of",
                        source="db",
                        evidence_text=evidence_text,
                        conflict_message=(
                            f"type conflict via alias '{a_text}': "
                            f"db entity type={e_type} != candidate={effective_type}"
                        ),
                        extra_diagnostics=coerce_diags,
                    )

        # 3c. Catalog mapped to canonical existing in DB
        if catalog_resolved and target_norm != norm_key:
            for entity in entities:
                e_name = entity.get("name") or ""
                if normalize_identity_key(e_name) == target_norm:
                    e_id = str(entity.get("id") or "")
                    e_type = str(entity.get("entity_type") or "")
                    if e_type == effective_type:
                        return IdentityDecision(
                            outcome=IdentityOutcome.ALIAS_OF,
                            canonical_name=e_name,
                            target_entity_id=e_id,
                            resolved_type=e_type if coerce_diags else "",
                            diagnostics=list(coerce_diags),
                        )
                    return self._type_mismatch_decision(
                        name=name,
                        candidate_type=effective_type,
                        existing_name=e_name,
                        existing_type=e_type,
                        existing_id=e_id,
                        match_as="alias_of",
                        source="db",
                        evidence_text=evidence_text,
                        conflict_message=(
                            f"catalog canonical '{e_name}' type={e_type} != candidate={effective_type}"
                        ),
                        extra_diagnostics=coerce_diags,
                    )

        # 4. In-batch accepted entities
        if batch_type_index and norm_key in batch_type_index:
            b_type = batch_type_index[norm_key]
            b_id = (batch_entity_ids or {}).get(norm_key, "")
            b_display = (batch_display_names or {}).get(norm_key) or clean_name
            if b_type == effective_type:
                return IdentityDecision(
                    outcome=IdentityOutcome.BIND,
                    canonical_name=clean_name,
                    target_entity_id=b_id,
                    resolved_type=effective_type if coerce_diags else "",
                    diagnostics=list(coerce_diags),
                )
            return self._type_mismatch_decision(
                name=name,
                candidate_type=effective_type,
                existing_name=b_display,
                existing_type=b_type,
                existing_id=b_id,
                match_as="bind",
                source="batch",
                evidence_text=evidence_text,
                conflict_message=(
                    f"batch type conflict for '{name}': "
                    f"batch entity type={b_type} != candidate={effective_type}"
                ),
                extra_diagnostics=coerce_diags,
            )

        if catalog_resolved and target_norm != norm_key and batch_type_index and target_norm in batch_type_index:
            b_type = batch_type_index[target_norm]
            b_id = (batch_entity_ids or {}).get(target_norm, "")
            if b_type == effective_type:
                return IdentityDecision(
                    outcome=IdentityOutcome.ALIAS_OF,
                    canonical_name=target_canonical,
                    target_entity_id=b_id,
                    resolved_type=effective_type if coerce_diags else "",
                    diagnostics=list(coerce_diags),
                )

        # Section hierarchy exception
        if effective_type == "Section":
            return IdentityDecision(
                outcome=IdentityOutcome.NEW,
                canonical_name=clean_name,
                resolved_type=effective_type if coerce_diags else "",
                diagnostics=list(coerce_diags),
            )

        # 5. Near-variant & Substring collision check -> Uncertain / LLM Arbiter
        near_targets: list[tuple[str, str, str]] = []  # (display_name, entity_type, entity_id)
        for entity in entities:
            near_targets.append(
                (
                    str(entity.get("name") or ""),
                    str(entity.get("entity_type") or ""),
                    str(entity.get("id") or ""),
                )
            )
        if batch_type_index and batch_display_names:
            for b_key, b_type in batch_type_index.items():
                near_targets.append(
                    (
                        batch_display_names.get(b_key) or b_key,
                        str(b_type or ""),
                        (batch_entity_ids or {}).get(b_key, ""),
                    )
                )

        # Helper to get prefix and leaf name for comparison
        def get_comparison_prefix_and_leaf(entity_name: str, entity_type: str) -> tuple[str, str]:
            if entity_type == "FunctionArea" and "::" in entity_name:
                parts = entity_name.split("::")
                return "::".join(parts[:-1]), parts[-1]
            if entity_type == "Field" and "." in entity_name:
                parts = entity_name.split(".")
                return ".".join(parts[:-1]), parts[-1]
            return "", entity_name

        prefix_candidate, leaf_candidate = get_comparison_prefix_and_leaf(name, effective_type)
        comp_candidate = norm_compact(leaf_candidate)
        norm_key_leaf = normalize_identity_key(leaf_candidate)

        for existing_name, existing_type, target_id in near_targets:
            if not existing_name or existing_type != effective_type:
                continue
            existing_norm = normalize_identity_key(existing_name)
            if existing_norm == norm_key:
                continue

            prefix_existing, leaf_existing = get_comparison_prefix_and_leaf(existing_name, existing_type)
            # If prefixes differ, they must be distinct entities. Skip collision.
            if normalize_identity_key(prefix_candidate) != normalize_identity_key(prefix_existing):
                continue

            comp_existing = norm_compact(leaf_existing)
            existing_norm_leaf = normalize_identity_key(leaf_existing)

            is_near_variant = False
            if existing_norm_leaf and (norm_key_leaf in existing_norm_leaf or existing_norm_leaf in norm_key_leaf):
                is_near_variant = True
            elif comp_candidate and comp_existing:
                if comp_candidate in comp_existing or comp_existing in comp_candidate:
                    is_near_variant = True
                elif effective_type == "DataTable":
                    # DataTable 严肃的数据库表名不应进行模糊 SequenceMatcher 相似度比对
                    pass
                else:
                    ratio = difflib.SequenceMatcher(None, comp_candidate, comp_existing).ratio()
                    if ratio >= 0.8 and min(len(comp_candidate), len(comp_existing)) >= 4:
                        is_near_variant = True

            if not is_near_variant:
                continue

            if self.arbiter:
                verdict, confidence = self.arbiter.arbitrate(
                    clean_name, effective_type, existing_name, existing_type
                )
                if verdict == "same" and confidence >= min_arbiter_conf:
                    return IdentityDecision(
                        outcome=IdentityOutcome.ALIAS_OF,
                        canonical_name=existing_name,
                        target_entity_id=target_id,
                        resolved_type=existing_type if coerce_diags else "",
                        diagnostics=list(coerce_diags),
                    )
                if verdict == "different" and confidence >= min_arbiter_conf:
                    return IdentityDecision(
                        outcome=IdentityOutcome.NEW,
                        canonical_name=clean_name,
                        resolved_type=effective_type if coerce_diags else "",
                        diagnostics=list(coerce_diags),
                    )
            return IdentityDecision(
                outcome=IdentityOutcome.UNCERTAIN,
                canonical_name=existing_name,
                target_entity_id=target_id,
                diagnostics=list(coerce_diags)
                + [
                    IdentityDiagnostic(
                        "possible_duplicate",
                        f"'{clean_name}' may duplicate existing entity '{existing_name}'",
                    )
                ],
            )

        # 6. Default new entity
        return IdentityDecision(
            outcome=IdentityOutcome.NEW,
            canonical_name=target_canonical,
            resolved_type=effective_type if coerce_diags else "",
            diagnostics=list(coerce_diags),
        )


class LLMEntityTypeArbiter:
    """Optional LLM backend for same-name type conflicts (Step vs Procedure, etc.)."""

    def __init__(self, llm_client: Any = None, *, use_graph_endpoint: bool = False):
        self.llm_client = llm_client
        self.use_graph_endpoint = use_graph_endpoint

    def arbitrate(
        self,
        name: str,
        candidate_type: str,
        existing_name: str,
        existing_type: str,
        *,
        evidence_text: str = "",
        source: str = "db",
    ) -> tuple[str, float]:
        if not self.llm_client and not self.use_graph_endpoint:
            return ("unsure", 0.0)
        try:
            evidence = (evidence_text or "").strip()
            evidence_block = f"候选证据: {evidence}\n" if evidence else ""
            prompt = (
                "你是知识图谱实体类型裁决器。同一称谓在库中已有类型，与新抽取类型不一致。\n"
                f"名称: {name}\n"
                f"已有记录: {existing_name} (类型: {existing_type}, 来源: {source})\n"
                f"新候选类型: {candidate_type}\n"
                f"{evidence_block}\n"
                "类型约定简述:\n"
                "- Procedure: 可复用业务流程/功能流程；Step: 流程内的单一步骤\n"
                "- Tool/Service/Product/Module: 产品体系层级\n"
                "- ConfigItem/Command/EnvironmentComponent: 配置、命令、环境组件\n\n"
                "只回答 JSON:\n"
                '{"verdict":"prefer_existing"|"prefer_candidate"|"different"|"unsure","confidence":0.0}\n'
                "prefer_existing=保留已有类型并折叠；prefer_candidate=更相信候选类型；"
                "different=实为不同实体（同名冲突需人工）；unsure=无法判断。"
            )
            if self.llm_client is not None:
                raw_response = self.llm_client.invoke(prompt)
                raw_text = raw_response.content if hasattr(raw_response, "content") else str(raw_response)
            else:
                raw_text = self._call_graph_llm(prompt)
            if "```" in raw_text:
                match = re.search(r"```(?:json)?\s*({.*?})\s*```", raw_text, re.DOTALL)
                if match:
                    raw_text = match.group(1)
            data = json.loads(raw_text.strip())
            verdict = str(data.get("verdict") or "unsure").lower()
            if verdict not in {"prefer_existing", "prefer_candidate", "different", "unsure"}:
                verdict = "unsure"
            confidence = float(data.get("confidence") or 0.0)
            return (verdict, confidence)
        except Exception:
            return ("unsure", 0.0)

    def _call_graph_llm(self, prompt: str) -> str:
        from rag_knowledge.config import Config
        from rag_knowledge.llm_http import chat

        cfg = Config()
        llm_cfg = cfg.graph_extraction_llm
        return chat(
            cfg.graph_extraction_endpoint,
            [{"role": "user", "content": prompt}],
            default_ollama=cfg.ollama_base_url,
            temperature=llm_cfg.temperature,
            format_json=True,
            timeout=120.0,
            think=False,
        )


class LLMIdentityArbiter:
    """
    Optional LLM backend for resolving uncertain entity identity candidate pairs.
    Decoupled from extraction --include-llm; off by default.
    """

    def __init__(self, llm_client: Any = None, *, use_graph_endpoint: bool = False):
        self.llm_client = llm_client
        self.use_graph_endpoint = use_graph_endpoint

    def arbitrate(
        self,
        candidate_name: str,
        candidate_type: str,
        target_name: str,
        target_type: str,
    ) -> tuple[str, float]:
        if not self.llm_client and not self.use_graph_endpoint:
            return ("unsure", 0.0)
        try:
            prompt = (
                f"你是一个严格的知识图谱实体身份裁决器。\n"
                f"请裁决以下两个称谓在领域上下文中是否指代同一个实体：\n"
                f"实体 1: {candidate_name} (类型: {candidate_type})\n"
                f"实体 2: {target_name} (类型: {target_type})\n\n"
                f"只回答 JSON 格式:\n"
                f'{{"verdict": "same" | "different" | "unsure", "confidence": 0.9, "canonical": "{target_name}"}}'
            )
            if self.llm_client is not None:
                raw_response = self.llm_client.invoke(prompt)
                raw_text = raw_response.content if hasattr(raw_response, "content") else str(raw_response)
            else:
                raw_text = self._call_graph_llm(prompt)
            if "```" in raw_text:
                match = re.search(r"```(?:json)?\s*({.*?})\s*```", raw_text, re.DOTALL)
                if match:
                    raw_text = match.group(1)
            data = json.loads(raw_text.strip())
            verdict = str(data.get("verdict") or "unsure").lower()
            confidence = float(data.get("confidence") or 0.0)
            return (verdict, confidence)
        except Exception:
            return ("unsure", 0.0)

    def _call_graph_llm(self, prompt: str) -> str:
        from rag_knowledge.config import Config
        from rag_knowledge.llm_http import chat

        cfg = Config()
        llm_cfg = cfg.graph_extraction_llm
        return chat(
            cfg.graph_extraction_endpoint,
            [{"role": "user", "content": prompt}],
            default_ollama=cfg.ollama_base_url,
            temperature=llm_cfg.temperature,
            format_json=True,
            timeout=120.0,
            think=False,
        )
