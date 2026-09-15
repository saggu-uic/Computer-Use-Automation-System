"""Control broker: who is in control of the live session, enforced.

AUTOMATION --trigger--> WAITING_FOR_HUMAN --take control--> HUMAN --resume--> VERIFYING
VERIFYING --check passes--> AUTOMATION ; VERIFYING --check fails--> WAITING_FOR_HUMAN
Abort / expiry end the run. Approvals wait in WAITING_FOR_HUMAN and return to AUTOMATION.
"""

from __future__ import annotations

import asyncio
import secrets
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable

from rote.evidence.recorder import RunRecorder, now_iso
from rote.models.intervention import Intervention


class ControlState(str, Enum):
    AUTOMATION = "AUTOMATION"
    WAITING_FOR_HUMAN = "WAITING_FOR_HUMAN"
    HUMAN = "HUMAN"
    VERIFYING = "VERIFYING"


LEGAL = {
    ControlState.AUTOMATION: {ControlState.WAITING_FOR_HUMAN},
    ControlState.WAITING_FOR_HUMAN: {ControlState.HUMAN, ControlState.AUTOMATION},
    ControlState.HUMAN: {ControlState.VERIFYING, ControlState.AUTOMATION},
    ControlState.VERIFYING: {ControlState.AUTOMATION, ControlState.WAITING_FOR_HUMAN},
}


class ControlNotHeld(RuntimeError):
    pass


class IllegalTransition(RuntimeError):
    pass


