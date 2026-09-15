"""Policy decisions: where automation may go, what it may do, and how risky each action is."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse

from rote.models.policy import Decision, Policy

Mode = Literal["discovery", "replay", "session"]


def _norm(text: str | None) -> str:
    return " ".join((text or "").split()).rstrip(": ").upper()


@dataclass
class PolicyDecision:
    decision: Decision
    risk: str
    rule: str
    reason: str = ""

    @property
    def blocked(self) -> bool:
        return self.decision == "block"

    @property
    def needs_approval(self) -> bool:
        return self.decision == "require_approval"

    def as_dict(self) -> dict[str, str]:
        return {"decision": self.decision, "risk": self.risk, "rule": self.rule, "reason": self.reason}


class PolicyEngine:
    def __init__(self, policy: Policy) -> None:
        self.policy = policy

    # ---------------- navigation ----------------
    def url_allowed(self, url: str) -> tuple[bool, str]:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self.policy.allowed_origins:
            return False, f"origin {origin} is not allowlisted"
        for pattern in self.policy.blocked_routes:
            if fnmatch.fnmatch(parsed.path, pattern):
                return False, f"route {parsed.path} is blocked by {pattern}"
        return True, "allowed"

    # ---------------- actions ----------------
    def action_allowed(self, action: str, key: str | None = None) -> tuple[bool, str]:
        if action not in self.policy.allowed_actions:
            return False, f"action {action!r} is not allowed"
        if action == "press_key" and key not in self.policy.allowed_keys:
            return False, f"key {key!r} is not allowed"
        return True, "allowed"

    def classify(
        self,
        element: dict[str, Any] | None,
        *,
        action: str,
        screen: str | None = None,
        dialog_text: str | None = None,
    ) -> tuple[str, str]:
        element = element or {}
        for rule in self.policy.risk_rules:
            m = rule.match
            if m.action is not None and m.action != action:
                continue
            if m.role is not None:
                roles = [m.role] if isinstance(m.role, str) else m.role
                if element.get("role") not in roles:
                    continue
            if m.name is not None and _norm(element.get("name")) != _norm(m.name):
                continue
            if m.name_regex is not None and not re.search(m.name_regex, _norm(element.get("name")), re.IGNORECASE):
                continue
            if m.screen is not None and m.screen != screen:
                continue
            if m.dialog_text_regex is not None and not re.search(m.dialog_text_regex, _norm(dialog_text), re.IGNORECASE):
                continue
            if m.form_submit is not None and bool(element.get("form_submit")) != m.form_submit:
                continue
            if m.form_method is not None and (element.get("form_method") or "").upper() != m.form_method.upper():
                continue
            return rule.risk, rule.id
        return self.policy.default_risk, "default"

    def decide(
        self,
        *,
        mode: Mode,
        action: str,
        element: dict[str, Any] | None,
        screen: str | None = None,
        dialog_text: str | None = None,
        key: str | None = None,
        declared_risk: str | None = None,
    ) -> PolicyDecision:
        allowed, reason = self.action_allowed(action, key)
        if not allowed:
            return PolicyDecision("block", "n/a", "allowed_actions", reason)
        risk, rule = self.classify(element, action=action, screen=screen, dialog_text=dialog_text)
        if risk == "safe":
            return PolicyDecision("allow", risk, rule, "safe action")
        if mode == "session":
            return PolicyDecision("block", risk, rule, "session steps may not perform irreversible actions")
        if mode == "discovery":
            decision = self.policy.modes.discovery.irreversible
            return PolicyDecision(decision, risk, rule, "irreversible action during discovery")
        if declared_risk == "irreversible":
            return PolicyDecision(self.policy.modes.replay.irreversible_declared, risk, rule, "declared irreversible step")
        return PolicyDecision(
            self.policy.modes.replay.irreversible_undeclared,
            risk,
            rule,
            f"irreversible control not declared in the artifact (artifact said {declared_risk or 'safe'})",
        )
