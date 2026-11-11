"""Connections screen logic: one-time logins and session checks.

You log in yourself in a normal, visible browser window. Only the browser
profile (cookies) is kept, in the private folder outside the project. The app
never sees or stores a password.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime

from sqlmodel import select

from .. import db, models as m
from ..agents.sites import SITES
from ..config import settings
from .reader import PageReader, detect_block

log = logging.getLogger(__name__)
_open_windows: dict[str, asyncio.Task] = {}


def set_status(site: str, status: str, note: str = "") -> None:
    with db.session_scope() as s:
        c = s.get(m.Connection, site) or m.Connection(site=site)
        c.status, c.note = status, note
        if status == "connected":
            c.last_verified_at = datetime.now().astimezone()
        s.add(c)


def get_status(site: str) -> str:
    with db.session_scope() as s:
        c = s.get(m.Connection, site)
        return c.status if c else "not_connected"


def has_profile(site: str) -> bool:
    p = PageReader.profile_dir(site)
    return p.exists() and any(p.iterdir())


async def verify(site: str, reader_factory=None) -> str:
    """Open the saved profile headless and check we are still logged in."""
    spec = SITES[site]
    if not spec.supports_login:
        return "not_needed"
    if not has_profile(site):
        set_status(site, "not_connected")
        return "not_connected"
    factory = reader_factory or (lambda s, mode: PageReader(s, mode, headless=True))
    async with factory(site, "login") as r:
        snap = await r.goto(spec.check_url, wait_ms=2500)
    block = detect_block(snap)
    status = "connected" if block is None else ("expired" if block == "login_wall" else "connected")
    note = ""
    if block == "captcha":
        if get_status(site) == "connected":     # a check on an already working login: keep it
            status, note = "connected", "verification page shown - may need you to solve a check"
        else:                                   # never got past the check: no usable login
            status, note = "not_connected", "the site blocked the login window with a bot check"
    set_status(site, status, note)
    return status


async def open_login_window(site: str, timeout_minutes: int = 15) -> str:
    """Open a visible browser on the site's login page and wait until you close it."""
    spec = SITES[site]
    if not spec.supports_login:
        return "not_needed"
    from playwright.async_api import async_playwright
    pdir = PageReader.profile_dir(site)
    pdir.mkdir(parents=True, exist_ok=True)
    set_status(site, "logging_in", "Log in in the browser window, then close it.")
    async with async_playwright() as pw:
        from .reader import launch_profile
        ctx = await launch_profile(pw, pdir, site, headless=False, viewport={"width": 1200, "height": 850})
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(spec.login_url)
        closed = asyncio.Event()
        ctx.on("close", lambda *_: closed.set())
        page.on("close", lambda *_: closed.set() if not ctx.pages else None)
        try:
            await asyncio.wait_for(closed.wait(), timeout=timeout_minutes * 60)
        except asyncio.TimeoutError:
            pass
        try:
            await ctx.close()
        except Exception:
            pass
    status = await verify(site)
    if status == "connected":
        # a site that blocked the guest browser gets another chance with your login
        from ..graphs.search_graph import _get_start_mode, _set_start_mode
        if _get_start_mode(site) == "manual":
            _set_start_mode(site, "guest")
    return status


def start_login(site: str) -> None:
    """Fire-and-forget from the API."""
    t = _open_windows.get(site)
    if t and not t.done():
        return
    _open_windows[site] = asyncio.get_event_loop().create_task(open_login_window(site))


def disconnect(site: str) -> None:
    p = PageReader.profile_dir(site)
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)
    set_status(site, "not_connected")


def list_connections() -> list[dict]:
    with db.session_scope() as s:
        rows = {c.site: c for c in s.exec(select(m.Connection)).all()}
    out = []
    from ..db import ALL_SOURCES
    labels = {"ats_feeds": "Company job feeds (ATS)", "arbeitsagentur": "Bundesagentur für Arbeit",
              "arbeitnow": "Arbeitnow", "remoteok": "RemoteOK", "career_pages": "Career pages"}
    for site in ALL_SOURCES:
        c = rows.get(site)
        spec = SITES.get(site)
        out.append({
            "site": site,
            "label": spec.label if spec else labels.get(site, site),
            "supports_login": bool(spec and spec.supports_login),
            "status": c.status if c else ("not_connected" if spec and spec.supports_login else "not_needed"),
            "last_verified_at": c.last_verified_at.isoformat() if c and c.last_verified_at else None,
            "note": c.note if c else "",
            "has_profile": has_profile(site) if spec and spec.supports_login else False,
        })
    return out


_ = settings
