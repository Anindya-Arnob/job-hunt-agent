"""Job titles: the suggestions while you type, the search words for each title you choose, and the words that
tell from a job's title whether it can be one of yours. Any title you type works, listed or not."""
from __future__ import annotations

import re
from functools import lru_cache

from .places import fold  # noqa: F401  (used by others through roles.fold)

# field -> titles; "Title|other name|other name" (the first other name is searched too)
CATALOGUE = {
    "Data & AI": [
        "Data Scientist|Data Science", "Data Analyst|Data Analytics", "Data Engineer|Big Data Engineer",
        "Machine Learning Engineer|ML Engineer", "AI Engineer|Artificial Intelligence Engineer",
        "Analytics Engineer", "Business Intelligence Analyst|BI Analyst|BI Developer", "Data Architect",
        "MLOps Engineer|ML Platform Engineer", "Computer Vision Engineer", "NLP Engineer|Natural Language Processing",
        "Generative AI Engineer|GenAI Engineer|LLM Engineer", "Prompt Engineer", "Research Scientist|AI Researcher",
        "Statistician", "Quantitative Analyst|Quant", "Data Governance Analyst", "Database Developer|SQL Developer",
    ],
    "Software & IT": [
        "Software Engineer|Software Developer|Programmer", "Frontend Developer|Front End Engineer|React Developer",
        "Backend Developer|Back End Engineer", "Full Stack Developer|Fullstack Engineer", "Mobile Developer|App Developer",
        "iOS Developer", "Android Developer", "Java Developer", "Python Developer", ".NET Developer|C# Developer",
        "JavaScript Developer|Node.js Developer", "PHP Developer", "Go Developer|Golang Developer", "Web Developer",
        "DevOps Engineer|Platform Engineer", "Site Reliability Engineer|SRE", "Cloud Engineer|AWS Engineer|Azure Engineer",
        "Cloud Architect|Solutions Architect", "Software Architect", "Embedded Software Engineer|Firmware Engineer",
        "Game Developer", "QA Engineer|Software Tester|Test Engineer", "Test Automation Engineer",
        "Systems Administrator|System Administrator|Sysadmin", "Network Engineer|Network Administrator",
        "IT Support Specialist|IT Support|Help Desk", "Desktop Support Technician", "Database Administrator|DBA",
        "Cybersecurity Analyst|Security Analyst|Information Security", "Security Engineer", "Penetration Tester|Ethical Hacker",
        "IT Consultant", "SAP Consultant|SAP Developer", "Salesforce Developer|Salesforce Administrator", "ERP Consultant",
        "IT Project Manager", "Scrum Master|Agile Coach", "Technical Writer", "IT Manager", "Blockchain Developer",
    ],
    "Product & Design": [
        "Product Manager", "Product Owner", "Business Analyst", "UX Designer|User Experience Designer",
        "UI Designer|User Interface Designer", "Product Designer", "UX Researcher", "Graphic Designer", "Motion Designer",
        "Video Editor", "Content Designer|UX Writer",
    ],
    "Engineering": [
        "Mechanical Engineer|Maschinenbau", "Electrical Engineer|Elektrotechnik", "Civil Engineer", "Chemical Engineer",
        "Industrial Engineer", "Manufacturing Engineer|Production Engineer", "Process Engineer", "Quality Engineer|Quality Assurance",
        "Automotive Engineer", "Aerospace Engineer", "Robotics Engineer", "Hardware Engineer", "Electronics Engineer",
        "Project Engineer", "Biomedical Engineer", "Environmental Engineer", "Energy Engineer|Renewable Energy",
        "Structural Engineer", "Architect|Architecture", "CAD Designer|Draftsman",
    ],
    "Business & Finance": [
        "Accountant|Accounting", "Financial Analyst|Finance Analyst", "Controller|Financial Controller", "Auditor|Audit",
        "Tax Consultant|Tax Advisor", "Investment Banking Analyst", "Risk Analyst", "Actuary", "Bookkeeper",
        "Management Consultant|Consultant", "Strategy Analyst", "Operations Manager|Operations", "Project Manager",
        "Program Manager", "Supply Chain Analyst|Supply Chain", "Logistics Coordinator|Logistics",
        "Procurement Specialist|Purchasing|Buyer", "Office Manager", "Administrative Assistant|Office Assistant",
        "Executive Assistant", "Business Development Manager|Business Development",
    ],
    "Sales & Marketing": [
        "Marketing Manager|Marketing", "Digital Marketing Specialist|Online Marketing", "Content Marketing|Content Creator",
        "SEO Specialist|SEO", "Social Media Manager|Social Media", "Performance Marketing|SEA", "Brand Manager",
        "Product Marketing Manager", "Sales Representative|Sales", "Account Executive", "Account Manager",
        "Sales Manager", "Customer Success Manager|Customer Success", "Customer Service Representative|Customer Service",
        "Inside Sales", "Copywriter", "Public Relations|PR Manager", "Communications Manager|Communications",
    ],
    "People & Education": [
        "Recruiter|Talent Acquisition", "HR Generalist|Human Resources", "HR Business Partner", "Payroll Specialist|Payroll",
        "Teacher|Lehrer", "Tutor", "Research Assistant", "Lecturer", "Trainer|Corporate Trainer", "Social Worker",
    ],
    "Health": [
        "Registered Nurse|Nurse", "Pharmacist", "Physician|Doctor", "Medical Assistant", "Physiotherapist|Physical Therapist",
        "Laboratory Technician|Lab Technician", "Clinical Research Associate|Clinical Research", "Psychologist",
        "Dentist", "Caregiver|Care Assistant", "Healthcare Administrator",
    ],
    "Legal & Public": [
        "Lawyer|Attorney", "Paralegal", "Legal Assistant", "Compliance Analyst|Compliance", "Policy Analyst",
    ],
    "Media": ["Journalist|Reporter", "Editor", "Photographer", "Translator|Interpreter"],
    "Hospitality & Retail": [
        "Chef|Cook", "Waiter|Server", "Barista", "Hotel Receptionist|Receptionist", "Retail Sales Associate|Sales Associate",
        "Store Manager", "Event Manager|Event Coordinator",
    ],
    "Trades & Logistics": [
        "Electrician", "Mechanic|Technician", "Plumber", "Carpenter", "Welder", "Truck Driver|Driver",
        "Warehouse Worker|Warehouse", "Forklift Operator", "Construction Worker", "Maintenance Technician",
    ],
}

