"""Rote command line. The canonical demo path (README) uses these commands."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Optional

import typer

from rote.config import (
    ENV_ANTHROPIC_KEY,
    ENV_GEMINI_KEY,
    ENV_OPERATOR_ID,
    ENV_OPERATOR_PASSWORD,
    EVIDENCE_DIR,
    FAKEBANK_CONTROL_PORT,
    SCHEMAS_DIR,
    load_dotenv_keys,
)

app = typer.Typer(help="Rote: an LLM discovers a task once; deterministic replay performs it every time after.", no_args_is_help=True, add_completion=False)
fakebank_app = typer.Typer(help="FakeBank Core, the target application.", no_args_is_help=True)
faults_app = typer.Typer(help="Fault injection (separate control port, never reachable by automation).", no_args_is_help=True)
evidence_app = typer.Typer(help="Evidence helpers.", no_args_is_help=True)
catalog_app = typer.Typer(help="Catalog helpers.", no_args_is_help=True)
schema_app = typer.Typer(help="JSON Schemas for reviewers and other languages.", no_args_is_help=True)
app.add_typer(fakebank_app, name="app")
fakebank_app.add_typer(faults_app, name="faults")
app.add_typer(evidence_app, name="evidence")
app.add_typer(catalog_app, name="catalog")
app.add_typer(schema_app, name="schema")

DEFAULT_TARGET = "http://127.0.0.1:8700"


@app.callback()
def _load_local_env() -> None:
    # The model key may live in a gitignored .env at the project root; real environment variables win.
    load_dotenv_keys()


def _pairs(values: list[str], what: str) -> dict[str, str]:
    out = {}
    for item in values:
        if "=" not in item:
            raise typer.BadParameter(f"{what} must be name=value, got {item!r}")
        key, value = item.split("=", 1)
        out[key.strip()] = value
    return out


def _echo_json(data: Any) -> None:
    typer.echo(json.dumps(data, indent=2, ensure_ascii=False, default=str))


# ---------------------------------------------------------------- FakeBank
@fakebank_app.command("start")
def app_start(port: int = 8700, control_port: int = FAKEBANK_CONTROL_PORT, host: str = "127.0.0.1") -> None:
    """Run FakeBank in the foreground. Needs FAKEBANK_OPERATOR_ID and FAKEBANK_OPERATOR_PASSWORD."""
    from fakebank.__main__ import serve

    typer.echo(f"FakeBank Core on http://{host}:{port} (fault control on :{control_port}). Ctrl+C to stop.")
    typer.echo(f"Rote commands against this instance need {ENV_OPERATOR_ID} and {ENV_OPERATOR_PASSWORD} set to the same values.")
    asyncio.run(serve(port, control_port, host))


@faults_app.command("set")
def faults_set(faults: list[str] = typer.Argument(..., help="e.g. notice=once latency.member_detail=10000"), control: str = f"http://127.0.0.1:{FAKEBANK_CONTROL_PORT}") -> None:
    from rote.runtime import FaultControl

    _echo_json(asyncio.run(FaultControl(control).set(_pairs(faults, "fault"))))


@faults_app.command("clear")
def faults_clear(control: str = f"http://127.0.0.1:{FAKEBANK_CONTROL_PORT}") -> None:
    from rote.runtime import FaultControl

    _echo_json(asyncio.run(FaultControl(control).clear()))


@fakebank_app.command("reset")
def app_reset(control: str = f"http://127.0.0.1:{FAKEBANK_CONTROL_PORT}") -> None:
    """Restore seed data and clear faults."""
    from rote.runtime import FaultControl

    _echo_json(asyncio.run(FaultControl(control).reset()))


# ---------------------------------------------------------------- discover
@app.command()
def discover(
    goal: str = typer.Option(..., help="The goal in plain English."),
    target: str = typer.Option(DEFAULT_TARGET, help="Application entry point (URL)."),
    name: Optional[str] = typer.Option(None, help="Capability name (otherwise the agent proposes one)."),
    input: list[str] = typer.Option([], "--input", help="Explicit input name=value[:type]; overrides detection."),
    output: list[str] = typer.Option([], "--output", help="Requested output name:type (optional)."),
    planner: str = typer.Option("llm", help="llm | scripted"),
    provider: str = typer.Option("anthropic", help="anthropic | gemini (for --planner llm)"),
    model: Optional[str] = typer.Option(None, help="Model id (default claude-opus-5 or gemini-2.5-flash)."),
    script: Optional[Path] = typer.Option(None, help="Script for --planner scripted."),
    max_steps: int = 30,
    max_minutes: float = 10.0,
    max_cost_usd: float = 0.50,
    yes: bool = typer.Option(False, "--yes", help="Skip the detected-inputs confirmation."),
    no_detect: bool = typer.Option(False, "--no-detect", help="Do not detect inputs in the goal."),
    headed: bool = typer.Option(False, help="Show the browser window."),
    validate_with: list[str] = typer.Option([], "--validate-with", help="Different input for the validation replay, name=value."),
    no_save: bool = typer.Option(False, "--no-save", help="Do not write the capability to the catalog."),
    fault: list[str] = typer.Option([], "--fault", help="Inject a FakeBank fault before the run."),
) -> None:
    """Discover how to achieve a goal with an LLM, compile it into a capability, and validate it by replay."""
    from rote.agent.inputs import build_bindings

    templated, bindings = build_bindings(goal, input, detect=not no_detect)
    if bindings:
        typer.echo("Detected inputs: " + ", ".join(b.describe() for b in bindings))
        if not yes and not typer.confirm("Continue?", default=True):
            raise typer.Exit(1)
    outputs = {}
    for item in output:
        out_name, _, out_type = item.partition(":")
        outputs[out_name] = out_type or "string"
    sys.exit(asyncio.run(_discover(templated, bindings, outputs, target, name, planner, provider, model, script,
                                   max_steps, max_minutes, max_cost_usd, headed, _pairs(validate_with, "--validate-with"),
                                   no_save, _pairs(fault, "--fault"))))


async def _discover(templated, bindings, outputs, target, name, planner_kind, provider, model, script, max_steps, max_minutes,
                    max_cost_usd, headed, validate_with, no_save, faults) -> int:
    from rote.agent.discovery import DiscoveryAgent, DiscoveryLimits
    from rote.agent.planners import create_planner
    from rote.compiler.compile import compile_run
    from rote.compiler.validate import different_inputs, validate_capability
    from rote.registry.describe import describe
    from rote.registry.store import dump_model
    from rote.runtime import Runtime

    planner = create_planner(planner_kind, provider=provider, model=model, script=script)
    runtime = await Runtime.create(target=target, headed=headed)
    try:
        typer.echo(f"Target: {runtime.profile.display_name} ({runtime.profile.product}) → {runtime.origin}")
        typer.echo(f"Operator page: {runtime.operator_url}")
        if faults and runtime.faults:
            await runtime.faults.set(faults)
        agent = DiscoveryAgent(runtime, planner, goal_template=templated, bindings=bindings, outputs=outputs,
                               limits=DiscoveryLimits(max_steps, max_minutes, max_cost_usd), name=name)
        result = await agent.run()
        usage = result.usage
        typer.echo(f"\nDiscovery {result.status}{' ' + result.code if result.code else ''}: {result.message}")
        typer.echo(f"LLM: {usage.get('calls', 0)} calls · {usage.get('input_tokens', 0) + usage.get('output_tokens', 0)} tokens · ${usage.get('cost_usd', 0):.4f}")
        typer.echo(f"Evidence: {result.run_dir}")
        if result.status != "completed":
            return 0 if result.status == "GOAL_NOT_ACHIEVABLE" else 1
        compiled = compile_run(result.run_dir, runtime.profile, runtime.catalog, name=name)
        typer.echo("\n" + compiled.report)
        if compiled.capability is None:
            return 1
        cap = compiled.capability
        same = {compiled.input_map[ph]: value for ph, value in result.params.items() if ph in compiled.input_map}
        other = different_inputs(cap, runtime.profile, same, validate_with)
        cap, results = await validate_capability(runtime, cap, same, other)
        for r in results:
            typer.echo(f"validation replay {r.invocation_id}: {r.status}{' ' + r.error.code if r.error else ''}")
        (result.run_dir / "compile" / "capability.json").write_text(json.dumps(dump_model(cap), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if not no_save:
            existing = runtime.catalog.find_same_content(cap)
            if existing:
                stored, path = existing
                if cap.status == "validated" and stored.status != "validated":
                    stored.status, stored.provenance = "validated", cap.provenance
                    runtime.catalog.save_capability(stored)
                typer.echo(f"\nSame content already in the catalog: {path}")
            else:
                typer.echo(f"\nSaved: {runtime.catalog.save_capability(cap)}")
        typer.echo("\n" + describe(cap, runtime.profile))
        return 0
    finally:
        await runtime.close()


# ---------------------------------------------------------------- replay
@app.command()
def replay(
    ref: str = typer.Argument(..., help="Capability id[@version] or path to a capability JSON."),
    input: list[str] = typer.Option([], "--input", help="name=value"),
    target: str = typer.Option(DEFAULT_TARGET),
    attended: bool = typer.Option(False, help="Escalations pause for a human (operator page)."),
    headed: bool = typer.Option(False, help="Show the browser window (use with --attended for manual handoff)."),
    repeat: int = typer.Option(1, help="Run N times and print a stability summary."),
    fault: list[str] = typer.Option([], "--fault", help="Inject a FakeBank fault before the first run."),
    as_json: bool = typer.Option(False, "--json", help="Print only the result JSON."),
) -> None:
    """Replay a capability deterministically (no LLM) and print the typed result."""
    sys.exit(asyncio.run(_replay(ref, _pairs(input, "--input"), target, attended, headed, repeat, _pairs(fault, "--fault"), as_json)))


async def _replay(ref, inputs, target, attended, headed, repeat, faults, as_json) -> int:
    from rote.replay.engine import ReplayEngine
    from rote.runtime import Runtime

    runtime = await Runtime.create(target=target, headed=headed, operator_page=attended)
    try:
        cap, path = runtime.catalog.load_capability(ref)
        if not as_json:
            typer.echo(f"Replaying {cap.ref} ({path}) against {runtime.origin} · {'attended' if attended else 'unattended'} · no LLM")
            if attended:
                typer.echo(f"Operator page: {runtime.operator_url}")
        if faults and runtime.faults:
            await runtime.faults.set(faults)
        results = []
        for _ in range(repeat):
            result = await ReplayEngine(runtime, cap, attended=attended).run(inputs)
            results.append(result)
            _echo_json(result.model_dump(mode="json"))
        if repeat > 1:
            statuses = {}
            for r in results:
                key = r.status + (f":{r.outcome.code}" if r.outcome else "") + (f":{r.error.code}" if r.error else "")
                statuses[key] = statuses.get(key, 0) + 1
            times = [r.timing_ms.get("total", 0) for r in results]
            typer.echo(f"\nStability over {repeat} runs: {statuses} · mean {sum(times) / len(times) / 1000:.2f} s · max {max(times) / 1000:.2f} s")
        return 1 if any(r.status == "failure" for r in results) else 0
    finally:
        await runtime.close()


@app.command()
def validate(ref: str, input: list[str] = typer.Option([], "--input"), other: list[str] = typer.Option([], "--other"), target: str = DEFAULT_TARGET) -> None:
    """Validation replays (same input + different input); marks the stored capability validated if both succeed."""

    async def run() -> int:
        from rote.compiler.validate import different_inputs, validate_capability
        from rote.runtime import Runtime

        runtime = await Runtime.create(target=target)
        try:
            cap, _path = runtime.catalog.load_capability(ref)
            same = _pairs(input, "--input")
            second = different_inputs(cap, runtime.profile, same, _pairs(other, "--other"))
            cap, results = await validate_capability(runtime, cap, same, second)
            for r in results:
                typer.echo(f"{r.invocation_id}: {r.status}{' ' + r.error.code if r.error else ''}")
            typer.echo(f"status: {cap.status} · saved {runtime.catalog.save_capability(cap)}")
            return 0 if cap.status == "validated" else 1
        finally:
            await runtime.close()

    sys.exit(asyncio.run(run()))


@app.command("describe")
def describe_cmd(ref: str) -> None:
    """Plain-language summary of a capability for reviewers."""
    from rote.registry.describe import describe
    from rote.registry.store import Catalog

    catalog = Catalog()
    cap, _path = catalog.load_capability(ref)
    typer.echo(describe(cap, catalog.profile(cap.app.product)))


@app.command()
def demo(headed: bool = False) -> None:
    """Run every scripted scenario against a fresh FakeBank and regenerate evidence (no API key needed)."""
    from rote.demo import run_demo

    sys.exit(asyncio.run(run_demo(headed=headed)))


@app.command()
def doctor() -> None:
    """Check the environment (never prints secret values)."""
    from rote.registry.store import Catalog

    ok = True
    typer.echo(f"python {sys.version.split()[0]}")
    try:
        catalog = Catalog()
        for product in catalog.products():
            catalog.profile(product)
            catalog.policy(product)
            typer.echo(f"catalog: {product} profile and policy load")
        typer.echo(f"capabilities: {len(catalog.all_capabilities())}")
    except Exception as exc:  # noqa: BLE001
        ok = False
        typer.echo(f"catalog problem: {exc}")

    async def browser() -> bool:
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            b = await pw.chromium.launch()
            await b.close()
        return True

    try:
        asyncio.run(browser())
        typer.echo("chromium: ok")
    except Exception as exc:  # noqa: BLE001
        ok = False
        typer.echo(f"chromium: missing ({str(exc).splitlines()[0]}) → run `playwright install chromium`")
    for var in (ENV_ANTHROPIC_KEY, ENV_GEMINI_KEY, ENV_OPERATOR_ID, ENV_OPERATOR_PASSWORD):
        typer.echo(f"{var}: {'set' if os.environ.get(var) else 'not set'}")
    typer.echo("ok" if ok else "problems found")


# ---------------------------------------------------------------- evidence, catalog, schema
@evidence_app.command("timeline")
def evidence_timeline(run_dir: Path) -> None:
    from rote.evidence.timeline import build_timeline

    text = build_timeline(run_dir)
    (run_dir / "timeline.md").write_text(text, encoding="utf-8")
    typer.echo(text)


@evidence_app.command("save")
def evidence_save(run_dir: Path, name: str) -> None:
    """Copy a run folder into evidence/<name> (for live discovery runs)."""
    destination = EVIDENCE_DIR / name
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(run_dir, destination)
    typer.echo(f"saved {destination}")


@evidence_app.command("costs")
def evidence_costs() -> None:
    from rote.demo import cost_table

    table = cost_table(EVIDENCE_DIR)
    (EVIDENCE_DIR / "costs.md").write_text(table, encoding="utf-8")
    typer.echo(table)


@catalog_app.command("list")
def catalog_list() -> None:
    from rote.registry.store import Catalog

    for cap in Catalog().all_capabilities():
        typer.echo(f"{cap.id}@{cap.version}  [{cap.status}]  {cap.contract.summary}")


@catalog_app.command("stamp")
def catalog_stamp(path: Path) -> None:
    """Record the current profile hash and content hash in a hand-authored capability after reviewing it."""
    from rote.models import Capability
    from rote.registry.store import Catalog, content_hash

    catalog = Catalog()
    cap = Capability.model_validate_json(path.read_text(encoding="utf-8"))
    cap.app.profile.hash = content_hash(catalog.profile(cap.app.product))
    typer.echo(f"stamped {catalog.save_capability(cap)}")


@schema_app.command("export")
def schema_export() -> None:
    from rote.models import Capability, CapabilityResult, Intervention, Policy, ProductProfile

    SCHEMAS_DIR.mkdir(exist_ok=True)
    for model in (Capability, ProductProfile, Policy, CapabilityResult, Intervention):
        path = SCHEMAS_DIR / f"{model.__name__.lower()}.schema.json"
        path.write_text(json.dumps(model.model_json_schema(by_alias=True), indent=2) + "\n", encoding="utf-8")
        typer.echo(f"wrote {path}")


if __name__ == "__main__":
    app()
