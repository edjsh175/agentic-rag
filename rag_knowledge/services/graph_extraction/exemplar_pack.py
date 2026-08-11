"""Load curated extraction exemplar packs for LLM few-shot injection.

Strategy B: always inject universal pattern pack for all doc_categories;
optionally append a category-specific pack (e.g. StampTools) within budget.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DEFAULT_MAX_CHARS = 3200
UNIVERSAL_BUDGET = 2400
CATEGORY_BUDGET = 800
_NONE = "(none)"

UNIVERSAL_PACK = "pattern_universal_v1.json"

# doc_category -> optional extra pack under data/extraction_exemplars/
_CATEGORY_PACKS: dict[str, str] = {
    "StampTools": "stamptools_v1.json",
}


def exemplar_root() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "extraction_exemplars"


@lru_cache(maxsize=16)
def _load_pack_file(path_str: str) -> dict:
    path = Path(path_str)
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"exemplar pack root must be object: {path}")
    exemplars = data.get("exemplars")
    if exemplars is not None and not isinstance(exemplars, list):
        raise ValueError(f"exemplar pack exemplars must be a list: {path}")
    return data


def pack_path_for_category(doc_category: str) -> Path | None:
    name = _CATEGORY_PACKS.get(str(doc_category or "").strip())
    if not name:
        return None
    path = exemplar_root() / name
    return path if path.is_file() else None


def universal_pack_path() -> Path | None:
    path = exemplar_root() / UNIVERSAL_PACK
    return path if path.is_file() else None


def load_pack(doc_category: str) -> dict:
    path = pack_path_for_category(doc_category)
    if path is None:
        return {}
    return _load_pack_file(str(path.resolve()))


def load_universal_pack() -> dict:
    path = universal_pack_path()
    if path is None:
        return {}
    return _load_pack_file(str(path.resolve()))


def _format_pack_body(pack: dict, *, heading: str) -> str:
    exemplars = pack.get("exemplars") or []
    if not exemplars:
        return ""
    lines: list[str] = [heading]
    for item in exemplars:
        if not isinstance(item, dict):
            continue
        eid = str(item.get("id") or "").strip() or "unnamed"
        scenario = str(item.get("scenario") or "").strip()
        section = str(item.get("section_path") or "").strip()
        excerpt = str(item.get("content_excerpt") or "").strip()
        good = item.get("good") if isinstance(item.get("good"), dict) else {}
        bad = item.get("bad") if isinstance(item.get("bad"), list) else []
        lines.append(f"## Exemplar {eid}" + (f" ({scenario})" if scenario else ""))
        if section:
            lines.append(f"section_path: {section}")
        if excerpt:
            lines.append(f"content_excerpt:\n{excerpt}")
        lines.append("GOOD extraction JSON:")
        lines.append(json.dumps(good, ensure_ascii=False, indent=2))
        if bad:
            lines.append("BAD (do NOT do):")
            for tip in bad:
                tip_s = str(tip or "").strip()
                if tip_s:
                    lines.append(f"- {tip_s}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20].rstrip() + "\n... (truncated)"


def format_exemplars_for_prompt(
    doc_category: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str:
    """Return prompt section: universal patterns for all categories + optional category pack."""
    parts: list[str] = []
    universal = load_universal_pack()
    uni_body = _format_pack_body(
        universal,
        heading=(
            "Universal navigational patterns (apply to ALL products; "
            "replace {Tool}/{Service} with names from THIS chunk; do not copy foreign product names):"
        ),
    )
    if uni_body:
        parts.append(_truncate(uni_body, min(UNIVERSAL_BUDGET, max_chars)))

    cat_pack = load_pack(doc_category)
    if cat_pack.get("exemplars"):
        cat_body = _format_pack_body(
            cat_pack,
            heading=(
                f"Category-specific exemplars for doc_category={doc_category} "
                "(optional extra hints; still prefer universal patterns):"
            ),
        )
        remain = max_chars - sum(len(p) + 2 for p in parts)
        if cat_body and remain > 80:
            parts.append(_truncate(cat_body, min(CATEGORY_BUDGET, remain)))

    if not parts:
        return _NONE
    text = "\n\n".join(parts)
    return _truncate(text, max_chars)


def clear_exemplar_cache() -> None:
    _load_pack_file.cache_clear()
