import asyncio

import pytest

from rote.handoff.broker import Broker, ControlNotHeld, ControlState, IllegalTransition


def _intervention(broker, kind="take_control"):
    return broker.new_intervention(kind=kind, run_id="r", mode="replay_attended", reason_code="X", reason="why")


async def test_take_control_resume_verify():
    broker = Broker(expiry_s=5)
    broker.require_automation_control()
    i = _intervention(broker)
    waiter = asyncio.create_task(broker.await_take_control(i))
    await asyncio.sleep(0.05)
    assert broker.state == ControlState.WAITING_FOR_HUMAN
    with pytest.raises(ControlNotHeld):
        broker.require_automation_control()
    with pytest.raises(IllegalTransition):
        broker.resume(i.id)  # resume before take control
    broker.take_control(i.id, "asha")
    assert broker.state == ControlState.HUMAN
    assert broker.record_human_action({"type": "click", "name": "OK"})
    broker.resume(i.id, "done")
    signal, _ = await waiter
    assert signal == "resume" and broker.state == ControlState.VERIFYING
    broker.verification_result(i, False, "wrong screen")
    assert broker.state == ControlState.WAITING_FOR_HUMAN and i.status == "open"
    waiter = asyncio.create_task(broker.await_take_control(i))
    await asyncio.sleep(0.05)
    broker.take_control(i.id, "asha")
    broker.resume(i.id)
    assert (await waiter)[0] == "resume"
    broker.verification_result(i, True, "ok")
    assert broker.state == ControlState.AUTOMATION and i.status == "resolved"
    assert len(broker.human_actions[i.id]) == 1


async def test_requests_are_published_only_when_automation_waits():
    broker = Broker(expiry_s=5)
    request = _intervention(broker)
    assert broker.open_interventions() == []  # not visible (or actionable) before the state change
    with pytest.raises(KeyError):
        broker.take_control(request.id, "too-early")
    waiter = asyncio.create_task(broker.await_take_control(request))
    await asyncio.sleep(0.05)
    assert [i.id for i in broker.open_interventions()] == [request.id]
    assert broker.state == ControlState.WAITING_FOR_HUMAN
    broker.abort(request.id)
    await waiter


async def test_human_actions_ignored_unless_human_holds_control():
    broker = Broker()
    assert broker.record_human_action({"type": "click"}) is False


async def test_abort_returns_control():
    broker = Broker(expiry_s=5)
    i = _intervention(broker)
    waiter = asyncio.create_task(broker.await_take_control(i))
    await asyncio.sleep(0.05)
    broker.abort(i.id, "asha", "not safe")
    assert (await waiter)[0] == "abort"
    assert broker.state == ControlState.AUTOMATION


async def test_approval_reject_and_expiry():
    broker = Broker(expiry_s=5)
    i = _intervention(broker, "approve_action")
    waiter = asyncio.create_task(broker.await_approval(i))
    await asyncio.sleep(0.05)
    broker.reject(i.id, "asha", "not in the goal")
    assert (await waiter)[0] == "rejected"
    assert broker.state == ControlState.AUTOMATION
    short = Broker(expiry_s=0.2)
    j = _intervention(short, "approve_action")
    assert (await short.await_approval(j))[0] == "expired"
    assert short.state == ControlState.AUTOMATION
