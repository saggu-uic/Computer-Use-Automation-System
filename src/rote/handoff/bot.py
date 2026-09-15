"""Test operator bot: plays the human through the operator page's JSON API.

Used by tests and `rote demo` so the handoff (take control -> human acts in the same live session ->
resume -> verification) is exercised end to end without a person at the keyboard.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

import httpx

HumanActions = Callable[[dict[str, Any]], Awaitable[None]]
ApprovalPolicy = Callable[[dict[str, Any]], bool]


class OperatorBot:
    def __init__(
        self,
        base_url: str,
        *,
        name: str = "test-operator-bot",
        on_take_control: HumanActions | None = None,
        approve: ApprovalPolicy | None = None,
        note: str = "",
    ) -> None:
        self.base_url = base_url
        self.name = name
        self.on_take_control = on_take_control
        self.approve = approve
        self.note = note
        self.handled: dict[str, int] = {}
        self.errors: list[str] = []
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self) -> "OperatorBot":
        self._task = asyncio.create_task(self._run())
        return self

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await asyncio.wait_for(self._task, timeout=5)

    async def _run(self) -> None:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=10.0) as client:
            while not self._stop.is_set():
                try:
                    items = (await client.get("/api/interventions")).json()
                except httpx.HTTPError:
                    items = []
                for item in items:
                    if item["status"] != "open" or self.handled.get(item["id"], 0) >= 2:
                        continue
                    self.handled[item["id"]] = self.handled.get(item["id"], 0) + 1
                    iid = item["id"]
                    try:
                        if item["kind"] == "approve_action":
                            decision = "approve" if (self.approve and self.approve(item)) else "reject"
                            response = await client.post(f"/api/interventions/{iid}/{decision}", json={"operator": self.name, "note": self.note or f"bot {decision}d"})
                            if response.status_code != 200:
                                self.handled[iid] -= 1  # not actionable yet; try again on the next poll
                            continue
                        response = await client.post(f"/api/interventions/{iid}/take-control", json={"operator": self.name})
                        if response.status_code != 200:
                            self.handled[iid] -= 1  # a human must never act without holding control
                            continue
                        await asyncio.sleep(0.3)
                        if self.on_take_control:
                            await asyncio.wait_for(self.on_take_control(item), timeout=20)
                        await asyncio.sleep(0.3)
                        await client.post(f"/api/interventions/{iid}/resume", json={"operator": self.name, "note": self.note})
                    except Exception as exc:  # noqa: BLE001 - a broken bot must not hang the run silently
                        detail = f"{type(exc).__name__}: {exc}".rstrip(": ")
                        self.errors.append(f"{iid}: {detail}")
                        await client.post(f"/api/interventions/{iid}/abort", json={"operator": self.name, "note": f"bot error: {detail}"})
                await asyncio.sleep(0.25)