# The role groups of earlier versions (and the AI / data scope) keep their search words
LEGACY = {
    "AI Engineering": ["AI Engineer"],
    "AI/ML Engineering": ["Machine Learning Engineer", "AI ML"],
    "Data Science": ["Data Science", "Data Scientist"],
    "Data Engineering": ["Data Engineer"],
    "Data Analytics Engineering": ["Analytics Engineer", "Data Analyst"],
    "Gen AI": ["Generative AI", "LLM"],
    "MLOps / LLMOps": ["MLOps", "LLMOps"],
    "Machine Learning": ["Machine Learning"],
}
# a few title words in German, so German postings pass the title check
GERMAN = {"developer": "entwickler", "engineer": "ingenieur", "data": "daten", "consultant": "berater",
          "sales": "vertrieb", "accountant": "buchhalt", "accounting": "buchhalt", "nurse": "pflege", "teacher": "lehrer",
          "scientist": "wissenschaft", "research": "forsch", "assistant": "assisten", "security": "sicherheit",
          "mechanic": "mechaniker", "electrician": "elektrik", "driver": "fahrer", "warehouse": "lager",
          "logistics": "logistik", "purchasing": "einkauf", "procurement": "einkauf", "finance": "finanz",
          "financial": "finanz", "controller": "controlling", "recruiter": "recruiting", "software": "softwareentwickl",
          "marketing": "marketing", "designer": "gestalter", "administrator": "administrator", "support": "support",
          "manager": "leiter", "mechanical": "maschinenbau", "electrical": "elektrotechnik", "chef": "koch",
          "lawyer": "rechtsanwalt", "pharmacist": "apotheke", "physician": "arzt", "teaching": "lehr"}
SHORT_OK = {"ai", "ml", "bi", "qa", "it", "ux", "ui", "hr", "seo", "sea", "pr", "sap", "sre", "dba", "cad", "nlp", "llm",
            "ios", "php", "go", "net", "erp", "ki"}
STOP = {"and", "of", "the", "for", "in", "with", "senior", "junior", "lead", "head", "specialist", "associate", "officer",
        "representative", "coordinator", "executive", "assistant", "general", "generalist", "partner", "agent"}
