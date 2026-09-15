"""Minimal operator page: see interventions, take control, resume, abort, approve, reject.

Served by the running Rote process. The same actions are available as a small JSON API, which the
test operator bot uses so the handoff is exercised end to end in tests and the demo.
"""

from __future__ import annotations

import asyncio
import html
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from rote.config import OPERATOR_PORT, RUNS_DIR
from rote.handoff.broker import Broker, IllegalTransition

STYLE = """
body{font-family:system-ui,Segoe UI,Arial,sans-serif;margin:24px;max-width:980px;color:#1b1b1b}
h1{font-size:20px} table{border-collapse:collapse;width:100%} td,th{border-bottom:1px solid #ddd;padding:6px 8px;text-align:left;font-size:14px}
.state{display:inline-block;padding:3px 8px;border-radius:4px;background:#eef;font-weight:600}
.box{border:1px solid #ccc;border-radius:6px;padding:12px 16px;margin:12px 0}
button{padding:6px 14px;margin-right:8px} input{padding:5px} img{max-width:100%;border:1px solid #ccc}
code{background:#f4f4f4;padding:1px 4px}
"""


def create_app(broker: Broker, runs_dir: Path = RUNS_DIR) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    e = html.escape

    def page(title: str, body: str, refresh: bool = True) -> HTMLResponse:
        meta = '<meta http-equiv="refresh" content="4">' if refresh else ""
        return HTMLResponse(f"<!doctype html><html><head><title>{e(title)}</title>{meta}<style>{STYLE}</style></head><body>{body}</body></html>")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        rows = "".join(
            f"<tr><td><a href='/interventions/{i.id}'>{i.id}</a></td><td>{e(i.kind)}</td><td>{e(i.status)}</td>"
            f"<td><code>{e(i.reason_code)}</code></td><td>{e(i.capability or i.goal or '')}</td><td>{e(i.created_at)}</td></tr>"
            for i in sorted(broker.interventions.values(), key=lambda x: x.created_at, reverse=True)
        ) or "<tr><td colspan=6>No interventions yet.</td></tr>"
        body = (
            f"<h1>Rote operator page</h1><p>Control state: <span class='state'>{broker.state.value}</span></p>"
            f"<table><tr><th>ID</th><th>Kind</th><th>Status</th><th>Reason</th><th>Capability / goal</th><th>Created</th></tr>{rows}</table>"
        )
        return page("Rote operator", body)

    @app.get("/interventions/{iid}", response_class=HTMLResponse)
    async def detail(iid: str):
        i = broker.interventions.get(iid)
        if not i:
            return page("Not found", "<p>No such intervention.</p>", refresh=False)
        actions = ""
        if i.kind == "take_control" and i.status == "open":
            actions = (
                f"<form method='post' action='/api/interventions/{i.id}/take-control'>"
                "Operator name <input name='operator' value='operator' required> <button>Take control</button></form>"
                f"<form method='post' action='/api/interventions/{i.id}/abort'><button>Abort run</button></form>"
            )
        elif i.kind == "take_control" and i.status == "in_progress":
            actions = (
                "<p><b>You have control.</b> Use the browser window, then resume.</p>"
                f"<form method='post' action='/api/interventions/{i.id}/resume'>Note <input name='note' size=50> <button>Resume automation</button></form>"
                f"<form method='post' action='/api/interventions/{i.id}/abort'><button>Abort run</button></form>"
            )
        elif i.kind == "approve_action" and i.status == "open":
            actions = (
                f"<form method='post' action='/api/interventions/{i.id}/approve'>Operator <input name='operator' value='operator' required> <button>Approve action</button></form>"
                f"<form method='post' action='/api/interventions/{i.id}/reject'>Operator <input name='operator' value='operator' required> Note <input name='note'> <button>Reject</button></form>"
            )
        expectation = ""
        if i.resume_expectation:
            expectation = (
                f"<p><b>Automation expects on resume:</b> {e(' or '.join(i.resume_expectation.expected_screens) or 'no overlays')}</p>"
                f"<p>{e(i.resume_expectation.instructions)}</p>"
            )
        screenshot = f"<img src='/interventions/{i.id}/screenshot' alt='masked screenshot'>" if i.screenshot else ""
        verification = f"<p><b>Last resume check:</b> {e(i.verification_message)}</p>" if i.verification_message else ""
        body = (
            f"<p><a href='/'>&larr; all interventions</a></p><h1>{e(i.id)} · {e(i.kind)} · {e(i.status)}</h1>"
            f"<div class='box'><p><b>Reason:</b> <code>{e(i.reason_code)}</code> {e(i.reason)}</p>"
            f"<p><b>Capability / goal:</b> {e(i.capability or i.goal or '')}</p><p><b>Step:</b> {e(str(i.step))}</p>"
            f"<p><b>Screen:</b> {e(i.screen_summary)}</p>"
            + (f"<p><b>Proposed action:</b> {e(i.proposed_action)}</p>" if i.proposed_action else "")
            + expectation + verification + f"</div><div class='box'>{actions or 'No actions available.'}</div>{screenshot}"
        )
        return page(i.id, body, refresh=i.status in ("open", "in_progress"))

    @app.get("/interventions/{iid}/screenshot")
    async def screenshot(iid: str):
        i = broker.interventions.get(iid)
        if not i or not i.screenshot:
            return JSONResponse({"error": "no screenshot"}, status_code=404)
        path = runs_dir / i.run_id / i.screenshot
        if not path.exists():
            return JSONResponse({"error": "missing"}, status_code=404)
        return FileResponse(path)

    @app.get("/api/state")
    async def state():
        return {"state": broker.state.value, "current": broker.current}

    @app.get("/api/interventions")
    async def list_interventions():
        return [i.model_dump() for i in broker.interventions.values()]

    @app.get("/api/interventions/{iid}")
    async def get_intervention(iid: str):
        i = broker.interventions.get(iid)
        return i.model_dump() if i else JSONResponse({"error": "not found"}, status_code=404)

    @app.post("/api/interventions/{iid}/{action}")
    async def act(iid: str, action: str, request: Request):
        is_form = "form" in request.headers.get("content-type", "")
        body = dict(await request.form()) if is_form else (await request.json() if await request.body() else {})
        operator = str(body.get("operator") or "operator")
        note = str(body.get("note") or "")
        try:
            if action == "take-control":
                broker.take_control(iid, operator)
            elif action == "resume":
                broker.resume(iid, note)
            elif action == "abort":
                broker.abort(iid, operator, note)
            elif action == "approve":
                broker.approve(iid, operator)
            elif action == "reject":
                broker.reject(iid, operator, note)
            else:
                return JSONResponse({"error": f"unknown action {action}"}, status_code=404)
        except KeyError:
            return JSONResponse({"error": "not found"}, status_code=404)
        except IllegalTransition as exc:
            if is_form:
                return RedirectResponse(f"/interventions/{iid}", status_code=303)
            return JSONResponse({"error": str(exc)}, status_code=409)
        await asyncio.sleep(0)
        if is_form:
            return RedirectResponse(f"/interventions/{iid}", status_code=303)
        return broker.interventions[iid].model_dump()

    return app


class OperatorServer:
    def __init__(self, broker: Broker, host: str = "127.0.0.1", port: int = OPERATOR_PORT) -> None:
        self.broker = broker
        self.host = host
        self.port = port
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    async def start(self) -> None:
        from rote.runtime import free_port, port_free

        if not port_free(self.port, self.host):
            self.port = free_port()
        config = uvicorn.Config(create_app(self.broker), host=self.host, port=self.port, log_level="warning", lifespan="off")
        self._server = uvicorn.Server(config)
        self._task = asyncio.create_task(self._server.serve())
        for _ in range(100):
            if self._server.started:
                return
            await asyncio.sleep(0.05)
        raise RuntimeError("operator page did not start")

    async def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except Exception:  # noqa: BLE001
                pass
