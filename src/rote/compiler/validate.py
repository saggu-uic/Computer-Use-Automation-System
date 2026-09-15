"""Validation replays: same input (reproduces without the LLM) and a different input (generalises)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rote.models import Capability, CapabilityResult, ProductProfile
from rote.replay.engine import ReplayEngine

if TYPE_CHECKING:
    from rote.runtime import Runtime


def different_inputs(cap: Capability, profile: ProductProfile, same: dict[str, str], explicit: dict[str, str] | None = None) -> dict[str, str] | None:
    chosen: dict[str, str] = {}
    for name in cap.contract.inputs:
        if explicit and name in explicit:
            chosen[name] = explicit[name]
            continue
        candidates = [v for v in profile.test_inputs.get(name, {}).get("happy", []) if v != same.get(name)]
        if not candidates:
            return None
        chosen[name] = candidates[0]
    return chosen


async def validate_capability(
    runtime: "Runtime", cap: Capability, same: dict[str, str], other: dict[str, str] | None
) -> tuple[Capability, list[CapabilityResult]]:
    results = [await ReplayEngine(runtime, cap, kind="validation").run(same)]
    if other is not None:
        results.append(await ReplayEngine(runtime, cap, kind="validation").run(other))
    cap.provenance.validated_by_runs = [r.invocation_id for r in results]
    passed = len(results) == 2 and all(r.status == "success" for r in results)
    if passed:
        cap.status = "validated"
    else:
        cap.status = "draft"
        detail = " · ".join(f"{r.invocation_id}: {r.status}{' ' + r.error.code if r.error else ''}" for r in results)
        if other is None:
            detail += " · no different test input available"
        cap.provenance.review_flags.append(f"validation did not pass: {detail}")
    return cap, results
