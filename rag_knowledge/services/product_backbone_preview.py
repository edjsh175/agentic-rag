"""Read-only product backbone preview graph built from a JSON seed."""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
from pathlib import Path
from typing import Any

from rag_knowledge.models.api import GraphDataResponse, GraphEdge, GraphNode

PREVIEW_CREATED_BY = "preview:product_backbone"


class ProductBackbonePreviewService:
    def __init__(self, path: str | Path | None = None):
        root = Path(__file__).resolve().parents[2]
        self.path = Path(path) if path else root / "data" / "product_relation_backbone_preview.json"

    def list_graph_data(self) -> GraphDataResponse:
        data = self._load()
        return self._to_graph_data(data)

    def create_entity(self, payload: dict[str, Any]) -> GraphNode:
        data = self._load()
        entities = data["entities"]
        name = self._required_text(payload.get("name"), "entity name")
        if any(self._name(item) == name for item in entities):
            raise ValueError(f"duplicate product backbone entity: {name}")

        item = self._entity_payload(payload, name=name)
        entities.append(item)
        self._write(data)
        return self._node(item)

    def update_entity(self, entity_id: str, payload: dict[str, Any]) -> GraphNode:
        data = self._load()
        entities = data["entities"]
        relations = data["relations"]
        index = self._find_entity_index_by_id(entities, entity_id)
        item = entities[index]
        old_name = self._name(item)
        next_name = self._required_text(payload.get("name", old_name), "entity name")

        for pos, existing in enumerate(entities):
            if pos != index and self._name(existing) == next_name:
                raise ValueError(f"duplicate product backbone entity: {next_name}")

        item.update(self._entity_payload(payload, name=next_name, current=item))
        if next_name != old_name:
            for relation in relations:
                if self._text(relation.get("source")) == old_name:
                    relation["source"] = next_name
                if self._text(relation.get("target")) == old_name:
                    relation["target"] = next_name
        self._write(data)
        return self._node(item)

    def delete_entity(self, entity_id: str) -> bool:
        data = self._load()
        entities = data["entities"]
        index = self._find_entity_index_by_id(entities, entity_id)
        name = self._name(entities[index])
        del entities[index]
        data["relations"] = [
            relation for relation in data["relations"]
            if self._text(relation.get("source")) != name and self._text(relation.get("target")) != name
        ]
        self._write(data)
        return True

    def create_relation(self, payload: dict[str, Any]) -> GraphEdge:
        data = self._load()
        entities = data["entities"]
        source = self._entity_name_by_id(entities, self._required_text(payload.get("source_id"), "source id"))
        target = self._entity_name_by_id(entities, self._required_text(payload.get("target_id"), "target id"))
        relation_type = self._required_text(payload.get("relation_type"), "relation type")
        if source == target:
            raise ValueError("self-loop product backbone relations are not allowed")

        for relation in data["relations"]:
            if (
                self._text(relation.get("source")) == source
                and self._text(relation.get("target")) == target
                and self._text(relation.get("relation_type")) == relation_type
            ):
                return self._edge(relation)

        item = {"source": source, "relation_type": relation_type, "target": target}
        note = self._text(payload.get("evidence_text") or payload.get("note"))
        if note:
            item["note"] = note
        data["relations"].append(item)
        self._write(data)
        return self._edge(item)

    def delete_relation(self, relation_id: str) -> bool:
        data = self._load()
        relations = data["relations"]
        next_relations = [
            item for item in relations
            if self._relation_id(
                self._text(item.get("source")),
                self._text(item.get("relation_type")),
                self._text(item.get("target")),
            ) != relation_id
        ]
        if len(next_relations) == len(relations):
            raise KeyError(f"product backbone relation not found: {relation_id}")
        data["relations"] = next_relations
        self._write(data)
        return True

    def _to_graph_data(self, data: dict) -> GraphDataResponse:
        entities = data["entities"]
        relations = data["relations"]
        entity_names = [str(item.get("name") or "").strip() for item in entities]
        duplicates = sorted({name for name in entity_names if name and entity_names.count(name) > 1})
        if duplicates:
            raise ValueError(f"duplicate product backbone entities: {duplicates}")

        entity_by_name = {name: item for name, item in zip(entity_names, entities) if name}
        missing_names = [item for item in entities if not str(item.get("name") or "").strip()]
        if missing_names:
            raise ValueError("product backbone entity name cannot be empty")

        nodes = [self._node(item) for item in entities]
        edges = []
        for item in relations:
            source = str(item.get("source") or "").strip()
            target = str(item.get("target") or "").strip()
            relation_type = str(item.get("relation_type") or "").strip()
            if not source or not target or not relation_type:
                raise ValueError(f"incomplete product backbone relation: {item}")
            if source not in entity_by_name or target not in entity_by_name:
                raise ValueError(f"missing product backbone relation endpoint: {source} --{relation_type}--> {target}")
            edges.append(self._edge(item))

        return GraphDataResponse(nodes=nodes, edges=edges)

    def _load(self) -> dict:
        if not self.path.exists():
            raise FileNotFoundError(f"product backbone preview not found: {self.path}")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if int(data.get("schema_version") or 0) != 1:
            raise ValueError("product backbone preview schema_version must be 1")
        if not isinstance(data.get("entities"), list) or not isinstance(data.get("relations"), list):
            raise ValueError("product backbone preview entities/relations must be lists")
        return data

    def _write(self, data: dict) -> None:
        self._to_graph_data(data)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=self.path.parent, suffix=".tmp") as tmp:
            tmp.write(payload)
            tmp.write("\n")
            tmp_path = Path(tmp.name)
        tmp_path.replace(self.path)

    def _entity_payload(self, payload: dict[str, Any], name: str, current: dict | None = None) -> dict:
        current = current or {}
        item = {
            "name": name,
            "graph_type": self._text(payload.get("graph_type", current.get("graph_type") or "Module")) or "Module",
            "layer": self._text(payload.get("layer", current.get("layer"))),
            "subtype": self._text(payload.get("subtype", current.get("subtype"))),
            "source": self._text(payload.get("source", current.get("source"))),
            "status": self._text(payload.get("status", current.get("status"))),
            "description": self._text(payload.get("description", current.get("description"))),
        }
        aliases = payload.get("alias_candidates", current.get("alias_candidates") or [])
        if isinstance(aliases, str):
            aliases = [part.strip() for part in re.split(r"[,，\n]", aliases) if part.strip()]
        if aliases:
            item["alias_candidates"] = [str(alias).strip() for alias in aliases if str(alias).strip()]
        return item

    def _find_entity_index_by_id(self, entities: list[dict], entity_id: str) -> int:
        for index, item in enumerate(entities):
            if self._entity_id(self._name(item)) == entity_id:
                return index
        raise KeyError(f"product backbone entity not found: {entity_id}")

    def _entity_name_by_id(self, entities: list[dict], entity_id: str) -> str:
        return self._name(entities[self._find_entity_index_by_id(entities, entity_id)])

    @staticmethod
    def _text(value: Any) -> str:
        return str(value or "").strip()

    @classmethod
    def _required_text(cls, value: Any, label: str) -> str:
        text = cls._text(value)
        if not text:
            raise ValueError(f"product backbone {label} cannot be empty")
        return text

    @classmethod
    def _name(cls, item: dict) -> str:
        return cls._required_text(item.get("name"), "entity name")

    def _node(self, item: dict) -> GraphNode:
        name = str(item["name"]).strip()
        properties = {
            "layer": item.get("layer") or "",
            "subtype": item.get("subtype") or "",
            "source": item.get("source") or "",
            "status": item.get("status") or "",
            "alias_candidates": item.get("alias_candidates") or [],
            "created_by": PREVIEW_CREATED_BY,
        }
        return GraphNode(
            id=self._entity_id(name),
            label=name,
            type=str(item.get("graph_type") or "Module"),
            doc_category=str(item.get("layer") or "") or None,
            canonical_name=name,
            description=str(item.get("description") or "") or None,
            properties_json=json.dumps(properties, ensure_ascii=False, sort_keys=True),
            confidence=1.0,
            review_status="pending",
            created_by=PREVIEW_CREATED_BY,
        )

    def _edge(self, item: dict) -> GraphEdge:
        source = str(item["source"]).strip()
        target = str(item["target"]).strip()
        relation_type = str(item["relation_type"]).strip()
        evidence = str(item.get("note") or "product_backbone_preview").strip()
        return GraphEdge(
            id=self._relation_id(source, relation_type, target),
            source=self._entity_id(source),
            target=self._entity_id(target),
            label=relation_type,
            confidence=1.0,
            review_status="pending",
            evidence_text=evidence,
        )

    @staticmethod
    def _entity_id(name: str) -> str:
        normalized = re.sub(r"\s+", "-", name.strip().lower())
        normalized = re.sub(r"[^0-9a-zA-Z_\-\u4e00-\u9fff.]+", "-", normalized).strip("-")
        digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
        return f"product-backbone:{normalized}:{digest}"

    @classmethod
    def _relation_id(cls, source: str, relation_type: str, target: str) -> str:
        payload = f"{source}\0{relation_type}\0{target}"
        digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
        return f"product-backbone:rel:{digest}"


class ProductBackboneComplexPreviewService(ProductBackbonePreviewService):
    def __init__(self):
        root = Path(__file__).resolve().parents[2]
        path = root / "data" / "archive" / "backups" / "product_relation_backbone_preview.2026-07-21.complex-detail.json"
        super().__init__(path=path)

