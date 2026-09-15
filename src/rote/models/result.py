"""The result contract returned to callers: success | business_outcome | failure."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Status = Literal["success", "business_outcome", "failure"]
Phase = Literal["preflight", "execution", "verification"]
Category = Literal[
    "caller_error", "configuration", "policy", "automation", "app", "in_doubt", "human", "infrastructure"
]

# code -> (category, retryable)
FAILURE_CODES: dict[str, tuple[Category, bool]] = {
    "INVALID_INPUT": ("caller_error", False),
    "ARTIFACT_INVALID": ("configuration", False),
    "PROFILE_MISMATCH": ("configuration", False),
    "POLICY_DENIED": ("policy", False),
    "AUTH_FAILED": ("configuration", False),
    "OPERATOR_NOT_AUTHORIZED": ("configuration", False),
    "TARGET_NOT_FOUND": ("automation", False),
    "TARGET_AMBIGUOUS": ("automation", False),
    "TARGET_NOT_ACTIONABLE": ("automation", False),
    "UNEXPECTED_SCREEN": ("automation", False),
    "CHECKPOINT_TIMEOUT": ("app", True),
    "APP_ERROR": ("app", True),
    "RECOVERY_BUDGET_EXCEEDED": ("app", True),
    "POLICY_BLOCKED": ("policy", False),
    "OUTPUT_PARSE_ERROR": ("automation", False),
    "SUCCESS_CONDITION_FAILED": ("automation", False),
    "OUTCOME_UNKNOWN": ("in_doubt", False),
    "HUMAN_TIMEOUT": ("human", True),
    "HUMAN_ABORTED": ("human", False),
    "HUMAN_REJECTED_ACTION": ("human", False),
    "HUMAN_REQUIRED": ("human", True),
    "RUN_TIMEOUT": ("app", True),
    "INFRA_ERROR": ("infrastructure", True),
}


class ErrorInfo(BaseModel):
    code: str
    category: Category
    phase: Phase
    retryable: bool
    message: str = ""
    step_id: str | None = None
    expected: str | None = None
    observed: str | None = None
    fields: dict[str, str] | None = None
    evidence: dict[str, str] | None = None
    intervention: dict[str, Any] | None = None

    @classmethod
    def make(cls, code: str, phase: Phase, **kwargs: Any) -> "ErrorInfo":
        category, retryable = FAILURE_CODES.get(code, ("automation", False))
        return cls(code=code, category=category, phase=phase, retryable=retryable, **kwargs)


class OutcomeInfo(BaseModel):
    code: str
    message: str = ""
    at_step: str | None = None


class CapabilityResult(BaseModel):
    invocation_id: str
    capability: str
    content_hash: str | None = None
    profile_hash: str | None = None
    mode: Literal["attended", "unattended"] = "unattended"
    status: Status
    outputs: dict[str, Any] = Field(default_factory=dict)
    outcome: OutcomeInfo | None = None
    error: ErrorInfo | None = None
    recoveries: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    interventions: list[dict[str, Any]] = Field(default_factory=list)
    ui_touched: bool = False
    side_effects_possible: bool = False
    timing_ms: dict[str, int] = Field(default_factory=dict)
    evidence: str | None = None
