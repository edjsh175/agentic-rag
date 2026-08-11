"""Rule extractors for StampServer deployment leaves: Procedure + Command.

GraphRAG policy: navigational skeleton only — install/config/deploy procedures and
shell commands under a path Service. No ConfigItem flood; no Service→Product skip
edges (backbone_guard handles that elsewhere).

Procedure titles come only from section_path leaf (FR-R2 / V1.6). Body lines may
still yield Commands; they must not invent Procedure titles (avoids ancestor fan-in).

Procedure→Module belongs_to is not in schema; without a Service owner we still emit
the Procedure entity + chunk link, but skip ownership edges.
"""
from __future__ import annotations

import re

from rag_knowledge.services.domain_catalog import DomainCatalogLoader
from rag_knowledge.services.graph_extraction import (
    ChunkLinkCandidate,
    EntityCandidate,
    ExtractionResult,
    RelationCandidate,
)
from rag_knowledge.services.graph_extraction.chapter_leaf_extractor import resolve_path_owner
from rag_knowledge.services.graph_extraction.llm_extractor import (
    _COMMAND_NAME_RE,
    chunk_has_command_signal,
)

_CREATED_BY = "rule:server_leaf"
_DEPLOY_PATH_MARKER = "服务部署"

_PROCEDURE_TITLE_RE = re.compile(r"^.{1,40}?(?:安装|配置|部署)$")

# Only applies when the path leaf equals the whitelist entry (not a body scan).
_PROCEDURE_WHITELIST = frozenset({
    "服务部署准备",
    "Redis安装",
    "Nginx代理设置",
})

_COMMAND_LINE_RE = re.compile(
    r"(?:^|\n)\s*(?:\$\s*)?((?:sudo\s+)?"
    r"(?:systemctl|service|yum|dnf|rpm|apt(?:-get)?|docker|podman|psql|tar\s+-[a-zA-Z]*|"
    r"chmod|chown|firewall-cmd|mkdir|curl|wget)\b[^\n]{0,160})",
    re.IGNORECASE | re.MULTILINE,
)


def _parts(chunk: dict) -> tuple[str, str, str, str, str]:
    metadata = chunk.get("metadata") or {}
    chunk_id = str(chunk.get("chunk_id") or metadata.get("chunk_id") or "")
    content = str(chunk.get("content") or "")
    source = str(metadata.get("source") or "")
    category = str(metadata.get("doc_category") or "")
    path = str(metadata.get("section_path") or "")
    return chunk_id, content, source, category, path


def _leaf_title(section_path: str) -> str:
    parts = [p.strip("：: \t\u3000") for p in section_path.split(">") if p.strip("：: \t\u3000")]
    return parts[-1] if parts else ""


def _is_procedure_title(title: str) -> bool:
    t = (title or "").strip()
    if not t or len(t) > 40:
        return False
    if t in _PROCEDURE_WHITELIST:
        return True
    return bool(_PROCEDURE_TITLE_RE.match(t))


def _extract_commands(content: str) -> list[str]:
    if not chunk_has_command_signal(content):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for match in _COMMAND_LINE_RE.finditer(content or ""):
        cmd = re.sub(r"\s+", " ", (match.group(1) or "").strip())
        if not cmd or cmd in seen:
            continue
        head = cmd.split("&&")[0].strip()
        if not _COMMAND_NAME_RE.search(head) and not re.match(
            r"^(?:sudo\s+)?tar\s+-", head, re.IGNORECASE
        ):
            continue
        seen.add(cmd)
        out.append(cmd[:160])
        if len(out) >= 8:
            break
    return out


class ServerLeafExtractor:
    """Extract deployment Procedure (path leaf only) + Command under StampServer."""

    def __init__(self, catalog: DomainCatalogLoader | None = None):
        self.catalog = catalog or DomainCatalogLoader()

    def extract(self, chunk: dict, context: ExtractionResult | None = None) -> ExtractionResult:
        chunk_id, content, source, category, path = _parts(chunk)
        result = ExtractionResult()
        if category != "StampServer":
            return result
        if _DEPLOY_PATH_MARKER not in path:
            return result

        owner = resolve_path_owner(path, self.catalog)
        props = {"created_by": _CREATED_BY}

        leaf = _leaf_title(path)
        primary_proc: str | None = leaf if _is_procedure_title(leaf) else None

        if primary_proc:
            result.entities.append(
                EntityCandidate(
                    primary_proc,
                    "Procedure",
                    category,
                    dict(props),
                    source_chunk_id=chunk_id,
                    evidence_text=primary_proc,
                )
            )
            if owner:
                result.relations.append(
                    RelationCandidate(owner, "has_procedure", primary_proc, chunk_id, primary_proc)
                )
            result.links.append(
                ChunkLinkCandidate(
                    primary_proc, chunk_id, section_path=path, source=source, evidence_text=primary_proc
                )
            )

        for cmd in _extract_commands(content):
            result.entities.append(
                EntityCandidate(
                    cmd,
                    "Command",
                    category,
                    dict(props),
                    source_chunk_id=chunk_id,
                    evidence_text=cmd,
                )
            )
            if primary_proc:
                result.relations.append(
                    RelationCandidate(primary_proc, "runs_command", cmd, chunk_id, cmd)
                )
            result.links.append(
                ChunkLinkCandidate(cmd, chunk_id, section_path=path, source=source, evidence_text=cmd)
            )

        return result
