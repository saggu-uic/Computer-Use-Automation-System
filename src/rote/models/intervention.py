from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ResumeExpectation(BaseModel):
    after_step: str | None = None
    expected_screens: list[str] = Field(default_factory=list)
    instructions: str = ""


class Intervention(BaseModel):
    id: str
    kind: Literal["take_control", "approve_action"]
    status: Literal["open", "in_progress", "resolved", "aborted", "expired"] = "open"
    run_id: str
    mode: str
    capability: str | None = None
    goal: str | None = None
    step: dict[str, Any] = Field(default_factory=dict)
    reason_code: str
    reason: str
    screen_summary: str = ""
    screenshot: str | None = None
    resume_expectation: ResumeExpectation | None = None
    proposed_action: str | None = None
    created_at: str
    expires_at: str
    operator: str | None = None
    resolution: str | None = None
    note: str | None = None
    verification_message: str | None = None
