"""Deterministic knowledge-graph extraction from structured chunks."""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import Any

from rag_knowledge.models.graph_schema import (
    DATA_SPEC_KEYWORDS,
    DOC_CATEGORY_TO_PRODUCT,
    KNOWN_SERVICE_NAMES,
    KNOWN_TOOL_NAMES,
    make_section_entity_name,
)


@dataclass(frozen=True)
class EntityCandidate:
    name: str
    entity_type: str
    doc_category: str = ""
    properties: dict[str, Any] = field(default_factory=dict)
    source_chunk_id: str = ""
    evidence_text: str = ""


@dataclass(frozen=True)
class RelationCandidate:
    source_name: str
    relation_type: str
    target_name: str
    source_chunk_id: str = ""
    evidence_text: str = ""


@dataclass(frozen=True)
class FieldCandidate:
    table_name: str
    field_name: str
    description: str = ""
    required: bool = False
    unit: str = ""
    value_range: str = ""
    source_chunk_id: str = ""

    @property
    def scoped_name(self) -> str:
        return f"{self.table_name}.{self.field_name}"


@dataclass(frozen=True)
class ChunkLinkCandidate:
    entity_name: str
    chunk_id: str
    link_type: str = "evidence"
    section_path: str = ""
    source: str = ""
    evidence_text: str = ""


@dataclass(frozen=True)
class ExtractionDiagnostic:
    code: str
    message: str
    chunk_id: str = ""


@dataclass
class ExtractionResult:
    entities: list[EntityCandidate] = field(default_factory=list)
    relations: list[RelationCandidate] = field(default_factory=list)
    fields: list[FieldCandidate] = field(default_factory=list)
    links: list[ChunkLinkCandidate] = field(default_factory=list)
    diagnostics: list[ExtractionDiagnostic] = field(default_factory=list)
    relation_metadata: dict[tuple[str, str, str], dict[str, Any]] = field(default_factory=dict)
    aliases: list[dict[str, Any]] = field(default_factory=list)

    def entity(self, name: str) -> EntityCandidate | None:
        return next((item for item in self.entities if item.name == name), None)

    def has_relation(self, source: str, relation: str, target: str) -> bool:
        return any(
            item.source_name == source
            and item.relation_type == relation
            and item.target_name == target
            for item in self.relations
        )

    def extend(self, other: "ExtractionResult") -> None:
        for attr in ("entities", "relations", "fields", "links", "diagnostics", "aliases"):
            current = getattr(self, attr)
            for item in getattr(other, attr):
                if item not in current:
                    current.append(item)
        self.relation_metadata.update(other.relation_metadata)


def _parts(chunk: dict) -> tuple[str, str, str, str, str, dict]:
    metadata = chunk.get("metadata") or {}
    chunk_id = str(chunk.get("chunk_id") or metadata.get("chunk_id") or "")
    content = str(chunk.get("content") or "")
    source = str(metadata.get("source") or "")
    category = str(metadata.get("doc_category") or "")
    path = str(metadata.get("section_path") or "")
    return chunk_id, content, source, category, path, metadata


class SectionPathExtractor:
    def extract(self, chunk: dict) -> ExtractionResult:
        chunk_id, content, source, category, path, metadata = _parts(chunk)
        result = ExtractionResult()
        parts = [part.strip() for part in path.split(">") if part.strip()]
        evidence = path or content[:500]

        document_name = source.strip()
        section_name = make_section_entity_name(source, path) if source and path else ""
        if document_name:
            result.entities.append(EntityCandidate(document_name, "Document", category, source_chunk_id=chunk_id, evidence_text=evidence))
        if section_name:
            result.entities.append(EntityCandidate(section_name, "Section", category, {"section_path": path}, chunk_id, evidence))
            result.relations.append(RelationCandidate(document_name, "has_section", section_name, chunk_id, evidence))

        product = DOC_CATEGORY_TO_PRODUCT.get(category)
        if product:
            result.entities.append(EntityCandidate(product, "Product", category, source_chunk_id=chunk_id, evidence_text=evidence))

        owners: list[tuple[str, str]] = []
        for part in parts:
            if part in KNOWN_TOOL_NAMES:
                owners.append((part, "Tool"))
            elif part in KNOWN_SERVICE_NAMES:
                owners.append((part, "Service"))
        for name, entity_type in owners:
            result.entities.append(EntityCandidate(name, entity_type, category, source_chunk_id=chunk_id, evidence_text=evidence))
            if product:
                result.relations.append(RelationCandidate(name, "belongs_to", product, chunk_id, evidence))

        table_name = None
        for index, part in enumerate(parts[:-1]):
            if part in DATA_SPEC_KEYWORDS:
                for candidate in parts[index + 1 :]:
                    if candidate.endswith("表"):
                        table_name = candidate
                        break
                break
        if table_name and metadata.get("content_type") == "table":
            result.entities.append(EntityCandidate(table_name, "DataTable", category, source_chunk_id=chunk_id, evidence_text=evidence))
            if owners:
                result.relations.append(RelationCandidate(owners[-1][0], "has_table", table_name, chunk_id, evidence))

        business_names = [product] if product else []
        business_names += [name for name, _ in owners]
        if table_name:
            business_names.append(table_name)
        if document_name:
            result.links.append(ChunkLinkCandidate(document_name, chunk_id, section_path=path, source=source, evidence_text=evidence))
        if section_name:
            result.links.append(ChunkLinkCandidate(section_name, chunk_id, section_path=path, source=source, evidence_text=evidence))
        for name in business_names:
            if section_name:
                result.relations.append(RelationCandidate(name, "defined_in", section_name, chunk_id, evidence))
            result.links.append(ChunkLinkCandidate(name, chunk_id, section_path=path, source=source, evidence_text=evidence))
        return result


