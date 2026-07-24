"""Anchor-constrained chunk filter for backbone-aligned retrieval evidence."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Sequence

from langchain_core.documents import Document

from rag_knowledge.services.backbone_guard import load_backbone_constraints, resolve_canonical

logger = logging.getLogger(__name__)


def aliases_for_canonical(canonical: str, constraints: dict) -> list[str]:
    """Return canonical name plus all aliases that map to it."""
    name = resolve_canonical(canonical, constraints)
    if not name:
        return []
    out = [name]
    seen = {name.casefold()}
    for alias, target in (constraints.get("canonical_by_alias") or {}).items():
        if resolve_canonical(str(target), constraints) != name:
            continue
        key = str(alias or "").strip()
        if not key or key.casefold() in seen:
            continue
        seen.add(key.casefold())
        out.append(key)
    return out


def resolve_product_line(canonical: str, constraints: dict | None = None) -> str:
    """Walk belongs_to ancestors until a Product entity is found."""
    constraints = constraints if constraints is not None else load_backbone_constraints()
    types = constraints.get("entity_type_by_name") or {}
    belongs_to = constraints.get("belongs_to") or {}
    current = resolve_canonical(canonical, constraints)
    if not current:
        return ""
    queue = [current]
    seen: set[str] = set()
    while queue:
        node = queue.pop(0)
        if not node or node in seen:
            continue
        seen.add(node)
        if str(types.get(node) or "") == "Product":
            return node
        parents = sorted(belongs_to.get(node) or ())
        queue.extend(parents)
    return ""


def product_line_markers(product_name: str, constraints: dict) -> set[str]:
    """Casefolded markers for product doc_category / aliases / canonical name."""
    if not product_name:
        return set()
    markers: set[str] = set()
    for alias in aliases_for_canonical(product_name, constraints):
        markers.add(alias.casefold())
    doc_cats = constraints.get("doc_category_by_name") or {}
    dc = str(doc_cats.get(product_name) or "").strip()
    if dc:
        markers.add(dc.casefold())
    return markers


def foreign_product_markers(product_name: str, constraints: dict) -> set[str]:
    """Markers for other Product entities (used to detect cross-product interference)."""
    types = constraints.get("entity_type_by_name") or {}
    foreign: set[str] = set()
    for name, etype in types.items():
        if str(etype or "") != "Product":
            continue
        if resolve_canonical(str(name), constraints) == product_name:
            continue
        foreign |= product_line_markers(str(name), constraints)
    # Avoid overly short tokens that collide with unrelated text.
    return {m for m in foreign if len(m) >= 4}


def _meta_blob(doc: Document) -> tuple[str, str, str, str]:
    meta = doc.metadata or {}
    section_path = str(meta.get("section_path") or "")
    section_title = str(meta.get("section_title") or "")
    doc_category = str(meta.get("doc_category") or "")
    source = str(meta.get("source") or meta.get("file_name") or "")
    return section_path, section_title, doc_category, source


def chunk_matches_anchor(
    doc: Document,
    *,
    canonicals: Sequence[str],
    constraints: dict,
) -> bool:
    """True when section_path hits a canonical or source/doc_category aligns to product line."""
    section_path, section_title, doc_category, source = _meta_blob(doc)
    section_fold = f"{section_path} {section_title}".casefold()
    source_name = Path(source).name.casefold() if source else ""
    doc_cat_fold = doc_category.casefold()

    for raw in canonicals:
        canonical = resolve_canonical(raw, constraints)
        if not canonical:
            continue
        for alias in aliases_for_canonical(canonical, constraints):
            if alias.casefold() in section_fold:
                return True
        product = resolve_product_line(canonical, constraints)
        markers = product_line_markers(product, constraints)
        if doc_cat_fold and doc_cat_fold in markers:
            return True
        if source_name and any(m in source_name for m in markers):
            return True
    return False


def chunk_is_foreign_interference(
    doc: Document,
    *,
    canonicals: Sequence[str],
    constraints: dict,
) -> bool:
    """True when chunk clearly belongs to another product line / service chapter."""
    if chunk_matches_anchor(doc, canonicals=canonicals, constraints=constraints):
        return False
    section_path, section_title, doc_category, source = _meta_blob(doc)
    section_fold = f"{section_path} {section_title}".casefold()
    source_name = Path(source).name.casefold() if source else ""
    doc_cat_fold = doc_category.casefold()

    products = {
        resolve_product_line(c, constraints)
        for c in canonicals
        if resolve_canonical(c, constraints)
    }
    products.discard("")
    foreign: set[str] = set()
    for product in products:
        foreign |= foreign_product_markers(product, constraints)
    if not foreign:
        return False
    if doc_cat_fold and doc_cat_fold in foreign:
        return True
    if source_name and any(m in source_name for m in foreign):
        return True
    # Cross-product service chapters that share surface aliases but not the tool canonical.
    if "管线更新服务".casefold() in section_fold:
        return True
    return False


def filter_docs_by_backbone_anchor(
    docs: list[Document],
    backbone_canonical: Iterable[str] | None,
    *,
    enabled: bool,
    constraints: dict | None = None,
) -> list[Document]:
    """Keep anchor-aligned chunks; drop clear foreign interference; empty → fallback."""
    if not enabled or not docs:
        return docs
    canonicals = [str(c).strip() for c in (backbone_canonical or ()) if str(c).strip()]
    if not canonicals:
        return docs

    constraints = constraints if constraints is not None else load_backbone_constraints()
    preferred = [
        doc
        for doc in docs
        if chunk_matches_anchor(doc, canonicals=canonicals, constraints=constraints)
    ]
    if preferred:
        if len(preferred) < len(docs):
            logger.info(
                "anchor_chunk_filter kept %d/%d docs | canonicals=%s",
                len(preferred),
                len(docs),
                canonicals,
            )
        return preferred

    cleaned = [
        doc
        for doc in docs
        if not chunk_is_foreign_interference(
            doc, canonicals=canonicals, constraints=constraints
        )
    ]
    if cleaned:
        if len(cleaned) < len(docs):
            logger.info(
                "anchor_chunk_filter demoted foreign interference %d→%d | canonicals=%s",
                len(docs),
                len(cleaned),
                canonicals,
            )
        return cleaned

    logger.warning(
        "anchor_chunk_filter empty after filter; fallback to unfiltered | "
        "canonicals=%s before=%d",
        canonicals,
        len(docs),
    )
    return docs
