from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from rag_knowledge.config import Config
from rag_knowledge.models.graph_schema import normalize_entity_name, validate_relation
from rag_knowledge.services.backbone_guard import (
    describe_conflict,
    format_backbone_context,
    load_backbone_constraints,
)
from rag_knowledge.services.relation_recovery import is_generic_entity_name
from . import (
    EntityCandidate,
    RelationCandidate,
    ExtractionDiagnostic,
    ExtractionResult
)
from .evidence_span import repair_evidence_span


logger = logging.getLogger(__name__)

ALLOWED_ENTITY_TYPES = {
    "Product", "Tool", "Utility", "Service", "Module", "EnvironmentComponent",
    "Feature", "Constraint",
    "Procedure", "Step", "Command", "ConfigItem", "Error", "Solution"
}

ALLOWED_RELATION_TYPES = {
    "belongs_to", "requires", "depends_on", "has_procedure", "has_step",
    "runs_command", "uses_config", "configured_by", "causes", "solved_by",
    "defined_in", "alias_of", "different_from"
}

# Deterministic anti-noise for ConfigItem (Round3b); keep lists small and explicit.
_NOISY_CONFIG_FORMATS = frozenset({
    "jpg", "jpgli", "webp", "crn", "dds", "ktx2", "png", "tiff", "geotiff",
})
_NOISY_CONFIG_CRS = frozenset({
    "wgs-84", "wgs84", "国家2000", "西安80", "北京54", "cgcs2000",
})
_NOISY_CONFIG_PROJECTIONS = frozenset({
    "高斯投影", "utm投影", "墨卡托投影", "enu投影", "web墨卡托",
    "高斯投影(本地)", "utm投影(本地)", "高斯投影（本地）", "utm投影（本地）",
})
_NOISY_CONFIG_UI_LABELS = frozenset({
    "中央子午线", "基准纬度", "坐标含带号", "北向偏移", "东向偏移",
    "投影面高程", "缩放参数", "渲染效率",
    "3度分带", "6度分带", "经纬度坐标", "平面坐标", "高程偏移",
    "四参数", "平移参数", "旋转参数", "七参数", "坐标偏移",
})
_COMMAND_NAME_RE = re.compile(
    r"^(?:sudo\s+)?"
    r"(?:systemctl|service|yum|dnf|rpm|apt(?:-get)?|docker|podman|psql|tar|chmod|chown|"
    r"mkdir|curl|wget|firewall-cmd|semanage|restorecon|nginx|pm2|node|npm|java|jar)\b",
    re.IGNORECASE,
)
_COMMAND_SIGNAL_RE = re.compile(
    r"(?:^|\n)\s*(?:\$\s*)?(?:sudo\s+)?"
    r"(?:systemctl|service|yum|dnf|rpm|apt(?:-get)?|docker|podman|psql|tar\s+-[a-zA-Z]*|"
    r"chmod|chown|firewall-cmd)\b",
    re.IGNORECASE | re.MULTILINE,
)


def normalize_name(name: str) -> str:
    """Normalize names: strip, merge spaces, convert full-width parentheses to half-width."""
    if not name:
        return ""
    name = name.strip()
    name = re.sub(r"\s+", " ", name)
    name = name.replace("（", "(").replace("）", ")")
    return name


def is_noisy_config_item(name: str) -> bool:
    """Return True when name is a known enum/UI-label false-positive ConfigItem."""
    n = normalize_name(name)
    if not n:
        return True
    low = n.lower()
    if low in _NOISY_CONFIG_FORMATS:
        return True
    if low in _NOISY_CONFIG_CRS or n in _NOISY_CONFIG_CRS:
        return True
    if low in _NOISY_CONFIG_PROJECTIONS or n in _NOISY_CONFIG_PROJECTIONS:
        return True
    if n in _NOISY_CONFIG_UI_LABELS:
        return True
    if re.fullmatch(r"epsg\s*:?\s*\d+", low):
        return True
    return False


def maybe_reclassify_as_command(entity_type: str, name: str) -> str:
    """Promote shell-like names mistyped as Procedure/Step/ConfigItem to Command."""
    if entity_type not in {"Procedure", "Step", "ConfigItem"}:
        return entity_type
    if _COMMAND_NAME_RE.search(normalize_name(name)):
        return "Command"
    return entity_type


def chunk_has_command_signal(content: str) -> bool:
    """True when chunk text contains concrete shell/CLI lines worth LLM Command extract."""
    return bool(_COMMAND_SIGNAL_RE.search(content or ""))


