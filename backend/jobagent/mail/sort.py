"""What is this mail? Confirmation, rejection, interview, test, offer, job alert ... plus company and role.

Rules first (fast, work offline); when Claude is available it reads the company mails and gives a
better kind, a one-line summary and what you need to do. Passwords and codes are never stored.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from pydantic import BaseModel, Field

from .. import llm
from .reader import Mail

log = logging.getLogger(__name__)

KINDS = ("confirmation", "rejection", "interview", "assessment", "offer", "update", "recruiter",
         "job_alert", "account", "other")

PLATFORM = re.compile(r"stepstone|glassdoor|xing\.com|linkedin\.com|indeed\.|jobs2web|monster\.|jobware|absolventa|"
                      r"talent\.com|careerjet|jooble|kimeta|arbeitsagentur|wellfound|joinhq|join\.com|jobteaser|"
                      r"careers\.microsoft|email\.careers\.|jobalert|job-alert|jobagent|ziprecruiter|welcometothejungle|"
                      r"get-in-it|praktikum\.info|jobmensa|studentjob|honeypot|otta\.com|instaffo", re.I)
ATS = re.compile(r"recruitee|personio|ashbyhq|myworkday|workday|successfactors|greenhouse|lever\.co|smartrecruiters|"
                 r"softgarden|rexx|jobs2web|teamtailor|workable|bamboohr|d\.vinci|dvinci|onlyfy|prescreen|concludis|"
                 r"umantis|avature|icims|taleo|jobvite|breezy|join\.com|zohorecruit|pinpoint|recruitingapp|"
                 r"hr\.|careers?@|recruiting@|jobs@|noreply|no-reply|donotreply", re.I)
FREEMAIL = {"gmail", "googlemail", "outlook", "hotmail", "yahoo", "gmx", "web", "icloud", "t-online", "posteo", "mail"}

ACCOUNT = re.compile(r"temporary password|temporäres passwort|ihr passwort|your password|kennwort|verify your e-?mail|"
                     r"bestätigen sie ihre e-?mail|confirm your e-?mail|e-mail-adresse bestätigen|registration on our|"
                     r"registrierung|created your (applicant )?(profile|account)|welcome to the .{0,40}(career|portal|cockpit)|"
                     r"willkommen (bei|im) .{0,40}(karriere|portal)|account (has been )?created|konto erstellt|"
                     r"one-time (code|password)|verification code|bestätigungscode|sign-in code|reset your password", re.I)
OFFER = re.compile(r"pleased to offer you|offer letter|job offer|we('d| would) like to offer you|arbeitsvertrag|"
                   r"vertragsangebot|ihnen (gerne )?(eine stelle|die position|einen vertrag) anbieten|"
                   r"freuen uns,? (ihnen|dir) .{0,60}(zusage|anbieten)", re.I)
REJECT = re.compile(r"\bunfortunately\b|\bleider\b|regret to inform|not been successful|unsuccessful|"
                    r"not (to )?(move|moving) forward|decided not to|will not be (moving|proceeding)|"
                    r"(other|another) candidate|andere(n|r)? (kandidat|bewerber)|nicht (weiter )?berücksichtig|"
                    r"nicht für (ihre|deine) bewerbung entscheiden|absage|keine positive rückmeldung|"
                    r"position has (already )?been filled|bereits besetzt|not able to offer|"
                    r"nicht in die engere (wahl|auswahl)|gegen (ihre|deine) bewerbung entschieden|"
                    r"we have decided to pursue|haben uns für eine?n? andere", re.I)
INTERVIEW = re.compile(r"(would|'d) (like|love) to invite you|invite you (to|for) (an? |the )?(first |second |next )?"
                       r"(interview|call|conversation|chat|meeting)|invitation (to|for) (an? )?(interview|gespräch)|"
                       r"schedule (an?|your|the) (interview|call|chat)|book (a|your) (time|slot|interview)|"
                       r"(select|pick|choose) a (time|slot)|calendly\.com|cal\.com/|"
                       r"(möchten|würden) (wir )?(sie|dich) (gerne |sehr gerne )?(zu|zum|für) .{0,40}(gespräch|interview|kennenlernen)|"
                       r"laden (wir )?(sie|dich) .{0,50}(gespräch|interview|kennenlernen) ein|einladung zu(m|r)? .{0,30}"
                       r"(gespräch|interview|kennenlernen)|terminvorschl|termin für (ein|das|unser)|vorstellungsgespräch",
                       re.I)
ASSESS = re.compile(r"coding (challenge|test|exercise)|online[- ]assessment|hackerrank|codility|codesignal|testgorilla|"
                    r"take[- ]home|case study|fallstudie|testaufgabe|online-test|aptitude test|assessment center|"
                    r"aufgabe für (sie|dich)|technical (task|test)", re.I)
UPDATE = re.compile(r"application (was|has been) viewed|bewerbung wurde (an)?gesehen|status (of|update|änderung)|"
                    r"still (in )?review|noch in (der )?prüfung|need (a bit )?more time|benötigen (noch )?(etwas )?(mehr )?zeit",
                    re.I)
CONFIRM = re.compile(r"received your application|application (has been |was )?(received|submitted|sent)|"
                     r"thank(s| you) for (your )?(applying|application|your interest)|thanks for applying|"
                     r"(vielen )?dank für (ihre|deine|ihr|dein) (bewerbung|interesse)|danken (ihnen|dir) für (ihre|deine) bewerbung|"
                     r"eingangsbestätigung|bewerbung (ist )?(bei uns )?(gut )?eingegangen|bewerbung erhalten|"
                     r"bewerbung wurde (erfolgreich )?(versendet|übermittelt|abgeschickt|gesendet)|one step closer|"
                     r"your application to .{2,60} (was|has been) sent|application submitted", re.I)
RECRUITER = re.compile(r"came across your profile|auf (ihr|dein) profil (aufmerksam|gestoßen)|"
                       r"position (that )?(might|may) (interest|be of interest)|i('m| am) a recruiter|headhunter|"
                       r"spannende (position|vakanz) für (sie|dich)", re.I)
ALERT_SUBJ = re.compile(r"jobs? (for|für) (you|dich|sie)|neue jobs|new jobs|job ?alert|jobagent|empfehlung|"
                        r"würde zu dir passen|stellt jetzt ein|passende (stellen|jobs)|match(es)? (your|dein) profil|"
                        r"talent community|veröffentlichte stellen|bewirb dich|apply now|jetzt bewerben|"
                        r"recommended jobs|jobs you may|beliebte(n|r)? (stelle|job)|guter match|top-jobs|weitere jobs", re.I)
APP_CONTEXT = re.compile(r"bewerbung|application|applying|applied|candidacy|kandidatur|position|stelle|role\b|interview", re.I)

SECRET = re.compile(r"((?:passwor[dt]|kennwort|code|pin|token|tan)\b(?:\s+(?:is|lautet|ist))?\s*(?:[:=]|\bis\b|\blautet\b)\s*)(\S{4,})",
                    re.I)


def redact(text: str) -> str:
    return SECRET.sub(lambda m: m.group(1) + "[hidden]", text or "")


# ------------------------------------------------------------------ company / role ---
_NAME_NOISE = re.compile(r"\b(recruiting(-team)?|recruitment|recruiter|hiring team|hiring|talent acquisition|careers?|"
                         r"karriere|jobs?|hr|team|workday|successfactors|do not reply|donotreply|no-?reply|noreply|"
                         r"bewerbermanagement|personalabteilung|people|group recruiting)\b", re.I)


def _looks_person(name: str) -> bool:
    w = name.split()
    return 2 <= len(w) <= 3 and all(x[:1].isupper() and x[1:].islower() for x in w if x.isalpha())


def company_from_sender(mail: Mail) -> str:
    name = mail.sender_name or ""
    if "/" in name and ATS.search(mail.sender):                     # recruitee: "Company/Job title"
        return name.split("/", 1)[0].strip()
    local, _, domain = mail.sender.partition("@")
    sld = domain.split(".")[-2] if domain.count(".") >= 1 else domain
    if " von " in name.lower() and PLATFORM.search(mail.sender):  # "Lisa Stein von Stepstone"
        return ""
    clean = re.split(r"\s[-|–]\s|!", name)[0]
    clean = _NAME_NOISE.sub(" ", clean)
    clean = re.sub(r"\s+", " ", clean).strip(" -|,.")
    squashed = re.sub(r"[^a-z0-9]", "", clean.lower())
    role_box = re.fullmatch(r"(jobs?|careers?|hr|recruiting|recruitment|talent|bewerbung(en)?|karriere|hiring|people|"
                            r"no-?reply|donotreply|info)", local, re.I)
    in_addr = squashed and squashed in re.sub(r"[^a-z0-9]", "", mail.sender.lower())
    if clean and clean.lower() not in ("the",) and (not _looks_person(clean) or in_addr or role_box):
        return clean
    # a person writes through an ATS: personio "revel8-jobs@m.personio.com"
    if ATS.search(domain):
        tag = re.sub(r"[-_.]?(jobs?|careers?|recruiting|hr|bewerbung|noreply|jobnotification|notification|talent)$", "", local, flags=re.I)
        if tag and not re.fullmatch(r"(no-?reply|donotreply|jobs?|careers?|recruiting|hr|info|mail)", tag, re.I):
            return tag
        return ""
    if sld and sld.lower() not in FREEMAIL and not PLATFORM.search(domain):
        return sld.capitalize() if sld.islower() else sld
    return ""


_ROLE_PATS = [
    r"(?:ihre|deine) bewerbung(?: als| für die position als| für die stelle als| für| auf)?:?\s+(?P<r>[^\n|]{4,140}?)(?:,\s*\(|\s*\(\d|\s*\||$)",
    r"position (?:of|as|als)\s+(?P<r>[^\n]{4,140}?)(?:\s+(?:at|bei|ID|in)\b|[.!]\s|\.$|$)",
    r"application (?:as|for)(?: the)?(?: position of)?\s+(?P<r>[^\n]{4,140}?)(?:\s+(?:at|role|position|and)\b|[.!]\s|$)",
    r"applying for the\s+(?P<r>[^\n]{4,120}?)\s+(?:role|position)",
    r"for the\s+(?P<r>[^\n]{4,120}?)\s+(?:role|position)\b",
    r"\|\s*(?:your application|ihre bewerbung)\s*\|\s*(?P<r>[^|\n]{4,140}?)\s*(?:\||$)",
    r"als\s+(?P<r>[^\n]{4,120}?(?:\(m/w/d\)|\(w/m/d\)|\(m/f/d\)|\(f/m/x\)|\(all genders\)))",
]


def role_from(mail: Mail) -> str:
    if "/" in (mail.sender_name or "") and ATS.search(mail.sender):
        return mail.sender_name.split("/", 1)[1].strip()
    for src in (mail.subject, (mail.text or "")[:1500]):
        for p in _ROLE_PATS:
            m_ = re.search(p, src, re.I)
            if m_:
                r = m_.group("r").strip(" .,:;-\"'")
                if 4 <= len(r) <= 140 and not re.match(r"^(the|a|an|our|unser|und|and)\b", r, re.I) and JOBWORD.search(r):
                    return r
    return ""


def company_from_text(text: str) -> str:
    for p in (r"interest in (?:joining |working at |working for )?(?:the )?([A-Z][\w&.\-]*(?: [A-Z0-9][\w&.\-]*){0,4})",
              r"\bat ([A-Z][\w&.\-]*(?: [A-Z0-9][\w&.\-]*){0,4})[.!,]",
              r"\bbei (?:der |den |dem )?([A-Z][\w&.\-]*(?: [A-Z0-9][\w&.\-]*){0,4}) (?:und|beworben|interessier)"):
        m_ = re.search(p, text or "")
        if m_ and m_.group(1).lower() not in ("the", "our", "uns", "this"):
            return m_.group(1).strip()
    return ""


def rule_kind(mail: Mail) -> str:
    head = f"{mail.subject}\n{(mail.text or '')[:4000]}"
    platform = bool(PLATFORM.search(mail.sender)) and not ATS.search(mail.sender.split("@")[-1].replace("jobs2web", ""))
    if ACCOUNT.search(head) and not REJECT.search(head) and not INTERVIEW.search(head):
        return "account"
    if platform:
        if re.search(r"(bewerbung|application) (wurde|was|has been) (erfolgreich )?(versendet|gesendet|sent|submitted)|"
                     r"application submitted|du hast dich .{0,40} beworben", head, re.I):
            return "confirmation"
        if re.search(r"application (was|has been) viewed|bewerbung wurde (an)?gesehen", head, re.I):
            return "update"
        if REJECT.search(mail.subject):
            return "rejection"
        return "job_alert"
    if not APP_CONTEXT.search(head):
        return "job_alert" if ALERT_SUBJ.search(mail.subject) else "other"
    if OFFER.search(head):
        return "offer"
    if REJECT.search(head):
        return "rejection"
    if INTERVIEW.search(head):
        return "interview"
    if ASSESS.search(head):
        return "assessment"
    if CONFIRM.search(head):
        return "confirmation"
    if RECRUITER.search(head):
        return "recruiter"
    if UPDATE.search(head):
        return "update"
    if ALERT_SUBJ.search(mail.subject) or (mail.list_unsubscribe and re.search(r"jobs|stellen", mail.subject, re.I)):
        return "job_alert"
    return "other"


class MailRead(BaseModel):
    id: str
    kind: str = Field(description="one of: " + ", ".join(KINDS))
    company: str = ""
    role: str = ""
    summary: str = Field("", description="one plain sentence: what the mail says")
    action: str = Field("", description="what the candidate has to do, e.g. 'Pick an interview slot by 12 Nov' - empty if nothing")
    when: str = Field("", description="interview / test / deadline date and time if given, as written")


class MailReads(BaseModel):
    mails: list[MailRead] = Field(default_factory=list)


SYSTEM = """You sort a job seeker's emails. For each mail decide:
kind: confirmation (application received/sent), rejection, interview (invitation, scheduling, a call), assessment
(coding test, case study, online test), offer, update (still in review, viewed, needs more time), recruiter (a recruiter
reaching out), job_alert (job recommendations from a platform or company talent community), account (registration,
password, verification - never repeat passwords or codes), other (newsletters, ads, unrelated).
company: the hiring company (not the job board, not the ATS like Workday/Personio/Recruitee). role: the job title if named.
summary: one short sentence. action: what the candidate must do and by when, else empty. when: interview/test date and
time exactly as written. Never invent anything that is not in the mail. Same language as the mail is fine."""


def classify(mails: list[Mail]) -> dict[str, MailRead]:
    """uid -> reading. Rules always; Claude refines company mails (not job alerts) when available."""
    out: dict[str, MailRead] = {}
    for x in mails:
        k = rule_kind(x)
        comp = company_from_sender(x) or (company_from_text(x.text) if k not in ("job_alert", "other") else "")
        if k == "job_alert":
            pm = re.search(r"(stepstone|glassdoor|xing|linkedin|indeed|monster|jobware|wellfound|microsoft|arbeitsagentur)",
                           x.sender, re.I)
            comp = {"stepstone": "StepStone", "xing": "XING", "linkedin": "LinkedIn"}.get(pm.group(1).lower(), pm.group(1).capitalize()) \
                if pm else comp
        out[str(x.uid)] = MailRead(id=str(x.uid), kind=k, company=comp, role=role_from(x) if k not in ("job_alert",) else "",
                                   summary=_key_sentence(x.text, k))
    todo = [x for x in mails if out[str(x.uid)].kind not in ("job_alert", "account")
            and (out[str(x.uid)].kind != "other" or APP_CONTEXT.search(x.subject))]
    for i in range(0, len(todo), 8):
        batch = todo[i:i + 8]
        items = [{"id": str(x.uid), "from": f"{x.sender_name} <{x.sender}>", "subject": x.subject,
                  "text": redact(x.text)[:2500], "rules_guess": out[str(x.uid)].model_dump()} for x in batch]
        try:
            res = llm.structured("read_mail", MailReads, SYSTEM, json.dumps(items, ensure_ascii=False),
                                 fake=lambda b=batch: MailReads(mails=[out[str(x.uid)] for x in b]))
        except Exception as e:
            log.info("mail reading by Claude failed: %s", e)
            continue
        for r in res.mails:
            if r.id in out and r.kind in KINDS:
                base = out[r.id]
                out[r.id] = MailRead(id=r.id, kind=r.kind, company=r.company or base.company, role=r.role or base.role,
                                     summary=redact(r.summary or base.summary), action=redact(r.action), when=r.when)
    return out


def _key_sentence(text: str, kind: str) -> str:
    """The sentence that says what happened ("Leider ...", "... einladen"), not the polite opening."""
    pat = {"rejection": REJECT, "interview": INTERVIEW, "assessment": ASSESS, "offer": OFFER, "update": UPDATE}.get(kind)
    t = re.sub(r"\s+", " ", redact(text or "")).strip()
    if pat:
        for sent in re.split(r"(?<=[.!?])\s+", t):
            if pat.search(sent) and 15 <= len(sent) <= 320:
                return sent.strip()
    return _first_sentence(text)


def _first_sentence(text: str) -> str:
    t = re.sub(r"\s+", " ", redact(text or "")).strip()
    t = re.sub(r"^(hallo|hi|hello|dear|guten tag|sehr geehrte[rs]?)[^,]{0,60},\s*", "", t, flags=re.I)
    m_ = re.match(r"(.{20,240}?[.!?])\s", t + " ")
    return (m_.group(1) if m_ else t[:200]).strip()


# ------------------------------------------------------------------ jobs in alert mails ---
class AlertJob(BaseModel):
    title: str
    company: str = ""
    location: str = ""
    job_type: str = ""
    link: str = Field("", description="the reference like L3 of the link that opens this job")


class AlertJobs(BaseModel):
    jobs: list[AlertJob] = Field(default_factory=list)


JOBWORD = re.compile(r"werkstudent|working student|intern|praktik|thesis|abschlussarbeit|trainee|junior|engineer|scientist|"
                     r"analyst|developer|entwickler|specialist|manager|consultant|associate|assistant|data|machine learning|\bml\b|\bai\b|\bki\b|research|student|"
                     r"\(m/w/d\)|\(w/m/d\)|\(m/f/d\)|\(f/m/[dx]\)|\(all genders\)|\(gn\)", re.I)
STRONG = re.compile(r"werkstudent|working student|intern|praktik|thesis|abschlussarbeit|trainee|engineer|scientist|"
                    r"analyst|developer|entwickler|\(m/w/d\)|\(w/m/d\)|\(m/f/d\)|\(f/m/[dx]\)|\(all genders\)|\(gn\)", re.I)
_TAG = r"(studentenjobs|werkstudent|praktikum|teilzeit|vollzeit|homeoffice( möglich)?|remote|hybrid|minijob|neu|new|top|" \
       r"festanstellung|befristet|unbefristet|internship|full[- ]time|part[- ]time|feste anstellung|abschlussarbeit)"
_SKIP_LINE = re.compile(r"^\W*(ich bin interessiert|jetzt bewerben|apply( now)?|bewerben|mehr (jobs|anzeigen)|view|ansehen|"
                        r"details|vor \d+|\d+ (tag|day|std|hour)|beliebter job|passt|gehalt|\d+\+? mitarbeiter)|"
                        r"^(\W*" + _TAG + r"\W*)+$", re.I)
URL = re.compile(r"https?://[^\s\]\)>\"']+")


def _with_refs(text: str) -> tuple[str, dict]:
    refs, seen = {}, {}

    def sub(m_):
        u = m_.group(0)
        if u not in seen:
            seen[u] = f"L{len(seen) + 1}"
            refs[seen[u]] = u
        return f"[{seen[u]}]"
    return URL.sub(sub, text), refs


def heuristic_jobs(text: str) -> list[dict]:
    """StepStone / Glassdoor / XING style: a title line, company and location lines, then the link."""
    text = re.sub(r"[\u034f\u200b-\u200f\u2060\ufeff\u00ad]", "", text or "").replace("\xa0", " ")
    lines = [l.strip(" \t•·-|") for l in text.splitlines()]
    out, used = [], set()
    used_title = lambda t: t.lower() in used
    for i, l in enumerate(lines):
        for u in URL.findall(l):
            window = [x for x in lines[max(0, i - 9):i + 1] if x and not URL.fullmatch(x.strip("[]"))]
            window = [URL.sub("", x).strip(" []") for x in window]
            window = [x for x in window if x and not _SKIP_LINE.match(x)]
            ok = lambda k: (8 <= len(window[k]) <= 140 and not used_title(window[k]) and
                            not re.search(r"weil du|because you|hallo|hello|schau dir|bewirb dich", window[k], re.I))
            title_idx = next((k for k in reversed(range(len(window))) if STRONG.search(window[k]) and ok(k)), None)
            if title_idx is None:
                title_idx = next((k for k in reversed(range(len(window))) if JOBWORD.search(window[k]) and ok(k)), None)
            if title_idx is None:
                continue
            title = window[title_idx]
            if title.lower() in used:
                continue
            rest = [re.sub(r"[\u034f\u200c\u200b\ufeff]", "", x).strip() for x in window[title_idx + 1:title_idx + 3]]
            rest = [x for x in rest if x and not _SKIP_LINE.match(x)]
            if not rest:                                       # a category link, not a posting
                continue
            used.add(title.lower())
            out.append({"title": title, "company": rest[0] if rest else "", "location": rest[1] if len(rest) > 1 else "",
                        "url": u})
    return out[:30]


def alert_jobs(mail: Mail, use_claude: bool = True) -> list[dict]:
    """[{title, company, location, job_type, url}] found in a job-alert mail."""
    text = (mail.text or "")[:16000]
    ref_text, refs = _with_refs(text)
    fake = lambda: AlertJobs(jobs=[AlertJob(title=j["title"], company=j["company"], location=j["location"],
                                            link=next((k for k, v in refs.items() if v == j["url"]), ""))
                                   for j in heuristic_jobs(text)])
    try:
        if not use_claude:
            raise RuntimeError("enough Claude calls for this check")
        res = llm.structured("mail_jobs", AlertJobs,
                             "List every individual job posting in this job-alert email. Use only what is written. "
                             "link = the [Lx] reference that opens that posting (the title link or the apply link). "
                             "Skip ads, articles, 'more jobs' links and the jobs it says the person already applied to.",
                             f"FROM: {mail.sender_name} <{mail.sender}>\nSUBJECT: {mail.subject}\n\n{ref_text[:12000]}",
                             fake=fake)
    except Exception as e:
        log.info("alert reading failed: %s", e)
        res = fake()
    out = []
    for j in res.jobs:
        url = refs.get(j.link.strip("[] "), "") if j.link else ""
        if j.title and url:
            out.append({"title": j.title.strip(), "company": j.company.strip(), "location": j.location.strip(),
                        "job_type": j.job_type.strip(), "url": url})
    return out
