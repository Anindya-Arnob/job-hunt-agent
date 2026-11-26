"""The app stays quick with thousands of jobs and hundreds of learned words, and the shortcuts give the same
answers as the slow way."""
import time

from jobagent import vocab
from jobagent.steps import knockouts as K

ADS = [
    "Werkstudent Data Science. Python, SQL und Power BI. Deutsch C1, Englisch B2. Immatrikuliert an einer Hochschule.",
    "We need 3+ years of experience with C++ and CUDA. A completed Master's degree is required. Work permit for the EU.",
    "Nice to have: Kubernetes, Docker. Machine Learning (scikit-learn, PyTorch). German is a plus.",
    "Abgeschlossenes Studium der Informatik, mehrjährige Berufserfahrung mit Java/Spring, fließend Deutsch.",
    "", "LLMs, RAG, LangGraph und Tool Calling – gerne mit ersten Erfahrungen in MLOps.",
]


def _slow_find_terms(text, terms=None):
    terms = terms or vocab.SKILLS
    out = [t for t in terms if vocab.term_regex(t).search(text or "")]
    return [t for t in out if not any(o != t and t.lower() in o.lower() for o in out)]


def test_the_quick_word_search_finds_the_same_words():
    for ad in ADS:
        assert vocab.find_terms(ad) == _slow_find_terms(ad)
        assert vocab.find_terms(ad, vocab.ALL_TERMS) == _slow_find_terms(ad, vocab.ALL_TERMS)


def test_what_an_ad_asks_for_is_read_once_and_gives_the_same_answer():
    from jobagent.docs.ats import required_levels
    for ad in ADS:
        levels, yrs, deg, permit, student = K._needs(ad)
        assert dict(levels) == required_levels(ad)
        assert yrs == K.required_years(ad) and deg == K.required_degree(ad)
        assert permit == any(K._PERMIT.search(s) and not K.NICE.search(s) for s in K._segments(ad))
        assert student == any(K._STUDENT.search(s) and not K.NICE.search(s) for s in K._segments(ad))
    big = " ".join(ADS) * 20
    K._needs(big)
    t = time.perf_counter()
    for _ in range(2000):
        K._needs(big)
    assert time.perf_counter() - t < 0.1                       # kept in memory, not read again


def test_learning_a_word_rescores_only_the_jobs_that_mention_it(setup, monkeypatch):  # noqa: F811
    from jobagent import db, models as m
    from jobagent.steps import adread
    with db.session_scope() as s:
        j = s.get(m.Job, setup["good"])
        j.full_description += " Experience with Gurobi is required."
        s.add(j)
    try:
        vocab.set_learned({"Gurobi": ["Gurobi Optimizer"]})
        assert adread._jobs_mentioning(["Gurobi"]) == [setup["good"]]
    finally:
        vocab.set_learned({})


from test_10_build_graph import setup  # noqa: E402,F401  (fixture)
