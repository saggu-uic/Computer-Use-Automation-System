"""Every runtime condition named in brief §3.3, against the live FakeBank, with the hand-written capability."""

import asyncio

import pytest

from rote.handoff.bot import OperatorBot
from rote.models import Capability
from rote.registry.store import dump_model
from rote.replay.engine import ReplayEngine

pytestmark = pytest.mark.integration


async def run(runtime, cap, member, faults=None, attended=False):
    if faults:
        await runtime.faults.set(faults)
    return await ReplayEngine(runtime, cap, attended=attended).run({"member_number": member})


async def test_success_two_members(runtime, handwritten):
    first = await run(runtime, handwritten, "100245")
    second = await run(runtime, handwritten, "100377")
    assert first.status == "success" and first.outputs["savings_balance"] == {"amount": "4210.55", "currency": "USD"}
    assert second.status == "success" and second.outputs["savings_balance"]["amount"] == "1292.40"
    assert not first.side_effects_possible and first.ui_touched


@pytest.mark.parametrize(
    "member, code",
    [("999999", "MEMBER_NOT_FOUND"), ("100733", "ACCOUNT_RESTRICTED"), ("100512", "NO_SAVINGS_ACCOUNT")],
)
async def test_business_outcomes(runtime, handwritten, member, code):
    result = await run(runtime, handwritten, member)
    assert result.status == "business_outcome" and result.outcome.code == code and result.error is None


async def test_preflight_invalid_input_does_not_touch_ui(runtime, handwritten):
    result = await run(runtime, handwritten, "12AB")
    assert result.status == "failure" and result.error.code == "INVALID_INPUT" and result.error.phase == "preflight"
    assert result.ui_touched is False


@pytest.mark.parametrize(
    "faults, recovery",
    [({"notice": "once"}, "SYSTEM_NOTICE_DISMISSED"), ({"latency.member_detail": "10000"}, "SLOW_LOAD_WAITED"), ({"session_expire_after": "2"}, "SESSION_RENEWED_RESTARTED")],
)
async def test_recoverable_conditions(runtime, handwritten, faults, recovery):
    result = await run(runtime, handwritten, "100245", faults)
    assert result.status == "success", result.error
    assert recovery in [r["code"] for r in result.recoveries]


@pytest.mark.parametrize(
    "faults, code",
    [
        ({"error500.member_detail": "once"}, "APP_ERROR"),
        ({"unauthorized.member_detail": "once"}, "OPERATOR_NOT_AUTHORIZED"),
        ({"unknown_popup": "once"}, "UNEXPECTED_SCREEN"),
        ({"supervisor_override": "once"}, "HUMAN_REQUIRED"),
        ({"latency.member_detail": "20000"}, "CHECKPOINT_TIMEOUT"),
    ],
)
async def test_failures(runtime, handwritten, faults, code):
    result = await run(runtime, handwritten, "100245", faults)
    assert result.status == "failure" and result.error.code == code
    assert result.error.evidence and "screenshot" in result.error.evidence


async def test_supervisor_override_handoff(runtime, handwritten):
    async def supervisor(_item):
        main = runtime.surface.frame("main")
        await main.fill("input[name=F0901]", "supervisor-test-pass", timeout=5000)
        await main.click("input[type=button][value=OK]", timeout=5000)

    bot = OperatorBot(runtime.operator_url, on_take_control=supervisor).start()
    try:
        result = await asyncio.wait_for(run(runtime, handwritten, "100245", {"supervisor_override": "once"}, attended=True), 90)
    finally:
        await bot.stop()
    assert result.status == "success", (result.error, bot.errors)
    assert result.interventions and result.interventions[0]["reason_code"] == "SUPERVISOR_OVERRIDE_REQUIRED"
    assert result.interventions[0]["human_actions"] >= 1


async def test_tampered_capability_is_blocked(runtime, handwritten):
    data = dump_model(handwritten)
    data["targets"]["close"] = {"locators": [{"kind": "role", "role": "button", "name": "CLOSE SHARE", "table": "SHARE ACCOUNTS", "row_where": {"TYPE": "REGULAR SAVINGS"}, "frame": "main"}]}
    data["steps"].insert(3, {"id": "sneaky_close", "action": "click", "target": "close", "risk": "safe"})
    tampered = Capability.model_validate(data)
    result = await ReplayEngine(runtime, tampered).run({"member_number": "100245"})
    assert result.status == "failure" and result.error.code == "POLICY_BLOCKED"
    assert (await runtime.faults.shares("100245"))["S-01"] == "ACTIVE"
