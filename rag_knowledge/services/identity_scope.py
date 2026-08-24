"""Identity-only scope for conversation-agent orchestration (PRD V1.6)."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from rag_knowledge.services.backbone_guard import (
    avoid_names_for_anchors,
    load_backbone_constraints,
    resolve_canonical,
)
from rag_knowledge.services.evidence_scope import BindingStrength


@dataclass(frozen=True)
class IdentityScope:
    """Locks who the conversation is about without constraining evidence exploration."""

    scope_id: str
    primary_entity: str | None
    binding_strength: BindingStrength
    forbidden_rebindings: frozenset[str]
    scope_reason: str
    doc_category: str | None = None
    identity_status: str = "unresolved"
    confirmed_entity: str | None = None
    confirmed_topic: str | None = None
    raw_entity_mention: str | None = None
    confirmed_entities: tuple[str, ...] = ()
    raw_entity_mentions: tuple[str, ...] = ()
    scope_version: str = "v1.6"

    @property
    def is_identity_locked(self) -> bool:
        return self.identity_status == "confirmed_entity" and self.binding_strength in {
            BindingStrength.CONFIRMED,
            BindingStrength.EXPLICIT,
            BindingStrength.INFERRED,
        }

    @property
    def primary_root(self) -> str | None:
        """Compatibility alias for trace/callers migrating from EvidenceScope."""
        return self.primary_entity

    @property
    def root_entities(self) -> tuple[str, ...]:
        if self.identity_status != "confirmed_entity":
            return ()
        return self.confirmed_entities or ((self.primary_entity,) if self.primary_entity else ())

    @property
    def fingerprint(self) -> str:
        raw = ":".join((
            self.primary_entity or "",
            ",".join(sorted(self.confirmed_entities)),
            self.binding_strength.value,
            ",".join(sorted(self.forbidden_rebindings)),
            self.doc_category or "",
            self.identity_status,
            self.scope_version,
        ))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope_id": self.scope_id,
            "primary_entity": self.primary_entity,
            "binding_strength": self.binding_strength.value,
            "is_identity_locked": self.is_identity_locked,
            "forbidden_rebindings": sorted(self.forbidden_rebindings),
            "scope_reason": self.scope_reason,
            "doc_category": self.doc_category,
            "identity_status": self.identity_status,
            "confirmed_entity": (
                self.confirmed_entity or self.primary_entity
                if self.identity_status == "confirmed_entity"
                else None
            ),
            "confirmed_entities": list(self.confirmed_entities),
            "confirmed_topic": self.confirmed_topic,
            "raw_entity_mention": self.raw_entity_mention,
            "raw_entity_mentions": list(self.raw_entity_mentions),
            "scope_version": self.scope_version,
            "fingerprint": self.fingerprint,
        }


def is_canonical_backbone_entity(name: str | None, constraints: dict) -> bool:
    if not name:
        return False
    val = str(name).strip()
    types = constraints.get("entity_type_by_name") or {}
    if val in types:
        return True
    val_cf = val.casefold()
    return any(k.casefold() == val_cf for k in types)


class IdentityScopeResolver:
    """Materialize identity after Stage-1 semantic understanding."""

    @classmethod
    def resolve(
        cls,
        semantic_task: Any,
        *,
        entity_name: str | None = None,
        clarification_selected: str | None = None,
        selected_candidate: dict[str, Any] | None = None,
        previous_confirmed_entity: str | None = None,
        doc_category: str | None = None,
        constraints: dict | None = None,
        raw_entity_mention: str | None = None,
    ) -> IdentityScope:
        constraints = constraints if constraints is not None else load_backbone_constraints()
        explicit_raw = str(entity_name or "").strip()
        explicit = cls._known_canonical(explicit_raw, constraints)
        previous = cls._known_canonical(previous_confirmed_entity, constraints)

        mentioned = tuple(getattr(semantic_task, "mentioned_entities", ()) or ())
        primary_raw = str(getattr(semantic_task, "primary_entity", None) or "").strip()
        primary = cls._known_canonical(primary_raw, constraints)

        raw_mentions: list[str] = [str(item).strip() for item in mentioned if str(item).strip()]
        if explicit_raw and explicit_raw not in raw_mentions:
            raw_mentions.insert(0, explicit_raw)
        if primary_raw and primary_raw not in raw_mentions:
            raw_mentions.append(primary_raw)
        if raw_entity_mention and str(raw_entity_mention).strip() not in raw_mentions:
            raw_mentions.insert(0, str(raw_entity_mention).strip())

        # Extract all valid backbone/graph entities from mentions
        valid_entities: list[str] = []
        for m in mentioned:
            canon_m = cls._known_canonical(m, constraints)
            if canon_m and canon_m not in valid_entities:
                valid_entities.append(canon_m)

        identity_status = "unresolved"
        confirmed_topic = None
        candidate = selected_candidate if isinstance(selected_candidate, dict) else {}
        candidate_label = str(candidate.get("label") or "").strip()
        raw_sel = str(clarification_selected or candidate_label or "").strip()
        candidate_source = str(candidate.get("source") or "").strip().casefold()
        candidate_binding = str(candidate.get("binding_status") or "").strip().casefold()
        candidate_id = str(candidate.get("candidate_id") or candidate.get("id") or "").strip().casefold()
        candidate_canonical = str(candidate.get("canonical_name") or "").strip()

        if raw_sel:
            is_other = (
                raw_sel.casefold() in {"other", "以上都不是", "以上都不是（other）"}
                or candidate_id == "other"
                or candidate_source in {"fixed_other", "other", "free_text"}
                or candidate_binding in {"other", "free_text"}
            )
            if is_other:
                primary = None
                valid_entities = []
                strength = BindingStrength.UNBOUND
                reason = "clarification_other"
                identity_status = "unresolved"
            else:
                # Callback metadata is client supplied.  Resolve the visible
                # selection itself and use canonical_name only as a consistency
                # check; metadata alone can never create an entity identity.
                resolved = cls._known_canonical(raw_sel, constraints)
                metadata_canonical = cls._known_canonical(candidate_canonical, constraints)
                if candidate_canonical and (
                    not resolved
                    or not metadata_canonical
                    or resolved.casefold() != metadata_canonical.casefold()
                ):
                    resolved = None
                if resolved:
                    primary = resolved
                    valid_entities = [resolved]
                    strength = BindingStrength.CONFIRMED
                    reason = "clarification_confirmed"
                    identity_status = "confirmed_entity"
                else:
                    primary = None
                    valid_entities = []
                    confirmed_topic = raw_sel
                    strength = BindingStrength.UNBOUND
                    reason = "clarification_topic"
                    identity_status = "confirmed_topic"
        elif explicit:
            primary = explicit
            valid_entities = [explicit]
            strength = BindingStrength.EXPLICIT
            reason = "request_explicit_entity"
            identity_status = "confirmed_entity"
        elif valid_entities:
            primary = valid_entities[0]
            strength = BindingStrength.EXPLICIT
            reason = "user_explicit_mention"
            identity_status = "confirmed_entity"
        elif primary:
            valid_entities = [primary]
            strength = BindingStrength.INFERRED
            reason = "stage1_resolved_entity"
            identity_status = "confirmed_entity"
        elif previous:
            primary = previous
            valid_entities = [previous]
            strength = BindingStrength.CONFIRMED
            reason = "conversation_confirmed_subject"
            identity_status = "confirmed_entity"
        else:
            primary = None
            valid_entities = []
            strength = BindingStrength.UNBOUND
            reason = "stage1_unbound"
            identity_status = "unresolved"

        if identity_status == "unresolved" and not raw_mentions:
            inferred_raw = cls._infer_unresolved_mention(
                str(getattr(semantic_task, "resolved_question", "") or "")
            )
            if inferred_raw:
                raw_mentions.append(inferred_raw)

        forbidden: set[str] = set()
        if primary:
            forbidden.update(avoid_names_for_anchors([primary], constraints))
            forbidden.discard(primary)

        confirmed_entities = tuple(valid_entities)
        confirmed_entity = confirmed_entities[0] if confirmed_entities else None
        raw_entity_mentions_tuple = tuple(raw_mentions)
        effective_raw_mention = raw_mentions[0] if raw_mentions else None

        cat = (doc_category or "").strip() or None
        raw = f"{primary or ''}:{','.join(sorted(confirmed_entities))}:{strength.value}:{reason}:{cat or ''}:{identity_status}"
        scope_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
        return IdentityScope(
            scope_id=scope_id,
            primary_entity=primary,
            binding_strength=strength,
            forbidden_rebindings=frozenset(forbidden),
            scope_reason=reason,
            doc_category=cat,
            identity_status=identity_status,
            confirmed_entity=confirmed_entity,
            confirmed_topic=confirmed_topic,
            raw_entity_mention=effective_raw_mention,
            confirmed_entities=confirmed_entities,
            raw_entity_mentions=raw_entity_mentions_tuple,
        )

    @staticmethod
    def _canonical(value: Any, constraints: dict) -> str | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        mapped = raw
        try:
            from rag_knowledge.services.sdk_code_job import map_clarification_text

            mapped = map_clarification_text(raw) or raw
        except Exception:
            pass
        canonical = resolve_canonical(mapped, constraints)
        if canonical != mapped and is_canonical_backbone_entity(canonical, constraints):
            return canonical
        label_name = mapped.split("（", 1)[0].split("(", 1)[0].strip()
        resolved_label = resolve_canonical(label_name, constraints) or label_name
        types = constraints.get("entity_type_by_name") or {}
        for canonical_name in types:
            if str(canonical_name).casefold() == resolved_label.casefold():
                return str(canonical_name)
        return resolved_label or mapped

    @classmethod
    def _known_canonical(cls, value: Any, constraints: dict) -> str | None:
        candidate = cls._canonical(value, constraints)
        if not candidate:
            return None
        types = constraints.get("entity_type_by_name") or {}
        for canonical_name in types:
            if str(canonical_name).casefold() == candidate.casefold():
                return str(canonical_name)
        try:
            from rag_knowledge.repository.relational_db import RelationalDB

            db = RelationalDB()
            entity = db.get_entity_by_name(candidate)
            if entity is None:
                return None
            if isinstance(entity, dict):
                return str(entity.get("canonical_name") or entity.get("name") or candidate).strip() or None
            return candidate
        except Exception:
            return None

    @staticmethod
    def _infer_unresolved_mention(question: str) -> str | None:
        text = str(question or "").strip()
        if not text:
            return None
        tokens = re.findall(r"(?<![A-Za-z0-9_.-])([A-Za-z][A-Za-z0-9_.-]{2,40})(?![A-Za-z0-9_.-])", text)
        stopwords = {
            "about", "and", "are", "can", "configure", "configuration", "does", "for",
            "from", "how", "install", "is", "setup", "should", "the", "this", "use",
            "what", "when", "where", "which", "why", "with",
        }
        candidates = [token for token in tokens if token.casefold() not in stopwords]
        if len(candidates) != 1:
            return None
        entity_cues = (
            "配置", "安装", "部署", "使用", "接口", "端口", "版本", "怎么", "如何", "是什么",
            "config", "install", "deploy", "setup", "version", "port", "api", "use",
        )
        if not any(cue in text.casefold() for cue in entity_cues):
            return None
        return candidates[0]

    @staticmethod
    def _same(left: str, right: str) -> bool:
        a = (left or "").strip().casefold()
        b = (right or "").strip().casefold()
        return bool(a and b and (a == b or a in b or b in a))
