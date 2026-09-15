"""Pre-flight checks: everything that can be refused before the UI is touched."""

from __future__ import annotations

from typing import Any

from rote.models import Capability, ErrorInfo, Policy, ProductProfile
from rote.registry.store import content_hash
from rote.replay.parsing import validate_input


def preflight(cap: Capability, inputs: dict[str, Any], profile: ProductProfile, policy: Policy, *, attended: bool) -> ErrorInfo | None:
    if cap.app.product != profile.product:
        return ErrorInfo.make(
            "ARTIFACT_INVALID", "preflight",
            message=f"capability is for product {cap.app.product!r}, target is {profile.product!r}",
        )
    actual = content_hash(profile)
    if cap.app.profile.hash != actual:
        return ErrorInfo.make(
            "PROFILE_MISMATCH", "preflight",
            message="the product profile changed since this capability was compiled; re-validate it",
            expected=cap.app.profile.hash[:23], observed=actual[:23],
        )
    fields: dict[str, str] = {}
    for name, spec in cap.contract.inputs.items():
        problem = validate_input(name, spec, inputs.get(name))
        if problem:
            fields[name] = problem
    for name in inputs:
        if name not in cap.contract.inputs:
            fields[name] = "not an input of this capability"
    if fields:
        return ErrorInfo.make("INVALID_INPUT", "preflight", fields=fields, message="inputs do not satisfy the contract")
    for step_id in cap.contract.irreversible_steps:
        decision = policy.modes.replay.irreversible_declared
        if decision == "block":
            return ErrorInfo.make("POLICY_DENIED", "preflight", step_id=step_id, message="policy blocks irreversible steps in replay")
        if decision == "require_approval" and not attended:
            return ErrorInfo.make(
                "POLICY_DENIED", "preflight", step_id=step_id,
                message="irreversible step needs human approval; run attended",
            )
    return None
