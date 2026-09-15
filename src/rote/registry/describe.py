"""Human-readable summary of a capability."""

from __future__ import annotations

from rote.models import Capability
from rote.models.profile import ProductProfile
from rote.registry.store import content_hash


def describe(cap: Capability, profile: ProductProfile | None = None) -> str:
    c = cap.contract
    lines = [
        f"{cap.id}  v{cap.version}  [{cap.status}]      {cap.app.product} {cap.app.product_versions} ({cap.app.surface})",
        "",
        f"WHAT IT DOES   {c.summary}",
    ]
    needs = [f"{n}  {s.type}{' ' + s.pattern if s.pattern else ''} ({s.classification.value})" for n, s in c.inputs.items()]
    lines.append("NEEDS          " + ("\n               ".join(needs) if needs else "nothing"))
    returns = []
    for name, spec in c.outputs.items():
        detail = f"{name}  {spec.type} ({spec.classification.value})"
        if spec.columns:
            detail += "  columns: " + ", ".join(f"{k}:{v}" for k, v in spec.columns.items())
        returns.append(detail)
    lines.append("RETURNS        " + ("\n               ".join(returns) if returns else "nothing (reaches a screen)"))
    lines.append("MAY INSTEAD RETURN   " + (" · ".join(o.code for o in c.outcomes) or "none"))
    lines.append(f"SIDE EFFECTS   {c.side_effects}" + (" (idempotent)" if c.idempotent else " (NOT idempotent)")
                 + (f" · irreversible steps: {', '.join(c.irreversible_steps)}" if c.irreversible_steps else ""))
    lines += ["", "STEPS"]
    for number, step in enumerate(cap.steps, start=1):
        target = cap.targets.get(step.target) if step.target else None
        what = target.description if target and target.description else (step.target or "")
        if step.action == "type":
            text = f"Type {step.value} into {what}"
        elif step.action == "extract":
            text = f"Read {step.into} ({step.parse}) from {what}"
        elif step.action == "click":
            text = f"Click {what}"
        else:
            text = f"{step.action} {what}".strip()
        risk = "  [IRREVERSIBLE]" if step.risk == "irreversible" else ""
        first = True
        branches = []
        for case in step.expect:
            then = "continue" if case.then == "continue" else (case.then.outcome or case.then.fail or case.then.escalate)
            branches.append(f"{case.screen} → {then}")
        if step.verify:
            branches.append("verify the field kept the value")
        if step.if_missing:
            branches.append(f"row missing → {step.if_missing.then.outcome}")
        head = f"  {number}. {text}{risk}"
        if branches:
            lines.append(f"{head:<58} expect " + branches[0])
            lines += [f"{'':<58}      | " + b for b in branches[1:]]
        else:
            lines.append(head)
    if profile:
        handled = [f"{name} → {s.kind}" for name, s in profile.global_screens.items()]
        lines += ["", "APP-WIDE HANDLING (profile)   " + " · ".join(handled)]
    success = " AND ".join(
        (f"on {cond.screen}" if cond.screen else f"{cond.output_valid} is valid") for cond in cap.success.all
    )
    lines.append(f"SUCCESS WHEN   {success or 'all steps completed'}")
    durable = sum(1 for t in cap.targets.values() if t.durable_count >= 1)
    two = sum(1 for t in cap.targets.values() if t.durable_count >= 2)
    fragile = sum(1 for t in cap.targets.values() if t.durable_count == 0)
    lines += [
        "",
        f"ROBUSTNESS     {durable}/{len(cap.targets)} targets have ≥1 verified durable locator; {two} have ≥2; {fragile} fragile-only",
    ]
    prov = cap.provenance
    origin = f"discovered {prov.discovered_from_run}" if prov.discovered_from_run else (prov.authored_by or "authored")
    validated = f" · validated by {', '.join(prov.validated_by_runs)}" if prov.validated_by_runs else ""
    lines.append(f"PROVENANCE     {origin}{validated} · {content_hash(cap)[:23]}")
    if prov.review_flags:
        lines.append("REVIEW FLAGS   " + "\n               ".join(prov.review_flags))
    return "\n".join(lines)
