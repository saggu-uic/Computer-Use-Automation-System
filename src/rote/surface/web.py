"""Playwright implementation of the Surface: legacy web apps with frames, tables and overlays."""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from playwright.async_api import (
    BrowserContext,
    Dialog,
    ElementHandle,
    Frame,
    Page,
    async_playwright,
)
from playwright.async_api import Error as PlaywrightError

from rote.models.capability import Locator, Target
from rote.surface.snapshot import CellNode, ElementNode, FieldNode, FrameView, RowNode, Snapshot, TableNode, fingerprint

WALKER_JS = Path(__file__).with_name("walker.js").read_text(encoding="utf-8")
LOCATOR_RANK = {"role": 0, "table_cell": 1, "table": 1, "field": 1, "label": 2, "near_text": 3, "text": 4, "css": 9}

HumanCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


@dataclass
class Resolution:
    element: ElementHandle | None = None
    frame: Frame | None = None
    rank: int | None = None
    locator: Locator | None = None
    reason: str = ""
    ambiguous: bool = False
    table_reason: str | None = None

    @property
    def found(self) -> bool:
        return self.element is not None


@dataclass
class ActResult:
    ok: bool
    dialog_opened: bool = False
    error: str | None = None


def rank_key(loc: Locator) -> tuple[int, int]:
    return LOCATOR_RANK.get(loc.kind, 8), len(loc.row_where or {})


