import asyncio
import json
from datetime import date, datetime, timedelta

import httpx

from jobagent.feeds import feeds as F

T = date.today()
RECENT = (datetime.now() - timedelta(days=1)).isoformat()
OLD = (datetime.now() - timedelta(days=30)).isoformat()


def handler(request: httpx.Request) -> httpx.Response:
    u = str(request.url)
    if "greenhouse" in u:
        return httpx.Response(200, json={"jobs": [
            {"title": "Working Student Data Science", "location": {"name": "Berlin"}, "absolute_url": "https://gh/1",
             "first_published": RECENT, "content": "&lt;p&gt;Python &amp;amp; SQL&lt;/p&gt;"},
            {"title": "Office Manager", "location": {"name": "Berlin"}, "absolute_url": "https://gh/2", "first_published": RECENT},
            {"title": "ML Intern", "location": {"name": "Berlin"}, "absolute_url": "https://gh/3", "first_published": OLD},
        ]})
    if "lever" in u:
        return httpx.Response(200, json=[{"text": "Machine Learning Intern", "createdAt": int(datetime.now().timestamp() * 1000),
                                          "hostedUrl": "https://lever/1", "categories": {"location": "Remote", "commitment": "Intern"},
                                          "descriptionPlain": "Train models.", "lists": [{"text": "Requirements", "content": "<li>PyTorch</li>"}]}])
    if "ashby" in u:
        return httpx.Response(200, json={"jobs": [{"title": "AI Engineer (Part-time)", "location": "Berlin", "publishedAt": RECENT,
                                                   "jobUrl": "https://ashby/1", "descriptionPlain": "LLM agents", "employmentType": "PartTime",
                                                   "isRemote": True}]})
    if "smartrecruiters" in u and u.endswith("/postings?limit=100"):
        return httpx.Response(200, json={"content": [{"id": "77", "name": "Data Engineering Werkstudent", "releasedDate": RECENT,
                                                      "location": {"city": "Munich", "country": "de"}, "typeOfEmployment": {"label": "Part-time"}}]})
    if "smartrecruiters" in u and u.endswith("/postings/77"):
        return httpx.Response(200, json={"jobAd": {"sections": {"jobDescription": {"text": "<p>Airflow and dbt</p>"}}}})
    if "personio" in u:
        xml = f"""<?xml version="1.0"?><workzag-jobs><position><id>9</id><office>Berlin</office><name>Praktikum Data Analytics</name>
        <employmentType>intern</employmentType><schedule>full-time</schedule><createdAt>{RECENT}</createdAt>
        <jobDescriptions><jobDescription><name>Your tasks</name><value>&lt;p&gt;Power BI&lt;/p&gt;</value></jobDescription></jobDescriptions></position></workzag-jobs>"""
        return httpx.Response(200, content=xml.encode())
    if "arbeitsagentur" in u and "/jobdetails/" in u:
        assert request.headers["X-API-Key"] == "jobboerse-jobsuche"
        return httpx.Response(200, json={"stellenangebotsBeschreibung": "Werkstudent KI, Python"})
    if "arbeitsagentur" in u:
        assert "veroeffentlichtseit=5" in u
        return httpx.Response(200, json={"maxErgebnisse": 1, "stellenangebote": [
            {"refnr": "10000-1", "titel": "Werkstudent KI (m/w/d)", "arbeitgeber": "Amt GmbH",
             "arbeitsort": {"ort": "Berlin", "land": "Deutschland"}, "aktuelleVeroeffentlichungsdatum": T.isoformat()}]})
    if "arbeitnow" in u:
        return httpx.Response(200, json={"data": [
            {"title": "Data Scientist Working Student", "company_name": "Now GmbH", "location": "Berlin", "url": "https://an/1",
             "created_at": int(datetime.now().timestamp()), "description": "<p>pandas</p>", "job_types": ["working student"], "remote": False},
            {"title": "Data Analyst", "company_name": "Old", "url": "https://an/2", "created_at": int((datetime.now() - timedelta(days=40)).timestamp())}]})
    if "remoteok" in u:
        return httpx.Response(200, json=[{"legal": "x"}, {"position": "ML Engineer Intern", "company": "R", "date": RECENT,
                                                          "url": "https://rok/1", "description": "<b>LLM</b>", "tags": ["intern"]}])
    return httpx.Response(404)


def client():
    return F._client(transport=httpx.MockTransport(handler))


def run(coro):
    return asyncio.run(coro)


def test_greenhouse_filters_role_and_date_and_decodes_html():
    async def go():
        async with client() as c:
            return await F.greenhouse("acme", "Acme", 5, c)
    jobs = run(go())
    assert [j.title for j in jobs] == ["Working Student Data Science"]
    assert jobs[0].description == "Python & SQL" and jobs[0].mode == "feed"


def test_lever_ashby_smartrecruiters_personio():
    async def go():
        async with client() as c:
            return (await F.lever("x", "X", 5, c), await F.ashby("y", "Y", 5, c),
                    await F.smartrecruiters("z", "Z", 5, c), await F.personio("p", "P", 5, c))
    lv, ab, sr, pe = run(go())
    assert lv[0].job_type_hint == "Intern" and "PyTorch" in lv[0].description
    assert ab[0].work_mode_hint == "Remote" and ab[0].description == "LLM agents"
    assert sr[0].location == "Munich, de" and "Airflow" in sr[0].description and sr[0].url.endswith("/77")
    assert pe[0].url == "https://p.jobs.personio.de/job/9" and "Power BI" in pe[0].description


def test_arbeitsagentur_search_and_details():
    async def go():
        async with client() as c:
            jobs, total = await F.arbeitsagentur("Werkstudent Data", "Berlin", 5, c)
            await F.arbeitsagentur_details(jobs, c)
            return jobs, total
    jobs, total = run(go())
    assert total == 1 and jobs[0].company == "Amt GmbH" and jobs[0].external_id == "10000-1"
    assert jobs[0].url.endswith("/jobdetail/10000-1") and "Python" in jobs[0].description


def test_arbeitnow_and_remoteok():
    async def go():
        async with client() as c:
            return await F.arbeitnow(5, c), await F.remoteok(5, c)
    an, rok = run(go())
    assert [j.title for j in an] == ["Data Scientist Working Student"] and an[0].description == "pandas"
    assert rok[0].work_mode_hint == "Remote" and rok[0].description == "LLM"


def test_relevance_filter():
    assert F.relevant("Werkstudent KI-Entwicklung") and F.relevant("Data Analyst") and F.relevant("ML Engineer")
    assert not F.relevant("Office Manager") and not F.relevant("Sales Intern")
