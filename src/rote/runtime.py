"""Runtime wiring: target app process, browser surface, policy, broker, operator page, per-run context."""

from __future__ import annotations

import asyncio
import os
import re
import secrets
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import urlparse

import httpx

from rote.config import (
    ENV_OPERATOR_ID,
    ENV_OPERATOR_PASSWORD,
    FAKEBANK_CONTROL_PORT,
    FAKEBANK_HOST,
    FAKEBANK_PORT,
    OPERATOR_PORT,
    ROOT,
)
from rote.evidence.recorder import RunRecorder
from rote.handoff.broker import Broker, ControlState
from rote.models import Intervention, Locator, Policy, ProductProfile
from rote.perception.masking import Masker
from rote.policy.engine import PolicyDecision, PolicyEngine
from rote.registry.store import Catalog
from rote.surface.snapshot import Snapshot
from rote.surface.web import WebSurface

if TYPE_CHECKING:
    from rote.gateway import ActionIntent
    from rote.handoff.operator_page import OperatorServer

SECRET_RE = re.compile(r"\{\{\s*secret:([\w/.-]+)\s*\}\}")
PARAM_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


class SecretStore:
    """Secrets live only in this object's memory. Never printed, logged, or written."""

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self._values = dict(values or {})

    def get(self, name: str) -> str:
        if name not in self._values:
            raise KeyError(f"secret {name!r} is not available")
        return self._values[name]

    def values(self) -> list[str]:
        return list(self._values.values())

    def __repr__(self) -> str:  # never reveal values
        return f"SecretStore({sorted(self._values)})"


def port_free(port: int, host: str = FAKEBANK_HOST) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) != 0


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((FAKEBANK_HOST, 0))
        return sock.getsockname()[1]


class FakeBankProcess:
    """Starts FakeBank as a child process with per-run generated operator credentials passed in memory."""

    def __init__(self, port: int = FAKEBANK_PORT, control_port: int = FAKEBANK_CONTROL_PORT, host: str = FAKEBANK_HOST) -> None:
        self.host, self.port, self.control_port = host, port, control_port
        self.proc: subprocess.Popen | None = None

    @property
    def origin(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def control_url(self) -> str:
        return f"http://{self.host}:{self.control_port}"

    async def start(self) -> SecretStore:
        operator_id = "OP" + secrets.token_hex(3).upper()
        password = secrets.token_urlsafe(18)
        env = dict(os.environ)
        env["FAKEBANK_OPERATOR_ID"] = operator_id
        env["FAKEBANK_OPERATOR_PASSWORD"] = password
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "fakebank", "--port", str(self.port), "--control-port", str(self.control_port), "--host", self.host],
            env=env, cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        async with httpx.AsyncClient(timeout=1.0) as client:
            for _ in range(100):
                if self.proc.poll() is not None:
                    raise RuntimeError("FakeBank exited during start-up")
                try:
                    if (await client.get(f"{self.control_url}/__health")).status_code == 200:
                        break
                except httpx.HTTPError:
                    await asyncio.sleep(0.2)
            else:
                raise RuntimeError("FakeBank did not become healthy")
        return SecretStore({"fakebank/operator_id": operator_id, "fakebank/operator_password": password})

    async def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


class FaultControl:
    def __init__(self, control_url: str) -> None:
        self.url = control_url

    async def _post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(self.url + path, json=body or {})
            response.raise_for_status()
            return response.json()

    async def set(self, faults: dict[str, str]) -> dict[str, Any]:
        return await self._post("/__faults", {"set": faults})

    async def clear(self) -> dict[str, Any]:
        return await self._post("/__faults/clear")

    async def reset(self) -> dict[str, Any]:
        return await self._post("/__reset")

    async def shares(self, member: str) -> dict[str, str]:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{self.url}/__state/shares", params={"m": member})
            response.raise_for_status()
            return response.json()


@dataclass
class RunContext:
    kind: str
    recorder: RunRecorder
    masker: Masker
    subject: str
    attended: bool = False
    params: dict[str, str] = field(default_factory=dict)


