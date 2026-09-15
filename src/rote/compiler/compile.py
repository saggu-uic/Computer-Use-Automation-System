"""Compile a completed discovery run into a capability.

Reads only the run record (events.jsonl, run.json) - never the model transcript.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rote.models import Capability, ProductProfile
from rote.models.capability import (
    PLACEHOLDER,
    AppRef,
    Contract,
    ExpectCase,
    IfMissing,
    InputSpec,
    Locator,
    OutcomeSpec,
    OutputSpec,
    Provenance,
    Screen,
    ScreenPredicate,
    StartRef,
    Step,
    Success,
    SuccessCondition,
    Target,
    ThenAction,
    Timeouts,
)
from rote.models.capability import ProfileRef
from rote.models.common import DataClass
from rote.perception.masking import LONG_DIGITS_RE, MONEY_RE, SSN_RE
from rote.registry.store import Catalog, content_hash, dump_model
from rote.replay.parsing import snake

KIND_TYPES = {"digits": ("string", r"^[0-9]+$"), "money": ("money", None), "date": ("date", None), "quoted": ("string", None)}
ELEMENT_ACTIONS = {"click", "type", "select", "check"}


@dataclass
class CompileResult:
    capability: Capability | None
    report: str
    errors: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    run_id: str = ""
    input_map: dict[str, str] = field(default_factory=dict)  # placeholder -> input name


def _events(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _unique(base: str, taken: dict | set) -> str:
    name, n = base, 2
    while name in taken:
        name = f"{base}_{n}"
        n += 1
    return name


def target_name(loc: Locator) -> str:
    if loc.kind == "table_cell":
        return f"{'_'.join(snake(v) for v in (loc.row_where or {}).values())}_{snake(loc.column or '')}_cell"
    if loc.kind == "table":
        return f"{snake(loc.table or '')}_table"
    if loc.kind == "label":
        return f"{snake(loc.label or '')}_field"
    if loc.kind in ("role", "text"):
        scope = "_".join(snake(v) for v in (loc.row_where or {}).values())
        base = f"{snake(loc.name or loc.text or '')}_{loc.role or 'text'}"
        return f"{scope}_{base}" if scope else base
    if loc.kind == "near_text":
        return f"{snake(loc.text or '')}_{loc.role}"
    return "element"


def compile_run(run_dir: Path, profile: ProductProfile, catalog: Catalog, *, name: str | None = None) -> CompileResult:
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    run_id = run["run_id"]
    events = _events(run_dir)
    errors: list[str] = []
    flags: list[str] = []

    if run.get("status") != "completed":
        msg = f"run {run_id} did not complete ({run.get('status')} {run.get('code') or ''}); nothing to compile"
        return CompileResult(None, msg, [f"RUN_NOT_COMPLETED: {run.get('code')}"], run_id=run_id)

    # ---- 1. collect; refuse runs where a human performed flow steps
    needs_human = {s.escalate for s in profile.global_screens.values() if s.kind == "needs_human" and s.escalate}
    reasons = {}
    for e in events:
        if e["type"] == "intervention_opened":
            item = e["data"].get("intervention") or {}
            reasons[item.get("id")] = item.get("reason_code")
    human_steps = [
        e for e in events
        if e["type"] == "human_action"
        and reasons.get(e["data"].get("intervention")) not in needs_human
        and (e["data"].get("action") or {}).get("type") in ("click", "change", "key")
    ]
    if human_steps:
        listed = "; ".join(
            f"{(h['data'].get('action') or {}).get('type')} {(h['data'].get('action') or {}).get('name') or (h['data'].get('action') or {}).get('label') or ''}".strip()
            for h in human_steps[:8]
        )
        error = (
            f"HUMAN_STEPS_NOT_COMPILABLE: a human performed flow steps during discovery ({listed}). "
            "A capability that silently depends on unrecorded steps would be unsafe; re-run discovery."
        )
        return CompileResult(None, error, [error], run_id=run_id)

    observed = sorted((e for e in events if e["type"] == "screen_observed"), key=lambda e: e["seq"])

    def screen_at(step: int) -> dict[str, Any]:
        return next((e["data"] for e in observed if e["step"] == step), {})

    def screen_after(step: int) -> dict[str, Any]:
        return next((e["data"] for e in observed if isinstance(e["step"], int) and e["step"] > step), {})

    items = [
        e for e in events
        if (e["type"] == "action_executed" and e["actor"] == "agent" and e["data"].get("ok"))
        or (e["type"] == "output_extracted" and e["actor"] == "agent")
    ]
    raw = []
    for e in items:
        before, after = screen_at(e["step"]), screen_after(e["step"])
        raw.append({
            "type": e["type"], "data": e["data"], "step": e["step"],
            "before": before.get("screen"), "binding": before.get("binding") or {}, "fp_before": before.get("fingerprint"),
            "after": after.get("screen"), "after_binding": after.get("binding") or {}, "fp_after": after.get("fingerprint"),
            "headings_after": after.get("headings") or [],
        })
    if not raw:
        return CompileResult(None, "no successful actions to compile", ["NO_ACTIONS"], run_id=run_id)

    # ---- 2. drop actions with no effect; keep only the last of repeated typing into one field
    kept = []
    for index, r in enumerate(raw):
        d = r["data"]
        if r["type"] == "action_executed":
            if d["action"] in ("click", "press_key") and r["fp_before"] and r["fp_before"] == r["fp_after"]:
                flags.append(f"dropped step {r['step']}: {d['action']} {d.get('target')} changed nothing")
                continue
            nxt = raw[index + 1] if index + 1 < len(raw) else None
            if d["action"] == "type" and nxt and nxt["type"] == "action_executed" and nxt["data"]["action"] == "type" and nxt["data"].get("locators") == d.get("locators"):
                flags.append(f"dropped step {r['step']}: typing repeated in the same field")
                continue
        kept.append(r)

    # ---- 4. inputs: name each used placeholder after the field it filled
    used: set[str] = set()
    for r in kept:
        blob = json.dumps(r["data"].get("value")) + json.dumps(r["data"].get("locators"))
        used |= set(PLACEHOLDER.findall(blob))
        used |= set((r["binding"] or {}).values()) | set((r["after_binding"] or {}).values())
    rename: dict[str, str] = {}
    inputs: dict[str, InputSpec] = {}
    for binding in run.get("inputs") or []:
        ph = binding["placeholder"]
        if ph not in used:
            flags.append(f"detected value {{{{{ph}}}}} was never used, so it is not an input")
            continue
        named, named_from = None, None
        if binding.get("explicit_name"):
            named, named_from = binding["explicit_name"], "--input"
        if not named:
            named, named_from = _name_from_field(kept, ph)
        if not named:
            named = _name_from_screen(kept, ph)
            named_from = "screen catalog placeholder" if named else None
        if not named:
            named = ph
            flags.append(f"input {ph} could not be named from a field label; rename it")
        named = _unique(snake(named), inputs)
        type_, pattern = KIND_TYPES.get(binding["kind"], ("string", None))
        if binding.get("explicit_type"):
            type_, pattern = binding["explicit_type"], None
        hint = profile.input_hints.get(named)
        inputs[named] = InputSpec(
            type=type_, pattern=pattern,
            classification=hint.classification if hint else DataClass.internal,
            description=hint.description if hint else "", named_from=named_from,
        )
        rename[ph] = named

    def apply(text: str) -> str:
        return PLACEHOLDER.sub(lambda m: "{{" + rename.get(m.group(1), m.group(1)) + "}}", text)

    # ---- 3, 5, 6, 7. targets, screens, expectations, risk
    catalog_screens = profile.screen_catalog
    targets: dict[str, Target] = {}
    target_keys: dict[str, str] = {}
    steps: list[Step] = []
    step_ids: set[str] = set()
    screens_used: set[str] = set()
    proposed: dict[str, Screen] = {}
    screen_bindings: dict[str, dict[str, str]] = {}
    outcomes: dict[str, str] = {}
    wired: list[str] = []
    proposed_outcomes: list[str] = []
    outputs: dict[str, OutputSpec] = {}
    irreversible: list[str] = []
    start = kept[0]["before"] or profile.entry.home_screen

    for r in kept:
        d = r["data"]
        for sid, bound in ((r["before"], r["binding"]), (r["after"], r["after_binding"])):
            if sid:
                screen_bindings.setdefault(sid, {}).update({cat: rename.get(ph, ph) for cat, ph in (bound or {}).items()})
        action = d.get("action") if r["type"] == "action_executed" else "extract"
        tname = None
        if action in ELEMENT_ACTIONS or action == "extract" or (action == "press_key" and d.get("locators")):
            locators = []
            for raw_loc in d.get("locators") or []:
                try:
                    locators.append(Locator(**json.loads(apply(json.dumps(raw_loc)))))
                except ValueError:
                    continue
            if not locators:
                errors.append(f"step {r['step']}: no verified locator for {d.get('target')}")
                continue
            if not any(loc.durable for loc in locators):
                flags.append(f"FRAGILE_ONLY: step {r['step']} ({d.get('target')}) has only a structural locator")
            key = json.dumps([loc.model_dump(exclude_none=True) for loc in locators], sort_keys=True)
            tname = target_keys.get(key)
            if not tname:
                tname = _unique(target_name(locators[0]), targets)
                targets[tname] = Target(description=_describe(d.get("target")), locators=locators)
                target_keys[key] = tname
        if r["before"]:
            screens_used.add(r["before"])

        if action == "extract":
            out = snake(d["output"])
            kind = d["type"]
            spec = OutputSpec(
                type=kind, columns=d.get("columns") if kind == "table" else None, max_rows=50 if kind == "table" else None,
                classification=DataClass(d.get("classification") or "internal"),
            )
            outputs[out] = spec
            if_missing = None
            first = targets[tname].locators[0]
            if first.kind == "table_cell" and first.row_where:
                code = re.sub(r"[^A-Z0-9_]", "", "NO_" + "_".join(snake(v).upper() for v in first.row_where.values()))
                outcomes[code] = "No row where " + ", ".join(f"{k} = {v}" for k, v in first.row_where.items()) + " (name proposed by the compiler)"
                proposed_outcomes.append(code)
                if_missing = IfMissing(then=ThenAction(outcome=code))
            step_id = _unique(f"read_{out}", step_ids)
            step_ids.add(step_id)
            steps.append(Step(id=step_id, action="extract", target=tname, into=out, parse=kind, columns=spec.columns, if_missing=if_missing, description=f"Read {out}"))
            continue

        value = apply(d["value"]) if d.get("value") is not None else None
        if action in ("type", "select") and value is not None and not PLACEHOLDER.search(value):
            if SSN_RE.search(value) or MONEY_RE.search(value) or LONG_DIGITS_RE.search(value):
                errors.append(f"step {r['step']}: a sensitive literal was typed; it must be an input")
            else:
                flags.append(f"step {r['step']}: literal value typed; consider making it an input")
        expect: list[ExpectCase] = []
        if r["after"] and r["after"] != r["before"]:
            expect.append(ExpectCase(screen=r["after"], then="continue"))
            screens_used.add(r["after"])
            for sid, screen in catalog_screens.items():
                if screen.kind == "outcome" and screen.outcome and r["before"] in screen.may_follow and sid != r["after"]:
                    expect.append(ExpectCase(screen=sid, then=ThenAction(outcome=screen.outcome)))
                    screens_used.add(sid)
                    outcomes.setdefault(screen.outcome, screen.description or "")
                    if screen.outcome not in wired:
                        wired.append(screen.outcome)
        elif r["after"] is None and action == "click" and r["fp_before"] != r["fp_after"]:
            heading = next((h for h in r["headings_after"] if "«" not in h and "{{" not in h), None)
            if heading:
                pid = _unique(snake(heading), set(catalog_screens) | set(proposed))
                proposed[pid] = Screen(match=[ScreenPredicate(heading=heading, frame="main")], proposed=True, description="proposed by the compiler from the page heading")
                expect.append(ExpectCase(screen=pid, then="continue"))
                screens_used.add(pid)
                flags.append(f"screen {pid} is not in the profile catalog; proposed from its heading")
            else:
                errors.append(f"step {r['step']}: the resulting screen is not in the catalog and has no usable heading")
        risk = "irreversible" if d.get("risk") == "irreversible" else "safe"
        base = {
            "click": f"click_{tname}", "type": f"enter_{tname}", "select": f"select_{tname}", "check": f"check_{tname}",
            "press_key": f"press_{snake(d.get('key') or 'key')}", "accept_dialog": "accept_dialog", "dismiss_dialog": "dismiss_dialog",
        }.get(action, action)
        step_id = _unique(base, step_ids)
        step_ids.add(step_id)
        if risk == "irreversible":
            irreversible.append(step_id)
        steps.append(Step(
            id=step_id, action=action, target=tname, value=value, key=d.get("key") if action == "press_key" else None,
            risk=risk, verify="value_matches" if action == "type" else None, expect=expect, description=_describe(d.get("target")),
        ))

    screens_used.add(start)
    screens: dict[str, Screen] = {}
    for sid in sorted(screens_used):
        if sid in catalog_screens:
            text = json.dumps(catalog_screens[sid].model_dump(mode="json", by_alias=True, exclude_none=True))
            for catalog_name, input_name in screen_bindings.get(sid, {}).items():
                text = text.replace("{{" + catalog_name + "}}", "{{" + input_name + "}}")
            screen = Screen.model_validate_json(text)
            screen.may_follow = []
            screens[sid] = screen
        elif sid in proposed:
            screens[sid] = proposed[sid]
    for sid, screen in screens.items():
        needed = set(PLACEHOLDER.findall(json.dumps(screen.model_dump(by_alias=True, exclude_none=True))))
        if needed - set(inputs):
            errors.append(f"screen {sid} needs {sorted(needed - set(inputs))}, which are not inputs of this capability")

    for declared in (run.get("declared_outputs") or {}):
        if snake(declared) not in outputs:
            errors.append(f"requested output {declared} was never extracted")

    final_screen = next((r["before"] for r in kept if r["type"] == "output_extracted" and r["before"]), None)
    if final_screen is None:
        final_screen = next((c.screen for s in reversed(steps) for c in s.expect if c.then == "continue"), None)
    conditions = [SuccessCondition(screen=final_screen)] if final_screen else []
    conditions += [SuccessCondition(output_valid=name_) for name_ in outputs]

    summary = apply(run.get("goal") or "")
    cap_name = snake(name or run.get("capability_name") or "discovered_capability")
    cap_id = f"{profile.capability_prefix}.{cap_name}"
    capability = None
    if not errors:
        try:
            capability = Capability(
                id=cap_id,
                version=catalog.next_version(cap_id),
                status="draft",
                app=AppRef(product=profile.product, profile=ProfileRef(version=profile.version, hash=content_hash(profile))),
                contract=Contract(
                    summary=summary, inputs=inputs, outputs=outputs,
                    outcomes=[OutcomeSpec(code=c, description=desc) for c, desc in outcomes.items()],
                    side_effects="irreversible" if irreversible else "none", idempotent=not irreversible,
                    irreversible_steps=irreversible,
                ),
                start=StartRef(screen=start),
                steps=steps,
                targets=targets,
                screens=screens,
                success=Success(all=conditions),
                timeouts=Timeouts(),
                provenance=Provenance(
                    discovered_from_run=run_id, goal=summary, planner=run.get("planner"),
                    compiled_at=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                    review_flags=flags,
                ),
            )
        except ValueError as exc:
            errors.append(f"ARTIFACT_INVALID: {exc}")

    report = _report(run, capability, cap_id, kept, raw, inputs, outputs, wired, proposed_outcomes, targets, flags, errors)
    if capability is not None:
        out_dir = run_dir / "compile"
        out_dir.mkdir(exist_ok=True)
        capability.provenance.content_hash = content_hash(capability)
        (out_dir / "capability.json").write_text(json.dumps(dump_model(capability), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (out_dir / "compile-report.md").write_text(report, encoding="utf-8")
    return CompileResult(capability, report, errors, flags, run_id, rename)


def _name_from_field(kept: list[dict[str, Any]], placeholder: str) -> tuple[str | None, str | None]:
    token = "{{" + placeholder + "}}"
    for r in kept:
        d = r["data"]
        if r["type"] == "action_executed" and d.get("action") in ("type", "select") and token in (d.get("value") or ""):
            for loc in d.get("locators") or []:
                if loc.get("kind") == "label" and loc.get("label"):
                    return loc["label"], f'{loc.get("role", "field")} labelled "{loc["label"]}"'
            for loc in d.get("locators") or []:
                if loc.get("kind") in ("role", "near_text") and (loc.get("name") or loc.get("text")):
                    label = loc.get("name") or loc.get("text")
                    return label, f'{loc.get("role", "field")} "{label}"'
    return None, None


def _name_from_screen(kept: list[dict[str, Any]], placeholder: str) -> str | None:
    for r in kept:
        for bound in (r["binding"], r["after_binding"]):
            for catalog_name, ph in (bound or {}).items():
                if ph == placeholder:
                    return catalog_name
    return None


def _describe(target: str | None) -> str:
    text = re.sub(r"\s*\[(main|nav|banner|top)\]\s*$", r" (\1 frame)", target or "")
    return text.strip()


def _report(run, capability, cap_id, kept, raw, inputs, outputs, wired, proposed_outcomes, targets, flags, errors) -> str:
    durable = sum(1 for t in targets.values() if t.durable_count >= 1)
    two = sum(1 for t in targets.values() if t.durable_count >= 2)
    fragile = sum(1 for t in targets.values() if t.durable_count == 0)
    lines = [
        "# Compile report",
        "",
        f"- **capability:** `{cap_id}` {capability.version if capability else ''} ({capability.status if capability else 'not produced'})",
        f"- **from run:** `{run['run_id']}` · goal: {run.get('goal')}",
        f"- **steps:** {len([k for k in kept])} kept, {len(raw) - len(kept)} dropped",
        "- **inputs:** " + (", ".join(f"`{n}` ({s.type}{' ' + s.pattern if s.pattern else ''}, {s.classification.value}) named from {s.named_from}" for n, s in inputs.items()) or "none"),
        "- **outputs:** " + (", ".join(f"`{n}` ({s.type}, {s.classification.value})" for n, s in outputs.items()) or "none"),
        "- **outcomes wired from the profile catalog:** " + (", ".join(wired) or "none"),
        "- **outcomes proposed by the compiler:** " + (", ".join(proposed_outcomes) or "none"),
        f"- **targets:** {len(targets)} · ≥1 verified durable locator: {durable} · ≥2: {two} · fragile-only: {fragile}",
        "",
        "## Review flags",
        *([f"- {f}" for f in flags] or ["- none"]),
        "",
        "## Errors",
        *([f"- {e}" for e in errors] or ["- none"]),
        "",
    ]
    return "\n".join(lines)
