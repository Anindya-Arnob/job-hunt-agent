import re
"""Click through every screen in a real browser:  python tests/ui_e2e.py http://127.0.0.1:8790 <screenshot dir>"""
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8790"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/ui_shots")
OUT.mkdir(parents=True, exist_ok=True)
CV = Path(__file__).parent / "fixtures" / "master_cv.docx"
errors = []


def shot(page, name):
    page.screenshot(path=str(OUT / f"{name}.png"), full_page=False)


with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1500, "height": 950})
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: m.type == "error" and errors.append(f"console: {m.text}"))
    page.on("dialog", lambda d: d.accept())
    page.set_default_timeout(30000)

    # 1 dashboard
    page.goto(BASE + "/")
    expect(page.get_by_text("Upload your main CV first.")).to_be_visible()
    shot(page, "01_dashboard_empty")

    # 2 My CV upload -> review -> confirm
    page.locator("aside").get_by_role("link", name=re.compile(r"^My\ CV(\s|$)")).click()
    page.locator("input[type=file][accept='.docx,.pdf']").set_input_files(str(CV))
    expect(page.get_by_text("Check what was read from your CV")).to_be_visible(timeout=60000)
    expect(page.locator("input[value='Alex Morgan']").first).to_be_visible()
    page.wait_for_timeout(1500)
    shot(page, "02_cv_review")
    page.get_by_role("button", name="Confirm & save").click()
    expect(page.get_by_text("Main", exact=True)).to_be_visible()
    shot(page, "03_cv_list")

    # 3 edit -> live preview -> save version
    page.get_by_role("button", name="Edit").first.click()
    expect(page.get_by_text("Live preview")).to_be_visible()
    expect(page.locator("img[alt='page 1']").first).to_be_visible(timeout=60000)
    page.get_by_placeholder("What changed?").fill("UI test edit")
    page.wait_for_timeout(1500)
    shot(page, "04_cv_editor")
    page.get_by_role("button", name="Save as new version").click()
    page.get_by_role("button", name="Versions (2)").click()
    expect(page.get_by_role("cell", name="UI test edit")).to_be_visible()
    page.get_by_role("button", name="Compare A → B").click()
    page.wait_for_timeout(800)
    shot(page, "05_cv_versions")
    page.get_by_title("Delete this version").first.click()
    expect(page.get_by_text("v1 deleted")).to_be_visible()
    page.get_by_role("button", name="Versions (1)").click()
    expect(page.get_by_text("current", exact=True)).to_be_visible()
    page.keyboard.press("Escape")
    page.goto(BASE + "/cv")

    # 4 search
    page.locator("aside").get_by_role("link", name=re.compile(r"^Search(\s|$)")).click()
    expect(page.get_by_text("Live progress")).to_be_visible()
    page.get_by_role("button", name="Run", exact=True).click()
    expect(page.get_by_role("button", name="Solved")).to_be_visible(timeout=120000)      # Indeed's human check
    shot(page, "06a_human_check")
    expect(page.get_by_text("Open job board →")).to_be_visible(timeout=180000)
    expect(page.get_by_role("button", name="Solved")).to_have_count(0)                  # passed -> prompt gone
    shot(page, "06_search_done")

    # 5 job board + detail
    page.locator("aside").get_by_role("link", name=re.compile(r"^Job\ board(\s|$)")).click()
    page.wait_for_selector(".ag-row", timeout=30000)
    n_rows = page.locator(".ag-center-cols-container .ag-row").count()
    assert n_rows > 3, n_rows
    page.locator(".ag-center-cols-container .ag-row").first.click()
    expect(page.get_by_text("Match score")).to_be_visible()
    expect(page.locator("span[title='When this app first found the job']")).to_contain_text("Found")
    expect(page.get_by_role("columnheader", name="Found")).to_be_visible()
    shot(page, "07_job_board_detail")
    page.goto(BASE + "/jobs")
    page.wait_for_selector(".ag-row", timeout=30000)
    rows_now = lambda: int(page.locator("b.tabular-nums").first.inner_text())     # "<n> of <m> jobs"
    total = rows_now()
    # filters: verdict chip -> active chip -> clear all
    page.get_by_role("button", name="Filters").click()
    page.locator("button", has_text="Skip").filter(has=page.locator("span.tabular-nums")).first.click()
    expect(page.get_by_text("Verdict: Skip")).to_be_visible()
    page.wait_for_timeout(300)
    assert rows_now() < total, (rows_now(), total)
    page.get_by_role("button", name="Clear all").click()
    page.wait_for_timeout(300)
    assert rows_now() == total
    page.get_by_role("button", name="Filters").click()
    # search box
    page.get_by_placeholder("Search title, company, place…").fill("zzz-nothing")
    expect(page.get_by_text("No jobs match these filters.")).to_be_visible()
    page.get_by_placeholder("Search title, company, place…").fill("")
    # columns: hide Verdict, show Company
    page.get_by_role("button", name="Columns").click()
    page.get_by_label("Verdict", exact=True).uncheck()
    page.get_by_label("Company (own column)").check()
    page.keyboard.press("Escape")
    expect(page.locator(".ag-header-cell[col-id=verdict]")).to_have_count(0)
    expect(page.locator(".ag-header-cell[col-id=company]")).to_be_visible()
    # sort by company A -> Z
    page.get_by_label("Sort by").select_option("company")
    page.wait_for_timeout(400)
    first = page.locator(".ag-center-cols-container .ag-row[row-index='0'] [col-id=company]").inner_text()
    second = page.locator(".ag-center-cols-container .ag-row[row-index='1'] [col-id=company]").inner_text()
    assert first.lower() <= second.lower(), (first, second)
    # density + saved view survive a reload
    page.get_by_title("Compact rows").click()
    page.get_by_role("button", name="Views").click()
    page.get_by_placeholder("Name this view…").fill("My test view")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_text("Saved view “My test view”")).to_be_visible()
    page.keyboard.press("Escape")
    page.reload()
    page.wait_for_selector(".ag-row", timeout=30000)
    expect(page.locator(".ag-header-cell[col-id=company]")).to_be_visible()
    assert page.locator(".ag-center-cols-container .ag-row").first.bounding_box()["height"] < 50
    page.get_by_role("button", name="Views").click()
    expect(page.get_by_role("button", name="My test view")).to_be_visible()
    page.get_by_role("button", name="Best matches").click()
    expect(page.get_by_text("Verdict: Apply", exact=True)).to_be_visible()
    shot(page, "07b_job_board_custom")
    # back to defaults for the rest of the test
    page.get_by_role("button", name="Clear all").click()
    page.get_by_role("button", name="Columns").click()
    page.get_by_role("button", name="Reset").click()
    page.keyboard.press("Escape")
    page.get_by_title("Comfortable rows").click()
    page.get_by_label("Sort by").select_option("score")
    page.get_by_role("button", name="Excluded").click()
    page.wait_for_timeout(1000)
    shot(page, "08_job_board_excluded")
    page.get_by_role("button", name="New").click()
    page.wait_for_timeout(800)

    # 6 build one CV from the board
    page.locator(".ag-pinned-left-cols-container .ag-row").first.locator("input[type=checkbox]").check()
    page.get_by_role("button", name="Build CV (1)").click()
    expect(page.get_by_role("heading", name="Build queue")).to_be_visible()
    expect(page.get_by_text("Result:")).to_be_visible(timeout=180000)
    page.wait_for_timeout(1000)
    shot(page, "09_build_queue")
    page.get_by_role("button", name="Untick all").click()
    expect(page.get_by_text("0 of ", exact=False)).to_be_visible()
    expect(page.get_by_role("button", name="Build 0")).to_be_disabled()
    page.get_by_role("button", name="Tick all", exact=True).click()
    n = page.locator("tbody input[type=checkbox]:checked").count()
    expect(page.get_by_text(f"{n} of {n} ticked")).to_be_visible()
    page.get_by_label("Tick all", exact=True).uncheck()
    assert page.locator("tbody input[type=checkbox]:checked").count() == 0

    # 7 documents + edit
    page.locator("aside").get_by_role("link", name=re.compile(r"^Documents(\s|$)")).click()
    expect(page.locator("img[alt='page 1']").first).to_be_visible(timeout=60000)
    page.wait_for_timeout(1500)
    shot(page, "10_documents")
    with page.expect_download() as dl:
        page.get_by_title("Download CV as PDF").click()
    assert dl.value.suggested_filename.endswith(".pdf"), dl.value.suggested_filename
    with page.expect_download() as dl2:
        page.get_by_title("Download Letter as Word").click()
    assert dl2.value.suggested_filename.endswith(".docx")
    page.get_by_role("button", name="Edit CV").click()
    page.get_by_role("button", name="Save & re-check").click()
    expect(page.get_by_text("Saved –")).to_be_visible(timeout=60000)
    page.get_by_role("button", name="Your main CV").click()
    page.wait_for_timeout(3000)

    # 7b My details (prefilled from the CV) + Apply queue
    page.locator("aside").get_by_role("link", name=re.compile(r"^My\ details(\s|$)")).click()
    expect(page.get_by_role("heading", name="My details")).to_be_visible()
    expect(page.get_by_label("First name")).to_have_value("Alex")
    expect(page.get_by_label("Last name")).to_have_value("Morgan")
    page.get_by_label("Werkstudent – € per hour").fill("18")
    page.get_by_label("Earliest start").fill("immediately")
    page.get_by_role("button", name="Save changes").click()
    expect(page.get_by_text("Details saved")).to_be_visible()
    page.wait_for_timeout(500)
    shot(page, "10b_my_details")
    page.locator("aside").get_by_role("link", name=re.compile(r"^Apply(\s|$)")).click()
    expect(page.get_by_text("Your jobs with a finished CV")).to_be_visible()
    expect(page.get_by_role("button", name="Apply in my browser")).to_be_visible()
    # nothing is ticked for you: Rebuild / Apply only work on the jobs you tick
    expect(page.get_by_role("button", name="Use the app's Chrome window (0)")).to_be_disabled()
    expect(page.get_by_role("button", name="Rebuild ticked (0)")).to_be_disabled()
    page.locator("label").filter(has=page.get_by_role("button", name="Apply in my browser")).first.locator("input[type=checkbox]").check()
    expect(page.get_by_role("button", name="Use the app's Chrome window (1)")).to_be_enabled()
    expect(page.get_by_role("button", name="Rebuild", exact=True)).to_be_visible()
    # pressed by mistake? the first click only asks
    page.get_by_role("button", name="Rebuild", exact=True).click()
    expect(page.get_by_role("button", name="Rebuild?")).to_be_visible()
    page.get_by_role("button", name=re.compile(r"^Applied\s*0$")).click()
    expect(page.get_by_text("Nothing applied yet")).to_be_visible()
    page.get_by_role("button", name=re.compile(r"^To apply\s*1$")).click()
    page.get_by_role("button", name="How to install (2 minutes)").click()
    expect(page.get_by_text("Load unpacked")).to_be_visible()
    page.wait_for_timeout(500)
    shot(page, "10c_apply")

    # 8 tracker: search, sort, status, expand a row and edit
    page.locator("aside").get_by_role("link", name=re.compile(r"^Tracker(\s|$)")).click()
    expect(page.get_by_role("link", name="CV", exact=True).first).to_be_visible()
    box = page.get_by_placeholder("Search company or job title…")
    box.fill("zzz-nothing")
    expect(page.get_by_text("No matches")).to_be_visible()
    box.fill("nordlicht")
    expect(page.get_by_text("1 of 1 jobs")).to_be_visible()
    box.fill("")
    page.get_by_role("button", name="Company", exact=True).click()
    page.get_by_text("Machine Learning Intern", exact=True).first.click()      # expand
    page.get_by_role("button", name="Mark applied today").click()
    expect(page.locator("select.rounded-full").first).to_have_value("applied")
    page.get_by_role("button", name="in 2 weeks").click()
    expect(page.get_by_text("Follow up ", exact=False).first).to_be_visible()
    page.get_by_placeholder("Contact person, interview date, next step…").fill("Spoke to Anna")
    page.get_by_placeholder("Search company or job title…").click()            # blur saves the note
    page.wait_for_timeout(800)
    shot(page, "11_tracker")

    # 9 connections + company
    page.locator("aside").get_by_role("link", name=re.compile(r"^Connections(\s|$)")).click()
    page.get_by_placeholder("Company name").fill("N26")
    page.get_by_placeholder("Board id").fill("n26")
    page.get_by_role("button", name="Add", exact=True).click()
    expect(page.get_by_role("cell", name="N26", exact=True)).to_be_visible()
    shot(page, "12_connections")

    # 10 runs
    page.locator("aside").get_by_role("link", name=re.compile(r"^Runs\ \&\ coverage(\s|$)")).click()
    expect(page.get_by_role("columnheader", name="Searches")).to_be_visible()
    shot(page, "13_runs")

    # 11 settings
    page.locator("aside").get_by_role("link", name=re.compile(r"^Settings(\s|$)")).click()
    inp = page.locator("div.grid", has=page.get_by_text("Verdict: Apply from", exact=True)).locator("input[type=number]")
    inp.fill("68")
    page.get_by_role("button", name="Save").first.click()
    expect(page.get_by_text("Settings saved")).to_be_visible()
    shot(page, "14_settings")

    # 11a delete the tailored CV + letter on Documents
    page.locator("aside").get_by_role("link", name=re.compile(r"^Documents(\s|$)")).click()
    page.get_by_title("Delete this CV and letter").click()
    expect(page.get_by_text("Deleted 4 file(s)")).to_be_visible()

    # 11b delete jobs from the board (bulk + from the detail panel)
    page.locator("aside").get_by_role("link", name=re.compile(r"^Job\ board(\s|$)")).click()
    page.wait_for_selector(".ag-row", timeout=30000)
    count = lambda: len(page.request.get(BASE + "/api/jobs?tab=new").json())
    before = count()
    rows = page.locator(".ag-pinned-left-cols-container .ag-row")
    rows.nth(0).locator("input[type=checkbox]").check()
    rows.nth(1).locator("input[type=checkbox]").check()
    page.get_by_role("button", name="Delete (2)").click()
    expect(page.get_by_text("Delete 2 jobs?")).to_be_visible()
    page.get_by_role("button", name="Delete", exact=True).click()
    expect(page.get_by_text("Deleted 2 jobs")).to_be_visible()
    page.wait_for_timeout(800)
    after = count()
    assert after == before - 2, (before, after)
    page.locator(".ag-center-cols-container .ag-row").first.click()
    page.get_by_title("Delete this job").click()
    page.get_by_role("button", name="Delete", exact=True).click()
    expect(page.get_by_text("Deleted 1 job")).to_be_visible()
    shot(page, "11b_after_delete")
    page.locator("aside").get_by_role("link", name=re.compile(r"^Settings(\s|$)")).click()
    page.get_by_role("button", name="Show (3)").click()
    expect(page.get_by_role("button", name="Undo").first).to_be_visible()

    # 12 dashboard filled
    page.locator("aside").get_by_role("link", name=re.compile(r"^Dashboard(\s|$)")).click()
    expect(page.get_by_text("Top matches")).to_be_visible()
    page.wait_for_timeout(800)
    shot(page, "15_dashboard")
    # --- Inbox: connect Gmail, the first check sorts the mails
    page.goto(BASE + "/inbox")
    expect(page.get_by_text("Connect your Gmail")).to_be_visible()
    shot(page, "16a_inbox_connect")
    page.get_by_placeholder("you@gmail.com").fill("me@gmail.com")
    page.get_by_placeholder("app password (xxxx xxxx xxxx xxxx)").fill("abcd abcd abcd abcd")
    page.get_by_role("button", name="Connect & check").click()
    expect(page.get_by_text("Beta Labs").first).to_be_visible(timeout=30000)
    expect(page.get_by_text("Mark done")).to_be_visible()
    shot(page, "16b_inbox_todo")
    page.get_by_role("button", name=re.compile(r"^All")).click()
    expect(page.get_by_text("Zeta Robotics").first).to_be_visible()
    expect(page.get_by_text("→ added to your tracker as rejected")).to_be_visible()
    expect(page.get_by_text(re.compile(r"good match\(es\) added to your Job board"))).to_be_visible()
    shot(page, "16c_inbox_all")
    b.close()

real_errors = [e for e in errors if "favicon" not in e]
print("ERRORS:", real_errors if real_errors else "none")
print("UI E2E PASSED" if not real_errors else "UI E2E HAD ERRORS")
