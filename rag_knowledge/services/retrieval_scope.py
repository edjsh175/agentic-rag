"""
统一检索范围（RetrievalScope）兼容外观（Facade）对象。

作为上层兼容层，内部已重构为调用 ScopeResolver 生成底层 EvidenceScope，
同时保持与历史调用接口的完全兼容。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from rag_knowledge.services.evidence_scope import (
    BindingStrength,
    EvidenceScope,
    ScopePolicy,
    ScopeResolver,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetrievalScope:
    """内部检索范围契约对象（兼容外观）。"""

    canonical_entity: str = ""
    doc_category: str | None = None
    explicit_selection: bool = False
    allowed_document_entity: str | None = None
    evidence_scope: EvidenceScope | None = None

    @classmethod
    def create(
        cls,
        question: str,
        *,
        entity_name: str | None = None,
        doc_category: str | None = None,
        clarification_selected: str | None = None,
        constraints: dict | None = None,
        policy: ScopePolicy | None = None,
    ) -> RetrievalScope:
        """从请求参数解析构建标准 RetrievalScope，内部基于 ScopeResolver 求解。"""
        ev_scope = ScopeResolver.resolve(
            question,
            entity_name=entity_name,
            doc_category=doc_category,
            clarification_selected=clarification_selected,
            constraints=constraints,
            policy=policy,
        )

        canonical = ev_scope.primary_root or ""
        explicit_selection = ev_scope.is_identity_locked
        cat = ev_scope.doc_category
        allowed_doc_ent = canonical if (explicit_selection and canonical) else None

        return cls(
            canonical_entity=canonical,
            doc_category=cat,
            explicit_selection=explicit_selection,
            allowed_document_entity=allowed_doc_ent,
            evidence_scope=ev_scope,
        )

    def to_evidence_scope(self) -> EvidenceScope:
        """获取或构造对应的 EvidenceScope。"""
        if self.evidence_scope is not None:
            return self.evidence_scope
        return ScopeResolver.resolve(
            "",
            entity_name=self.canonical_entity or None,
            doc_category=self.doc_category,
        )

    def to_dict(self) -> dict[str, Any]:
        data = {
            "canonical_entity": self.canonical_entity,
            "doc_category": self.doc_category,
            "explicit_selection": self.explicit_selection,
            "allowed_document_entity": self.allowed_document_entity,
        }
        if self.evidence_scope is not None:
            data["evidence_scope"] = self.evidence_scope.to_dict()
        return data
