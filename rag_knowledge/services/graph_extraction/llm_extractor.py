from __future__ import annotations

import json
import logging
import re
import os
from pathlib import Path
from typing import Any
import httpx

from rag_knowledge.config import Config
from rag_knowledge.services.backbone_guard import format_backbone_context, load_backbone_constraints
from . import (
    EntityCandidate,
    RelationCandidate,
    ExtractionDiagnostic,
    ExtractionResult
)

logger = logging.getLogger(__name__)

ALLOWED_ENTITY_TYPES = {
    "Product", "Tool", "Service", "Module", "EnvironmentComponent",
    "Procedure", "Step", "Command", "ConfigItem", "Error", "Solution"
}

ALLOWED_RELATION_TYPES = {
    "belongs_to", "requires", "depends_on", "has_procedure", "has_step",
    "runs_command", "uses_config", "configured_by", "causes", "solved_by",
    "defined_in", "alias_of", "different_from"
}


def normalize_name(name: str) -> str:
    """Normalize names: strip, merge spaces, convert full-width parentheses to half-width."""
    if not name:
        return ""
    name = name.strip()
    name = re.sub(r"\s+", " ", name)
    name = name.replace("（", "(").replace("）", ")")
    return name


class LLMGraphExtractor:
    """Schema-constrained LLM semantic graph extractor (MVP-4)."""

    def __init__(self, *, backbone_constraints: dict | None = None):
        self.cfg = Config()
        prompt_dir = Path(__file__).parent / "prompts"
        prompt_file = prompt_dir / f"llm_graph_extractor_{self.cfg.graph_extraction_llm.prompt_version}.md"
        if not prompt_file.exists():
            prompt_file = prompt_dir / "llm_graph_extractor_v1.md"
        self.prompt_template = prompt_file.read_text(encoding="utf-8")
        self._backbone_constraints = backbone_constraints
        self._backbone_context = format_backbone_context(
            backbone_constraints if backbone_constraints is not None else load_backbone_constraints()
        )

    def build_prompt(self, *, doc_category: str, section_path: str, content: str) -> str:
        """Assemble extraction prompt (exposed for unit tests)."""
        return (
            self.prompt_template
            .replace("{backbone_context}", self._backbone_context)
            .replace("{doc_category}", doc_category)
            .replace("{section_path}", section_path)
            .replace("{content}", content)
        )

    def extract(self, chunk: dict) -> ExtractionResult:
        """Extract entities and relations from a chunk using LLM."""
        chunk_id = str(chunk.get("chunk_id") or "")
        content = str(chunk.get("content") or "")
        metadata = chunk.get("metadata") or {}
        doc_category = str(metadata.get("doc_category") or "")
        section_path = str(metadata.get("section_path") or "")

        result = ExtractionResult()

        prompt = self.build_prompt(
            doc_category=doc_category,
            section_path=section_path,
            content=content,
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
        llm_cfg = self.cfg.graph_extraction_llm
        max_retries = max(1, llm_cfg.max_retries)
        
        provider = llm_cfg.provider.lower()
        model = llm_cfg.model
        temp = llm_cfg.temperature

        last_error = None
        for attempt in range(max_retries):
            try:
                if provider == "openai":
                    api_key = os.getenv("OPENAI_API_KEY")
                    if not api_key:
                        raise ValueError("OPENAI_API_KEY env variable is required for provider=openai")
                    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
                    
                    headers = {
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json"
                    }
                    payload = {
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": temp,
                        "response_format": {"type": "json_object"}
                    }
                    
                    with httpx.Client(timeout=60.0) as client:
                        resp = client.post(f"{base_url}/chat/completions", headers=headers, json=payload)
                        resp.raise_for_status()
                        return resp.json()["choices"][0]["message"]["content"]
                
                else:  # Default/ollama
                    payload = {
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "stream": False,
                        # qwen3 thinking mode often returns empty content under format=json
                        "think": False,
                        "options": {
                            "temperature": temp,
                        },
                        "format": "json"
                    }
                    
                    # qwen3:30b graph prompts often exceed 60s; 180s keeps Round-2 pilot reliable
                    with httpx.Client(timeout=180.0) as client:
                        resp = client.post(f"{self.cfg.ollama_base_url}/api/chat", json=payload)
                        resp.raise_for_status()
                        message = resp.json().get("message") or {}
                        content = (message.get("content") or "").strip()
                        if not content:
                            # Fallback if server ignored think=false
                            content = (message.get("thinking") or "").strip()
                        return content
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
            if etype not in ALLOWED_ENTITY_TYPES:
                result.diagnostics.append(
                    ExtractionDiagnostic(
                        code="invalid_entity_type",
                        message=f"Rejected entity '{name}' with invalid type '{etype}'",
                        chunk_id=chunk_id
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

            if not (evidence in content or evidence in section_path):
                result.diagnostics.append(
                    ExtractionDiagnostic(
                        code="invalid_evidence_text",
                        message=f"Rejected entity '{name}' due to evidence_text '{evidence}' not matching content or section_path",
                        chunk_id=chunk_id
                    )
                )
                continue

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

            if not (evidence in content or evidence in section_path):
                result.diagnostics.append(
                    ExtractionDiagnostic(
                        code="invalid_evidence_text",
                        message=f"Rejected relation '{src} ->[{rtype}]-> {tgt}' due to evidence_text '{evidence}' not matching content or section_path",
                        chunk_id=chunk_id
                    )
                )
                continue

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

            if not (evidence in content or evidence in section_path):
                result.diagnostics.append(
                    ExtractionDiagnostic(
                        code="invalid_evidence_text",
                        message=f"Rejected alias '{ent} -> {alias}' due to evidence_text '{evidence}' not matching content or section_path",
                        chunk_id=chunk_id
                    )
                )
                continue

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
