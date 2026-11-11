"""Read-only browser access for the site agents.

Three modes per site:
* guest  - a fresh, cookie-less browser context (no login, hidden)
* window - a visible window with its own remembered cookies (for sites with a bot check)
* login  - the site's saved persistent profile (your second account)

Every page that is blocked or shows a check is saved to data/debug/<site>/
(HTML + screenshot + one log line), so problems can be looked at afterwards.

The agents only ever call `goto()` and read text, links and HTML. There is
deliberately no click/type method here, so an agent cannot apply, message,
follow or connect.
"""
from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..config import settings

log = logging.getLogger(__name__)

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")


@dataclass
class Snapshot:
    url: str
    final_url: str
    status: int
    title: str = ""
    text: str = ""
    html: str = ""
    links: list[tuple[str, str]] = field(default_factory=list)  # (text, absolute href)
    error: str = ""

    def links_block(self, limit: int = 400) -> str:
        out = []
        for text, href in self.links[:limit]:
            t = " ".join(text.split())[:120]
            if t:
                out.append(f"- {t} -> {href}")
        return "\n".join(out)


def uses_chrome(site: str) -> bool:
    try:
        from ..agents.sites import SITES
        return bool(getattr(SITES.get(site), "use_chrome", False))
    except Exception:
        return False


async def launch_profile(pw, pdir, site: str, headless: bool = False, **opts):
    """A saved browser profile; your installed Chrome for sites that need it, else the bundled browser."""
    if uses_chrome(site):
        try:
            return await pw.chromium.launch_persistent_context(str(pdir), channel="chrome", headless=headless, **opts)
        except Exception as e:
            log.info("installed Chrome not usable (%s) - using the bundled browser", e)
    return await pw.chromium.launch_persistent_context(str(pdir), headless=headless, **opts)


class PageReader:
    """One browser context (guest or a site's login profile)."""

    def __init__(self, site: str, mode: str = "guest", headless: Optional[bool] = None):
        self.site, self.mode = site, mode
        self.headless = settings.headless_guest if headless is None else headless
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

    @staticmethod
    def profile_dir(site: str) -> Path:
        return settings.profiles_dir / site

    async def __aenter__(self) -> "PageReader":
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        if self.mode == "window":
            # A normal, visible browser window with its own remembered cookies. Sites behind a bot check
            # (e.g. Indeed) challenge headless browsers on every page; once you have passed the check in
            # this window, its cookie is kept for later searches. Uses your installed Chrome when present.
            pdir = self.profile_dir(f"{self.site}_window")
            pdir.mkdir(parents=True, exist_ok=True)
            opts = dict(headless=False, viewport={"width": 1280, "height": 860}, locale="de-DE")
            try:
                self._context = await self._pw.chromium.launch_persistent_context(str(pdir), channel="chrome", **opts)
            except Exception:
                self._context = await self._pw.chromium.launch_persistent_context(str(pdir), **opts)
        elif self.mode == "login":
            pdir = self.profile_dir(self.site)
            pdir.mkdir(parents=True, exist_ok=True)
            if uses_chrome(self.site):
                self._context = await launch_profile(self._pw, pdir, self.site, headless=self.headless,
                                                     viewport={"width": 1280, "height": 860}, locale="de-DE")
            else:
                self._context = await self._pw.chromium.launch_persistent_context(
                    str(pdir), headless=self.headless, user_agent=USER_AGENT,
                    viewport={"width": 1366, "height": 900}, locale="en-US")
        else:
            self._browser = await self._pw.chromium.launch(headless=self.headless)
            # Same browser version as the real engine (a made-up version is an easy bot signal).
            ua = USER_AGENT
            try:
                major = self._browser.version.split(".")[0]
                ua = USER_AGENT.replace("Chrome/130.0.0.0", f"Chrome/{major}.0.0.0")
            except Exception:
                pass
            self._context = await self._browser.new_context(
                user_agent=ua, viewport={"width": 1366, "height": 900}, locale="en-US")
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        return self

    async def __aexit__(self, *exc) -> None:
        try:
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
        finally:
            if self._pw:
                await self._pw.stop()

    async def pause(self) -> None:
        """Human pacing between page loads."""
        d = settings.page_delay_seconds
        if d > 0:
            await asyncio.sleep(d * random.uniform(0.8, 1.5))

    async def goto(self, url: str, wait_ms: Optional[int] = None) -> Snapshot:
        page = self._page
        wait_ms = settings.page_settle_ms if wait_ms is None else wait_ms
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            if wait_ms:
                await page.wait_for_timeout(wait_ms)
            status = resp.status if resp else 0
            html = await page.content()
            text = await page.evaluate("() => document.body ? document.body.innerText : ''")
            links = await page.evaluate(
                "() => Array.from(document.querySelectorAll('a[href]')).map(a => [a.innerText || a.getAttribute('aria-label') || '', a.href])")
            title = await page.title()
            return Snapshot(url=url, final_url=page.url, status=status, title=title, text=text or "",
                            html=html, links=[(t, h) for t, h in links if h and h.startswith("http")])
        except Exception as e:  # network errors, timeouts
            log.warning("goto failed %s: %s", url, e)
            return Snapshot(url=url, final_url=url, status=0, error=str(e))

    async def current(self) -> Optional[Snapshot]:
        """What the window shows right now (no navigation) - used to notice a solved check."""
        try:
            page = self._page
            text = await page.evaluate("() => document.body ? document.body.innerText : ''")
            html = await page.content()
            return Snapshot(url=page.url, final_url=page.url, status=200, title=await page.title(),
                            text=text or "", html=html)
        except Exception:
            return None

    async def save_debug(self, snap: "Snapshot", block: str, note: str = "") -> None:
        """Keep the blocked page (HTML, screenshot, log line) in data/debug/<site>/."""
        try:
            from datetime import datetime
            d = Path(settings.data_dir) / "debug" / self.site
            d.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            (d / f"{stamp}_{block}.html").write_text(snap.html or "", encoding="utf-8")
            try:
                await self._page.screenshot(path=str(d / f"{stamp}_{block}.png"))
            except Exception:
                pass
            line = (f"{stamp}\tmode={self.mode}\tstatus={snap.status}\tblock={block}\ttitle={snap.title[:80]!r}\t"
                    f"url={snap.final_url}\t{note}\ttext={' '.join(snap.text.split())[:300]!r}\n")
            with open(d / "log.txt", "a", encoding="utf-8") as f:
                f.write(line)
            files = sorted(p for p in d.iterdir() if p.suffix in (".html", ".png"))
            for old in files[:-80]:                     # keep the last 40 pages
                old.unlink(missing_ok=True)
        except Exception as e:  # pragma: no cover - debugging must never break a run
            log.debug("save_debug failed: %s", e)

    async def show_for_user(self) -> None:
        """Bring the window to front so you can solve a captcha (headed login mode only)."""
        try:
            await self._page.bring_to_front()
        except Exception:
            pass


