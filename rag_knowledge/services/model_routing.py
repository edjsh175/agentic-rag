"""阶段级模型角色路由。

普通文本问答只在这里决定 Main/Helper 的职责归属；具体 provider 与模型
仍由 :class:`rag_knowledge.config.Config` 的 endpoint 配置负责。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_DEFAULT_ROLES = {
    "common_stage1": "helper_llm",
    "agent_controller": "llm",
    "agent_answer": "llm",
    "linear_preprocess": "helper_llm",
    "linear_escalation": "llm",
    "grounding_reviewer": "helper_llm",
}
_FIXED_ROLES = {"grounding_reviewer": "helper_llm"}

_ALLOWED_ROLES = frozenset({"llm", "helper_llm"})


@dataclass(frozen=True)
class ModelRoute:
    stage: str
    role: str
    configured: bool = False


class ModelRoutePolicy:
    """Resolve one logical stage to one configured model role.

    Missing routing configuration intentionally falls back to the PRD defaults.
    In particular, Agent Controller never infers its role from whether a Helper
    endpoint happens to exist.
    """

    def __init__(self, cfg: Any | None = None):
        self._cfg = cfg

    def route(self, stage: str) -> ModelRoute:
        key = str(stage or "").strip() or "linear_preprocess"
        if key in _FIXED_ROLES:
            return ModelRoute(stage=key, role=_FIXED_ROLES[key], configured=False)
        default = _DEFAULT_ROLES.get(key, "llm")
        routing = getattr(self._cfg, "model_routing", None)
        attr = f"{key}_role"
        configured_value = getattr(routing, attr, None) if routing is not None else None
        role = str(configured_value or default).strip().lower()
        if role not in _ALLOWED_ROLES:
            role = default
        return ModelRoute(stage=key, role=role, configured=bool(configured_value))

    def role_for(self, stage: str) -> str:
        return self.route(stage).role

    def common_stage1_role(self) -> str:
        return self.role_for("common_stage1")

    def agent_controller_role(self) -> str:
        return self.role_for("agent_controller")

    def agent_answer_role(self) -> str:
        return self.role_for("agent_answer")

    def linear_preprocess_role(self) -> str:
        return self.role_for("linear_preprocess")

    def linear_escalation_role(self) -> str:
        return self.role_for("linear_escalation")

    def grounding_reviewer_role(self) -> str:
        return self.role_for("grounding_reviewer")
