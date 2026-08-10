"""Extraction coverage contract: catalog tools must accrue domain leaves, not just exist.

Success is measured against ``domain_catalog`` ownership, not candidate counts.
Architecture layers / document Sections are out of scope here.

Two tiers for top-level tools:
- ``structure_ok``: chapter scaffold (FunctionArea) present
- ``domain_ok``: navigational domain skeleton (Procedure / Feature / Table / Format / …)

GUI ConfigItem and fine-grained Step do **not** count for ``domain_ok`` — those belong
in evidence chunks, not as graph coverage credit.

``covered`` / ``--strict`` / quality gaps use ``domain_ok``.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from rag_knowledge.repository.relational_db import RelationalDB
from rag_knowledge.services.domain_catalog import DomainCatalogLoader

# Chapter-scaffold leaves from section_path rules (not domain facts).
STRUCTURE_LEAF_TYPES: frozenset[str] = frozenset({"FunctionArea"})

# Navigational domain leaves that justify graph storage (not GUI labels).
DOMAIN_LEAF_TYPES: frozenset[str] = frozenset(
    {
        "Feature",
        "DataTable",
        "Field",
        "Procedure",
        "Format",
        "Command",
        "Error",
        "Solution",
    }
)

# Tracked in samples but excluded from domain_ok gate.
AUX_LEAF_TYPES: frozenset[str] = frozenset(
    {
        "ConfigItem",
        "Step",
        "Constraint",
    }
)

# Union for neighborhood sampling / leaf_type_counts.
EXTRACTION_LEAF_TYPES: frozenset[str] = STRUCTURE_LEAF_TYPES | DOMAIN_LEAF_TYPES | AUX_LEAF_TYPES

_EXTRACTION_CREATED_PREFIXES = ("rule:", "llm:")


def _is_extraction_created(created_by: str | None) -> bool:
    value = str(created_by or "")
    return value.startswith(_EXTRACTION_CREATED_PREFIXES)


@dataclass
class ToolCoverageRow:
    tool: str
    owner: str
    top_level: bool
    tool_present: bool
    extraction_leaf_count: int = 0
    structure_leaf_count: int = 0
    domain_leaf_count: int = 0
    leaf_type_counts: dict[str, int] = field(default_factory=dict)
    sample_leaves: list[str] = field(default_factory=list)

    @property
    def structure_ok(self) -> bool:
        if not self.tool_present:
            return False
        if not self.top_level:
            return True
        return self.structure_leaf_count > 0 or self.domain_leaf_count > 0

    @property
    def domain_ok(self) -> bool:
        if not self.tool_present:
            return False
        if not self.top_level:
            # Subtools inherit coverage expectation from parent; presence is enough.
            return True
        return self.domain_leaf_count > 0

    @property
    def covered(self) -> bool:
        """Strict gate alias: domain knowledge, not scaffold alone."""
        return self.domain_ok

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "owner": self.owner,
            "top_level": self.top_level,
            "tool_present": self.tool_present,
            "extraction_leaf_count": self.extraction_leaf_count,
            "structure_leaf_count": self.structure_leaf_count,
            "domain_leaf_count": self.domain_leaf_count,
            "leaf_type_counts": dict(self.leaf_type_counts),
            "sample_leaves": list(self.sample_leaves),
            "structure_ok": self.structure_ok,
            "domain_ok": self.domain_ok,
            "covered": self.covered,
        }


@dataclass
class ProductCoverageReport:
    product: str
    tools: list[ToolCoverageRow] = field(default_factory=list)
    skipped_catalog_tools: list[str] = field(default_factory=list)

    @property
    def uncovered_top_level(self) -> list[ToolCoverageRow]:
        """Domain gaps (strict gate)."""
        return [row for row in self.tools if row.top_level and not row.domain_ok]

    @property
    def structure_uncovered_top_level(self) -> list[ToolCoverageRow]:
        return [row for row in self.tools if row.top_level and not row.structure_ok]

    @property
    def missing_tools(self) -> list[ToolCoverageRow]:
        return [row for row in self.tools if not row.tool_present]

    def as_dict(self) -> dict[str, Any]:
        top = [row for row in self.tools if row.top_level]
        structure_covered = [row for row in top if row.structure_ok]
        domain_covered = [row for row in top if row.domain_ok]
        return {
            "product": self.product,
            "top_level_total": len(top),
            "top_level_structure_covered": len(structure_covered),
            "top_level_structure_uncovered": len(top) - len(structure_covered),
            "top_level_domain_covered": len(domain_covered),
            "top_level_domain_uncovered": len(top) - len(domain_covered),
            # Back-compat aliases = domain tier (strict gate).
            "top_level_covered": len(domain_covered),
            "top_level_uncovered": len(top) - len(domain_covered),
            "missing_tools": len(self.missing_tools),
            "structure_uncovered_tools": [row.tool for row in self.structure_uncovered_top_level],
            "domain_uncovered_tools": [row.tool for row in self.uncovered_top_level],
            "uncovered_tools": [row.tool for row in self.uncovered_top_level],
            "skipped_catalog_tools": list(self.skipped_catalog_tools),
            "tools": [row.as_dict() for row in self.tools],
        }


class ExtractionCoverageService:
    """Evaluate catalog Tool coverage against formal-graph extraction leaves."""

    def __init__(
        self,
        db: RelationalDB | None = None,
        catalog: DomainCatalogLoader | None = None,
    ):
        # Prefer explicit db; allow catalog-only construction without opening live DB.
        self._db = db
        self.catalog = catalog or DomainCatalogLoader()

    @property
    def db(self) -> RelationalDB:
        if self._db is None:
            self._db = RelationalDB()
        return self._db

    def catalog_tools_for_product(self, product: str) -> list[tuple[str, str, bool]]:
        """Return (tool, owner, top_level) for tools under product (direct or via parent tool)."""
        product = (product or "").strip()
        owners = {
            seed.name: (seed.belongs_to or "").strip()
            for seed in self.catalog.seeds()
            if seed.entity_type == "Tool" and (seed.belongs_to or "").strip()
        }

        def under_product(tool: str) -> bool:
            seen: set[str] = set()
            current = tool
            while current and current not in seen:
                seen.add(current)
                parent = owners.get(current)
                if not parent:
                    return False
                if parent == product:
                    return True
                if parent not in owners:
                    return parent == product
                current = parent
            return False

        rows: list[tuple[str, str, bool]] = []
        for tool, owner in sorted(owners.items()):
            if not under_product(tool):
                continue
            rows.append((tool, owner, owner == product))
        return rows

    def inspect_product(self, product: str) -> ProductCoverageReport:
        product = (product or "").strip()
        report = ProductCoverageReport(product=product)
        graph = self._load_graph()
        for tool, owner, top_level in self.catalog_tools_for_product(product):
            meta = graph["entities"].get(tool)
            if meta and meta.get("entity_type") != "Tool":
                report.skipped_catalog_tools.append(
                    f"{tool}:live_type={meta.get('entity_type')}"
                )
                continue
            present = bool(meta) and meta.get("entity_type") == "Tool"
            leaves = self._extraction_leaves_for_tool(tool, graph) if present else {}
            structure_count = sum(
                len(names) for etype, names in leaves.items() if etype in STRUCTURE_LEAF_TYPES
            )
            domain_count = sum(
                len(names) for etype, names in leaves.items() if etype in DOMAIN_LEAF_TYPES
            )
            sample: list[str] = []
            for leaf_type, names in sorted(leaves.items()):
                for name in sorted(names):
                    if len(sample) < 8:
                        sample.append(f"{leaf_type}:{name}")
            report.tools.append(
                ToolCoverageRow(
                    tool=tool,
                    owner=owner,
                    top_level=top_level,
                    tool_present=present,
                    extraction_leaf_count=structure_count + domain_count,
                    structure_leaf_count=structure_count,
                    domain_leaf_count=domain_count,
                    leaf_type_counts={k: len(v) for k, v in sorted(leaves.items())},
                    sample_leaves=sample,
                )
            )
        return report

    def inspect_products(self, products: Iterable[str] | None = None) -> list[ProductCoverageReport]:
        if products is None:
            products = sorted(
                {
                    seed.name
                    for seed in self.catalog.seeds()
                    if seed.entity_type == "Product"
                }
            )
        return [self.inspect_product(name) for name in products]

    def _load_graph(self) -> dict[str, Any]:
        entities: dict[str, dict[str, Any]] = {}
        inbound_belongs: dict[str, list[str]] = defaultdict(list)
        outbound: dict[str, list[tuple[str, str]]] = defaultdict(list)
        with self.db._get_conn() as conn:
            for row in conn.execute(
                "SELECT id, name, entity_type, created_by FROM entities WHERE review_status = 'approved'"
            ):
                entities[str(row["name"])] = {
                    "id": row["id"],
                    "name": row["name"],
                    "entity_type": row["entity_type"],
                    "created_by": row["created_by"] or "",
                }
            for row in conn.execute(
                """
                SELECT s.name AS source_name, t.name AS target_name, r.relation_type
                FROM relations r
                JOIN entities s ON s.id = r.source_entity_id
                JOIN entities t ON t.id = r.target_entity_id
                WHERE r.review_status = 'approved'
                """
            ):
                src, dst, rel = row["source_name"], row["target_name"], row["relation_type"]
                outbound[src].append((rel, dst))
                if rel == "belongs_to":
                    inbound_belongs[dst].append(src)
        return {
            "entities": entities,
            "inbound_belongs": inbound_belongs,
            "outbound": outbound,
        }

    def _extraction_leaves_for_tool(
        self,
        tool: str,
        graph: dict[str, Any],
    ) -> dict[str, set[str]]:
        entities = graph["entities"]
        inbound_belongs = graph["inbound_belongs"]
        outbound = graph["outbound"]

        # Neighborhood around tool (inbound belongs_to + outbound has_*), depth 3
        # so FunctionArea -> DataTable -> Field is included.
        # Do not walk into child Tools — subtool leaves are scored on the subtool row.
        frontier = {tool}
        seen = {tool}
        for _ in range(3):
            nxt: set[str] = set()
            for node in frontier:
                for child in inbound_belongs.get(node, []):
                    if child in seen:
                        continue
                    child_meta = entities.get(child) or {}
                    if child_meta.get("entity_type") == "Tool":
                        continue
                    nxt.add(child)
                    seen.add(child)
                for rel, target in outbound.get(node, []):
                    if rel.startswith("has_") or rel in {
                        "uses_config",
                        "supports_format",
                        "causes",
                        "solved_by",
                    }:
                        if target not in seen:
                            nxt.add(target)
                            seen.add(target)
            frontier = nxt
            if not frontier:
                break

        leaves: dict[str, set[str]] = defaultdict(set)
        for name in seen:
            if name == tool:
                continue
            meta = entities.get(name)
            if not meta:
                continue
            if meta["entity_type"] not in EXTRACTION_LEAF_TYPES:
                continue
            if not _is_extraction_created(meta["created_by"]):
                continue
            leaves[meta["entity_type"]].add(name)
        return leaves
