"""Product-ownership rules: catalog is the sole authority for Tool/Service parents.

Architecture layers (``工具与数据处理层`` etc.) are taxonomy facets already stored on
entities as ``layer`` / ``doc_category``. They must not compete with Product parents
on ``belongs_to``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from rag_knowledge.services.domain_catalog import DomainCatalogLoader

# Official five-layer + support layers from the product entity hierarchy list.
ARCHITECTURE_LAYER_NAMES: frozenset[str] = frozenset(
    {
        "工具与数据处理层",
        "系统数据与存储层",
        "系统服务层",
        "客户端与渲染层",
        "业务应用层",
        "二次开发与集成层",
        "运维管理层",
        "运行与部署基础设施层",
        "标准规范体系",
        "安全保障体系",
    }
)


def is_architecture_layer_name(name: str) -> bool:
    return str(name or "").strip() in ARCHITECTURE_LAYER_NAMES


@dataclass
class OwnershipRepairReport:
    dropped_layer_edges: list[tuple[str, str]] = field(default_factory=list)
    ensured_owner_edges: list[tuple[str, str]] = field(default_factory=list)
    skipped_catalog_entries: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dropped_layer_edges": [
                {"source": s, "target": t} for s, t in self.dropped_layer_edges
            ],
            "ensured_owner_edges": [
                {"source": s, "target": t} for s, t in self.ensured_owner_edges
            ],
            "skipped_catalog_entries": list(self.skipped_catalog_entries),
            "dropped_count": len(self.dropped_layer_edges),
            "ensured_count": len(self.ensured_owner_edges),
        }

    @property
    def dropped_count(self) -> int:
        return len(self.dropped_layer_edges)

    @property
    def ensured_count(self) -> int:
        return len(self.ensured_owner_edges)


@dataclass
class OwnershipGap:
    child: str
    expected_parent: str
    actual_parents: list[str]
    reason: str

    def code(self) -> str:
        return f"missing_catalog_owner:{self.child}:{self.expected_parent}"


def catalog_ownership_expectations(
    catalog: DomainCatalogLoader | None = None,
) -> dict[str, str]:
    """Return child -> required direct belongs_to parent from domain_catalog."""
    loader = catalog or DomainCatalogLoader()
    expectations: dict[str, str] = {}
    for seed in loader.seeds():
        if seed.entity_type not in {"Tool", "Service"}:
            continue
        owner = (seed.belongs_to or "").strip()
        if not owner:
            continue
        # PipelineWebGL etc. may be modeled as Product in backbone; still record expectation
        # and let callers skip when live type mismatches.
        expectations[seed.name] = owner
    return expectations


def repair_backbone_payload(
    payload: dict[str, Any],
    *,
    catalog: DomainCatalogLoader | None = None,
) -> tuple[dict[str, Any], OwnershipRepairReport]:
    """Drop architecture-layer belongs_to edges; ensure catalog owner edges exist."""
    report = OwnershipRepairReport()
    expectations = catalog_ownership_expectations(catalog)
    entities = [e for e in (payload.get("entities") or []) if isinstance(e, dict)]
    type_by_name = {
        str(e.get("name") or "").strip(): str(e.get("entity_type") or "").strip()
        for e in entities
        if str(e.get("name") or "").strip()
    }

    relations_in = [r for r in (payload.get("relations") or []) if isinstance(r, dict)]
    kept: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for item in relations_in:
        source = str(item.get("source") or "").strip()
        target = str(item.get("target") or "").strip()
        relation_type = str(item.get("relation_type") or "").strip()
        if not source or not target or not relation_type:
            continue
        source_type = type_by_name.get(source, "")
        if (
            relation_type == "belongs_to"
            and is_architecture_layer_name(target)
            and source_type in {"Tool", "Service", "Product"}
        ):
            report.dropped_layer_edges.append((source, target))
            continue
        key = (source, relation_type, target)
        if key in seen:
            continue
        seen.add(key)
        kept.append(dict(item))

    for child, owner in sorted(expectations.items()):
        child_type = type_by_name.get(child, "")
        owner_type = type_by_name.get(owner, "")
        if child not in type_by_name:
            report.skipped_catalog_entries.append(f"missing_child:{child}")
            continue
        if owner not in type_by_name:
            report.skipped_catalog_entries.append(f"missing_owner:{child}->{owner}")
            continue
        # Only enforce when both ends are composition types already in the payload.
        if child_type not in {"Tool", "Service"}:
            report.skipped_catalog_entries.append(f"type_mismatch_child:{child}:{child_type}")
            continue
        if owner_type not in {"Product", "Tool", "Service"}:
            report.skipped_catalog_entries.append(f"type_mismatch_owner:{child}->{owner}:{owner_type}")
            continue
        key = (child, "belongs_to", owner)
        if key in seen:
            continue
        seen.add(key)
        kept.append(
            {
                "source": child,
                "relation_type": "belongs_to",
                "target": owner,
                "note": "ownership:catalog",
            }
        )
        report.ensured_owner_edges.append((child, owner))

    out = dict(payload)
    out["relations"] = kept
    return out, report


def find_ownership_gaps(
    *,
    entity_types: dict[str, str],
    belongs_to_parents: dict[str, list[str]],
    catalog: DomainCatalogLoader | None = None,
) -> list[OwnershipGap]:
    """Gaps where a catalog Tool/Service exists but lacks the required owner edge."""
    gaps: list[OwnershipGap] = []
    for child, owner in sorted(catalog_ownership_expectations(catalog).items()):
        child_type = entity_types.get(child)
        if child_type not in {"Tool", "Service"}:
            continue
        if owner not in entity_types:
            gaps.append(
                OwnershipGap(
                    child=child,
                    expected_parent=owner,
                    actual_parents=list(belongs_to_parents.get(child) or []),
                    reason="owner_entity_missing",
                )
            )
            continue
        parents = [p for p in (belongs_to_parents.get(child) or []) if p]
        layer_parents = [p for p in parents if is_architecture_layer_name(p)]
        if layer_parents:
            gaps.append(
                OwnershipGap(
                    child=child,
                    expected_parent=owner,
                    actual_parents=parents,
                    reason="architecture_layer_parent",
                )
            )
        if owner not in parents:
            gaps.append(
                OwnershipGap(
                    child=child,
                    expected_parent=owner,
                    actual_parents=parents,
                    reason="missing_owner_edge",
                )
            )
    return gaps


def belongs_to_parents_from_relations(
    relations: Iterable[dict[str, Any]],
    *,
    source_key: str = "source",
    target_key: str = "target",
    relation_key: str = "relation_type",
) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for row in relations:
        if str(row.get(relation_key) or "") != "belongs_to":
            continue
        source = str(row.get(source_key) or "").strip()
        target = str(row.get(target_key) or "").strip()
        if not source or not target:
            continue
        out.setdefault(source, []).append(target)
    return out
