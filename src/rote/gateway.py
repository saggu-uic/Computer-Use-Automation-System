"""The Action Gateway: the one path every automation action takes.

control check -> runtime risk classification -> policy decision -> (approval) -> act -> verify -> masked event.
Human actions do not pass through here; they are observed and recorded by the broker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from playwright.async_api import ElementHandle

from rote.models.capability import Locator
from rote.surface.web import ActResult

if TYPE_CHECKING:
    from rote.runtime import RunContext, Runtime

ELEMENT_ACTIONS = {"click", "type", "select", "check"}


@dataclass
class ActionIntent:
    action: str
    element: ElementHandle | None = None
    ref: str | None = None
    target_desc: str = ""
    value: str | None = None
    key: str | None = None
    checked: bool | None = None
    declared_risk: str | None = None
    step_id: str | int | None = None
    screen: str | None = None
    why: str | None = None
    params: dict[str, str] = field(default_factory=dict)


@dataclass
class ActionOutcome:
    status: str  # ok | blocked | rejected | failed
    policy: dict[str, str]
    error: str | None = None
    effect: dict[str, Any] | None = None
    locators: list[Locator] = field(default_factory=list)
    dialog_opened: bool = False
    facts: dict[str, Any] | None = None


class Gateway:
    def __init__(self, runtime: "Runtime", run: "RunContext") -> None:
        self.rt = runtime
        self.run = run

    async def perform(self, intent: ActionIntent, *, mode: str, actor: str, record_locators: bool = False) -> ActionOutcome:
        rt, rec = self.rt, self.run.recorder
        rt.broker.require_automation_control()

        element = intent.element
        if element is None and intent.ref:
            element = await rt.surface.element_for_ref(intent.ref)
        if intent.action in ELEMENT_ACTIONS and element is None:
            rec.emit("action_executed", actor=actor, step=intent.step_id, ok=False, action=intent.action,
                     target=intent.target_desc, error="element is no longer on the screen",
                     summary=f"{actor} {intent.action} {intent.target_desc} ✗ element is no longer on the screen")
            return ActionOutcome("failed", {}, error="element is no longer on the screen (stale reference)")

        facts = await rt.surface.element_facts(element) if element is not None else None
        dialog = rt.surface.pending_dialog.message if rt.surface.pending_dialog else None
        decision = rt.policy_engine.decide(
            mode=mode, action=intent.action, element=facts, screen=intent.screen,
            dialog_text=dialog, key=intent.key, declared_risk=intent.declared_risk,
        )
        rec.emit(
            "policy_decision", actor=actor, step=intent.step_id,
            summary=f"policy {decision.decision}: {intent.action} {intent.target_desc or (dialog or '')} ({decision.risk}, rule {decision.rule})",
            action=intent.action, target=intent.target_desc, **decision.as_dict(),
        )
        if decision.blocked:
            return ActionOutcome("blocked", decision.as_dict(), error=decision.reason, facts=facts)
        if decision.needs_approval:
            granted, info = await rt.request_approval(self.run, intent, decision, mode)
            if not granted:
                return ActionOutcome("rejected", decision.as_dict(), error=info.get("note") or info.get("signal"), facts=facts)

        locators: list[Locator] = []
        if record_locators and intent.ref:
            locators = rt.clean_locators(self.run, await rt.surface.locator_candidates(intent.ref))

        real = rt.fill(intent.value, intent.params) if intent.value is not None else None
        surface = rt.surface
        action = intent.action
        if action == "click":
            result = await surface.click(element)
        elif action == "type":
            result = await surface.fill(element, real or "")
        elif action == "select":
            result = await surface.select(element, real or "")
        elif action == "check":
            result = await surface.set_checked(element, bool(intent.checked))
        elif action == "press_key":
            result = await surface.press(element, intent.key or "Enter")
        elif action == "accept_dialog":
            result = await surface.accept_dialog()
        elif action == "dismiss_dialog":
            result = await surface.dismiss_dialog()
        else:
            result = ActResult(ok=False, error=f"unsupported action {action!r}")

        effect = None
        if result.ok and action == "type" and element is not None:
            current = await surface.input_value(element)
            effect = {"value_matches": current == real}

        shown_value = f" value {intent.value}" if intent.value is not None else ""
        status = "" if result.ok else f" ✗ {result.error}"
        rec.emit(
            "action_executed", actor=actor, step=intent.step_id,
            summary=f"{actor} {action} {intent.target_desc}{shown_value}{status}" + (f' — "{intent.why}"' if intent.why else ""),
            action=action, target=intent.target_desc, value=intent.value, ok=result.ok, error=result.error,
            dialog_opened=result.dialog_opened, effect=effect, risk=decision.risk, screen=intent.screen, why=intent.why,
            locators=[loc.model_dump(exclude_none=True) for loc in locators],
        )
        return ActionOutcome(
            "ok" if result.ok else "failed", decision.as_dict(), error=result.error, effect=effect,
            locators=locators, dialog_opened=result.dialog_opened, facts=facts,
        )
