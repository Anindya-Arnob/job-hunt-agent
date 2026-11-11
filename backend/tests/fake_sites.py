"""A local web server that imitates the job sites, for testing the agents end to end
with a real browser: result pages, pagination, job pages, login walls, captchas,
unknown layouts and result counts at the site limit."""
from __future__ import annotations

import threading
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

TODAY = date.today()

LI_JOBS = [
    # (id, title, company, location, days_ago)
    (4100000001, "Werkstudent Data Science (m/w/d)", "Acme Analytics GmbH", "Berlin, Berlin, Germany", 1),
    (4100000002, "Machine Learning Intern", "Nordlicht AI", "Berlin, Germany", 2),
    (4100000003, "Senior Data Engineer", "BigCorp AG", "Munich, Germany", 1),
    (4100000004, "Working Student GenAI / LLM", "PromptWorks", "Remote", 3),
    (4100000005, "Data Analyst Part-time", "Kiez Retail", "Berlin, Germany", 9),
] + [(4100000100 + i, f"Data Science Intern {i}", f"Company {i} GmbH", "Hamburg, Germany", 2) for i in range(25)]

DESCRIPTIONS = {
    4100000001: "We are looking for a Werkstudent (working student) Data Science, 20 hours per week. "
                "Tasks: build machine learning models in Python, SQL, scikit-learn, PyTorch. "
                "Requirements: enrolled student, very good English, German nice to have. Tableau is a plus.",
    4100000002: "Machine Learning Internship (6 months). You will fine-tune LLMs with Hugging Face Transformers, "
                "LoRA and PEFT, build RAG pipelines with LangChain and evaluate models. Python, PyTorch, Docker. English.",
    4100000004: "Working student for GenAI: prompt engineering, RAG, vector databases, LangGraph agents, FastAPI. "
                "Fluent German (C1) required. Python.",
}


def li_card(jid, title, company, loc, days):
    d = (TODAY - timedelta(days=days)).isoformat()
    slug = title.lower().replace(" ", "-").replace("/", "")
    return f"""<li><div class="base-card job-search-card">
<a class="base-card__full-link" href="/li/jobs/view/{slug}-{jid}?refId=abc&trackingId=xyz">{title}</a>
<h3 class="base-search-card__title">{title}</h3>
<h4 class="base-search-card__subtitle"><a>{company}</a></h4>
<span class="job-search-card__location">{loc}</span>
<time class="job-search-card__listdate" datetime="{d}">{days} days ago</time>
</div></li>"""


def li_detail(jid):
    text = DESCRIPTIONS.get(jid, "Data science internship. Python, pandas, machine learning. English required. " * 3)
    jt = "Internship" if "Intern" in text or "Internship" in text else "Part-time"
    return f"""<html><body><section class="description">
<div class="show-more-less-html__markup">{text}<ul><li>Python</li><li>SQL</li></ul></div>
<ul class="description__job-criteria-list"><li class="description__job-criteria-item">
<h3 class="description__job-criteria-subheader">Employment type</h3>
<span class="description__job-criteria-text">{jt}</span></li></ul></section></body></html>"""


GD_RESULTS = """<html><body><h1>3 Data Science Intern Jobs in Berlin</h1><ul>
<li data-test="jobListing"><a data-test="job-title" href="/gd/job-listing/ds-intern-JV_1.htm?jl=1009">Data Science Intern</a>
<span class="EmployerProfile_compactEmployerName__x">Glass Co</span><div data-test="emp-location">Berlin</div>
<div data-test="job-age">2d</div></li>
<li data-test="jobListing"><a data-test="job-title" href="/gd/job-listing/ml-ws-JV_2.htm?jl=1010">ML Working Student</a>
<span class="EmployerProfile_compactEmployerName__x">Door GmbH</span><div data-test="emp-location">Berlin</div>
<div data-test="job-age">1d</div></li></ul></body></html>"""

LOGIN_WALL = """<html><head><title>Sign in</title></head><body><h1>Sign in to see more jobs</h1>
<p>Join now to see all results. Sign in to continue.</p><form><input name=email></form></body></html>"""

CAPTCHA = """<html><head><title>Just a moment...</title></head><body>
<p>Verify you are human by completing the action below.</p></body></html>"""

UNKNOWN_LAYOUT = """<html><body><div class="zz-list">
<div class="zz-card"><a href="/xg/jobs/werkstudent-ki-berlin-123">Werkstudent KI-Entwicklung (m/w/d)</a> Firma Eins · Berlin · vor 2 Tagen</div>
<div class="zz-card"><a href="/xg/jobs/praktikum-data-456">Praktikum Data Engineering</a> Firma Zwei · Berlin · vor 1 Tag</div>
<a href="/about">About us and our company story</a>
</div><p>""" + ("Some filler text about the site. " * 20) + "</p></body></html>"

SS_RESULTS = """<html><body><h1>2.345 Jobs für Werkstudent Data</h1>
<article data-testid="job-item"><a data-testid="job-item-title" href="/ss/stellenangebote--Werkstudent-Data-Science-Berlin--111-inline.html">Werkstudent Data Science</a>
<span data-at="job-item-company-name">Stepco GmbH</span><span data-at="job-item-location">Berlin</span>
<time datetime="{d}">vor 1 Tag</time></article></body></html>"""


