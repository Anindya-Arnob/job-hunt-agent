from datetime import timedelta

import pytest

from jobagent import db, models as m
from jobagent.cv import service as CV
from jobagent.cv.parser import parse_cv, parse_docx
from jobagent.steps.score import store_score
from jobagent.schemas import ScoreResult

from profile_fixture import sample_profile


def test_parse_real_master_cv(master_cv_path):
    p = parse_docx(master_cv_path)
    assert p.name == "Alex Morgan"
    assert p.contact.email == "alex.morgan@example.com" and p.contact.phone == "+49 170 1234567"
    assert "linkedin.com/in/" in p.contact.linkedin and p.contact.github.endswith("alex-morgan-demo")
    assert len(p.skills) == 10 and p.skills[0].items[:2] == ["Python", "SQL"]
    e = p.experience[0]
    assert (e.title, e.org, e.dates) == ("AI Engineer Intern", "Pixelforge AI", "Dec 2025 – Feb 2026") and len(e.bullets) == 4
    assert len(p.projects) == 8 and p.projects[0].name == "Tech Review YouTube RAG System"
    assert p.projects[0].link.endswith("/rag")
    assert "88,000+" in p.projects[0].bullets[0]
    assert [ed.degree.split()[0] for ed in p.education] == ["M.Sc.", "B.Tech"]
    assert p.education[0].school.startswith("Berlin School")
    assert {(l.language, l.level) for l in p.languages} == {("English", "Advanced (C1)"), ("German", "Elementary (A2)")}
    assert p.uncertain_fields == []


def test_upload_confirm_main_versions(master_cv_path):
    draft = CV.upload(master_cv_path)
    assert draft["profile"]["name"] == "Alex Morgan"
    assert CV.get_main() is None
    r = CV.confirm(draft["profile"], draft["source_file"], name="ML / GenAI focus")
    main, vid = CV.get_main()
    assert vid == r["version_id"] and main.name == "Alex Morgan"
    # edit -> v2
    data = main.model_dump()
    data["skills"][0]["items"].append("Rust")
    r2 = CV.save_version(r["profile_id"], data, note="added Rust")
    p2, vid2 = CV.get_main()
    assert vid2 == r2["version_id"] and "Rust" in p2.skills[0].items
    diff = CV.compare(vid, vid2)
    assert diff[0]["section"] == "skills" and any("Rust" in c for c in diff[0]["changes"])
    # restore v1 -> v3 equal to v1
    r3 = CV.restore_version(vid)
    assert "Rust" not in CV.get_main()[0].skills[0].items
    versions = CV.list_profiles()[0]["versions"]
    assert [v["version"] for v in versions] == [3, 2, 1] and versions[0]["note"] == "Restored v1"
    _ = r3


def test_main_cannot_be_deleted_and_archive_purge():
    a = CV.confirm(sample_profile().model_dump(), name="A")
    b = CV.confirm(sample_profile().model_dump(), name="B", make_main=False)
    with pytest.raises(CV.CVError):
        CV.archive(a["profile_id"])
    CV.archive(b["profile_id"])
    rows = {r["name"]: r for r in CV.list_profiles()}
    assert rows["B"]["archived_at"] and rows["B"]["delete_after"]
    CV.restore(b["profile_id"])
    CV.archive(b["profile_id"])
    assert CV.purge_archived() == 0
    assert CV.purge_archived(now=CV._now() + timedelta(days=31)) == 1
    assert [r["name"] for r in CV.list_profiles()] == ["A"]


def test_changing_main_marks_scores_outdated():
    a = CV.confirm(sample_profile().model_dump(), name="A")
    with db.session_scope() as s:
        j = m.Job(title="x", company="y")
        s.add(j)
        s.flush()
        jid = j.id
    store_score(jid, ScoreResult(skills=30, experience=20, title_fit=8, education=8, language=10, type_location=5),
                cv_version_id=a["version_id"])
    b = CV.confirm(sample_profile().model_dump(), name="B", make_main=True)
    with db.session_scope() as s:
        sc = s.exec(db.select(m.Score)).first()
        assert sc.is_outdated
    _ = b


def test_honesty_numbers():
    nums = CV.numbers_in("Processed 88,000+ comments with 92%+ accuracy, ~51.7% avg, 1.09M+ pairs, 4-bit")
    assert nums == {"88000", "92", "51.7", "1.09", "4"}
    pn = CV.profile_numbers(sample_profile())
    assert {"92", "4000", "88000"} <= pn


def test_template_style(master_cv_path):
    from pathlib import Path
    tpl = Path(__file__).parent / "fixtures" / "template.docx"
    style = CV.set_template(tpl)
    assert style["navy"] == "1B2A4A" and style["teal"] == "2A7F8C" and style["font"] == "Calibri"
    s = CV.active_style()
    assert s["heading_pt"] == 10 and s["body_pt"] == 9


def test_pdf_upload_falls_back_to_text_parser(tmp_path, master_cv_path):
    import subprocess
    out = tmp_path / "pdf"
    out.mkdir()
    r = subprocess.run(["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(out), str(master_cv_path)],
                       capture_output=True, timeout=180)
    pdf = out / (master_cv_path.stem + ".pdf")
    if not pdf.exists():
        pytest.skip("LibreOffice not available")
    p = parse_cv(pdf)
    assert p.name and p.uncertain_fields   # offline text parser flags what you must check
    _ = r
