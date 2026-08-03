"""External domain seed catalog used by graph extraction."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


from typing import Any


_CATEGORY_TYPES = {
    "products": "Product",
    "tools": "Tool",
    "services": "Service",
    "environment_components": "EnvironmentComponent",
}


@dataclass(frozen=True)
class CatalogSeedEntity:
    name: str
    entity_type: str
    category: str
    aliases: list[str] = field(default_factory=list)
    belongs_to: str = ""
    different_from: list[str] = field(default_factory=list)


class DomainCatalogLoader:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else Path(__file__).resolve().parents[2] / "data" / "domain_catalog.json"
        if not self.path.exists():
            raise FileNotFoundError(f"domain catalog not found: {self.path}")
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid domain catalog JSON at {self.path}: {exc}") from exc
        self._entries: dict[str, tuple[str, str]] = {}
        self._categories: dict[str, str] = {}
        self._owners: dict[str, str] = {}
        self._seeds: list[CatalogSeedEntity] = []
        self._load(data)

    def _load(self, data: object) -> None:
        if not isinstance(data, dict):
            raise ValueError(f"invalid domain catalog structure at {self.path}: root must be an object")
        for category, entity_type in _CATEGORY_TYPES.items():
            entries = data.get(category)
            if not isinstance(entries, list):
                raise ValueError(f"invalid domain catalog structure at {self.path}: {category} must be a list")
            for item in entries:
                if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip():
                    raise ValueError(f"invalid domain catalog entry at {self.path}: {category}")
                name = item["name"].strip()
                owner = item.get("belongs_to")
                if owner is not None and (not isinstance(owner, str) or not owner.strip()):
                    raise ValueError(f"invalid belongs_to at {self.path}: {name}")
                aliases = item.get("aliases", [])
                if not isinstance(aliases, list) or not all(isinstance(alias, str) for alias in aliases):
                    raise ValueError(f"invalid aliases at {self.path}: {name}")
                if isinstance(owner, str):
                    self._owners[name] = owner.strip()
                different_from = item.get("different_from", [])
                if not isinstance(different_from, list) or not all(isinstance(value, str) for value in different_from):
                    raise ValueError(f"invalid different_from at {self.path}: {name}")
                self._add(name, name, entity_type)
                for alias in aliases:
                    self._add(alias, name, entity_type)
                for doc_category in item.get("doc_categories", []):
                    if isinstance(doc_category, str):
                        self._categories[doc_category] = name
                self._seeds.append(
                    CatalogSeedEntity(
                        name=name,
                        entity_type=entity_type,
                        category=category,
                        aliases=[alias.strip() for alias in aliases if isinstance(alias, str) and alias.strip()],
                        belongs_to=owner.strip() if isinstance(owner, str) else "",
                        different_from=[value.strip() for value in different_from if value.strip()],
                    )
                )

    def _add(self, key: str, canonical: str, entity_type: str) -> None:
        normalized = self.normalize_key(key)
        previous = self._entries.get(normalized)
        if previous and previous != (canonical, entity_type):
            raise ValueError(f"duplicate domain catalog alias at {self.path}: {key}")
        self._entries[normalized] = (canonical, entity_type)

    @staticmethod
    def normalize_key(value: str) -> str:
        return " ".join(value.strip().replace("（", "(").replace("）", ")").split()).casefold()

    def resolve(self, name: str) -> tuple[str, str] | None:
        return self._entries.get(self.normalize_key(name))

    def product_for_category(self, category: str) -> str | None:
        return self._categories.get(category)

    def owner_for(self, name: str) -> str | None:
        resolved = self.resolve(name)
        if not resolved:
            return None
        return self._owners.get(resolved[0])

    def entries(self, entity_type: str | None = None) -> list[tuple[str, str]]:
        values = set(self._entries.values())
        return sorted(value for value in values if entity_type is None or value[1] == entity_type)

    def seeds(self) -> list[CatalogSeedEntity]:
        return list(self._seeds)

    def related_entities_for(self, name: str, top_k: int = 6) -> list[dict[str, Any]]:
        """Return multi-source scored related entities with structural decision reasons."""
        raw_name = (name or "").strip()
        if not raw_name:
            return []

        resolved = self.resolve(raw_name)
        canonical = resolved[0] if resolved else raw_name
        norm_input = self.normalize_key(raw_name)

        seed_map = {s.name.casefold(): s for s in self._seeds}
        target_seed = seed_map.get(canonical.casefold())

        explicit_set = set()
        if target_seed and target_seed.different_from:
            explicit_set = {v.casefold() for v in target_seed.different_from}

        scored: list[dict[str, Any]] = []

        for seed in self._seeds:
            cand_name = seed.name
            cand_norm = self.normalize_key(cand_name)
            if cand_norm == self.normalize_key(canonical):
                continue

            score = 0.0
            reasons: list[str] = []

            # 1. Explicit hand-crafted relation (1.0)
            if cand_norm in explicit_set:
                score += 1.0
                reasons.append("explicit_different_from")

            # 2. Name / Prefix similarity (0.7 max)
            aliases_norm = [self.normalize_key(a) for a in seed.aliases]
            all_keys = [cand_norm] + aliases_norm

            sim_val = 0.0
            for k in all_keys:
                if norm_input and (norm_input == k or norm_input in k or k in norm_input):
                    match_ratio = len(norm_input) / max(len(k), 1) if norm_input in k else len(k) / max(len(norm_input), 1)
                    sim_val = max(sim_val, 0.5 + 0.5 * match_ratio)
            if sim_val > 0.0:
                score += 0.7 * sim_val
                reasons.append("name_similarity")

            # 3. BelongsTo topology match (0.35)
            if target_seed and target_seed.belongs_to and seed.belongs_to:
                if target_seed.belongs_to.casefold() == seed.belongs_to.casefold():
                    score += 0.35
                    reasons.append("belongs_to_match")

            # 4. Category mismatch penalty (-0.15)
            if target_seed and target_seed.category and seed.category:
                if (
                    target_seed.category != seed.category
                    and target_seed.category in {"products", "tools", "services"}
                    and seed.category in {"environment_components"}
                ):
                    score -= 0.15
                    reasons.append("category_mismatch_penalty")

            if score > 0.1:
                scored.append({
                    "name": cand_name,
                    "score": round(score, 3),
                    "reasons": reasons,
                })

        scored.sort(key=lambda x: (-x["score"], x["name"]))
        return scored[:top_k]