# what a title of the AI / data kind may contain (kept from earlier versions: broad on purpose)
DATA_TITLE = re.compile(
    r"(\b(ai|ki|ml|bi|nlp|llms?|rag|etl|sql|cv|gen ?ai|mlops|llmops|dataops)\b|\ba\.i\.|data|daten|"
    r"artificial|künstlich|kuenstlich|intelligen|machine|maschinell|learning|deep|neural|genai|generativ|"
    r"analy|scien|wissenschaft|statisti|python|algorith|quant|vision|robot|agent|chatbot|prompt|"
    r"automati|business intelligence|power ?bi|tableau|dashboard|reporting|warehouse|pipeline|cloud|"
    r"forecast|predict|insight|research|forschung|informatik|computer|software|developer|entwickl|"
    r"engineer|ingenieur|tech|digital|it[- ]|mathemati|optimi)", re.I)


@lru_cache(maxsize=1)
def _entries() -> list[tuple[str, list[str], str]]:
    out = []
    for fld, items in CATALOGUE.items():
        for it in items:
            parts = it.split("|")
            out.append((parts[0], parts[1:], fld))
    for name, terms in LEGACY.items():
        out.append((name, [t for t in terms if t != name], "Data & AI (groups)"))
    return out


def _entry(role: str):
    f = fold(role)
    for e in _entries():
        if fold(e[0]) == f:
            return e
    return None


def terms_for(role: str) -> list[str]:
    """What is typed into the job sites for a title: the title itself and its most common other name."""
    if role in LEGACY:
        return list(LEGACY[role])
    e = _entry(role)
    if e:
        return [e[0]] + e[1][:1]
    return [role.strip()] if role.strip() else []


def field_of(role: str) -> str:
    e = _entry(role)
    return e[2] if e else ""


def is_data(role: str) -> bool:
    return role in LEGACY or field_of(role) == "Data & AI"


def _tokens(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z0-9.#+]+", fold(text)):
        w = w.strip(".")
        if w in SHORT_OK or (len(w) >= 3 and w not in STOP):
            out.add(w)
    return out


@lru_cache(maxsize=256)
def _title_rx(roles: tuple[str, ...]) -> re.Pattern | None:
    words: set[str] = set()
    for r in roles:
        e = _entry(r)
        for t in [r] + (LEGACY.get(r, [])) + (e[1] if e else []):
            words |= _tokens(t)
    words |= {GERMAN[w] for w in list(words) if w in GERMAN}
    if not words:
        return None
    parts = [(rf"\b{re.escape(w)}\b" if w in SHORT_OK else re.escape(w)) for w in sorted(words, key=len, reverse=True)]
    return re.compile("|".join(parts), re.I)


def title_fits(title: str, roles: list[str], extra: list[str] | None = None) -> bool:
    """Could a job with this title be one of your job titles? Broad on purpose: one shared word is enough
    (the classifier decides later from the full description)."""
    if not roles or not (title or "").strip():
        return True
    t = fold(title)
    if any(is_data(r) for r in roles) and DATA_TITLE.search(title):
        return True
    rx = _title_rx(tuple(roles))
    if rx and rx.search(t):
        return True
    return any(w and fold(w) in t for w in extra or [])


def suggest(q: str, limit: int = 12) -> list[dict]:
    fq = fold(q)
    if not fq:
        popular = ["Software Engineer", "Data Analyst", "Data Scientist", "Product Manager", "DevOps Engineer",
                   "UX Designer", "Marketing Manager", "Accountant", "Mechanical Engineer", "Registered Nurse",
                   "Sales Representative", "Project Manager"]
        hits = [e for p in popular for e in _entries() if e[0] == p]
    else:
        def score(e):
            names = [e[0]] + e[1]
            if fold(e[0]).startswith(fq):
                return 0
            if any(fold(n).startswith(fq) for n in names):
                return 1
            if any(f" {fq}" in f" {fold(n)}" for n in names):
                return 2
            if any(fq in fold(n) for n in names):
                return 3
            return 9
        hits = sorted((e for e in _entries() if score(e) < 9), key=lambda e: (score(e), len(e[0])))
    out, seen = [], set()
    for name, other, fld in hits:
        if name in seen:
            continue
        seen.add(name)
        out.append({"value": name, "label": name, "group": fld, "hint": ", ".join(other[:2])})
        if len(out) >= limit:
            break
    return out