class WebSurface:
    kind = "web"

    def __init__(self) -> None:
        self._pw = None
        self._browser = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.pending_dialog: Dialog | None = None
        self._dialog_event = asyncio.Event()
        self._pending_click: asyncio.Task | None = None
        self._ref_map: dict[str, tuple[Frame, int]] = {}
        self.human_callback: HumanCallback | None = None
        self.navigation_callback: Callable[[str, str], None] | None = None
        self.dialog_callback: Callable[[str], None] | None = None
        self._url_guard: Callable[[str], tuple[bool, str]] | None = None
        self._on_block: Callable[[str, str], None] | None = None
        self.headed = False

    # ---------------- lifecycle ----------------
    @classmethod
    async def launch(
        cls,
        *,
        headed: bool = False,
        url_guard: Callable[[str], tuple[bool, str]] | None = None,
        on_network_block: Callable[[str, str], None] | None = None,
    ) -> "WebSurface":
        self = cls()
        self.headed = headed
        self._url_guard = url_guard
        self._on_block = on_network_block
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=not headed)
        self.context = await self._browser.new_context(viewport={"width": 1280, "height": 800})
        await self.context.add_init_script(WALKER_JS)
        await self.context.expose_binding("__roteHuman", self._on_human_binding)
        if url_guard:
            await self.context.route("**/*", self._route)
        self.context.on("page", self._on_new_page)
        self.page = await self.context.new_page()
        self.page.on("dialog", self._on_dialog)
        self.page.on("framenavigated", self._on_frame_navigated)
        return self

    async def close(self) -> None:
        for closer in (
            lambda: self.context.close() if self.context else None,
            lambda: self._browser.close() if self._browser else None,
            lambda: self._pw.stop() if self._pw else None,
        ):
            try:
                result = closer()
                if result is not None:
                    await result
            except Exception:  # noqa: BLE001 - best effort shutdown
                pass

    async def goto(self, url: str) -> None:
        await self.page.goto(url, wait_until="domcontentloaded")

    # ---------------- guards and callbacks ----------------
    async def _route(self, route) -> None:
        url = route.request.url
        if url.startswith(("data:", "about:", "blob:", "chrome")):
            await route.continue_()
            return
        allowed, reason = self._url_guard(url) if self._url_guard else (True, "")
        if allowed:
            await route.continue_()
        else:
            if self._on_block:
                self._on_block(url, reason)
            await route.abort("blockedbyclient")

    def _on_new_page(self, page: Page) -> None:
        if self.page is None or page is self.page:
            return
        asyncio.ensure_future(self._check_popup(page))

    async def _check_popup(self, page: Page) -> None:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=5000)
        except PlaywrightError:
            pass
        allowed = self._url_guard(page.url)[0] if self._url_guard else True
        if not allowed:
            if self._on_block:
                self._on_block(page.url, "popup_outside_allowlist")
            await page.close()

    def _on_dialog(self, dialog: Dialog) -> None:
        self.pending_dialog = dialog
        self._dialog_event.set()
        if self.dialog_callback:
            self.dialog_callback(dialog.message)

    def _on_frame_navigated(self, frame: Frame) -> None:
        if self.navigation_callback:
            self.navigation_callback(frame.name or "top", frame.url)

    async def _on_human_binding(self, source: dict[str, Any], payload: dict[str, Any]) -> None:
        frame = source.get("frame")
        payload = dict(payload or {})
        payload["frame"] = (frame.name or "top") if frame else None
        if self.human_callback:
            result = self.human_callback(payload)
            if asyncio.iscoroutine(result):
                await result

    # ---------------- frames ----------------
    def frame(self, name: str | None) -> Frame | None:
        if name in (None, "", "top"):
            return self.page.main_frame
        # after a frameset reload, a detached frame with the same name can linger briefly; never use it
        return next((f for f in self.page.frames if f.name == name and not f.is_detached()), None)

    def _live_frames(self) -> list[Frame]:
        return [f for f in self.page.frames if not f.is_detached()]

    async def _ensure_walker(self, frame: Frame) -> bool:
        try:
            present = await frame.evaluate("() => !!(window.__rote && window.__rote.version === 1)")
            if not present:
                await frame.evaluate(WALKER_JS)
            return True
        except PlaywrightError:
            return False

    # ---------------- perception ----------------
    async def snapshot(self) -> Snapshot:
        frames: list[FrameView] = []
        elements: dict[str, ElementNode] = {}
        tables: dict[str, TableNode] = {}
        cells: dict[str, CellNode] = {}
        fields: dict[str, FieldNode] = {}
        ref_map: dict[str, tuple[Frame, int]] = {}
        counter = 0
        for frame in self._live_frames():
            if not await self._ensure_walker(frame):
                continue
            try:
                raw = await frame.evaluate("() => window.__rote.scan()")
            except PlaywrightError:
                continue
            name = frame.name or "top"
            index_to_ref: dict[int, str] = {}

            def new_ref(idx: int) -> str:
                nonlocal counter
                counter += 1
                ref = f"e{counter}"
                ref_map[ref] = (frame, idx)
                index_to_ref[idx] = ref
                return ref

            for e in raw["elements"]:
                ref = new_ref(e["index"])
                elements[ref] = ElementNode(ref=ref, frame=name, **e)
            for t in raw["tables"]:
                tref = new_ref(t["index"])
                rows = []
                for r in t["rows"]:
                    row_cells: list[CellNode | None] = []
                    for c in r["cells"]:
                        if c is None:
                            row_cells.append(None)
                            continue
                        cref = new_ref(c["index"])
                        cell = CellNode(
                            ref=cref, frame=name, index=c["index"], column=c["column"], text=c["text"],
                            table=t["name"], row=r["values"], box=c["box"],
                        )
                        cells[cref] = cell
                        row_cells.append(cell)
                    rows.append(
                        RowNode(
                            values=r["values"],
                            cells=row_cells,
                            controls=[index_to_ref[i] for i in r["controls"] if i in index_to_ref],
                        )
                    )
                tables[tref] = TableNode(
                    ref=tref, frame=name, index=t["index"], name=t["name"], columns=t["columns"],
                    rows=rows, box=t["box"], dialog=t["dialog"],
                )
            for f in raw.get("fields", []):
                fref = new_ref(f["index"])
                fields[fref] = FieldNode(ref=fref, frame=name, **f)
            frames.append(
                FrameView(
                    name=name,
                    url=raw["url"],
                    title=raw.get("title") or "",
                    headings=[h["text"] for h in raw["headings"]],
                    dialogs=raw["dialogs"],
                    layout=_convert_layout(raw["layout"], index_to_ref),
                )
            )
        self._ref_map = ref_map
        return Snapshot(
            frames=frames, elements=elements, tables=tables, cells=cells, fields=fields,
            fingerprint=fingerprint(frames, elements, tables),
        )

    async def stable_snapshot(self, cap_ms: int = 1500) -> Snapshot:
        """Snapshots until two consecutive fingerprints match (capped), so we don't read a half-rendered page."""
        snap = await self.snapshot()
        waited = 0
        while waited < cap_ms:
            await asyncio.sleep(0.25)
            waited += 250
            nxt = await self.snapshot()
            if nxt.fingerprint == snap.fingerprint and _main_ready(nxt):
                return nxt
            snap = nxt
        return snap

    async def probe(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for frame in self._live_frames():
            name = frame.name or "top"
            try:
                result = await frame.evaluate("() => window.__rote ? window.__rote.probe() : null")
            except PlaywrightError:
                result = None
            out[name] = result or {"url": frame.url, "headings": [], "text": "", "dialogs": [], "ready": "loading"}
        return out

    async def element_for_ref(self, ref: str) -> ElementHandle | None:
        if ref not in self._ref_map:
            return None
        frame, idx = self._ref_map[ref]
        try:
            handle = await frame.evaluate_handle("i => (window.__rote && window.__rote.els[i]) || null", idx)
            element = handle.as_element()
            if element is None or not await element.evaluate("e => e.isConnected"):
                return None
            return element
        except PlaywrightError:
            return None

    def frame_for_ref(self, ref: str) -> Frame | None:
        return self._ref_map.get(ref, (None, 0))[0]

    async def locator_candidates(self, ref: str) -> list[Locator]:
        if ref not in self._ref_map:
            return []
        frame, idx = self._ref_map[ref]
        try:
            raw = await frame.evaluate("i => window.__rote.candidatesFor(i)", idx)
        except PlaywrightError:
            return []
        frame_name = frame.name or None
        verified = []
        for cand in raw:
            if not cand["same"]:
                continue
            try:
                verified.append(Locator(**{**cand["locator"], "frame": frame_name}))
            except ValueError:
                continue
        verified.sort(key=rank_key)
        return verified

    # ---------------- resolution ----------------
    async def _resolve_one(self, frame: Frame, loc: Locator, params: dict[str, str]) -> tuple[ElementHandle | None, int, str | None]:
        payload = loc.model_dump(exclude_none=True, exclude={"frame", "fragile", "exact"} if loc.exact else {"frame", "fragile"})
        try:
            handle = await frame.evaluate_handle("([l, p]) => window.__rote.resolve(l, p)", [payload, params])
            count = await (await handle.get_property("count")).json_value()
            reason = await (await handle.get_property("reason")).json_value()
            element = (await handle.get_property("el")).as_element()
            return element, count, reason
        except PlaywrightError as exc:
            return None, 0, f"frame_error: {str(exc)[:80]}"

    async def resolve(self, target: Target, params: dict[str, str]) -> Resolution:
        first: Resolution | None = None
        reasons: list[str] = []
        table_reason = None
        definitive_absence = False
        for rank, loc in enumerate(target.locators, start=1):
            if first is None and definitive_absence and not loc.durable:
                # A durable locator already established the row is not there; a positional fallback
                # would silently read a different row.
                reasons.append(f"{loc.kind}: skipped (row confirmed absent)")
                continue
            frame = self.frame(loc.frame)
            if frame is None or not await self._ensure_walker(frame):
                reasons.append(f"{loc.kind}: frame {loc.frame!r} not available")
                continue
            element, count, reason = await self._resolve_one(frame, loc, params)
            if loc.kind == "table_cell" and reason in ("no_table", "no_row", "no_column"):
                table_reason = table_reason or reason
                definitive_absence = definitive_absence or reason == "no_row"
            if count != 1 or element is None:
                reasons.append(f"{loc.kind}: {reason or f'{count} matches'}")
                continue
            if first is None:
                first = Resolution(element=element, frame=frame, rank=rank, locator=loc, reason="ok")
                if not loc.durable:
                    break
                continue
            if loc.durable and first.locator.durable and frame == first.frame:
                same = await frame.evaluate("([a, b]) => a === b", [first.element, element])
                if not same:
                    return Resolution(
                        ambiguous=True,
                        locator=loc,
                        rank=rank,
                        reason=f"durable locators disagree: {first.locator.describe()} vs {loc.describe()}",
                    )
        if first:
            return first
        return Resolution(reason="; ".join(reasons) or "no locators", table_reason=table_reason)

    # ---------------- actions ----------------
    async def click(self, element: ElementHandle, timeout_ms: int = 6000) -> ActResult:
        self._dialog_event.clear()
        click_task = asyncio.ensure_future(element.click(timeout=timeout_ms))
        dialog_task = asyncio.ensure_future(self._dialog_event.wait())
        done, _ = await asyncio.wait({click_task, dialog_task}, return_when=asyncio.FIRST_COMPLETED)
        if click_task in done:
            dialog_task.cancel()
            exc = click_task.exception()
            if exc:
                return ActResult(ok=False, error=str(exc).splitlines()[0][:200])
            return ActResult(ok=True, dialog_opened=self.pending_dialog is not None)
        self._pending_click = click_task
        return ActResult(ok=True, dialog_opened=True)

    async def fill(self, element: ElementHandle, value: str, timeout_ms: int = 6000) -> ActResult:
        try:
            await element.fill(value, timeout=timeout_ms)
            return ActResult(ok=True)
        except PlaywrightError as exc:
            return ActResult(ok=False, error=str(exc).splitlines()[0][:200])

    async def input_value(self, element: ElementHandle) -> str | None:
        try:
            return await element.input_value()
        except PlaywrightError:
            return None

    async def select(self, element: ElementHandle, option: str, timeout_ms: int = 6000) -> ActResult:
        try:
            await element.select_option(label=option, timeout=timeout_ms)
            return ActResult(ok=True)
        except PlaywrightError as exc:
            return ActResult(ok=False, error=str(exc).splitlines()[0][:200])

    async def set_checked(self, element: ElementHandle, checked: bool, timeout_ms: int = 6000) -> ActResult:
        try:
            await element.set_checked(checked, timeout=timeout_ms)
            return ActResult(ok=True)
        except PlaywrightError as exc:
            return ActResult(ok=False, error=str(exc).splitlines()[0][:200])

    async def press(self, element: ElementHandle | None, key: str) -> ActResult:
        try:
            if element is not None:
                await element.press(key)
            else:
                await self.page.keyboard.press(key)
            return ActResult(ok=True)
        except PlaywrightError as exc:
            return ActResult(ok=False, error=str(exc).splitlines()[0][:200])

    async def _finish_dialog(self, accept: bool) -> ActResult:
        dialog = self.pending_dialog
        if dialog is None:
            return ActResult(ok=False, error="no dialog is open")
        try:
            if accept:
                await dialog.accept()
            else:
                await dialog.dismiss()
        except PlaywrightError as exc:
            return ActResult(ok=False, error=str(exc)[:200])
        finally:
            self.pending_dialog = None
            self._dialog_event.clear()
        if self._pending_click is not None:
            try:
                await asyncio.wait_for(self._pending_click, timeout=10)
            except Exception:  # noqa: BLE001 - the click already happened; navigation may detach the handle
                pass
            self._pending_click = None
        return ActResult(ok=True)

    async def accept_dialog(self) -> ActResult:
        return await self._finish_dialog(True)

    async def dismiss_dialog(self) -> ActResult:
        return await self._finish_dialog(False)

    async def read_text(self, element: ElementHandle) -> str:
        return " ".join((await element.inner_text()).split())

    async def read_table(self, element: ElementHandle) -> dict[str, Any] | None:
        return await element.evaluate("e => window.__rote.readTable(e)")

    async def element_facts(self, element: ElementHandle) -> dict[str, Any]:
        """Role/name/form facts for policy classification of a resolved element."""
        return await element.evaluate(
            """e => {
              const t = e.tagName.toLowerCase();
              const type = (e.getAttribute('type') || '').toLowerCase();
              const role = e.getAttribute('role') || (t === 'a' ? 'link' : t === 'button' ? 'button' : t === 'select' ? 'combobox'
                 : t === 'textarea' ? 'textbox' : t === 'td' || t === 'th' ? 'cell' : t === 'table' ? 'table'
                 : t === 'input' ? (['submit','button','reset','image'].includes(type) ? 'button' : type === 'checkbox' ? 'checkbox' : 'textbox')
                 : e.hasAttribute('onclick') ? 'button' : null);
              const name = t === 'input' && ['submit','button','reset'].includes(type) ? (e.value || '') : (e.innerText || '');
              return { role, name: name.replace(/\\s+/g, ' ').trim(),
                       form_submit: (t === 'input' && ['submit','image'].includes(type)) || (t === 'button' && (e.getAttribute('type') || 'submit') === 'submit'),
                       form_method: e.form ? (e.form.getAttribute('method') || 'GET').toUpperCase() : null };
            }"""
        )

    # ---------------- evidence ----------------
    async def screenshot(self, path: Path, mask_values: list[str]) -> Path | None:
        from PIL import Image, ImageDraw

        try:
            png = await self.page.screenshot()
        except PlaywrightError:
            return None
        rects: list[tuple[float, float, float, float]] = []
        for frame in self._live_frames():
            ox = oy = 0.0
            if frame != self.page.main_frame:
                try:
                    frame_element = await frame.frame_element()
                    bbox = await frame_element.bounding_box()
                except PlaywrightError:
                    continue
                if not bbox:
                    continue
                ox, oy = bbox["x"], bbox["y"]
            try:
                found = await frame.evaluate("v => window.__rote ? window.__rote.rectsFor(v) : []", mask_values)
            except PlaywrightError:
                continue
            rects.extend((r["x"] + ox, r["y"] + oy, r["width"], r["height"]) for r in found)
        image = Image.open(io.BytesIO(png)).convert("RGB")
        draw = ImageDraw.Draw(image)
        for x, y, w, h in rects:
            draw.rectangle([x - 2, y - 1, x + w + 2, y + h + 1], fill=(0, 0, 0))
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path)
        return path

    async def set_block(self, on: bool, label: str = "AUTOMATION IN CONTROL") -> None:
        for frame in self._live_frames():
            try:
                await frame.evaluate("([on, l]) => window.__rote && window.__rote.setBlock(on, l)", [on, label])
            except PlaywrightError:
                continue


def _convert_layout(items: list[dict[str, Any]], index_to_ref: dict[int, str]) -> list[dict[str, Any]]:
    out = []
    for item in items:
        kind = item.get("type")
        if kind in ("control", "table"):
            ref = index_to_ref.get(item["index"])
            if ref:
                out.append({"type": kind, "ref": ref})
        elif kind == "dialog":
            out.append({**item, "items": _convert_layout(item["items"], index_to_ref)})
        elif kind == "row":
            out.append({**item, "cells": [_convert_layout(cell, index_to_ref) for cell in item["cells"]]})
        elif kind == "text" and "field" in item:
            ref = index_to_ref.get(item["field"])
            out.append({"type": "text", "text": item["text"], **({"ref": ref} if ref else {})})
        else:
            out.append(item)
    return out


def _main_ready(snap: Snapshot) -> bool:
    main = snap.frame("main")
    return main is None or bool(main.headings or main.layout)
