"""FakeBank Core web app (port 8700) and fault control API (port 8701)."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from fakebank.faults import Faults
from fakebank.seed import Store, fmt_money

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
COOKIE = "FBSESSID"
RELEASE = "R3.2"


class Bank:
    def __init__(self, operator_id: str, operator_password: str) -> None:
        self.operator_id = operator_id
        self.operator_password = operator_password
        self.sessions: dict[str, str] = {}
        self.expired: set[str] = set()
        self.store = Store()
        self.faults = Faults()


def credentials_from_env() -> tuple[str, str]:
    operator_id = os.environ.get("FAKEBANK_OPERATOR_ID")
    password = os.environ.get("FAKEBANK_OPERATOR_PASSWORD")
    if not operator_id or not password:
        raise SystemExit(
            "FakeBank needs FAKEBANK_OPERATOR_ID and FAKEBANK_OPERATOR_PASSWORD. "
            "`rote demo` and `rote discover/replay` start FakeBank with generated values automatically."
        )
    return operator_id, password


def create_apps(bank: Bank) -> tuple[FastAPI, FastAPI]:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    control = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def render(request: Request, template: str, status: int = 200, **ctx) -> HTMLResponse:
        ctx.setdefault("overlays", [])
        return TEMPLATES.TemplateResponse(request, template, {"release": RELEASE, **ctx}, status_code=status)

    def sign_on(request: Request, message: str = "") -> HTMLResponse:
        return render(request, "signon.html", message=message)

    def guard(request: Request, screen: str) -> Response | None:
        """Session, fault, and latency handling shared by every content screen."""
        token = request.cookies.get(COOKIE)
        if not token or token not in bank.sessions:
            expired = token in bank.expired if token else False
            return sign_on(request, "SESSION EXPIRED - PLEASE SIGN ON" if expired else "")
        if bank.faults.session_should_expire():
            bank.sessions.pop(token, None)
            bank.expired.add(token)
            return sign_on(request, "SESSION EXPIRED - PLEASE SIGN ON")
        if bank.faults.take_once("error500", screen):
            return render(request, "error500.html", status=500)
        if bank.faults.take_once("unauthorized", screen):
            return render(request, "unauthorized.html")
        delay = bank.faults.latency.get(screen)
        if delay and request.query_params.get("_ready") != "1":
            sep = "&" if request.url.query else "?"
            target = f"{request.url.path}?{request.url.query}{sep}_ready=1" if request.url.query else f"{request.url.path}?_ready=1"
            return render(request, "processing.html", target=target, delay=delay)
        return None

    def overlays(screen: str) -> list[str]:
        found = []
        if bank.faults.take_notice():
            found.append("notice")
        if screen == "member_detail":
            if bank.faults.take_flag("supervisor_override"):
                found.append("supervisor_override")
            if bank.faults.take_flag("unknown_popup"):
                found.append("unknown_popup")
        return found

    @app.get("/", response_class=HTMLResponse)
    async def frameset(request: Request):
        return render(request, "frameset.html")

    @app.get("/cgi/BANNER", response_class=HTMLResponse)
    async def banner(request: Request):
        return render(request, "banner.html")

    @app.get("/cgi/NAV", response_class=HTMLResponse)
    async def nav(request: Request):
        return render(request, "nav.html")

    @app.get("/cgi/SIGNON", response_class=HTMLResponse)
    async def signon_get(request: Request):
        return sign_on(request)

    @app.post("/cgi/SIGNON")
    async def signon_post(request: Request, F0001: str = Form(""), F0002: str = Form("")):
        ok = secrets.compare_digest(F0001, bank.operator_id) and secrets.compare_digest(F0002, bank.operator_password)
        if not ok:
            return sign_on(request, "INVALID OPERATOR ID OR PASSWORD")
        token = secrets.token_hex(16)
        bank.sessions[token] = F0001
        response = RedirectResponse("/cgi/MENU", status_code=303)
        response.set_cookie(COOKIE, token, httponly=True, samesite="lax")
        return response

    @app.get("/cgi/SIGNOFF", response_class=HTMLResponse)
    async def signoff(request: Request):
        bank.sessions.pop(request.cookies.get(COOKIE, ""), None)
        return sign_on(request, "SIGNED OFF")

    @app.get("/cgi/MENU", response_class=HTMLResponse)
    async def menu(request: Request):
        if (blocked := guard(request, "menu")) is not None:
            return blocked
        return render(request, "menu.html", overlays=overlays("menu"))

    @app.get("/cgi/MBRINQ", response_class=HTMLResponse)
    async def member_inquiry(request: Request):
        if (blocked := guard(request, "member_inquiry")) is not None:
            return blocked
        number = request.query_params.get("F0031")
        message = ""
        if number is not None:
            number = number.strip()
            if not (number.isdigit() and 6 <= len(number) <= 10):
                message = "MEMBER # MUST BE 6-10 DIGITS"
            elif bank.store.get(number) is None:
                message = "NO MEMBER FOUND FOR NUMBER ENTERED"
            else:
                return RedirectResponse(f"/cgi/MBRDTL?m={number}", status_code=303)
        return render(request, "member_inquiry.html", message=message, overlays=overlays("member_inquiry"))

    @app.get("/cgi/MBRDTL", response_class=HTMLResponse)
    async def member_detail(request: Request, m: str = ""):
        if (blocked := guard(request, "member_detail")) is not None:
            return blocked
        member = bank.store.get(m)
        if member is None:
            return render(request, "member_inquiry.html", message="NO MEMBER FOUND FOR NUMBER ENTERED")
        if member.restricted:
            return render(request, "restricted.html", member=member)
        shares = [
            {"share_id": s.share_id, "type": s.type, "balance": fmt_money(s.balance), "status": s.status}
            for s in member.shares
        ]
        return render(request, "member_detail.html", member=member, shares=shares, overlays=overlays("member_detail"))

    @app.get("/cgi/MBRTRN", response_class=HTMLResponse)
    async def member_transactions(request: Request, m: str = ""):
        if (blocked := guard(request, "member_transactions")) is not None:
            return blocked
        member = bank.store.get(m)
        if member is None:
            return render(request, "member_inquiry.html", message="NO MEMBER FOUND FOR NUMBER ENTERED")
        if member.restricted:
            return render(request, "restricted.html", member=member)
        rows = bank.store.recent_transactions(member)
        return render(request, "transactions.html", member=member, rows=rows, overlays=overlays("member_transactions"))

    @app.post("/cgi/SHRCLS")
    async def close_share(request: Request, m: str = Form(""), s: str = Form("")):
        if (blocked := guard(request, "member_detail")) is not None:
            return blocked
        member = bank.store.get(m)
        if member:
            for share in member.shares:
                if share.share_id == s:
                    share.status = "CLOSED"
        return RedirectResponse(f"/cgi/MBRDTL?m={m}", status_code=303)

    @app.get("/cgi/REPORTS", response_class=HTMLResponse)
    async def reports(request: Request):
        if (blocked := guard(request, "menu")) is not None:
            return blocked
        return render(request, "unauthorized.html")

    @app.get("/cgi/ADMIN", response_class=HTMLResponse)
    async def admin(request: Request):
        if (blocked := guard(request, "menu")) is not None:
            return blocked
        return render(request, "admin.html")

    # ---------------- control API (separate port) ----------------

    @control.get("/__faults")
    async def get_faults():
        return bank.faults.as_dict()

    @control.post("/__faults")
    async def set_faults(request: Request):
        body = await request.json()
        try:
            for key, value in body.get("set", {}).items():
                bank.faults.set(key, str(value))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return bank.faults.as_dict()

    @control.post("/__faults/clear")
    async def clear_faults():
        bank.faults.clear()
        return bank.faults.as_dict()

    @control.post("/__reset")
    async def reset():
        bank.faults.clear()
        bank.store.reset()
        return {"ok": True}

    @control.get("/__state/shares")
    async def share_state(m: str):
        member = bank.store.get(m)
        if member is None:
            return JSONResponse({"error": "no such member"}, status_code=404)
        return {s.share_id: s.status for s in member.shares}

    @control.get("/__health")
    async def health():
        return {"ok": True, "release": RELEASE}

    return app, control