IN_RESULTS = """<html><body><div class="jobsearch-JobCountAndSortPane-jobCount">2 jobs</div>
<div class="job_seen_beacon"><h2 class="jobTitle"><a class="jcs-JobTitle" data-jk="in111" href="/rc/clk?jk=in111">Werkstudent Data Science (m/w/d)</a></h2>
<span data-testid="company-name">Indico GmbH</span><div data-testid="text-location">Berlin</div><span class="date">vor 1 Tag</span></div>
<div class="job_seen_beacon"><h2 class="jobTitle"><a class="jcs-JobTitle" data-jk="in222" href="/rc/clk?jk=in222">Praktikum Machine Learning</a></h2>
<span data-testid="company-name">Indico Labs</span><div data-testid="text-location">Berlin</div><span class="date">Heute</span></div>
</body></html>"""


DE_CHECK = """<html><head><title>Einen Moment&hellip;</title></head><body>
<p>Überprüfung, ob Sie ein Mensch sind. Dies kann einige Sekunden dauern.</p>
<div id="challenge-platform"></div>
<script>setTimeout(function(){document.cookie='human=ok; path=/'; location.reload();}, 100);</script></body></html>"""

FORBIDDEN = "<html><head><title>Forbidden</title></head><body><p>Access to this page is not allowed.</p></body></html>"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body: str, status: int = 200):
        b = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        logged_in = "session=ok" in (self.headers.get("Cookie") or "")
        p = u.path
        self.server.hits.append(self.path)
        # ---- LinkedIn guest API
        if p.startswith("/li/jobs-guest/jobs/api/seeMoreJobPostings/search"):
            start = int(q.get("start", ["0"])[0])
            cards = LI_JOBS[start:start + 10]          # LinkedIn's guest API returns 10 per page
            return self._send("<html><body><ul>" + "".join(li_card(*c) for c in cards) + "</ul></body></html>")
        if p.startswith("/li/jobs-guest/jobs/api/jobPosting/"):
            return self._send(li_detail(int(p.rsplit("/", 1)[1])))
        # ---- "Glassdoor": guest gets a login wall, login gets results
        if p.startswith("/gd/Job/jobs.htm"):
            return self._send(GD_RESULTS if logged_in else LOGIN_WALL)
        if p.startswith("/gd/job-listing/"):
            return self._send("<html><body><div class='JobDetails_jobDescription__a'>" +
                              ("Data science internship in Berlin with Python and SQL. " * 10) + "</div></body></html>")
        if p.startswith("/gdexpired/"):
            return self._send(LOGIN_WALL)
        # ---- "Indeed": captcha
        human = "human=ok" in (self.headers.get("Cookie") or "")
        if p.startswith("/inde/jobs"):        # German check that passes by itself in a real browser
            return self._send(IN_RESULTS if human else DE_CHECK, 200 if human else 403)
        if p.startswith("/inray/jobs"):       # Indeed today: refuses automated browsers even after the box is ticked
            import secrets
            return self._send("<html><head><title>Nur einen Moment…</title></head><body><h1>Zusätzliche Verifizierung erforderlich</h1>"
                              f"<p>Ihre Ray-ID für diese Anfrage lautet {secrets.token_hex(8)}</p>"
                              "<div class='cf-turnstile'>Bestätigen Sie, dass Sie ein Mensch sind</div></body></html>", 403)
        if p.startswith("/inlogin/jobs"):     # guest gets the check, your logged-in profile gets results
            return self._send(IN_RESULTS if logged_in else CAPTCHA, 200 if logged_in else 403)
        if p.startswith("/inblock/jobs"):     # plain 403, no check
            return self._send(FORBIDDEN, 403)
        if p.startswith("/in/jobs"):
            return self._send(IN_RESULTS if human else CAPTCHA, 200 if human else 403)
        if p.startswith("/viewjob"):
            human = human or logged_in
            return self._send("<html><body><div id='jobDescriptionText'>" + ("Python, SQL and ML for a Werkstudent in Berlin. " * 10)
                              + "</div></body></html>" if human else CAPTCHA, 200 if human else 403)
        # ---- unknown layout (XING-like) -> Claude/heuristic fallback
        if p.startswith("/xg/jobs/search"):
            return self._send(UNKNOWN_LAYOUT)
        if p.startswith("/xg/jobs/"):
            return self._send("<html><body><article>" + ("Werkstudent role, Python, data pipelines, Airflow. " * 10) + "</article></body></html>")
        # ---- StepStone with a count at the limit
        if p.startswith("/ss/jobs/"):
            return self._send(SS_RESULTS.replace("{d}", (TODAY - timedelta(days=1)).isoformat()))
        if p.startswith("/ss/stellenangebote"):
            return self._send("<html><body><div data-at='section-text-description-content'>" +
                              ("Werkstudent Data Science Berlin, Python, SQL, Power BI. " * 10) + "</div></body></html>")
        return self._send("<html><body>not found</body></html>", 404)


class FakeSites:
    def __init__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.hits = []
        self.port = self.server.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()

    @property
    def hits(self):
        return self.server.hits


from jobagent.browser.reader import PageReader  # noqa: E402


class FakeReader(PageReader):
    """Login mode = a cookie the fake server accepts (stands in for a saved profile)."""

    def __init__(self, site, mode, base):
        super().__init__(site, "guest", headless=True)
        self.real_mode, self.base = mode, base

    async def __aenter__(self):
        await super().__aenter__()
        if self.real_mode == "login":
            await self._context.add_cookies([{"name": "session", "value": "ok", "url": self.base}])
        return self
