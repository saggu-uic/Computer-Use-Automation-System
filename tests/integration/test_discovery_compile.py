"""Discovery with the scripted planner, compile, and validation replays."""

import json
from pathlib import Path

import pytest

from rote.agent.discovery import DiscoveryAgent
from rote.agent.inputs import build_bindings
from rote.agent.planners.scripted import ScriptedPlanner
from rote.compiler.compile import compile_run
from rote.compiler.validate import different_inputs, validate_capability
from rote.config import SCRIPTS_DIR
from rote.handoff.bot import OperatorBot
from rote.replay.engine import ReplayEngine

pytestmark = pytest.mark.integration


class FeedbackRecordingPlanner(ScriptedPlanner):
    """Scripted planner that keeps the feedback each turn received."""

    def __init__(self, script: Path) -> None:
        super().__init__(script)
        self.feedback: list[str] = []

    async def next_action(self, ctx):
        self.feedback.append(ctx.last_result)
        return await super().next_action(ctx)


async def discover(runtime, script: Path | str, goal: str | None = None, planner: ScriptedPlanner | None = None):
    path = Path(script) if isinstance(script, Path) else SCRIPTS_DIR / script
    data = json.loads(path.read_text(encoding="utf-8"))
    templated, bindings = build_bindings(goal or data["goal"], [])
    return await DiscoveryAgent(runtime, planner or ScriptedPlanner(path), goal_template=templated, bindings=bindings).run()


async def test_balance_discovery_compiles_and_validates(runtime):
    result = await discover(runtime, "get_savings_balance.json")
    assert result.status == "completed"
    compiled = compile_run(result.run_dir, runtime.profile, runtime.catalog)
    assert compiled.capability is not None, compiled.errors
    cap = compiled.capability
    assert list(cap.contract.inputs) == ["member_number"]
    assert cap.contract.inputs["member_number"].named_from.startswith("textbox labelled")
    assert {"MEMBER_NOT_FOUND", "ACCOUNT_RESTRICTED", "INVALID_MEMBER_NUMBER"} <= {o.code for o in cap.contract.outcomes}
    same = {compiled.input_map["value_1"]: "100245"}
    cap, results = await validate_capability(runtime, cap, same, different_inputs(cap, runtime.profile, same))
    assert cap.status == "validated", [r.error for r in results]
    not_found = await ReplayEngine(runtime, cap).run({"member_number": "999999"})
    assert not_found.status == "business_outcome" and not_found.outcome.code == "MEMBER_NOT_FOUND"
    # regression: a member without the row must be an outcome, never another row's balance
    assert not any(loc.kind == "css" for loc in cap.targets[cap.steps[-1].target].locators)
    no_savings = await ReplayEngine(runtime, cap).run({"member_number": "100512"})
    assert no_savings.status == "business_outcome" and no_savings.outcome.code == "NO_REGULAR_SAVINGS", no_savings


async def test_transactions_table_capability(runtime):
    result = await discover(runtime, "recent_transactions.json")
    compiled = compile_run(result.run_dir, runtime.profile, runtime.catalog)
    assert compiled.capability is not None, compiled.errors
    cap = compiled.capability
    output = cap.contract.outputs["recent_transactions"]
    assert output.type == "table" and output.columns["amount"] == "money"
    replay = await ReplayEngine(runtime, cap).run({"member_number": "100377"})
    assert replay.status == "success" and len(replay.outputs["recent_transactions"]) == 3
    none = await ReplayEngine(runtime, cap).run({"member_number": "100512"})
    assert none.status == "business_outcome" and none.outcome.code == "NO_TRANSACTIONS"
    invalid = await ReplayEngine(runtime, cap).run({"member_number": "55010"})
    assert invalid.status == "business_outcome" and invalid.outcome.code == "INVALID_MEMBER_NUMBER"


async def test_label_value_field_capability(runtime):
    result = await discover(runtime, "get_member_since.json")
    assert result.status == "completed", result.message
    compiled = compile_run(result.run_dir, runtime.profile, runtime.catalog)
    assert compiled.capability is not None, compiled.errors
    cap = compiled.capability
    extract = next(s for s in cap.steps if s.action == "extract")
    assert cap.targets[extract.target].locators[0].kind == "field"
    same = {compiled.input_map["value_1"]: "100245"}
    cap, results = await validate_capability(runtime, cap, same, different_inputs(cap, runtime.profile, same))
    assert cap.status == "validated", [r.error for r in results]
    joined = await ReplayEngine(runtime, cap).run({"member_number": "100377"})
    assert joined.status == "success" and joined.outputs["member_since"] == "2016-09-02", joined
    missing = await ReplayEngine(runtime, cap).run({"member_number": "999999"})
    assert missing.status == "business_outcome" and missing.outcome.code == "MEMBER_NOT_FOUND"
    restricted = await ReplayEngine(runtime, cap).run({"member_number": "100733"})
    assert restricted.status == "business_outcome" and restricted.outcome.code == "ACCOUNT_RESTRICTED"


async def test_goal_not_achievable(runtime):
    result = await discover(runtime, "wire_transfer_not_possible.json")
    assert result.status == "GOAL_NOT_ACHIEVABLE"
    assert compile_run(result.run_dir, runtime.profile, runtime.catalog).capability is None


async def test_misled_agent_cannot_close_a_share(runtime):
    await runtime.faults.reset()
    bot = OperatorBot(runtime.operator_url, approve=lambda item: False).start()
    planner = FeedbackRecordingPlanner(SCRIPTS_DIR / "misled_close_share.json")
    try:
        result = await discover(runtime, "misled_close_share.json", planner=planner)
    finally:
        await bot.stop()
    events = [json.loads(line) for line in (result.run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(e["type"] == "approval_resolved" and e["data"]["intervention"]["resolution"] == "rejected" for e in events)
    assert any(e["type"] == "network_blocked" for e in events)
    # regression: a blocked navigation is explained to the agent, not shown as a blank screen
    assert any(f.startswith("BLOCKED by policy") and "ADMIN" in f for f in planner.feedback), planner.feedback
    assert set((await runtime.faults.shares("100901")).values()) == {"ACTIVE"}


async def test_human_flow_steps_are_not_compilable(runtime, tmp_path):
    script = tmp_path / "ask.json"
    script.write_text(json.dumps({"name": "ask", "steps": [
        {"tool": "ask_human", "reason": "unsure where members are", "question": "please open member inquiry"},
        {"tool": "done", "summary": "human did it", "capability_name": "human_did_it"},
    ]}), encoding="utf-8")

    async def human(_item):
        await runtime.surface.frame("nav").click("text=MEMBER INQ")

    bot = OperatorBot(runtime.operator_url, on_take_control=human).start()
    try:
        result = await discover(runtime, script, goal="Open member inquiry")
    finally:
        await bot.stop()
    assert result.status == "completed"
    compiled = compile_run(result.run_dir, runtime.profile, runtime.catalog)
    assert compiled.capability is None
    assert compiled.errors[0].startswith("HUMAN_STEPS_NOT_COMPILABLE")
