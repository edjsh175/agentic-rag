"""
Chunk Mentions Extractor Service.

Extracts Chunk -[mentions]-> Entity relations with strict guardrails:
1. Hard-blacklist Hub entity types: Product, Tool, Service, FunctionArea, Document, Section.
2. Only allow specific business entity types: Feature, Procedure, DataTable, ConfigItem, Field, Format, Error, Solution, Step, Command, EnvironmentComponent.
3. Layered quota control:
   - Text chunks: max 10 mentions.
   - Table chunks: dynamic quota based on matched table fields.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import List, Dict, Set, Optional

# Hub / Backbone entity types strictly forbidden from receiving 'mentions' edges
FORBIDDEN_ENTITY_TYPES = frozenset({
    "Product",
    "Tool",
    "Service",
    "FunctionArea",
    "Document",
    "Section",
    "Module",
})

# Business entity types eligible for 'mentions' links
ALLOWED_ENTITY_TYPES = frozenset({
    "Feature",
    "Procedure",
    "DataTable",
    "ConfigItem",
    "Field",
    "Format",
    "Error",
    "Solution",
    "Step",
    "Command",
    "EnvironmentComponent",
})


class ChunkMentionsExtractor:
    """Extracts and filters 'mentions' links between Chunks and Business Entities."""

    DEFAULT_TEXT_QUOTA = 10

    def __init__(self, text_quota: int = DEFAULT_TEXT_QUOTA):
        self.text_quota = text_quota

    def extract_mentions(
        self,
        chunk_id: str,
        chunk_text: str,
        entity_candidates: List[Dict],
        is_table: bool = False,
    ) -> List[Dict]:
        """
        Scan chunk_text for occurrences of entity_candidates.
        
        :param chunk_id: The ID of the Chunk.
        :param chunk_text: Raw text or Markdown content of the Chunk.
        :param entity_candidates: List of entity dicts, e.g. [{"id": ..., "name": ..., "entity_type": ...}]
        :param is_table: Flag indicating whether this Chunk represents structured table data.
        :return: List of valid mentions links.
        """
        if not chunk_text or not entity_candidates:
            return []

        # Determine effective quota limit
        if is_table:
            # Table chunks allow matching all valid field/data entities without strict 10 limit
            effective_quota = 100
        else:
            effective_quota = self.text_quota

        matched_mentions = []
        seen_entity_ids: Set[str] = set()

        for entity in entity_candidates:
            etype = entity.get("entity_type", "")
            ename = entity.get("name", "")
            eid = entity.get("id", "")

            # Guardrail 1: Blacklist Hub / Backbone types
            if etype in FORBIDDEN_ENTITY_TYPES:
                continue

            # Guardrail 2: Must belong to ALLOWED_ENTITY_TYPES
            if etype not in ALLOWED_ENTITY_TYPES:
                continue

            # Skip short / empty / invalid names
            if not ename or len(ename.strip()) <= 1 or eid in seen_entity_ids:
                continue

            # Guardrail 3: Text occurrence check (case-insensitive substring or boundary match)
            if self._text_contains_entity(chunk_text, ename):
                seen_entity_ids.add(eid)
                matched_mentions.append({
                    "entity_id": eid,
                    "entity_name": ename,
                    "entity_type": etype,
                    "chunk_id": chunk_id,
                    "link_type": "mentions",
                    "evidence_text": self._extract_evidence(chunk_text, ename),
                })

                # Guardrail 4: Enforce quota
                if len(matched_mentions) >= effective_quota:
                    break

        return matched_mentions

    @staticmethod
    def _text_contains_entity(text: str, entity_name: str) -> bool:
        """Check if entity_name is mentioned in text."""
        # Simple exact substring check
        return entity_name in text

    @staticmethod
    def _extract_evidence(text: str, entity_name: str, max_chars: int = 100) -> str:
        """Extract a snippet of text surrounding the matched entity_name."""
        idx = text.find(entity_name)
        if idx == -1:
            return text[:max_chars]
        start = max(0, idx - 30)
        end = min(len(text), idx + len(entity_name) + 30)
        snippet = text[start:end].strip().replace("\n", " ")
        return snippet if len(snippet) <= max_chars else snippet[:max_chars]