class TableFieldExtractor:
    HEADER_NAMES = {"字段名", "字段名称"}

    def extract(self, chunk: dict, context: ExtractionResult | None) -> ExtractionResult:
        chunk_id, content, _, _, _, metadata = _parts(chunk)
        result = ExtractionResult()
        if metadata.get("content_type") != "table":
            return result
        table = next((item for item in (context.entities if context else []) if item.entity_type == "DataTable"), None)
        if not table:
            result.diagnostics.append(ExtractionDiagnostic("missing_table_context", "表格 chunk 无法关联 DataTable", chunk_id))
            return result
        lines = [line.strip() for line in content.splitlines() if line.strip().startswith("|")]
        if len(lines) < 3:
            return result
        rows = list(csv.reader(io.StringIO("\n".join(line.strip("|") for line in lines)), delimiter="|"))
        headers = [cell.strip() for cell in rows[0]]
        field_index = next((i for i, name in enumerate(headers) if name in self.HEADER_NAMES), None)
        if field_index is None:
            return result
        indexes = {name: i for i, name in enumerate(headers)}
        for row in rows[2:]:
            row = [cell.strip() for cell in row]
            if field_index >= len(row) or not row[field_index]:
                continue
            description = self._value(row, indexes, "说明", "描述")
            required_text = self._value(row, indexes, "必填", "是否必填", "必要性")
            required = required_text in {"是", "必填", "必要", "Y", "yes", "true"} or "必要字段" in description or "必填" in description
            result.fields.append(FieldCandidate(
                table_name=table.name,
                field_name=row[field_index],
                description=description,
                required=required,
                unit=self._value(row, indexes, "单位"),
                value_range=self._value(row, indexes, "值域", "取值范围"),
                source_chunk_id=chunk_id,
            ))
        return result

    @staticmethod
    def _value(row: list[str], indexes: dict[str, int], *names: str) -> str:
        for name in names:
            index = indexes.get(name)
            if index is not None and index < len(row):
                return row[index]
        return ""


class ConfigBlockExtractor:
    CONFIG_LINE = re.compile(r"(?m)^\s*([A-Za-z][A-Za-z0-9_]*Config)\s+([^\s]+)\s*$")

    def extract(self, chunk: dict, context: ExtractionResult | None) -> ExtractionResult:
        chunk_id, content, _, category, path, metadata = _parts(chunk)
        result = ExtractionResult()
        is_config_section = any(word in path for word in ("服务配置", "配置", "Apache", "Nginx"))
        cleaned = content.replace("```apache", "").replace("```nginx", "").replace("```", "")
        matches = list(self.CONFIG_LINE.finditer(cleaned))
        if metadata.get("content_type") != "code" and not is_config_section and not matches:
            return result
        owner = next(
            (item for item in reversed(context.entities if context else []) if item.entity_type in {"Service", "Tool"}),
            None,
        )
        if matches and not owner:
            result.diagnostics.append(ExtractionDiagnostic("missing_config_owner", "配置项无法关联 Tool/Service", chunk_id))
            return result
        for match in matches:
            name, config_path = match.groups()
            evidence = match.group(0).strip()
            result.entities.append(EntityCandidate(name, "ConfigItem", category, {"path": config_path}, chunk_id, evidence))
            result.relations.append(RelationCandidate(owner.name, "uses_config", name, chunk_id, evidence))
            result.links.append(ChunkLinkCandidate(name, chunk_id, evidence_text=evidence))
        return result


__all__ = [
    "ChunkLinkCandidate", "ConfigBlockExtractor", "EntityCandidate",
    "ExtractionDiagnostic", "ExtractionResult", "FieldCandidate",
    "RelationCandidate", "SectionPathExtractor", "TableFieldExtractor",
]

from .pipeline import (
    BuildBatchResult,
    GraphBuilder,
    GraphCandidateApplier,
    GraphQualityService,
    GraphSpecialRuleRestorer,
    QualityReport,
)

__all__ += [
    "BuildBatchResult",
    "GraphBuilder",
    "GraphCandidateApplier",
    "GraphQualityService",
    "GraphSpecialRuleRestorer",
    "QualityReport",
]
