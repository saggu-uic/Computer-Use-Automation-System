"""LLM-driven discovery: observe -> decide -> act until done, cannot_complete, a limit, or escalation."""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rote.agent.inputs import KIND_TYPES, InputBinding
from rote.agent.planners.base import Planner, TurnContext
from rote.agent.tools import REF_TOOLS, TOOL_NAMES, validate_args
from rote.gateway import ActionIntent
from rote.handoff.broker import ControlNotHeld
from rote.models.common import DataClass
from rote.replay import matcher
from rote.replay.engine import RestartRun, StepFailure, StepRunner, park_browser
from rote.replay.parsing import ParseError, infer_columns, parse_table, parse_value, snake
from rote.surface.snapshot import CellNode, TableNode

if TYPE_CHECKING:
    from rote.runtime import RunContext, Runtime

TOOL_ACTIONS = {
    "click": "click", "type_text": "type", "select_option": "select", "set_checkbox": "check",
    "press_key": "press_key", "accept_dialog": "accept_dialog", "dismiss_dialog": "dismiss_dialog",
}
SENSITIVE = {DataClass.pii, DataClass.financial, DataClass.secret}


@dataclass
class DiscoveryLimits:
    max_steps: int = 30
    max_minutes: float = 10.0
    max_cost_usd: float = 0.50


@dataclass
class DiscoveryResult:
    run_dir: Path
    run_id: str
    status: str  # completed | GOAL_NOT_ACHIEVABLE | failed
    code: str | None
    message: str
    capability_name: str | None
    params: dict[str, str]
    outputs: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)