class Runtime:
    def __init__(
        self,
        *,
        catalog: Catalog,
        profile: ProductProfile,
        policy: Policy,
        origin: str,
        secrets_store: SecretStore,
        headed: bool = False,
    ) -> None:
        self.catalog = catalog
        self.profile = profile
        self.policy = policy
        self.policy_engine = PolicyEngine(policy)
        self.origin = origin
        self.secrets = secrets_store
        self.headed = headed
        self.broker = Broker()
        self.surface: WebSurface | None = None
        self.fakebank: FakeBankProcess | None = None
        self.faults: FaultControl | None = None
        self.operator: "OperatorServer | None" = None
        self.current_run: RunContext | None = None
        # reasons for navigations the network guard aborted, oldest first (discovery explains them to the agent)
        self.network_blocks: list[str] = []
        self.announce: Callable[[Intervention, str], None] = _default_announce

    # ---------------- construction ----------------
    @classmethod
    async def create(
        cls,
        *,
        target: str | None = None,
        product: str | None = None,
        headed: bool = False,
        operator_page: bool = True,
        auto_start: bool = True,
        fresh_ports: bool = False,
        catalog: Catalog | None = None,
    ) -> "Runtime":
        catalog = catalog or Catalog()
        profile = catalog.profile(product) if product else None
        if profile is None:
            profile = catalog.profile_for_origin(target) if target else catalog.profile(catalog.products()[0])
        if profile is None:
            raise ValueError(f"no product profile matches target {target!r}; pass --profile")
        policy = catalog.policy(profile.product)
        origin = (target or profile.entry.origin).rstrip("/")
        fakebank = None
        control_url = None
        parsed = urlparse(origin)
        local = parsed.hostname in ("127.0.0.1", "localhost")

        if local and auto_start and (fresh_ports or port_free(parsed.port or 80)):
            port = free_port() if fresh_ports else (parsed.port or FAKEBANK_PORT)
            control_port = free_port() if fresh_ports else FAKEBANK_CONTROL_PORT
            fakebank = FakeBankProcess(port=port, control_port=control_port)
            store = await fakebank.start()
            origin = fakebank.origin
            control_url = fakebank.control_url
        else:
            operator_id = os.environ.get(ENV_OPERATOR_ID)
            password = os.environ.get(ENV_OPERATOR_PASSWORD)
            if not operator_id or not password:
                raise RuntimeError(
                    f"{origin} is already running, so Rote needs its operator credentials in "
                    f"{ENV_OPERATOR_ID} and {ENV_OPERATOR_PASSWORD} (or stop it and let Rote start FakeBank itself)."
                )
            store = SecretStore({"fakebank/operator_id": operator_id, "fakebank/operator_password": password})
            if local:
                control_url = f"http://{parsed.hostname}:{FAKEBANK_CONTROL_PORT}"

        if origin != profile.entry.origin:
            # Same product on a different address (tests, demo on free ports): bind entry and allowlist to it, explicitly.
            profile = profile.model_copy(deep=True)
            profile.entry.origin = origin
            policy = policy.model_copy(deep=True)
            policy.allowed_origins = [origin]

        runtime = cls(catalog=catalog, profile=profile, policy=policy, origin=origin, secrets_store=store, headed=headed)
        runtime.fakebank = fakebank
        runtime.faults = FaultControl(control_url) if control_url else None
        runtime.surface = await WebSurface.launch(
            headed=headed, url_guard=runtime.policy_engine.url_allowed, on_network_block=runtime._on_network_block,
        )
        runtime.surface.human_callback = runtime._on_human
        runtime.broker.on_state_change.append(runtime._on_control_change)
        if operator_page:
            from rote.handoff.operator_page import OperatorServer

            runtime.operator = OperatorServer(runtime.broker, port=OPERATOR_PORT)
            await runtime.operator.start()
        return runtime

    async def close(self) -> None:
        if self.surface:
            await self.surface.close()
        if self.operator:
            await self.operator.stop()
        if self.fakebank:
            await self.fakebank.stop()

    # ---------------- runs ----------------
    def new_run(self, kind: str, *, subject: str, params: dict[str, str] | None = None, attended: bool = False) -> RunContext:
        masker = Masker(self.profile, params=params or {}, secrets=self.secrets.values())
        recorder = RunRecorder(kind, redact=masker.redact)
        run = RunContext(kind=kind, recorder=recorder, masker=masker, subject=subject, attended=attended, params=dict(params or {}))
        self.broker.attach(recorder)
        self.current_run = run
        return run

    def fill(self, template: str | None, params: dict[str, str]) -> str | None:
        if template is None:
            return None
        text = SECRET_RE.sub(lambda m: self.secrets.get(m.group(1)), template)
        return PARAM_RE.sub(lambda m: str(params.get(m.group(1), m.group(0))), text)

    def clean_locators(self, run: RunContext, locators: list[Locator]) -> list[Locator]:
        """Drop locators that embed data (names, balances); turn input values into placeholders; dedupe; cap."""
        cleaned: list[Locator] = []
        seen: set[str] = set()
        for loc in locators:
            data = loc.model_dump(exclude_none=True)
            ok = True

            def scrub(value: Any) -> Any:
                nonlocal ok
                if isinstance(value, str):
                    substituted = value
                    for name, real in sorted(run.params.items(), key=lambda kv: -len(kv[1])):
                        if real:
                            substituted = re.sub(rf"(?<![\w]){re.escape(real)}(?![\w])", "{{" + name + "}}", substituted, flags=re.IGNORECASE)
                    if run.masker.mask_text(substituted) != substituted:
                        ok = False
                    return substituted
                if isinstance(value, dict):
                    return {k: scrub(v) for k, v in value.items()}
                return value

            scrubbed = {k: (scrub(v) if k not in ("kind", "frame", "value") else v) for k, v in data.items()}
            if loc.kind == "css":
                scrubbed["value"] = data["value"]
            if not ok:
                continue
            key = repr(sorted(scrubbed.items()))
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(Locator(**scrubbed))
        durable = [c for c in cleaned if c.durable][:3]
        fragile = [c for c in cleaned if not c.durable][:1]
        if any(c.kind in ("table_cell", "table") for c in durable):
            fragile = []  # a positional fallback to a data cell could read the wrong row
        return durable + fragile

    async def masked_screenshot(self, run: RunContext, relative: str, snap: Snapshot | None = None) -> str | None:
        try:
            snap = snap or await self.surface.snapshot()
            run.masker.observe(snap)
            path = run.recorder.path(relative)
            saved = await self.surface.screenshot(path, run.masker.sensitive_values())
            return relative.replace("\\", "/") if saved else None
        except Exception:  # noqa: BLE001 - evidence must never break a run
            return None

    async def request_approval(self, run: RunContext, intent: "ActionIntent", decision: PolicyDecision, mode: str) -> tuple[bool, dict[str, Any]]:
        if not self.operator:
            run.recorder.emit("approval_resolved", summary="approval impossible: no operator available (unattended)", decision="rejected")
            return False, {"signal": "no_operator"}
        snap = await self.surface.snapshot()
        intervention = self.broker.new_intervention(
            kind="approve_action",
            run_id=run.recorder.run_id,
            mode=mode,
            capability=run.subject if mode == "replay" else None,
            goal=run.subject if mode == "discovery" else None,
            step={"id": intent.step_id},
            reason_code="IRREVERSIBLE_ACTION",
            reason=run.masker.mask_text(f"{decision.reason} (policy rule {decision.rule})"),
            proposed_action=run.masker.mask_text(f"{intent.action} {intent.target_desc} on screen {intent.screen or 'unknown'}"),
            screen_summary=run.masker.mask_text("; ".join(snap.headings())),
        )
        intervention.screenshot = await self.masked_screenshot(run, f"interventions/{intervention.id}/before.png", snap)
        self.announce(intervention, self.operator_url)
        signal, info = await self.broker.await_approval(intervention)
        run.recorder.write_json(f"interventions/{intervention.id}/request.json", intervention.model_dump())
        return signal == "approved", {"signal": signal, **info}

    @property
    def operator_url(self) -> str:
        return self.operator.url if self.operator else ""

    # ---------------- callbacks ----------------
    def _on_network_block(self, url: str, reason: str) -> None:
        self.network_blocks.append(reason)
        if self.current_run:
            self.current_run.recorder.emit("network_blocked", summary=f"network blocked: {reason}", url=url, reason=reason)

    async def _on_human(self, payload: dict[str, Any]) -> None:
        self.broker.record_human_action(payload)

    async def _on_control_change(self, state: ControlState) -> None:
        if self.headed and self.surface:
            await self.surface.set_block(state == ControlState.AUTOMATION)


def _default_announce(intervention: Intervention, url: str) -> None:
    print(
        f"\n>>> INTERVENTION {intervention.id} [{intervention.kind}] {intervention.reason_code}: {intervention.reason}\n"
        f">>> Operator page: {url}/interventions/{intervention.id}\n",
        flush=True,
    )