class Broker:
    def __init__(self, recorder: RunRecorder | None = None, *, expiry_s: int = 900) -> None:
        self.state = ControlState.AUTOMATION
        self.recorder = recorder
        self.expiry_s = expiry_s
        self.interventions: dict[str, Intervention] = {}
        self.human_actions: dict[str, list[dict[str, Any]]] = {}
        self._signals: dict[str, asyncio.Queue] = {}
        self.current: str | None = None
        self.on_state_change: list[Callable[[ControlState], Any]] = []

    def attach(self, recorder: RunRecorder) -> None:
        self.recorder = recorder

    # ---------------- enforcement ----------------
    def require_automation_control(self) -> None:
        if self.state != ControlState.AUTOMATION:
            raise ControlNotHeld(f"automation does not hold control (state {self.state.value})")

    def _transition(self, new: ControlState, actor: str, reason: str) -> None:
        if new not in LEGAL[self.state]:
            raise IllegalTransition(f"{self.state.value} -> {new.value} is not allowed")
        old = self.state
        self.state = new
        if self.recorder:
            self.recorder.emit(
                "control_transition",
                actor=actor,
                summary=f"control {old.value} → {new.value} ({reason})",
                **{"from": old.value, "to": new.value, "reason": reason, "intervention": self.current},
            )
        for callback in self.on_state_change:
            try:
                result = callback(new)
                if asyncio.iscoroutine(result):
                    asyncio.ensure_future(result)
            except Exception:  # noqa: BLE001
                pass

    # ---------------- automation side ----------------
    def new_intervention(self, **fields: Any) -> Intervention:
        """Builds a request. It becomes visible and actionable only when a run starts waiting on it
        (await_take_control / await_approval), atomically with the state change. Unattended runs never
        wait, so their routing-only requests never appear on the operator page."""
        now = datetime.now(timezone.utc)
        return Intervention(
            id=f"int_{secrets.token_hex(3)}",
            created_at=now_iso(),
            expires_at=(now + timedelta(seconds=self.expiry_s)).isoformat(timespec="seconds").replace("+00:00", "Z"),
            **fields,
        )

    def _publish(self, intervention: Intervention) -> None:
        if intervention.id not in self.interventions:
            self.interventions[intervention.id] = intervention
            self.human_actions[intervention.id] = []
            self._signals[intervention.id] = asyncio.Queue()

    async def _wait_signal(self, intervention: Intervention) -> tuple[str, dict[str, Any]]:
        try:
            return await asyncio.wait_for(self._signals[intervention.id].get(), timeout=self.expiry_s)
        except asyncio.TimeoutError:
            return "expired", {}

    async def await_take_control(self, intervention: Intervention) -> tuple[str, dict[str, Any]]:
        """Open a take-control intervention and wait until the operator resumes, aborts, or it expires.

        Returns ("resume", info) with state VERIFYING, or ("abort"|"expired", info)."""
        self.current = intervention.id
        if self.state == ControlState.AUTOMATION:
            # no await between these lines: the request is published and the state changes in one step
            self._transition(ControlState.WAITING_FOR_HUMAN, "automation", intervention.reason_code)
            self._publish(intervention)
            self._emit_intervention("intervention_opened", intervention)
        while True:
            signal, info = await self._wait_signal(intervention)
            if signal == "resume":
                return signal, info
            if signal in ("abort", "expired"):
                intervention.status = "aborted" if signal == "abort" else "expired"
                if self.state != ControlState.AUTOMATION:
                    self._transition(ControlState.AUTOMATION, info.get("operator", "system"), signal)
                self._emit_intervention("intervention_resolved", intervention)
                self.current = None
                return signal, info

    def verification_result(self, intervention: Intervention, ok: bool, message: str) -> None:
        if ok:
            intervention.status = "resolved"
            intervention.verification_message = message
            self._transition(ControlState.AUTOMATION, "automation", "resume verified")
            self._emit_intervention("intervention_resolved", intervention)
            self.current = None
        else:
            intervention.status = "open"
            intervention.verification_message = message
            self._transition(ControlState.WAITING_FOR_HUMAN, "automation", "resume check failed")
            if self.recorder:
                self.recorder.emit("resume_rejected", summary=f"resume check failed: {message}", intervention=intervention.id, message=message)

    async def await_approval(self, intervention: Intervention) -> tuple[str, dict[str, Any]]:
        """Returns ("approved"|"rejected"|"abort"|"expired", info). State is AUTOMATION again afterwards,
        except for "take_control", which leaves the state at HUMAN for the caller to continue."""
        self.current = intervention.id
        self._transition(ControlState.WAITING_FOR_HUMAN, "automation", intervention.reason_code)
        self._publish(intervention)
        self._emit_intervention("approval_requested", intervention)
        signal, info = await self._wait_signal(intervention)
        if signal == "take_control":
            return signal, info
        intervention.status = "resolved" if signal in ("approved", "rejected") else ("aborted" if signal == "abort" else "expired")
        intervention.resolution = signal
        if self.state != ControlState.AUTOMATION:
            self._transition(ControlState.AUTOMATION, info.get("operator", "system"), signal)
        self._emit_intervention("approval_resolved", intervention)
        self.current = None
        return signal, info

    def _emit_intervention(self, type_: str, intervention: Intervention) -> None:
        if not self.recorder:
            return
        summary = {
            "intervention_opened": f"intervention {intervention.id} opened: {intervention.reason_code}",
            "approval_requested": f"approval requested {intervention.id}: {intervention.proposed_action}",
            "approval_resolved": f"approval {intervention.id}: {intervention.resolution} by {intervention.operator}",
            "intervention_resolved": f"intervention {intervention.id} {intervention.status}",
        }[type_]
        self.recorder.emit(type_, actor=f"human:{intervention.operator}" if intervention.operator and type_.endswith("resolved") else "system",
                           summary=summary, intervention=intervention.model_dump())

    # ---------------- operator side ----------------
    def _get(self, intervention_id: str) -> Intervention:
        if intervention_id not in self.interventions:
            raise KeyError(intervention_id)
        return self.interventions[intervention_id]

    def take_control(self, intervention_id: str, operator: str) -> Intervention:
        intervention = self._get(intervention_id)
        if intervention.status not in ("open",) or self.state != ControlState.WAITING_FOR_HUMAN:
            raise IllegalTransition(f"cannot take control of {intervention_id} in state {self.state.value}/{intervention.status}")
        intervention.operator = operator or "operator"
        intervention.status = "in_progress"
        self._transition(ControlState.HUMAN, f"human:{intervention.operator}", "take control")
        if intervention.kind == "approve_action":
            self._signals[intervention_id].put_nowait(("take_control", {"operator": intervention.operator}))
        return intervention

    def resume(self, intervention_id: str, note: str = "") -> Intervention:
        intervention = self._get(intervention_id)
        if self.state != ControlState.HUMAN or intervention.status != "in_progress":
            raise IllegalTransition("resume is only possible after take control")
        intervention.note = note or None
        self._transition(ControlState.VERIFYING, f"human:{intervention.operator}", "resume")
        self._signals[intervention_id].put_nowait(("resume", {"operator": intervention.operator, "note": note}))
        return intervention

    def abort(self, intervention_id: str, operator: str = "operator", note: str = "") -> Intervention:
        intervention = self._get(intervention_id)
        if intervention.status in ("resolved", "aborted", "expired"):
            raise IllegalTransition(f"intervention {intervention_id} is already {intervention.status}")
        intervention.operator = intervention.operator or operator
        intervention.note = note or intervention.note
        self._signals[intervention_id].put_nowait(("abort", {"operator": intervention.operator, "note": note}))
        return intervention

    def approve(self, intervention_id: str, operator: str) -> Intervention:
        return self._decide(intervention_id, operator, "approved", "")

    def reject(self, intervention_id: str, operator: str, note: str = "") -> Intervention:
        return self._decide(intervention_id, operator, "rejected", note)

    def _decide(self, intervention_id: str, operator: str, decision: str, note: str) -> Intervention:
        intervention = self._get(intervention_id)
        if intervention.kind != "approve_action" or intervention.status != "open":
            raise IllegalTransition(f"{intervention_id} is not an open approval request")
        intervention.operator = operator or "operator"
        intervention.note = note or None
        self._signals[intervention_id].put_nowait((decision, {"operator": intervention.operator, "note": note}))
        return intervention

    def record_human_action(self, payload: dict[str, Any]) -> bool:
        """Called for every UI event from the page; only kept while a human holds control."""
        if self.state != ControlState.HUMAN or not self.current:
            return False
        intervention = self.interventions[self.current]
        self.human_actions[self.current].append(payload)
        if self.recorder:
            what = payload.get("name") or payload.get("label") or payload.get("route") or ""
            self.recorder.emit(
                "human_action",
                actor=f"human:{intervention.operator}",
                summary=f"human {payload.get('type')} {payload.get('role') or ''} \"{what}\"".replace("  ", " "),
                intervention=intervention.id,
                action=payload,
            )
        return True

    def open_interventions(self) -> list[Intervention]:
        return [i for i in self.interventions.values() if i.status in ("open", "in_progress")]
