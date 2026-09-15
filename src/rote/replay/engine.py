"""Deterministic replay and the step runner shared with sign-on and discovery.

No model is consulted here. Every branch the application can take is declared (step expectations,
product-wide global screens); anything undeclared becomes a clear failure or a human escalation.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from playwright.async_api import Error as PlaywrightError

from rote.gateway import ActionIntent, Gateway
from rote.handoff.broker import ControlNotHeld
from rote.models import Capability, CapabilityResult, ErrorInfo, OutcomeInfo, ResumeExpectation, Screen, Step, Target
from rote.models.capability import OutputSpec
from rote.models.common import DataClass
from rote.registry.store import content_hash
from rote.replay import matcher
from rote.replay.parsing import ParseError, parse_table, parse_value
from rote.replay.preflight import preflight
from rote.surface.web import Resolution

if TYPE_CHECKING:
    from rote.runtime import RunContext, Runtime

MAX_RECOVERIES = 3
POLL_S = 0.15
MODAL_KINDS = {"interstitial", "needs_human"}
SENSITIVE = {DataClass.pii, DataClass.financial, DataClass.secret}


class StepFailure(Exception):
    def __init__(
        self,
        code: str,
        *,
        step: Step | None = None,
        expected: str | None = None,
        observed: str | None = None,
        message: str = "",
        phase: str = "execution",
        intervention: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{code}: {message or observed or ''}")
        self.code = code
        self.step_id = step.id if step else None
        self.expected = expected
        self.observed = observed
        self.message = message
        self.phase = phase
        self.intervention = intervention


class BusinessOutcome(Exception):
    def __init__(self, code: str, step_id: str | None, message: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.step_id = step_id
        self.message = message


class RestartRun(Exception):
    """Session was renewed; start again from the start screen (only allowed before any irreversible step)."""


@dataclass
class RunState:
    irreversible_attempted: bool = False
    recoveries: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    interventions: list[dict[str, Any]] = field(default_factory=list)
    outputs: dict[str, Any] = field(default_factory=dict)
    handler_counts: dict[str, int] = field(default_factory=dict)
    restarts: int = 0
    current_screen: str | None = None
    checkpoints: int = 0


def mask_output(spec: OutputSpec | None, value: Any) -> Any:
    if spec is None or spec.classification not in SENSITIVE:
        return value
    if isinstance(value, list):
        return f"«{len(value)} rows, {spec.classification.value}»"
    return f"«{spec.classification.value}»"


class StepRunner:
    def __init__(
        self,
        runtime: "Runtime",
        run: "RunContext",
        *,
        mode: str,
        attended: bool,
        subject: str,
        state: RunState | None = None,
        outputs: dict[str, OutputSpec] | None = None,
    ) -> None:
        self.rt = runtime
        self.run = run
        self.mode = mode  # replay | session | discovery
        self.attended = attended
        self.subject = subject
        self.state = state or RunState()
        self.outputs = outputs or {}
        self.gateway = Gateway(runtime, run)
        self.profile = runtime.profile

    @property
    def surface(self):
        return self.rt.surface

    @property
    def policy_mode(self) -> str:
        return {"session": "session", "discovery": "discovery"}.get(self.mode, "replay")

    @property
    def actor(self) -> str:
        return {"session": "session", "discovery": "agent"}.get(self.mode, "replay")

    # ---------------- probing ----------------
    async def probe(self) -> matcher.Probe:
        probe = await self.surface.probe()
        dialog = self.surface.pending_dialog
        if dialog is not None:
            probe["__native__"] = {"url": "", "headings": [], "text": "", "dialogs": [matcher.norm(dialog.message)], "ready": "complete"}
        return probe

    def observed(self, probe: matcher.Probe) -> str:
        main = probe.get("main") or {}
        parts = [f"route {urlparse(main.get('url', '')).path or '?'}"]
        if main.get("headings"):
            parts.append("headings " + " / ".join(main["headings"]))
        dialogs = matcher.all_dialogs(probe)
        if dialogs:
            parts.append("dialogs " + " / ".join(d[:80] for d in dialogs))
        if not main.get("headings") and main.get("text"):
            parts.append("text " + main["text"][:120])
        return self.run.masker.mask_text("; ".join(parts))

    def _global_hit(self, probe: matcher.Probe, params: dict[str, str], kinds: set[str]) -> tuple[str, Screen] | None:
        for name, screen in self.profile.global_screens.items():
            if screen.kind in kinds and matcher.screen_matches(screen, probe, params):
                return name, screen
        return None

    def _unknown_dialogs(self, probe: matcher.Probe, include_native: bool = True) -> list[str]:
        known = [k for s in self.profile.global_screens.values() for k in matcher.dialog_predicates(s)]
        unknown = []
        for name, frame in probe.items():
            if name == "__native__" and not include_native:
                continue
            for dialog in frame.get("dialogs", []):
                if not any(matcher.norm(k) in matcher.norm(dialog) for k in known):
                    unknown.append(dialog)
        return unknown

    def _login_visible(self, probe: matcher.Probe) -> bool:
        screens = [s for s in self.profile.global_screens.values() if s.kind == "session_lost"]
        screens += [self.profile.screen_catalog[k] for k in ("sign_on",) if k in self.profile.screen_catalog]
        return any(matcher.screen_matches(s, probe, {}) for s in screens)

    def _record_recovery(self, code: str, step_id: str | None, detail: str = "") -> None:
        if self.mode == "replay" and len(self.state.recoveries) >= MAX_RECOVERIES:
            raise StepFailure("RECOVERY_BUDGET_EXCEEDED", message=f"more than {MAX_RECOVERIES} recoveries in one run")
        item = {"code": code, "step_id": step_id}
        self.state.recoveries.append(item)
        self.run.recorder.emit(
            "recovery_applied", actor="recovery", step=step_id,
            summary=f"recovery: {code}" + (f" ({detail})" if detail else ""), **item,
        )

    # ---------------- session ----------------
    async def wait_main(self, timeout_s: float = 10.0) -> matcher.Probe:
        end = time.monotonic() + timeout_s
        probe = await self.probe()
        while time.monotonic() < end:
            main = probe.get("main")
            if main and (main.get("headings") or main.get("text")) and main.get("ready") == "complete":
                return probe
            await asyncio.sleep(POLL_S)
            probe = await self.probe()
        return probe

    async def ensure_session(self) -> None:
        probe = await self.probe()
        main = probe.get("main")
        if not main or not (main.get("url") or "").startswith(self.rt.origin):
            await self.surface.goto(self.rt.origin + self.profile.entry.home_path)
            probe = await self.wait_main()
        if self._login_visible(probe):
            await self.login()

    async def login(self) -> None:
        runner = StepRunner(self.rt, self.run, mode="session", attended=False, subject="sign on")
        runner.state.current_screen = "sign_on"
        for step in self.profile.session.login_steps:
            await runner.run_step(step, {}, self.profile.shared_targets, self.profile.screen_catalog, 8000)
        self.run.recorder.emit(
            "session_login", actor="session",
            summary="signed on (credentials typed by code from the profile; never shown to any model)",
        )

    async def wait_for(self, screen: Screen, params: dict[str, str], timeout_s: float) -> bool:
        end = time.monotonic() + timeout_s
        while True:
            if matcher.screen_matches(screen, await self.probe(), params):
                return True
            if time.monotonic() >= end:
                return False
            await asyncio.sleep(POLL_S)

    async def ensure_screen(self, screen_id: str, screens: dict[str, Screen], params: dict[str, str]) -> None:
        screen = screens[screen_id]
        if not await self.wait_for(screen, params, 1.5):
            await self.surface.goto(self.rt.origin + self.profile.entry.home_path)
            probe = await self.wait_main()
            if self._login_visible(probe):
                await self.login()
            if not await self.wait_for(screen, params, 6.0):
                probe = await self.probe()
                raise StepFailure(
                    "UNEXPECTED_SCREEN", expected=screen_id, observed=self.observed(probe),
                    message="could not reach the start screen",
                )
        self.state.current_screen = screen_id

    # ---------------- overlays, escalation ----------------
    async def clear_modal_globals(self, step: Step | None, params: dict[str, str], screens: dict[str, Screen], expected: list[str]) -> None:
        for _ in range(5):
            probe = await self.probe()
            hit = self._global_hit(probe, params, MODAL_KINDS)
            if hit:
                await self._handle_modal(hit, step, params, screens, expected, probe)
                continue
            unknown = self._unknown_dialogs(probe, include_native=self.mode != "discovery")
            if unknown and self.mode != "session":
                await self._unexpected(step, probe, screens, expected, params, f"unknown dialog: {unknown[0][:80]}")
                continue
            return
        raise StepFailure("RECOVERY_BUDGET_EXCEEDED", step=step, message="overlays kept appearing")

    async def _handle_modal(self, hit: tuple[str, Screen], step: Step | None, params: dict[str, str], screens: dict[str, Screen], expected: list[str], probe: matcher.Probe) -> None:
        name, screen = hit
        step_id = step.id if step else None
        if screen.kind == "interstitial":
            used = self.state.handler_counts.get(name, 0)
            if screen.max_per_run is not None and used >= screen.max_per_run:
                raise StepFailure(
                    "RECOVERY_BUDGET_EXCEEDED", step=step, observed=self.observed(probe),
                    message=f"{name} appeared more than {screen.max_per_run} times",
                )
            self.state.handler_counts[name] = used + 1
            handler = screen.handler if isinstance(screen.handler, dict) else {}
            target = self.profile.shared_targets.get(handler.get("dismiss", ""))
            if target is None:
                raise StepFailure("ARTIFACT_INVALID", step=step, message=f"global screen {name} has no dismiss target")
            res = await self.surface.resolve(target, params)
            if not res.found:
                raise StepFailure("TARGET_NOT_FOUND", step=step, expected=target.description, observed=res.reason)
            outcome = await self.gateway.perform(
                ActionIntent(action="click", element=res.element, target_desc=target.description, declared_risk="safe", step_id=step_id, screen=name),
                mode=self.policy_mode, actor="recovery",
            )
            if outcome.status != "ok":
                raise StepFailure("TARGET_NOT_ACTIONABLE", step=step, expected=target.description, observed=outcome.error)
            await asyncio.sleep(0.2)
            self._record_recovery(f"{name.upper()}_DISMISSED", step_id, screen.description or "")
        else:
            await self.escalate(
                screen.escalate or "HUMAN_REQUIRED", screen.description or f"{name} needs a person",
                step, screens, expected, params,
            )

    async def _unexpected(self, step: Step | None, probe: matcher.Probe, screens: dict[str, Screen], expected: list[str], params: dict[str, str], reason: str) -> None:
        if self.attended:
            await self.escalate("UNEXPECTED_SCREEN", reason, step, screens, expected, params)
            return
        raise StepFailure(
            "UNEXPECTED_SCREEN", step=step, expected=" | ".join(e for e in expected if e) or None,
            observed=self.observed(probe), message=reason,
        )

    async def escalate(self, reason_code: str, reason: str, step: Step | None, screens: dict[str, Screen], expected: list[str], params: dict[str, str]) -> None:
        expected = [e for e in expected if e]
        probe = await self.probe()
        snap = await self.surface.snapshot()
        broker = self.rt.broker
        live = self.attended and self.rt.operator is not None
        intervention = broker.new_intervention(
            kind="take_control",
            run_id=self.run.recorder.run_id,
            mode=f"{self.mode}_{'attended' if self.attended else 'unattended'}",
            capability=self.subject if self.mode != "discovery" else None,
            goal=self.subject if self.mode == "discovery" else None,
            step={"id": step.id, "action": step.action, "description": step.description or step.target or ""} if step else {},
            reason_code=reason_code,
            reason=self.run.masker.mask_text(reason),
            screen_summary=self.observed(probe),
            resume_expectation=ResumeExpectation(
                after_step=step.id if step else None,
                expected_screens=expected,
                instructions=(
                    "Resolve this in the browser window, leave the application on "
                    + (" or ".join(expected) if expected else "a screen where the task can continue")
                    + ", then press Resume."
                ),
            ),
        )
        intervention.screenshot = await self.rt.masked_screenshot(self.run, f"interventions/{intervention.id}/before.png", snap)
        if not live:
            self.run.recorder.write_json(f"interventions/{intervention.id}/request.json", intervention.model_dump())
            self.run.recorder.emit("intervention_opened", summary=f"intervention {intervention.id} raised for routing (unattended): {reason_code}",
                                   intervention=intervention.model_dump())
            raise StepFailure(
                "UNEXPECTED_SCREEN" if reason_code == "UNEXPECTED_SCREEN" else "HUMAN_REQUIRED",
                step=step, expected=" | ".join(expected) or None, observed=self.observed(probe), message=intervention.reason,
                intervention=intervention.model_dump(include={"id", "kind", "reason_code", "reason", "resume_expectation", "screenshot", "created_at"}),
            )
        self.rt.announce(intervention, self.rt.operator_url)
        started = time.monotonic()
        while True:
            signal, info = await broker.await_take_control(intervention)
            if signal in ("abort", "expired"):
                self._close_intervention(intervention, started, "aborted" if signal == "abort" else "expired")
                raise StepFailure(
                    "HUMAN_ABORTED" if signal == "abort" else "HUMAN_TIMEOUT", step=step,
                    message=info.get("note") or f"intervention {signal}",
                )
            await asyncio.sleep(0.3)
            await self.rt.masked_screenshot(self.run, f"interventions/{intervention.id}/after_human.png")
            probe = await self.probe()
            matched = [s for s in expected if s in screens and matcher.screen_matches(screens[s], probe, params)]
            blocking = self._global_hit(probe, params, MODAL_KINDS) or self._unknown_dialogs(probe)
            if (matched or not expected) and not blocking:
                broker.verification_result(intervention, True, f"resume check passed: {matched[0] if matched else 'no expectation'}")
                break
            broker.verification_result(intervention, False, f"expected {' | '.join(expected) or 'no overlays'}; observed {self.observed(probe)}")
        self._close_intervention(intervention, started, "resolved")

    def _close_intervention(self, intervention, started: float, resolution: str) -> None:
        actions = self.rt.broker.human_actions.get(intervention.id, [])
        rec = self.run.recorder
        base = f"interventions/{intervention.id}"
        rec.write_json(f"{base}/request.json", intervention.model_dump())
        rec.write_text(f"{base}/human_actions.jsonl", "\n".join(json.dumps(a, default=str) for a in actions))
        duration = int((time.monotonic() - started) * 1000)
        rec.write_json(f"{base}/resolution.json", {
            "resolution": resolution, "operator": intervention.operator, "note": intervention.note,
            "verification": intervention.verification_message, "human_actions": len(actions), "duration_ms": duration,
        })
        self.state.interventions.append({
            "id": intervention.id, "kind": intervention.kind, "reason_code": intervention.reason_code,
            "resolution": resolution, "operator": intervention.operator, "human_actions": len(actions), "duration_ms": duration,
        })

    # ---------------- terminal states ----------------
    async def _handle_terminal(self, hit: tuple[str, Screen], step: Step | None, probe: matcher.Probe) -> None:
        name, screen = hit
        if screen.kind == "app_error":
            raise StepFailure(screen.fail or "APP_ERROR", step=step, observed=self.observed(probe), message=screen.description or name)
        if screen.kind == "session_lost":
            if self.mode == "session":
                return
            if self.state.irreversible_attempted:
                raise StepFailure("OUTCOME_UNKNOWN", step=step, observed=self.observed(probe),
                                  message="session lost after an irreversible step was attempted; not retried")
            used = self.state.handler_counts.get(name, 0)
            if screen.max_per_run is not None and used >= screen.max_per_run:
                raise StepFailure("RECOVERY_BUDGET_EXCEEDED", step=step, observed=self.observed(probe), message="session lost again")
            self.state.handler_counts[name] = used + 1
            self._record_recovery("SESSION_RENEWED_RESTARTED", step.id if step else None, "re-login, restart from the start screen")
            await self.login()
            raise RestartRun()

    async def _known_state_or_fail(self, step: Step, probe: matcher.Probe, params: dict[str, str], screens: dict[str, Screen], target: Target, res: Resolution) -> None:
        for screen in screens.values():
            if screen.kind == "outcome" and screen.outcome and matcher.screen_matches(screen, probe, params):
                raise BusinessOutcome(screen.outcome, step.id, screen.description or "")
        hit = self._global_hit(probe, params, {"app_error", "session_lost"})
        if hit and not (hit[1].kind == "session_lost" and self.mode == "session"):
            await self._handle_terminal(hit, step, probe)
        if self._global_hit(probe, params, MODAL_KINDS) or self._unknown_dialogs(probe):
            await self.clear_modal_globals(step, params, screens, [self.state.current_screen] if self.state.current_screen else [])
            return
        raise StepFailure("TARGET_NOT_FOUND", step=step, expected=target.description, observed=f"{self.observed(probe)}; {res.reason}")

    # ---------------- steps ----------------
    async def resolve_with_wait(self, target: Target, params: dict[str, str], timeout_ms: int, screens: dict[str, Screen]) -> Resolution:
        end = time.monotonic() + timeout_ms / 1000
        while True:
            res = await self.surface.resolve(target, params)
            if res.found or res.ambiguous or time.monotonic() >= end:
                return res
            probe = await self.probe()
            if any(s.kind == "outcome" and matcher.screen_matches(s, probe, params) for s in screens.values()):
                return res
            if self._global_hit(probe, params, {"app_error", "session_lost"} | MODAL_KINDS) or self._unknown_dialogs(probe):
                return res
            if res.table_reason == "no_row":
                return res
            await asyncio.sleep(0.2)

    async def run_step(self, step: Step, params: dict[str, str], targets: dict[str, Target], screens: dict[str, Screen], default_timeout_ms: int) -> None:
        timeout_ms = step.timeout_ms or default_timeout_ms
        rec = self.run.recorder
        if step.action not in ("accept_dialog", "dismiss_dialog") and self.mode != "session":
            await self.clear_modal_globals(step, params, screens, [self.state.current_screen] if self.state.current_screen else [])
        if step.action == "wait":
            await asyncio.sleep(min(timeout_ms, 5000) / 1000)
            return
        target = targets.get(step.target) if step.target else None
        outcome = None
        intent = None
        for attempt in (1, 2):
            element = None
            res = None
            if target is not None:
                res = await self.resolve_with_wait(target, params, timeout_ms, screens)
                if res.ambiguous:
                    raise StepFailure("TARGET_AMBIGUOUS", step=step, expected=target.description, observed=res.reason)
                if not res.found:
                    if step.action == "extract" and step.if_missing and res.table_reason == "no_row":
                        raise BusinessOutcome(step.if_missing.then.outcome, step.id, "row not present")
                    await self._known_state_or_fail(step, await self.probe(), params, screens, target, res)
                    continue
                if res.rank and res.rank > 1:
                    warning = {"code": "LOCATOR_FALLBACK_USED", "step_id": step.id, "target": step.target, "rank": res.rank}
                    self.state.warnings.append(warning)
                    rec.emit("locator_fallback_used", step=step.id, summary=f"warning: {step.target} resolved by fallback locator #{res.rank}", **warning)
                element = res.element
            if step.action == "extract":
                self.state.outputs[step.into] = await self._extract(step, element)
                return
            desc = target.description if target is not None and target.description else (res.locator.describe() if res and res.locator else step.action)
            intent = ActionIntent(
                action=step.action, element=element, target_desc=desc, value=step.value, checked=step.checked,
                key=step.key, declared_risk=step.risk, step_id=step.id, screen=self.state.current_screen, params=params,
            )
            outcome = await self.gateway.perform(intent, mode=self.policy_mode, actor=self.actor)
            if outcome.status == "failed" and attempt == 1:
                probe = await self.probe()
                if self._global_hit(probe, params, MODAL_KINDS) or self._unknown_dialogs(probe):
                    await self.clear_modal_globals(step, params, screens, [self.state.current_screen] if self.state.current_screen else [])
                    continue
            break
        else:
            raise StepFailure("TARGET_NOT_FOUND", step=step, expected=target.description if target else None,
                              message="target still not available after handling overlays")
        if outcome.status == "blocked":
            raise StepFailure("POLICY_BLOCKED", step=step, expected=intent.target_desc, message=outcome.error or "")
        if outcome.status == "rejected":
            raise StepFailure("HUMAN_REJECTED_ACTION", step=step, expected=intent.target_desc, message=outcome.error or "")
        if outcome.status == "failed":
            raise StepFailure("TARGET_NOT_ACTIONABLE", step=step, expected=intent.target_desc, observed=outcome.error)
        if step.risk == "irreversible":
            self.state.irreversible_attempted = True
        if step.verify == "value_matches" and outcome.effect and outcome.effect.get("value_matches") is False:
            raise StepFailure("TARGET_NOT_ACTIONABLE", step=step, expected=intent.target_desc, message="the field did not keep the typed value")
        if step.expect:
            await self.await_expectations(step, params, screens, timeout_ms)

    async def await_expectations(self, step: Step, params: dict[str, str], screens: dict[str, Screen], timeout_ms: int) -> None:
        end = time.monotonic() + timeout_ms / 1000
        extended = False
        expected = [c.screen for c in step.expect]
        continue_screens = [c.screen for c in step.expect if c.then == "continue"]
        while True:
            probe = await self.probe()
            hit = self._global_hit(probe, params, MODAL_KINDS)
            if hit and self.mode != "session":
                await self._handle_modal(hit, step, params, screens, expected, probe)
                continue
            unknown = self._unknown_dialogs(probe)
            if unknown and self.mode != "session" and not (step.risk == "irreversible" and self.surface.pending_dialog):
                await self._unexpected(step, probe, screens, expected, params, f"unknown dialog: {unknown[0][:80]}")
                continue
            # Only accept a screen once the document is fully parsed: an overlay at the end of the page
            # (supervisor override, notices) must be seen before the page underneath counts as "reached".
            main_ready = (probe.get("main") or {}).get("ready", "complete") == "complete"
            for case in (step.expect if main_ready else []):
                screen = screens[case.screen]
                if not matcher.screen_matches(screen, probe, params):
                    continue
                then = case.then if case.then == "continue" else (case.then.outcome or case.then.fail or case.then.escalate)
                self.run.recorder.emit("state_matched", actor=self.actor, step=step.id, summary=f"{step.id}: screen {case.screen} → {then}", screen=case.screen, then=then)
                if extended:
                    self._record_recovery("SLOW_LOAD_WAITED", step.id, "loading indicator was still visible at the step timeout")
                if case.then == "continue":
                    self.state.current_screen = case.screen
                    await self._checkpoint(step)
                    return
                if case.then.outcome:
                    raise BusinessOutcome(case.then.outcome, step.id, screen.description or "")
                if case.then.fail:
                    raise StepFailure(case.then.fail, step=step, observed=self.observed(probe), message=screen.description or "")
                await self.escalate(case.then.escalate, screen.description or case.screen, step, screens, continue_screens, params)
                break
            terminal = self._global_hit(probe, params, {"app_error", "session_lost"})
            if terminal and not (terminal[1].kind == "session_lost" and self.mode == "session"):
                await self._handle_terminal(terminal, step, probe)
            is_loading = self._global_hit(probe, params, {"transient"}) is not None or matcher.loading(probe)
            if time.monotonic() >= end:
                if step.risk == "irreversible":
                    raise StepFailure("OUTCOME_UNKNOWN", step=step, expected=" | ".join(expected), observed=self.observed(probe),
                                      message="no confirmation after an irreversible step; never retried automatically")
                if is_loading and not extended:
                    extended = True
                    end = time.monotonic() + timeout_ms / 1000
                    self.run.recorder.emit("slow_load", step=step.id, summary=f"{step.id}: still loading after {timeout_ms} ms; waiting once more")
                    continue
                if is_loading:
                    raise StepFailure("CHECKPOINT_TIMEOUT", step=step, expected=" | ".join(expected), observed=self.observed(probe),
                                      message=f"still loading after {2 * timeout_ms} ms")
                await self._unexpected(step, probe, screens, expected, params, "none of the expected screens appeared")
                end = time.monotonic() + timeout_ms / 1000
                continue
            await asyncio.sleep(POLL_S)

    async def _checkpoint(self, step: Step) -> None:
        if self.mode != "replay":
            return
        self.state.checkpoints += 1
        await self.rt.masked_screenshot(self.run, f"screens/{self.state.checkpoints:02d}_{step.id}.png")

    async def _extract(self, step: Step, element) -> Any:
        spec = self.outputs.get(step.into)
        try:
            if step.parse == "table":
                raw = await self.surface.read_table(element)
                value = parse_table(raw, step.columns, spec.max_rows if spec else None)
            else:
                value = parse_value(step.parse, await self.surface.read_text(element))
        except ParseError as exc:
            raise StepFailure("OUTPUT_PARSE_ERROR", step=step, message=str(exc)) from exc
        shown = mask_output(spec, value)
        self.run.recorder.emit(
            "output_extracted", actor=self.actor, step=step.id,
            summary=f"extracted {step.into} ({step.parse}): {shown if not isinstance(shown, (list, dict)) else '«structured»'}",
            output=step.into, type=step.parse, value=shown,
        )
        return value


class ReplayEngine:
    def __init__(self, runtime: "Runtime", capability: Capability, *, attended: bool = False, kind: str = "replay") -> None:
        self.rt = runtime
        self.cap = capability
        self.attended = attended
        self.kind = kind

    async def run(self, inputs: dict[str, Any]) -> CapabilityResult:
        rt, cap = self.rt, self.cap
        inputs = {k: str(v) for k, v in inputs.items()}
        params = {**cap.constants, **inputs}
        run = rt.new_run(self.kind, subject=cap.ref, params=params, attended=self.attended)
        rec = run.recorder
        rec.emit(
            "run_started",
            summary=f"{self.kind} {cap.ref} ({'attended' if self.attended else 'unattended'}) · no model in the loop",
            capability=cap.ref, content_hash=content_hash(cap), inputs=inputs, attended=self.attended,
        )
        state = RunState()
        runner = StepRunner(rt, run, mode="replay", attended=self.attended, subject=cap.ref, state=state, outputs=cap.contract.outputs)
        error = preflight(cap, inputs, rt.profile, rt.policy, attended=self.attended)
        status, outcome, ui_touched = "failure", None, False
        if error is None:
            ui_touched = True
            try:
                if self.attended:
                    await self._execute(runner, params)
                else:
                    await asyncio.wait_for(self._execute(runner, params), timeout=cap.timeouts.run_max_ms / 1000)
                await self._check_success(runner, params)
                status = "success"
            except BusinessOutcome as bo:
                status = "business_outcome"
                described = next((o.description for o in cap.contract.outcomes if o.code == bo.code), "")
                outcome = OutcomeInfo(code=bo.code, message=described or bo.message, at_step=bo.step_id)
            except StepFailure as sf:
                error = ErrorInfo.make(
                    sf.code, sf.phase, step_id=sf.step_id, expected=sf.expected, observed=sf.observed,
                    message=sf.message, intervention=sf.intervention,
                )
            except asyncio.TimeoutError:
                error = ErrorInfo.make("RUN_TIMEOUT", "execution", message=f"run exceeded {cap.timeouts.run_max_ms} ms")
            except (PlaywrightError, ControlNotHeld) as exc:
                error = ErrorInfo.make("INFRA_ERROR", "execution", message=str(exc).splitlines()[0][:200])
        else:
            rec.emit("preflight_failed", summary=f"pre-flight refused: {error.code} (UI not touched)", error=error.model_dump())
        if error is not None and ui_touched:
            error.evidence = await self._failure_bundle(run, error)
        if ui_touched:
            await rt.masked_screenshot(run, "screens/final.png")
            await park_browser(rt)
        result = CapabilityResult(
            invocation_id=rec.run_id,
            capability=cap.ref,
            content_hash=content_hash(cap),
            profile_hash=cap.app.profile.hash,
            mode="attended" if self.attended else "unattended",
            status=status,
            outputs=state.outputs if status == "success" else {},
            outcome=outcome,
            error=error,
            recoveries=state.recoveries,
            warnings=state.warnings,
            interventions=state.interventions,
            ui_touched=ui_touched,
            side_effects_possible=state.irreversible_attempted,
            timing_ms={"total": rec.elapsed_ms()},
            evidence=f"runs/{rec.run_id}",
        )
        masked = self.masked_result(result)
        rec.write_json("result.json", masked)
        code = outcome.code if outcome else (error.code if error else "")
        rec.emit("run_finished", summary=f"result: {status} {code}".strip(), status=status, code=code)
        rec.finish({"capability": cap.ref, "status": status, "code": code or None, "inputs": inputs, "result": masked})
        return result

    async def _execute(self, runner: StepRunner, params: dict[str, str]) -> None:
        cap = self.cap
        while True:
            try:
                await runner.ensure_session()
                await runner.ensure_screen(cap.start.screen, cap.screens, params)
                for step in cap.steps:
                    await runner.run_step(step, params, cap.targets, cap.screens, cap.timeouts.step_default_ms)
                return
            except RestartRun:
                runner.state.restarts += 1
                runner.state.outputs.clear()
                runner.state.current_screen = None
                if runner.state.restarts > 2:
                    raise StepFailure("RECOVERY_BUDGET_EXCEEDED", message="restarted more than twice")

    async def _check_success(self, runner: StepRunner, params: dict[str, str]) -> None:
        probe = await runner.probe()
        for cond in self.cap.success.all:
            if cond.screen and not matcher.screen_matches(self.cap.screens[cond.screen], probe, params):
                raise StepFailure("SUCCESS_CONDITION_FAILED", phase="verification", expected=cond.screen, observed=runner.observed(probe))
            if cond.output_valid and runner.state.outputs.get(cond.output_valid) in (None, "", []):
                if self.cap.contract.outputs[cond.output_valid].type != "table":
                    raise StepFailure("SUCCESS_CONDITION_FAILED", phase="verification", expected=f"valid {cond.output_valid}", observed="missing")

    async def _failure_bundle(self, run: "RunContext", error: ErrorInfo) -> dict[str, str]:
        rec = run.recorder
        # paths are relative to the run folder, so they stay valid when a run is copied into evidence/
        bundle = {"events": "events.jsonl"}
        try:
            snap = await self.rt.surface.snapshot()
            if await self.rt.masked_screenshot(run, "failure/screen.png", snap):
                bundle["screenshot"] = "failure/screen.png"
            rec.write_text("failure/snapshot.txt", run.masker.render(snap))
            bundle["snapshot"] = "failure/snapshot.txt"
        except Exception:  # noqa: BLE001
            pass
        rec.write_json("failure/context.json", error.model_dump())
        return bundle

    def masked_result(self, result: CapabilityResult) -> dict[str, Any]:
        data = result.model_dump(mode="json")
        data["outputs"] = {name: mask_output(self.cap.contract.outputs.get(name), value) for name, value in result.outputs.items()}
        return data


async def park_browser(runtime: "Runtime") -> None:
    """End every run on a blank page: no page timers or half-finished screens leak into the next run,
    which always starts again from the entry point (the session cookie is kept)."""
    try:
        await runtime.surface.goto("about:blank")
    except PlaywrightError:
        pass


async def invoke(runtime: "Runtime", ref: str, inputs: dict[str, Any], *, attended: bool = False) -> CapabilityResult:
    capability, _path = runtime.catalog.load_capability(ref)
    return await ReplayEngine(runtime, capability, attended=attended).run(inputs)
