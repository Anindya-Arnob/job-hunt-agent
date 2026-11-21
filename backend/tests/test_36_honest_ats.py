"""An honest ATS score: the ad's requirements in words (German too), the language level it asks for, and a
warning when an ad gives too few keywords to judge by."""
import pytest

from jobagent.docs import ats, qa
from jobagent.schemas import CVContent, JobForWriting

from profile_fixture import sample_profile

BITS = """Anforderungen
Bitte beachte, dass für diese Stelle eine Arbeitserlaubnis für Deutschland erforderlich ist.
Sprache: Deutsch - Fließend (C1), Englisch - Konversationssicher (B1-B2)
* Mehrjährige, fundierte Erfahrung in der Softwareentwicklung - Sicherer Umgang mit modernen Software-Engineering-Praktiken,
APIs, automatisierten Tests, Git und CI/CD - Praktische Erfahrung mit LLM-Orchestrierung, Tool Calling und der Integration von
KI-Funktionalitäten - Verständnis für die technischen Herausforderungen produktiver KI-Anwendungen (Fehlerbehandlung,
Sicherheit, Kostenkontrolle) - Strukturierte Analysefähigkeit und Freude an der direkten Zusammenarbeit mit Kunden
Tätigkeiten
* Entwicklung innovativer KI-Lösungen - Analyse von Geschäftsprozessen und Datenstrukturen - Konzeption von KI-Anwendungen
mit Schwerpunkt auf LLMs, NLP und agentischen Workflows - Entwicklung von Backend-Services, APIs und Integrationen mit
Python - Sicherstellung von Betriebsthemen wie Fehlerbehandlung, Monitoring, Sicherheit und Performance"""


def test_a_german_ad_gives_its_real_requirements():
    kw = ats.keywords(JobForWriting(company="BITS", title="Forward Deployed AI Engineer", description=BITS))
    assert len(kw) >= 20                                # it used to find 6
    for k in ("Automated Testing", "Tool Calling", "LLM Orchestration", "Error Handling", "Security", "Cost Control",
              "Monitoring", "Customer Collaboration", "Backend Development", "Python", "CI/CD"):
        assert k in kw, k
    assert len({k.lower() for k in kw}) == len(kw)


@pytest.mark.parametrize("term,text,found", [
    ("Error Handling", "Robuste Fehlerbehandlung mit Retries", True),
    ("Error Handling", "added retries, timeouts and fallbacks", True),
    ("Automated Testing", "236 automated tests with pytest", True),
    ("Automated Testing", "Wrote unit tests", True),
    ("Cost Control", "a daily budget cap and token tracking", True),
    ("Customer Collaboration", "Zusammenarbeit mit Kunden", True),
    ("LangGraph", "LLM orchestration with LangChain", False),     # a tool asked for by name stays itself
    ("Artificial Intelligence", "maintained the pipeline", False),  # 'ai' inside a word is not AI
    ("Artificial Intelligence", "KI-Lösungen entwickelt", True),
    ("Monitoring", "Trained models", False),
])
def test_forms_of_a_keyword(term, text, found):
    assert ats.hit(term, text) is found


@pytest.mark.parametrize("text,want", [
    ("Sprache: Deutsch - Fließend (C1), Englisch - Konversationssicher (B1-B2)", {"german": "C1", "english": "B1"}),
    ("Fluent English required, German is a plus", {"english": "C1"}),
    ("Deutschkenntnisse von Vorteil", {}),
    ("Sehr gute Deutsch- und Englischkenntnisse", {"german": "C1", "english": "C1"}),
    ("German B2 and English C1", {"german": "B2", "english": "C1"}),
])
def test_required_language_levels(text, want):
    assert ats.required_levels(text) == want


def _cv(german_level):
    p = sample_profile()
    return p, CVContent(tagline="AI Engineer", summary=p.summary, skills=[g.model_copy(deep=True) for g in p.skills], experience=p.experience,
                        projects=p.projects[:2], education=p.education,
                        languages=[{"language": "English", "level": "C1"}, {"language": "German", "level": german_level}])


def test_a_language_below_the_level_is_a_knock_out():
    job = JobForWriting(company="BITS", title="Forward Deployed AI Engineer", description=BITS)
    prof, a2 = _cv("Grundkenntnisse (A2)")
    r = ats.score(job, a2, prof)
    assert "German: the ad asks for C1, your CV says A2" in r["knockouts"] and r["parts"]["languages"] < 5
    _, c1 = _cv("Fließend (C1)")
    r2 = ats.score(job, c1, prof)
    assert not any("German" in k for k in r2["knockouts"]) and r2["parts"]["languages"] == 5
    checks = ats.screen_checks(job, a2, prof, prof, r, 80, "", None)
    ko = next(c for c in checks if c.name == "Knock-outs the ad states")
    assert not ko.passed and ko.soft and "C1" in ko.detail          # shown, never a rewrite


def test_few_keywords_are_flagged():
    prof, c = _cv("C1")
    short = ats.score(JobForWriting(company="x", title="Data Analyst", description="Python and SQL."), c, prof)
    assert short["few_keywords"] and short["n_keywords"] < 10
    assert not ats.score(JobForWriting(company="BITS", title="AI Engineer", description=BITS), c, prof)["few_keywords"]


def test_a_concept_your_main_cv_shows_in_other_words_is_not_an_unknown_tool():
    prof, c = _cv("C1")
    prof.projects[0].bullets.append("Added retries, timeouts and fallbacks to every API call.")
    c.skills[0].items += ["Error Handling", "Snowflake Cortex"]
    bad = qa.unknown_tools(c, prof)
    assert "Error Handling" not in bad and "Snowflake Cortex" in bad
