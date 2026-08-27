"""Identity-only scope for conversation-agent orchestration (PRD V1.6 & Entity Candidate Resolution PRD)."""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Any

from rag_knowledge.services.backbone_guard import (
    avoid_names_for_anchors,
    load_backbone_constraints,
    resolve_canonical,
)
from rag_knowledge.services.entity_candidate_resolver import (
    EntityCandidate,
    IdentityResolution,
    get_entity_candidate_resolver,
)
from rag_knowledge.services.evidence_scope import BindingStrength

logger = logging.getLogger(__name__)


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
    confirmed_entity_id: str | None = None
    confirmed_topic: str | None = None
    raw_entity_mention: str | None = None
    confirmed_entities: tuple[str, ...] = ()
    raw_entity_mentions: tuple[str, ...] = ()
    candidate_entities: tuple[EntityCandidate, ...] = ()
    identity_resolution: IdentityResolution | None = None
    clarification_snapshot_id: str | None = None
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
            "confirmed_entity_id": self.confirmed_entity_id,
            "confirmed_entities": list(self.confirmed_entities),
            "confirmed_topic": self.confirmed_topic,
            "raw_entity_mention": self.raw_entity_mention,
            "raw_entity_mentions": list(self.raw_entity_mentions),
            "candidate_count": len(self.candidate_entities),
            "clarification_snapshot_id": self.clarification_snapshot_id,
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
    """Materialize authoritative identity before ControllerState using EntityCandidateResolver."""

    @classmethod
    def resolve(
        cls,
        semantic_task: Any,
        *,
        question: str | None = None,
        entity_name: str | None = None,
        clarification_selected: str | None = None,
        clarification_option_id: str | None = None,
        clarification_snapshot_id: str | None = None,
        selected_candidate: dict[str, Any] | None = None,
        previous_confirmed_entity: str | None = None,
        doc_category: str | None = None,
        constraints: dict | None = None,
        raw_entity_mention: str | None = None,
    ) -> IdentityScope:
        constraints = constraints if constraints is not None else load_backbone_constraints()
        resolver = get_entity_candidate_resolver(constraints=constraints)

        raw_q = getattr(semantic_task, "resolved_question", None)
        query_text = (raw_q if isinstance(raw_q, str) else (question if isinstance(question, str) else "")).strip()

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

        valid_entities: list[str] = []
        for m in mentioned:
            canon_m = cls._known_canonical(m, constraints)
            if canon_m and canon_m not in valid_entities:
                valid_entities.append(canon_m)

        identity_status = "unresolved"
        confirmed_topic = None
        confirmed_entity_id = None
        resolution: IdentityResolution | None = None
        candidate_entities: tuple[EntityCandidate, ...] = ()

        candidate = selected_candidate if isinstance(selected_candidate, dict) else {}
        candidate_label = str(candidate.get("label") or "").strip()
        raw_sel = str(clarification_selected or candidate_label or "").strip()
        candidate_source = str(candidate.get("source") or "").strip().casefold()
        candidate_binding = str(candidate.get("binding_status") or "").strip().casefold()
        candidate_id = str(
            candidate.get("candidate_id")
            or candidate.get("entity_id")
            or candidate.get("id")
            or clarification_option_id
            or ""
        ).strip()
        candidate_canonical = str(candidate.get("canonical_name") or "").strip()
        snapshot_id = str(clarification_snapshot_id or "").strip() or None

        is_callback = bool(raw_sel or candidate_id or snapshot_id)

        if is_callback:
            is_other = (
                raw_sel.casefold() in {"other", "以上都不是", "以上都不是（other）"}
                or candidate_id.casefold() in {"other", "fixed_other"}
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
                # 1. Authoritative Snapshot Verification
                validated_entity = None
                if snapshot_id:
                    if candidate_id:
                        validated_entity = resolver.validate_callback_selection(candidate_id, snapshot_id=snapshot_id)
                    if not validated_entity and raw_sel:
                        validated_entity = resolver.validate_callback_selection(raw_sel, snapshot_id=snapshot_id)
                    if not validated_entity and candidate_canonical:
                        validated_entity = resolver.validate_callback_selection(candidate_canonical, snapshot_id=snapshot_id)

                    if validated_entity:
                        primary = validated_entity.canonical_name
                        confirmed_entity_id = validated_entity.entity_id
                        valid_entities = [primary]
                        strength = BindingStrength.CONFIRMED
                        reason = "clarification_confirmed_from_snapshot"
                        identity_status = "confirmed_entity"
                    else:
                        primary = None
                        valid_entities = []
                        confirmed_topic = raw_sel
                        strength = BindingStrength.UNBOUND
                        reason = "clarification_snapshot_mismatch"
                        identity_status = "unresolved"
                elif clarification_option_id:
                    primary = None
                    valid_entities = []
                    confirmed_topic = raw_sel
                    strength = BindingStrength.UNBOUND
                    reason = "clarification_snapshot_required"
                    identity_status = "unresolved"
                else:
                    # Legacy label-only callbacks predate snapshot-backed options.
                    resolved = None
                    if candidate_id and candidate_id.startswith("ent_"):
                        reg_ent = resolver.registry.get_by_id(candidate_id)
                        if reg_ent is not None:
                            resolved = reg_ent.canonical_name
                            confirmed_entity_id = reg_ent.entity_id

                    if not resolved:
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
                        reg_ent = resolver.registry.get_by_name(resolved)
                        if reg_ent:
                            confirmed_entity_id = reg_ent.entity_id
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
            reg_ent = resolver.registry.get_by_name(explicit)
            if reg_ent:
                confirmed_entity_id = reg_ent.entity_id
            valid_entities = [explicit]
            strength = BindingStrength.EXPLICIT
            reason = "request_explicit_entity"
            identity_status = "confirmed_entity"

        else:
            raw_tt = getattr(semantic_task, "task_type", None)
            task_type = raw_tt.strip() if isinstance(raw_tt, str) else ""

            if len(valid_entities) >= 2 or (task_type == "multi_entity_relation" and valid_entities):
                confirmed_entities = tuple(valid_entities)
                primary = primary if (primary and primary in valid_entities) else (valid_entities[0] if valid_entities else None)
                if primary:
                    reg_ent = resolver.registry.get_by_name(primary)
                    if reg_ent:
                        confirmed_entity_id = reg_ent.entity_id
                strength = BindingStrength.EXPLICIT
                reason = "multi_entity_context"
                identity_status = "confirmed_entity"
            else:
                context_ents = (previous,) if previous else ()
                resolution = resolver.resolve_identity(
                    query_text,
                    context_entities=context_ents,
                    doc_category=doc_category,
                )

                if resolution.status == "confirmed":
                    primary = resolution.confirmed_entity_name
                    confirmed_entity_id = resolution.confirmed_entity_id
                    valid_entities = [primary] if primary else []
                    strength = BindingStrength.CONFIRMED
                    reason = "entity_candidate_resolver_confirmed"
                    identity_status = "confirmed_entity"
                elif resolution.status == "ambiguous":
                    primary = None
                    valid_entities = [c.canonical_name for c in resolution.candidates]
                    candidate_entities = resolution.candidates
                    strength = BindingStrength.UNBOUND
                    reason = f"ambiguous_candidates_surface_{resolution.surface}"
                    identity_status = "ambiguous_entity"
                else:
                    # unresolved or not_required
                    if previous and not mentioned and not resolution.candidates:
                        primary = previous
                        reg_ent = resolver.registry.get_by_name(previous)
                        if reg_ent:
                            confirmed_entity_id = reg_ent.entity_id
                        valid_entities = [previous]
                        strength = BindingStrength.CONFIRMED
                        reason = "conversation_confirmed_subject"
                        identity_status = "confirmed_entity"
                    elif valid_entities:
                        primary = valid_entities[0]
                        reg_ent = resolver.registry.get_by_name(primary)
                        if reg_ent:
                            confirmed_entity_id = reg_ent.entity_id
                        strength = BindingStrength.EXPLICIT
                        reason = "user_explicit_mention"
                        identity_status = "confirmed_entity"
                    elif primary:
                        valid_entities = [primary]
                        reg_ent = resolver.registry.get_by_name(primary)
                        if reg_ent:
                            confirmed_entity_id = reg_ent.entity_id
                        strength = BindingStrength.INFERRED
                        reason = "stage1_resolved_entity"
                        identity_status = "confirmed_entity"
                    else:
                        primary = None
                        valid_entities = []
                        strength = BindingStrength.UNBOUND
                        reason = resolution.reason if resolution else "stage1_unbound"
                        identity_status = "unresolved"

        if identity_status == "unresolved" and not raw_mentions:
            inferred_raw = cls._infer_unresolved_mention(query_text)
            if inferred_raw:
                raw_mentions.append(inferred_raw)

        forbidden: set[str] = set()
        if primary:
            forbidden.update(avoid_names_for_anchors([primary], constraints))

        confirmed_entities: tuple[str, ...] = (
            tuple(valid_entities) if len(valid_entities) >= 2 and identity_status == "confirmed_entity"
            else ()
        )

        confirmed_entity_value = (
            primary
            if identity_status == "confirmed_entity" and primary
            else (confirmed_entities[0] if confirmed_entities else None)
        )

        scope_raw = ":".join((
            primary or "",
            ",".join(sorted(confirmed_entities)),
            strength.value,
            doc_category or "",
            identity_status,
            reason,
        ))
        scope_id = f"scope_{hashlib.sha256(scope_raw.encode('utf-8')).hexdigest()[:16]}"

        return IdentityScope(
            scope_id=scope_id,
            primary_entity=primary,
            binding_strength=strength,
            forbidden_rebindings=frozenset(forbidden),
            scope_reason=reason,
            doc_category=doc_category,
            identity_status=identity_status,
            confirmed_entity=confirmed_entity_value,
            confirmed_entity_id=confirmed_entity_id,
            confirmed_topic=confirmed_topic,
            raw_entity_mention=raw_mentions[0] if raw_mentions else None,
            confirmed_entities=confirmed_entities,
            raw_entity_mentions=tuple(raw_mentions),
            candidate_entities=candidate_entities,
            identity_resolution=resolution,
            clarification_snapshot_id=snapshot_id,
        )

    @classmethod
    def _canonical(cls, value: Any, constraints: dict) -> str | None:
        if not value:
            return None
        text = str(value).strip()
        if not text:
            return None

        aliases = constraints.get("canonical_by_alias") or {}
        if text in aliases:
            return aliases[text]
        for k, v in aliases.items():
            if str(k).casefold() == text.casefold():
                return str(v)

        types = constraints.get("entity_type_by_name") or {}
        if text in types:
            return text
        for k in types:
            if str(k).casefold() == text.casefold():
                return str(k)

        clean_text = text.split("（", 1)[0].split("(", 1)[0].strip()
        if clean_text != text and clean_text:
            if clean_text in aliases:
                return aliases[clean_text]
            for k, v in aliases.items():
                if str(k).casefold() == clean_text.casefold():
                    return str(v)
            if clean_text in types:
                return clean_text
            for k in types:
                if str(k).casefold() == clean_text.casefold():
                    return str(k)

        resolved = resolve_canonical(clean_text or text, constraints)
        if resolved:
            for k in types:
                if str(k).casefold() == resolved.casefold():
                    return str(k)
            return resolved

        return None

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