class DiscoveryAgent:
    def __init__(
        self,
        runtime: "Runtime",
        planner: Planner,
        *,
        goal_template: str,
        bindings: list[InputBinding],
        outputs: dict[str, str] | None = None,
        limits: DiscoveryLimits | None = None,
        name: str | None = None,
    ) -> None:
        self.rt = runtime
        self.planner = planner
        self.goal = goal_template
        self.bindings = bindings
        self.outputs = outputs or {}
        self.limits = limits or DiscoveryLimits()
        self.name = name

    async def run(self) -> DiscoveryResult:
        rt = self.rt
        params = {b.placeholder: b.value for b in self.bindings}
        run = rt.new_run("discovery", subject=self.goal, params=params, attended=True)
        rec = run.recorder
        planner_info = {"provider": self.planner.provider, "model": self.planner.model}
        rec.emit(
            "run_started", actor="system",
            summary=f'discovery · goal "{self.goal}" · target {rt.profile.display_name} {rt.origin} · planner {planner_info["provider"]}/{planner_info["model"]}',
            goal=self.goal, target=rt.origin, product=rt.profile.product, planner=planner_info,
            inputs={b.placeholder: b.kind for b in self.bindings}, outputs=self.outputs,
        )
        runner = StepRunner(rt, run, mode="discovery", attended=True, subject=self.goal)
        history: list[str] = []
        feedback = "(start)"
        extracted: dict[str, dict[str, Any]] = {}
        status, code, message = "failed", None, ""
        capability_name = self.name
        strikes = blocks = done_rejects = no_effect = 0
        seen: Counter[str] = Counter()
        outputs_at_seen: dict[str, int] = {}
        started = time.monotonic()
        catalog = rt.profile.screen_catalog
        try:
            await runner.ensure_session()
            await runner.ensure_screen(rt.profile.entry.home_screen, catalog, {})
            for step in range(1, self.limits.max_steps + 1):
                if time.monotonic() - started > self.limits.max_minutes * 60:
                    code, message = "DISCOVERY_TIMEOUT", f"exceeded {self.limits.max_minutes} minutes"
                    break
                if rec.llm_usage["cost_usd"] >= self.limits.max_cost_usd:
                    code, message = "DISCOVERY_BUDGET_EXCEEDED", f"spent ${rec.llm_usage['cost_usd']:.4f}"
                    break
                try:
                    await runner.clear_modal_globals(None, params, catalog, [])
                    probe = await runner.probe()
                    terminal = runner._global_hit(probe, params, {"session_lost", "app_error"})
                    if terminal:
                        if terminal[1].kind == "app_error":
                            feedback = f"the application showed an error ({terminal[0]}); try another way or call cannot_complete"
                        else:
                            await runner._handle_terminal(terminal, None, probe)
                except RestartRun:
                    history.append(f"{step}. (session expired; Rote signed on again and returned to the main menu)")
                    feedback = "session was renewed; you are at the main menu"
                    continue

                snap = await rt.surface.stable_snapshot()
                probe = await rt.surface.probe()
                matches = sorted(matcher.match_catalog(catalog, probe, params), key=lambda m: 0 if catalog[m[0]].kind == "outcome" else 1)
                screen_id, binding = (matches[0] if matches else (None, {}))
                seen[snap.fingerprint] += 1
                outputs_at_seen.setdefault(snap.fingerprint, len(extracted))
                screen_text = run.masker.render(snap)
                if rt.surface.pending_dialog:
                    screen_text += f'\nOPEN BROWSER DIALOG: "{run.masker.mask_text(rt.surface.pending_dialog.message)}" (use accept_dialog or dismiss_dialog)'
                rec.write_text(f"snapshots/{step:02d}.txt", screen_text)
                await rt.masked_screenshot(run, f"screens/{step:02d}.png", snap)
                rec.emit(
                    "screen_observed", step=step, summary=f"step {step}: screen {screen_id or 'not in catalog'}",
                    screen=screen_id, binding=binding, fingerprint=snap.fingerprint, headings=snap.headings(),
                )

                ctx = TurnContext(
                    goal=self.goal, inputs={b.placeholder: b.kind for b in self.bindings}, outputs=self.outputs,
                    extracted={k: v["type"] for k, v in extracted.items()}, step=step, max_steps=self.limits.max_steps,
                    last_result=feedback, history=history[-15:], screen=screen_text, snapshot=snap,
                )
                decision = await self.planner.next_action(ctx)
                rec.record_llm_usage(decision.input_tokens, decision.output_tokens, decision.cost_usd)
                ref = decision.args.get("ref")
                target_desc = run.masker.mask_text(snap.node(ref).describe()) if ref and snap.has_ref(ref) else (ref or "")
                problem = decision.error or (validate_args(decision.tool, decision.args) if decision.tool in TOOL_NAMES else f"unknown tool {decision.tool!r}")
                rec.emit(
                    "llm_decision", actor="agent", step=step,
                    summary=f'agent {decision.tool} {target_desc}'.strip() + (f' — "{decision.why}"' if decision.why else "") + (f" ✗ {problem}" if problem else ""),
                    tool=decision.tool, args=decision.args, why=decision.why, error=problem,
                    usage={"input_tokens": decision.input_tokens, "output_tokens": decision.output_tokens, "cost_usd": decision.cost_usd},
                )
                if problem:
                    strikes += 1
                    feedback = f"invalid decision: {problem}. Call exactly one valid tool."
                    if strikes >= 2:
                        await self._escalate(runner, "PLANNER_CONFUSED", f"the planner made {strikes} invalid decisions in a row", params)
                        strikes = 0
                    continue
                tool, args = decision.tool, decision.args

                if tool == "done":
                    missing = [name for name in self.outputs if name not in extracted]
                    if missing:
                        done_rejects += 1
                        feedback = f"done rejected: requested outputs not extracted yet: {missing}"
                        if done_rejects >= 2:
                            await self._escalate(runner, "PREMATURE_DONE", "the planner called done twice without the requested outputs", params)
                        continue
                    capability_name = self.name or snake(args.get("capability_name", "")) or None
                    status, message = "completed", args.get("summary", "")
                    break
                if tool == "cannot_complete":
                    status, code, message = "GOAL_NOT_ACHIEVABLE", "GOAL_NOT_ACHIEVABLE", args.get("reason", "")
                    break
                if tool == "ask_human":
                    await self._escalate(runner, "AGENT_ASKED", f"{args.get('reason', '')} — {args.get('question', '')}", params)
                    history.append(f"{step}. asked a human: {run.masker.mask_text(args.get('reason', ''))} → the human resumed")
                    feedback = "a human operator handled your request; look at the current screen"
                    continue
                if tool == "wait":
                    await asyncio.sleep(min(float(args.get("seconds", 1)), 5.0))
                    history.append(f"{step}. waited")
                    feedback = "waited"
                    continue
                if tool in REF_TOOLS and not snap.has_ref(ref):
                    strikes += 1
                    feedback = f"reference {ref!r} is not on the current screen"
                    if strikes >= 2:
                        await self._escalate(runner, "PLANNER_CONFUSED", "the planner used references that are not on the screen", params)
                        strikes = 0
                    continue
                strikes = 0

                if tool == "extract":
                    feedback = await self._extract(run, snap, step, args, extracted, target_desc)
                    history.append(f"{step}. extract {args.get('output_name')} from {target_desc} → {feedback}")
                    continue

                intent = ActionIntent(
                    action=TOOL_ACTIONS[tool], ref=ref, target_desc=target_desc, value=args.get("text", args.get("option")),
                    key=args.get("key"), checked=args.get("checked"), step_id=step, screen=screen_id, why=decision.why, params=params,
                )
                before = matcher.signature(await rt.surface.probe())
                network_blocks_before = len(rt.network_blocks)
                outcome = await runner.gateway.perform(intent, mode="discovery", actor="agent", record_locators=tool in ("click", "type_text", "select_option", "set_checkbox"))
                value_note = f" {args['text']}" if "text" in args else ""
                if outcome.status == "blocked":
                    blocks += 1
                    feedback = f"BLOCKED by policy: {outcome.error}. Choose a different approach."
                    history.append(f"{step}. {tool} {target_desc} → blocked by policy")
                    if blocks >= 2:
                        await self._escalate(runner, "REPEATED_POLICY_BLOCK", "two actions were blocked by policy", params)
                        blocks = 0
                    continue
                if outcome.status == "rejected":
                    feedback = f"a human REJECTED this irreversible action ({outcome.error}). Do not retry it; choose another way or call cannot_complete."
                    history.append(f"{step}. {tool} {target_desc} → rejected by a human")
                    continue
                if outcome.status == "failed":
                    feedback = f"action failed: {outcome.error}"
                    history.append(f"{step}. {tool} {target_desc} → failed")
                    continue
                changed = await self._wait_change(before)
                blocked_navigation = rt.network_blocks[network_blocks_before:]
                if blocked_navigation:
                    # the action itself was allowed, but the page it opened is off limits; without this the agent only sees a blank frame
                    blocks += 1
                    feedback = f"BLOCKED by policy: this action tried to open a page that is off limits ({run.masker.mask_text(blocked_navigation[-1])}). Choose a different approach."
                    history.append(f"{step}. {tool} {target_desc} → navigation blocked by policy")
                    if blocks >= 2:
                        await self._escalate(runner, "REPEATED_POLICY_BLOCK", "two actions were blocked by policy", params)
                        blocks = 0
                    continue
                history.append(f"{step}. {tool} {target_desc}{value_note} → {'screen changed' if changed else 'no visible change'}")
                feedback = "ok, the screen changed" if changed else "ok, no visible change"
                if tool in ("click", "select_option", "press_key") and not changed:
                    no_effect += 1
                    if no_effect >= 2:
                        await self._escalate(runner, "STUCK_NO_PROGRESS", "two actions in a row changed nothing", params)
                        no_effect = 0
                else:
                    no_effect = 0
                if seen[snap.fingerprint] >= 3 and outputs_at_seen.get(snap.fingerprint) == len(extracted):
                    await self._escalate(runner, "STUCK_LOOP", "the same screen came back three times without progress", params)
                    seen.clear()
            else:
                code, message = "DISCOVERY_STEP_LIMIT", f"no result within {self.limits.max_steps} steps"
        except StepFailure as failure:
            code, message = failure.code, failure.message or failure.observed or ""
        except ControlNotHeld as exc:
            code, message = "INFRA_ERROR", str(exc)
        finally:
            await self.planner.aclose()

        await rt.masked_screenshot(run, "screens/final.png")
        await park_browser(rt)
        rec.emit("run_finished", summary=f"discovery {status}" + (f" {code}" if code else "") + (f": {message}" if message else ""), status=status, code=code)
        summary = {
            "goal": self.goal,
            "status": status,
            "code": code,
            "message": message,
            "capability_name": capability_name,
            "target": rt.origin,
            "product": rt.profile.product,
            "planner": planner_info,
            "inputs": [
                {"placeholder": b.placeholder, "kind": b.kind, "explicit_name": b.explicit_name, "explicit_type": b.explicit_type}
                for b in self.bindings
            ],
            "declared_outputs": self.outputs,
            "extracted": {k: {kk: vv for kk, vv in v.items() if kk != "value"} for k, v in extracted.items()},
        }
        rec.finish(summary)
        return DiscoveryResult(
            run_dir=rec.dir, run_id=rec.run_id, status=status, code=code, message=message,
            capability_name=capability_name, params=params, outputs={k: v["value"] for k, v in extracted.items()},
            usage=dict(rec.llm_usage),
        )

    async def _escalate(self, runner: StepRunner, code: str, reason: str, params: dict[str, str]) -> None:
        await runner.escalate(code, reason, None, self.rt.profile.screen_catalog, [], params)

    async def _wait_change(self, before: str, timeout_s: float = 3.0) -> bool:
        end = time.monotonic() + timeout_s
        while time.monotonic() < end:
            await asyncio.sleep(0.2)
            if matcher.signature(await self.rt.surface.probe()) != before:
                await asyncio.sleep(0.3)
                return True
        return False

    async def _extract(self, run: "RunContext", snap, step: int, args: dict[str, Any], extracted: dict[str, dict[str, Any]], target_desc: str) -> str:
        rt = self.rt
        ref = args["ref"]
        node = snap.node(ref)
        name = snake(args["output_name"])
        kind = args.get("value_type", "string")
        element = await rt.surface.element_for_ref(ref)
        if element is None:
            return "the element is no longer on the screen"
        columns = None
        try:
            if isinstance(node, TableNode) or kind == "table":
                if not isinstance(node, TableNode):
                    return "value_type table needs the ref of a TABLE, not a cell or control"
                raw = await rt.surface.read_table(element)
                columns = infer_columns(raw)
                value = parse_table(raw, columns)
                kind = "table"
            else:
                value = parse_value(kind, await rt.surface.read_text(element))
        except ParseError as exc:
            return f"could not read a {kind}: {exc}"
        classification = DataClass.internal
        if isinstance(node, TableNode):
            if any(run.masker.classify_cell(node.name, col) in SENSITIVE for col in node.columns if col):
                classification = DataClass.financial
        elif isinstance(node, CellNode):
            classification = run.masker.classify_cell(node.table, node.column) or (DataClass.financial if kind == "money" else DataClass.internal)
        locators = rt.clean_locators(run, await rt.surface.locator_candidates(ref))
        extracted[name] = {"type": kind, "value": value, "columns": columns, "classification": classification.value, "target": target_desc}
        size = f"{len(value)} rows" if isinstance(value, list) else "1 value"
        run.recorder.emit(
            "output_extracted", actor="agent", step=step,
            summary=f"agent extract {name} ({kind}, {size}) from {target_desc}",
            output=name, type=kind, columns=columns, classification=classification.value, target=target_desc,
            locators=[loc.model_dump(exclude_none=True) for loc in locators], value="«masked»",
        )
        return f"extracted {name} ({kind}, {size})"


def binding_types(bindings: list[InputBinding]) -> dict[str, str]:
    return {b.placeholder: b.explicit_type or KIND_TYPES.get(b.kind, "string") for b in bindings}
