"""Run the full app against the fake job sites (for UI testing):  python tests/ui_server.py <port>"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))
tmp = Path(os.environ.get("UI_TMP") or tempfile.mkdtemp(prefix="ui_"))
os.environ.update(DATA_DIR=str(tmp / "data"), JOB_DIR=str(tmp / "Job"), PRIVATE_DIR=str(tmp / "private"),
                  LLM_MODE="fake", PAGE_DELAY_SECONDS="0", PAGE_SETTLE_MS="50")

import httpx  # noqa: E402
import uvicorn  # noqa: E402

from fake_sites import FakeReader, FakeSites  # noqa: E402
from jobagent.agents import sites as S  # noqa: E402
from jobagent.graphs import search_graph as G  # noqa: E402
from test_07_search_graph import feed_handler  # noqa: E402

fs = FakeSites().__enter__()
for site, prefix in [("linkedin", "/li"), ("glassdoor", "/gd"), ("xing", "/xg"), ("stepstone", "/ss"), ("indeed", "/in")]:
    S.SITES[site].base = fs.base + prefix
class HumanReader(FakeReader):
    """In the visible window a 'person' passes Indeed's check 4 s after the prompt appears."""

    async def show_for_user(self):
        async def solve():
            await asyncio.sleep(4)
            await self._context.add_cookies([{"name": "human", "value": "ok", "url": fs.base}])
            await self._page.reload()
        asyncio.ensure_future(solve())


G.READER_FACTORY = lambda s, mode: (HumanReader if mode == "window" else FakeReader)(s, mode, fs.base)
G.FEED_TRANSPORT = httpx.MockTransport(feed_handler)


async def _web(prompt, q):            # stand-in for Claude's web search: one posting per call
    url = f"https://jobs.example.org/{q.location.lower()}-{abs(hash(q.group)) % 9999}"
    return ('```json\n[{"title": "Working Student Data Science ' + q.group.split()[0] + '", "company": "WebCo GmbH", '
            '"location": "' + q.location + '", "url": "' + url + '", "posted": "1 day ago", '
            '"summary": "Python, SQL, machine learning. 20h per week. English."}]\n```'), {url}, 2, 0.0

G.WEB_CALLER = _web
G.WEB_HTTP = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
from jobagent.config import settings as _S  # noqa: E402
_S.websearch_max_calls = 3
orig = G.planner.plan_site_queries
G.planner.plan_site_queries = lambda *a, **k: orig(*a, **k)[:1]
G.planner.plan_feed_queries = lambda *a, **k: [{"keywords": "Werkstudent Data", "location": "Berlin"}]
# a fake Gmail for the Inbox page
sys.path.insert(0, os.path.dirname(__file__))
import test_21_mail as TM  # noqa: E402
from jobagent.mail import reader as _MR, service as _MS  # noqa: E402
import jobagent.feeds.websearch as _W  # noqa: E402
_BOX = [TM.mk(1, "job.abc.acme@recruitee-email.com", "Acme Analytics GmbH/Data Science Intern", "We have received your application!",
              "Hi Alex, Thank you for your application as Data Science Intern and your interest in Acme Analytics GmbH."),
        TM.mk(2, "careers@zeta-robotics.com", "Zeta Robotics Recruiting", "Ihre Bewerbung: Working Student Machine Learning",
              "Guten Tag, vielen Dank für Ihre Bewerbung. Leider müssen wir Ihnen mitteilen, dass wir uns für andere Kandidaten entschieden haben."),
        TM.mk(3, "jobs@beta-labs.de", "Beta Labs HR", "Einladung zum Vorstellungsgespräch – Werkstudent KI (m/w/d)",
              "Hallo Alex, vielen Dank für Ihre Bewerbung als Werkstudent KI (m/w/d). Wir möchten Sie gerne zu einem "
              "Vorstellungsgespräch einladen. Bitte wählen Sie einen Termin: https://calendly.com/beta/30min"),
        TM.mk(5, "info@jobagent.stepstone.de", "Lisa von Stepstone", "Alex, unsere Empfehlung: neue Jobs für dich", TM.STEPSTONE,
              html=True, unsub=True)]
_MR.IMAP_FACTORY = lambda: TM.FakeImap(_BOX)


async def _same(urls):
    return {u: u.replace("click.stepstone.de/f/a/", "www.stepstone.de/stellenangebote--") + ".html" for u in urls}


async def _nofetch(jobs, client=None, limit=80):
    return 0
_MS.RESOLVE = _same
_W.fetch_descriptions = _nofetch
print("UI tmp:", tmp, flush=True)
uvicorn.run("jobagent.api.main:app", host="127.0.0.1", port=int(sys.argv[1] if len(sys.argv) > 1 else 8780), log_level="warning")