# ------------------------------------------------------------ detection ---
LOGIN_MARKERS = (
    "authwall", "/login", "/signin", "sign in to view", "sign in to see", "join now to see",
    "log in to continue", "melde dich an", "anmelden, um", "join to view", "sign in to continue",
    "create a free account to", "to see more jobs, sign in",
)
CAPTCHA_MARKERS = (
    "captcha", "verify you are human", "verify that you are human", "are you a robot",
    "just a moment...", "checking your browser", "security check", "unusual traffic",
    "bestätigen sie, dass sie ein mensch sind", "press & hold",
    # German / other wording of the same checks (the visible window runs in German)
    "einen moment", "sie ein mensch sind", "ob sie ein mensch", "menschlicher besucher", "kein roboter",
    "zusätzliche verifizierung", "additional verification required", "verify you're human",
    "performing security verification", "sicherheitsüberprüfung", "security of your connection",
    "sicherheit ihrer verbindung", "ungewöhnliche aktivität", "unusual activity", "access denied - indeed",
    "request unsuccessful. incapsula",
)
# Bot-check pages recognised by their code, whatever language they are in
CHALLENGE_HTML = (
    "challenge-platform", "cf-chl", "cf_chl_opt", "cf-turnstile", "challenges.cloudflare.com",
    "cf-browser-verification", "captcha-delivery.com", "px-captcha", "_incapsula_resource",
    "hcaptcha.com/1/api", "geo.captcha-delivery",
)


STRONG_MARKERS = (
    "verify you are human", "verify that you are human", "verify you're human", "are you a robot",
    "checking your browser", "sie ein mensch sind", "ob sie ein mensch", "kein roboter", "press & hold",
    "performing security verification",
)


def is_challenge(snap: Snapshot) -> bool:
    head = (snap.title + "\n" + snap.text[:3000]).lower()
    if any(m in head for m in STRONG_MARKERS):
        return True
    # weaker signs (a word like "captcha" or "einen Moment") only count on an error status or a
    # page with almost no text - a normal results page can mention them or load a captcha script
    thin = snap.status in (403, 429, 503) or len(snap.text.strip()) < 1500
    if not thin:
        return False
    html = (snap.html or "")[:60000].lower()
    return any(m in head for m in CAPTCHA_MARKERS) or any(m in html for m in CHALLENGE_HTML)


def detect_block(snap: Snapshot) -> Optional[str]:
    """Return 'login_wall', 'captcha', 'blocked', 'error' or None (page looks fine)."""
    if snap.error or snap.status == 0:
        return "error"
    url = snap.final_url.lower()
    head = (snap.title + "\n" + snap.text[:3000]).lower()
    if is_challenge(snap):
        return "captcha"
    if snap.status == 429:
        return "blocked"
    if "authwall" in url or "/login" in url or "/signin" in url or "uas/login" in url or "secure.indeed.com/auth" in url:
        return "login_wall"
    if any(m in head for m in LOGIN_MARKERS) and len(snap.text) < 4000:
        return "login_wall"
    if snap.status >= 500 or snap.status in (403,):
        return "blocked"
    if snap.status >= 400:
        return "error"
    return None