def early_check_relation_endpoints(
    source_name: str,
    relation_type: str,
    target_name: str,
    type_index: dict[str, str],
) -> tuple[str, str, bool, str | None]:
    """Validate relation against known endpoint types in the same extract.

    Returns (source_name, target_name, flipped, reject_reason).
    Deterministic schema-only (no LLM). Unknown endpoint types defer.
    """
    from rag_knowledge.services.relation_direction import (
        DirectionAction,
        RelationDirectionService,
    )

    src = normalize_entity_name(source_name)
    tgt = normalize_entity_name(target_name)
    st = type_index.get(src)
    tt = type_index.get(tgt)
    if not st or not tt or not relation_type:
        return source_name, target_name, False, None

    decision = RelationDirectionService(arbiter=None).decide(
        source_name,
        relation_type,
        target_name,
        source_type=st,
        target_type=tt,
    )
    if decision.action == DirectionAction.ILLEGAL:
        ok, reason = validate_relation(st, relation_type, tt)
        return (
            source_name,
            target_name,
            False,
            reason or f"{st}-[{relation_type}]->{tt}",
        )
    if decision.action == DirectionAction.FLIP:
        return decision.source_name, decision.target_name, True, None
    return source_name, target_name, False, None


class LLMGraphExtractor:
    """Schema-constrained LLM semantic graph extractor (MVP-4)."""

    def __init__(self, *, backbone_constraints: dict | None = None):
        self.cfg = Config()
        self.backbone_constraints = (
            backbone_constraints
            if backbone_constraints is not None
            else load_backbone_constraints()
        )
        prompt_dir = Path(__file__).parent / "prompts"
        prompt_file = prompt_dir / f"llm_graph_extractor_{self.cfg.graph_extraction_llm.prompt_version}.md"
        if not prompt_file.exists():
            prompt_file = prompt_dir / "llm_graph_extractor_v1.md"
        self.prompt_template = prompt_file.read_text(encoding="utf-8")
        self._backbone_constraints = backbone_constraints
        self._backbone_context = format_backbone_context(
            backbone_constraints if backbone_constraints is not None else load_backbone_constraints()
        )

    def build_prompt(
        self,
        *,
        doc_category: str,
        section_path: str,
        content: str,
        function_area_context: str = "None available",
        salvage_note: str = "",
    ) -> str:
        """Assemble extraction prompt (exposed for unit tests)."""
        prompt = (
            self.prompt_template
            .replace("{backbone_context}", self._backbone_context)
            .replace("{function_area_context}", function_area_context or "None available")
            .replace("{doc_category}", doc_category)
            .replace("{section_path}", section_path)
            .replace("{content}", content)
        )
        if salvage_note:
            prompt = f"{prompt.rstrip()}\n{salvage_note}"
        return prompt

    def extract(
        self,
        chunk: dict,
        function_areas: list[str] | None = None,
        *,
        salvage_note: str = "",
    ) -> ExtractionResult:
        """Extract entities and relations from a chunk using LLM."""
        chunk_id = str(chunk.get("chunk_id") or "")
        content = str(chunk.get("content") or "")
        metadata = chunk.get("metadata") or {}
        doc_category = str(metadata.get("doc_category") or "")
        section_path = str(metadata.get("section_path") or "")

        result = ExtractionResult()

        fa_ctx = (
            "\n".join(f"- {fa}" for fa in function_areas)
            if function_areas
            else "None available"
        )

        prompt = self.build_prompt(
            doc_category=doc_category,
            section_path=section_path,
            content=content,
            function_area_context=fa_ctx,
            salvage_note=salvage_note,
        )

        try:
            raw_response = self._call_llm_with_retries(prompt)
            data = self._clean_and_parse_json(raw_response)
            self._validate_and_normalize(data, chunk_id, doc_category, result, content, section_path)
        except Exception as e:
            logger.error("LLM extraction failed for chunk %s: %s", chunk_id, e)
            result.diagnostics.append(
                ExtractionDiagnostic(
                    code="llm_extraction_failed",
                    message=f"LLM extraction failed: {str(e)}",
                    chunk_id=chunk_id
                )
            )

        return result

    def _call_llm_with_retries(self, prompt: str) -> str:
        """Call LLM with retries on HTTP errors."""
        from rag_knowledge.llm_http import chat

        llm_cfg = self.cfg.graph_extraction_llm
        max_retries = max(1, llm_cfg.max_retries)
        endpoint = self.cfg.graph_extraction_endpoint
        last_error = None
        for attempt in range(max_retries):
            try:
                return chat(
                    endpoint,
                    [{"role": "user", "content": prompt}],
                    default_ollama=self.cfg.ollama_base_url,
                    temperature=llm_cfg.temperature,
                    format_json=True,
                    timeout=180.0,
                    think=False,
                )
            except Exception as e:
                last_error = e
                logger.warning("LLM extraction call attempt %d failed: %s", attempt + 1, e)

        raise last_error or RuntimeError("LLM extraction call failed after all retries")

    def _clean_and_parse_json(self, raw: str) -> dict:
        """Sanitize markdown wrapper and parse JSON."""
        cleaned = raw.strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()
        return json.loads(cleaned)

    def _validate_and_normalize(self, data: dict, chunk_id: str, doc_category: str, result: ExtractionResult, content: str, section_path: str):
        """Validate candidate schema, confidence thresholds, non-empty evidence, and normalize names."""
        llm_cfg = self.cfg.graph_extraction_llm
        min_conf = llm_cfg.min_confidence

        # 1. Parse and validate entities
        entities_data = data.get("entities", [])
        if not isinstance(entities_data, list):
            entities_data = []

        for item in entities_data:
            name = normalize_name(item.get("name", ""))
            if not name:
                continue

            etype = item.get("entity_type", "")
            if etype == "FunctionArea":
                result.diagnostics.append(
                    ExtractionDiagnostic(
                        code="function_area_readonly",
                        message=f"Rejected LLM-created FunctionArea entity '{name}'. FunctionArea nodes are read-only.",
                        chunk_id=chunk_id
                    )
                )
                continue

            if etype not in ALLOWED_ENTITY_TYPES:
                result.diagnostics.append(
                    ExtractionDiagnostic(
                        code="invalid_entity_type",
                        message=f"Rejected entity '{name}' with invalid type '{etype}'",
                        chunk_id=chunk_id
                    )
                )
                continue

            etype = maybe_reclassify_as_command(etype, name)
            from rag_knowledge.services.entity_type_guard import coerce_entity_type

            etype = coerce_entity_type(name, etype)

            if is_generic_entity_name(name) and etype in {
                "Procedure",
                "Step",
                "Command",
                "ConfigItem",
                "Tool",
                "Utility",
                "Module",
            }:
                result.diagnostics.append(
                    ExtractionDiagnostic(
                        code="generic_entity_name",
                        message=f"Rejected {etype} '{name}' as generic/ambiguous leaf name",
                        chunk_id=chunk_id,
                    )
                )
                continue

            if etype == "ConfigItem" and is_noisy_config_item(name):
                result.diagnostics.append(
                    ExtractionDiagnostic(
                        code="noisy_config_item",
                        message=f"Rejected ConfigItem '{name}' as format/CRS/UI-label noise",
                        chunk_id=chunk_id,
                    )
                )
                continue

            bb_constraints = self.backbone_constraints
            conflict_msg = describe_conflict("entity", {"name": name, "entity_type": etype}, bb_constraints)
            if conflict_msg:
                result.diagnostics.append(
                    ExtractionDiagnostic(
                        code="conflicts_product_backbone",
                        message=f"Rejected entity '{name}' due to product backbone lock: {conflict_msg}",
                        chunk_id=chunk_id,
                    )
                )
                continue

            evidence = str(item.get("evidence_text", "")).strip()
            if not evidence:
                result.diagnostics.append(
                    ExtractionDiagnostic(
                        code="missing_evidence",
                        message=f"Rejected entity '{name}' due to missing evidence_text",
                        chunk_id=chunk_id
                    )
                )
                continue

            repaired = repair_evidence_span(
                evidence, content, section_path, anchor=name
            )
            if repaired is None:
                result.diagnostics.append(
                    ExtractionDiagnostic(
                        code="invalid_evidence_text",
                        message=f"Rejected entity '{name}' due to evidence_text '{evidence}' not matching content or section_path",
                        chunk_id=chunk_id
                    )
                )
                continue
            evidence = repaired.text

            if "confidence" not in item:
                result.diagnostics.append(
                    ExtractionDiagnostic(
                        code="missing_confidence",
                        message=f"Rejected entity '{name}' due to missing confidence",
                        chunk_id=chunk_id
                    )
                )
                continue


            try:
                conf = float(item["confidence"])
            except (TypeError, ValueError):
                result.diagnostics.append(
                    ExtractionDiagnostic(
                        code="invalid_confidence",
                        message=f"Rejected entity '{name}' with non-numeric confidence '{item.get('confidence')}'",
                        chunk_id=chunk_id
                    )
                )
                continue

            if conf < 0.0 or conf > 1.0:
                result.diagnostics.append(
                    ExtractionDiagnostic(
                        code="confidence_out_of_range",
                        message=f"Rejected entity '{name}' with confidence '{conf}' out of range [0, 1]",
                        chunk_id=chunk_id
                    )
                )
                continue

            if conf < min_conf:
                result.diagnostics.append(
                    ExtractionDiagnostic(
                        code="low_confidence",
                        message=f"Rejected entity '{name}' due to low confidence: {conf:.2f} < {min_conf:.2f}",
                        chunk_id=chunk_id
                    )
                )
                continue

            properties = item.get("properties") or {}
            if not isinstance(properties, dict):
                properties = {}

            # Populate created_by in properties or payload
            result.entities.append(
                EntityCandidate(
                    name=name,
                    entity_type=etype,
                    doc_category=doc_category,
                    properties={
                        **properties,
                        "created_by": "llm:schema_extractor",
                        "prompt_version": llm_cfg.prompt_version,
                        "extractor_version": llm_cfg.extractor_version,
                        "confidence": conf
                    },
                    source_chunk_id=chunk_id,
                    evidence_text=evidence
                )
            )

        # Local type index from entities accepted in this same extract (early pair check).
        type_index: dict[str, str] = {
            normalize_entity_name(e.name): e.entity_type for e in result.entities
        }

        # 2. Parse and validate relations
        relations_data = data.get("relations", [])
        if not isinstance(relations_data, list):
            relations_data = []

        for item in relations_data:
            src = normalize_name(item.get("source_name", ""))
            tgt = normalize_name(item.get("target_name", ""))
            if not src or not tgt:
                continue

            rtype = item.get("relation_type", "")
            if rtype not in ALLOWED_RELATION_TYPES:
                result.diagnostics.append(
                    ExtractionDiagnostic(
                        code="invalid_relation_type",
                        message=f"Rejected relation '{src} ->[{rtype}]-> {tgt}' with invalid type '{rtype}'",
                        chunk_id=chunk_id
                    )
                )
                continue

            evidence = str(item.get("evidence_text", "")).strip()
            if not evidence:
                result.diagnostics.append(
                    ExtractionDiagnostic(
                        code="missing_evidence",
                        message=f"Rejected relation '{src} ->[{rtype}]-> {tgt}' due to missing evidence_text",
                        chunk_id=chunk_id
                    )
                )
                continue

            repaired = repair_evidence_span(
                evidence, content, section_path, anchor=src
            ) or repair_evidence_span(
                evidence, content, section_path, anchor=tgt
            )
            if repaired is None:
                result.diagnostics.append(
                    ExtractionDiagnostic(
                        code="invalid_evidence_text",
                        message=f"Rejected relation '{src} ->[{rtype}]-> {tgt}' due to evidence_text '{evidence}' not matching content or section_path",
                        chunk_id=chunk_id
                    )
                )
                continue
            evidence = repaired.text

            if "confidence" not in item:
                result.diagnostics.append(
                    ExtractionDiagnostic(
                        code="missing_confidence",
                        message=f"Rejected relation '{src} ->[{rtype}]-> {tgt}' due to missing confidence",
                        chunk_id=chunk_id
                    )
                )
                continue


            try:
                conf = float(item["confidence"])
            except (TypeError, ValueError):
                result.diagnostics.append(
                    ExtractionDiagnostic(
                        code="invalid_confidence",
                        message=f"Rejected relation '{src} ->[{rtype}]-> {tgt}' with non-numeric confidence '{item.get('confidence')}'",
                        chunk_id=chunk_id
                    )
                )
                continue

            if conf < 0.0 or conf > 1.0:
                result.diagnostics.append(
                    ExtractionDiagnostic(
                        code="confidence_out_of_range",
                        message=f"Rejected relation '{src} ->[{rtype}]-> {tgt}' with confidence '{conf}' out of range [0, 1]",
                        chunk_id=chunk_id
                    )
                )
                continue

            if conf < min_conf:
                result.diagnostics.append(
                    ExtractionDiagnostic(
                        code="low_confidence",
                        message=f"Rejected relation '{src} ->[{rtype}]-> {tgt}' due to low confidence: {conf:.2f} < {min_conf:.2f}",
                        chunk_id=chunk_id
                    )
                )
                continue

            src, tgt, flipped, reject_reason = early_check_relation_endpoints(
                src, rtype, tgt, type_index
            )
            if reject_reason:
                result.diagnostics.append(
                    ExtractionDiagnostic(
                        code="illegal_relation_pair",
                        message=(
                            f"Rejected relation '{src} ->[{rtype}]-> {tgt}' "
                            f"as illegal endpoint types: {reject_reason}"
                        ),
                        chunk_id=chunk_id,
                    )
                )
                continue

            rel_conflict = describe_conflict(
                "relation",
                {"source_name": src, "relation_type": rtype, "target_name": tgt},
                self.backbone_constraints,
            )
            if rel_conflict:
                result.diagnostics.append(
                    ExtractionDiagnostic(
                        code="conflicts_product_backbone",
                        message=f"Rejected relation '{src} ->[{rtype}]-> {tgt}' due to product backbone lock: {rel_conflict}",
                        chunk_id=chunk_id,
                    )
                )
                continue

            result.relations.append(
                RelationCandidate(
                    source_name=src,
                    relation_type=rtype,
                    target_name=tgt,
                    source_chunk_id=chunk_id,
                    evidence_text=evidence
                )
            )

            # Store metadata
            key = (src, rtype, tgt)
            result.relation_metadata[key] = {
                "confidence": conf,
                "prompt_version": llm_cfg.prompt_version,
                "extractor_version": llm_cfg.extractor_version,
                **({"direction_flipped": True} if flipped else {}),
            }

        # 3. Parse and validate aliases
        aliases_data = data.get("aliases", [])
        if not isinstance(aliases_data, list):
            aliases_data = []

        for item in aliases_data:
            ent = normalize_name(item.get("entity_name", ""))
            alias = normalize_name(item.get("alias", ""))
            if not ent or not alias:
                continue

            evidence = str(item.get("evidence_text", "")).strip()
            if not evidence:
                result.diagnostics.append(
                    ExtractionDiagnostic(
                        code="missing_evidence",
                        message=f"Rejected alias '{ent} -> {alias}' due to missing evidence_text",
                        chunk_id=chunk_id
                    )
                )
                continue

            repaired = repair_evidence_span(
                evidence, content, section_path, anchor=ent
            ) or repair_evidence_span(
                evidence, content, section_path, anchor=alias
            )
            if repaired is None:
                result.diagnostics.append(
                    ExtractionDiagnostic(
                        code="invalid_evidence_text",
                        message=f"Rejected alias '{ent} -> {alias}' due to evidence_text '{evidence}' not matching content or section_path",
                        chunk_id=chunk_id
                    )
                )
                continue
            evidence = repaired.text

            if "confidence" not in item:
                result.diagnostics.append(
                    ExtractionDiagnostic(
                        code="missing_confidence",
                        message=f"Rejected alias '{ent} -> {alias}' due to missing confidence",
                        chunk_id=chunk_id
                    )
                )
                continue


            try:
                conf = float(item["confidence"])
            except (TypeError, ValueError):
                result.diagnostics.append(
                    ExtractionDiagnostic(
                        code="invalid_confidence",
                        message=f"Rejected alias '{ent} -> {alias}' with non-numeric confidence '{item.get('confidence')}'",
                        chunk_id=chunk_id
                    )
                )
                continue

            if conf < 0.0 or conf > 1.0:
                result.diagnostics.append(
                    ExtractionDiagnostic(
                        code="confidence_out_of_range",
                        message=f"Rejected alias '{ent} -> {alias}' with confidence '{conf}' out of range [0, 1]",
                        chunk_id=chunk_id
                    )
                )
                continue

            if conf < min_conf:
                result.diagnostics.append(
                    ExtractionDiagnostic(
                        code="low_confidence",
                        message=f"Rejected alias '{ent} -> {alias}' due to low confidence: {conf:.2f} < {min_conf:.2f}",
                        chunk_id=chunk_id
                    )
                )
                continue

            result.aliases.append({
                "entity_name": ent,
                "alias": alias,
                "confidence": conf,
                "evidence_text": evidence,
                "source_chunk_id": chunk_id,
                "created_by": "llm:schema_extractor"
            })

        # 4. Parse diagnostics
        diagnostics_data = data.get("diagnostics", [])
        if not isinstance(diagnostics_data, list):
            diagnostics_data = []

        for item in diagnostics_data:
            code = str(item.get("code", "unknown_diagnostic"))
            msg = str(item.get("message", ""))
            result.diagnostics.append(
                ExtractionDiagnostic(
                    code=code,
                    message=msg,
                    chunk_id=chunk_id
                )
            )
