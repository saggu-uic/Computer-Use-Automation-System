"""Safety policy: allowlist, action rules, and risk classification. Owned separately from artifacts."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from rote.models.common import Risk, Strict


class RiskMatch(Strict):
    role: str | list[str] | None = None
    name: str | None = None
    name_regex: str | None = None
    screen: str | None = None
    action: str | None = None
    dialog_text_regex: str | None = None
    form_submit: bool | None = None
    form_method: str | None = None


class RiskRule(Strict):
    id: str
    match: RiskMatch
    risk: Risk
    note: str = ""


Decision = Literal["allow", "block", "require_approval"]


class DiscoveryMode(Strict):
    irreversible: Decision = "require_approval"


class ReplayMode(Strict):
    irreversible_declared: Decision = "require_approval"
    irreversible_undeclared: Decision = "block"


class Modes(Strict):
    discovery: DiscoveryMode = Field(default_factory=DiscoveryMode)
    replay: ReplayMode = Field(default_factory=ReplayMode)


class Policy(Strict):
    schema_version: Literal["1.0"] = "1.0"
    product: str
    allowed_origins: list[str]
    blocked_routes: list[str] = Field(default_factory=list)
    allowed_actions: list[str]
    allowed_keys: list[str] = Field(default_factory=lambda: ["Enter", "Tab", "Escape"])
    risk_rules: list[RiskRule]
    default_risk: Risk = "safe"
    modes: Modes = Field(default_factory=Modes)
