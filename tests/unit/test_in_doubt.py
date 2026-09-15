"""The in-doubt rule: after an irreversible step, no confirmation means OUTCOME_UNKNOWN, never a retry."""

from types import SimpleNamespace

import pytest

from rote.handoff.broker import Broker
from rote.models import ExpectCase, Screen, ScreenPredicate, Step
from rote.perception.masking import Masker
from rote.replay.engine import StepFailure, StepRunner


class StubSurface:
    def __init__(self, probe):
        self._probe = probe
        self.pending_dialog = None

    async def probe(self):
        return self._probe


class StubRecorder:
    run_id = "test"

    def __init__(self):
        self.events = []

    def emit(self, type_, **data):
        self.events.append((type_, data))


def _runner(profile, probe):
    runtime = SimpleNamespace(profile=profile, surface=StubSurface(probe), broker=Broker(), operator=None, origin="http://x")
    run = SimpleNamespace(recorder=StubRecorder(), masker=Masker(profile))
    runner = StepRunner(runtime, run, mode="replay", attended=False, subject="cap@1")
    runner.state.irreversible_attempted = True
    return runner


SUBMIT = Step(id="submit", action="click", target="t", risk="irreversible", expect=[ExpectCase(screen="confirmation")])
SCREENS = {"confirmation": Screen(match=[ScreenPredicate(heading="SHARE OPENED", frame="main")])}


async def test_session_lost_after_irreversible_step_is_outcome_unknown(profile):
    probe = {"main": {"url": "http://x/cgi/SIGNON", "headings": ["SIGN ON"], "text": "SIGN ON", "dialogs": [], "ready": "complete"}}
    runner = _runner(profile, probe)
    with pytest.raises(StepFailure) as failure:
        await runner.await_expectations(SUBMIT, {}, SCREENS, timeout_ms=300)
    assert failure.value.code == "OUTCOME_UNKNOWN"
    assert not any(t == "recovery_applied" for t, _ in runner.run.recorder.events)


async def test_no_confirmation_after_irreversible_step_is_outcome_unknown(profile):
    probe = {"main": {"url": "http://x/cgi/X", "headings": ["SOMETHING ELSE"], "text": "SOMETHING ELSE", "dialogs": [], "ready": "complete"}}
    runner = _runner(profile, probe)
    with pytest.raises(StepFailure) as failure:
        await runner.await_expectations(SUBMIT, {}, SCREENS, timeout_ms=300)
    assert failure.value.code == "OUTCOME_UNKNOWN"
