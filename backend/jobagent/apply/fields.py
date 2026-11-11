"""What does a form field ask, and what is the honest answer?

Input: one field as read from the page (label, name, type, options ...).
Output: a Decision - fill it (and with what), upload a file, leave it for you, or draft an answer.
Nothing is ever guessed: a question the app can't answer from your details, your saved answers
or your CV is left empty and highlighted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from .profile import ApplicantProfile, find_answer


@dataclass
class Field:
    fid: str                        # data-ja-id given by the page script
    tag: str                        # input / select / textarea
    type: str = "text"              # text, email, tel, file, radio, checkbox, number, date, url ...
    label: str = ""
    name: str = ""
    placeholder: str = ""
    required: bool = False
    options: list[str] = field(default_factory=list)   # select options / radio labels
    value: str = ""                  # what is in it now
    maxlength: int = 0               # the box's character limit (0 = none)

    @property
    def text(self) -> str:
        return " ".join(x for x in (self.label, self.placeholder, self.name.replace("_", " ").replace("-", " ")) if x).lower()


@dataclass
class Decision:
    action: str                     # fill / upload / leave / consent / draft / skip
    value: str = ""
    source: str = ""                # details / saved / cv / claude / file
    why: str = ""                   # shown to you for anything left open


@dataclass
class Context:
    profile: ApplicantProfile
    job_type: str = ""              # Working Student / Internship / Part-time
    cv_pdf: str = ""
    letter_pdf: str = ""
    cv_text: str = ""               # your CV as plain text (for yes/no skill questions)
    company: str = ""
    title: str = ""
    job_text: str = ""
    letter_text: str = ""           # the cover letter written for this job
    drafter: Optional[Callable[[str], str]] = None      # Claude: question -> honest draft answer
    answerer: Optional[Callable[[list], dict]] = None   # Claude: all remaining questions of a page at once


# ------------------------------------------------------------------ helpers ---
YES = ("yes", "ja", "true", "y")
NO = ("no", "nein", "false", "n")


def _has(text: str, *pats: str) -> bool:
    return any(re.search(p, text, re.I) for p in pats)


def choose_option(options: list[str], value: str) -> Optional[str]:
    """The option that means `value` (exact, then contained, then yes/no and language levels)."""
    if not options or not value:
        return None
    v = value.strip().lower()
    opts = [(o, o.strip().lower()) for o in options if o and o.strip()]
    for o, ol in opts:
        if ol == v:
            return o
    for o, ol in opts:
        if v and (v in ol or (len(ol) > 2 and ol in v)) and ol not in ("", "-", "bitte wählen", "please select", "select"):
            return o
    if v in YES or v.startswith("yes"):
        return next((o for o, ol in opts if ol in YES or ol.startswith(("yes", "ja"))), None)
    if v in NO or v.startswith("no"):
        return next((o for o, ol in opts if ol in NO or ol.startswith(("no", "nein"))), None)
    lvl = re.search(r"\b([abc][12])\b", v)
    if lvl:
        code = lvl.group(1)
        hit = next((o for o, ol in opts if re.search(rf"\b{code}\b", ol)), None)
        if hit:
            return hit
        words = {"c2": ("native", "mutter", "proficient", "verhandlungssicher"), "c1": ("fluent", "fließend", "advanced", "sehr gut"),
                 "b2": ("upper", "gut", "good", "professional"), "b1": ("intermediate", "conversational", "mittel"),
                 "a2": ("basic", "grund", "elementary", "beginner"), "a1": ("basic", "grund", "beginner")}
        return next((o for o, ol in opts if any(w in ol for w in words.get(code, ()))), None)
    if v in ("native", "muttersprache"):
        return next((o for o, ol in opts if "native" in ol or "mutter" in ol or "c2" in ol), None)
    return None


def _prefer_not(options: list[str]) -> Optional[str]:
    return next((o for o in options if _has(o, r"prefer not|keine angabe|möchte (ich )?nicht|not to say|decline|divers.*nicht")), None)


def _salary(ctx: Context, f: Field) -> Decision:
    s = ctx.profile.salary
    jt = (ctx.job_type or "").lower()
    try:
        hours = float(ctx.profile.hours_per_week or 20)
    except ValueError:
        hours = 20.0
    hourly = s.part_time_hourly if "part" in jt else s.working_student_hourly
    monthly = s.internship_monthly if ("intern" in jt or "prakti" in jt) else ""
    t = f.text
    unit = "year" if _has(t, r"year|jahr|annual|p\.?\s?a\b|jährlich") else "month" if _has(t, r"month|monat") \
        else "hour" if _has(t, r"hour|stunde|/h\b|per h") else ""

    def num(x: str) -> Optional[float]:
        try:
            return float(str(x).replace(",", ".").replace("€", "").strip())
        except ValueError:
            return None

    h, mo = num(hourly), num(monthly)
    if h is None and mo is None:
        return Decision("leave", why="Expected salary not set in My details")
    if mo is None and h is not None:
        mo = round(h * hours * 52 / 12)
    if h is None and mo is not None:
        h = round(mo * 12 / 52 / hours, 2)
    numeric = f.type in ("number", "range") or _has(t, r"\b(eur|€|amount|betrag|zahl)\b")
    if unit == "year":
        val = round(mo * 12)
        return Decision("fill", str(val) if numeric else f"{val} € per year", "details")
    if unit == "month":
        return Decision("fill", str(round(mo)) if numeric else f"{round(mo)} € per month", "details")
    if unit == "hour":
        return Decision("fill", f"{h:g}" if numeric else f"{h:g} € per hour", "details")
    text = (f"{h:g} € per hour ({hours:g} h/week)" if not monthly else f"{round(mo)} € per month")
    if s.note:
        text += f", {s.note}"
    return Decision("fill", text if not numeric else (f"{h:g}" if not monthly else str(round(mo))), "details")


def _experience(ctx: Context, f: Field) -> Optional[Decision]:
    """'Do you have experience with Spark?' - only Yes if your CV shows it."""
    m = re.search(r"(?:experience|erfahrung|kenntnisse|knowledge|familiar|vertraut)\s+(?:with|in|mit|im|of|von)\s+([^?.,;:]{2,40})", f.label, re.I)
    if not m:
        return None
    skill = re.sub(r"\b(the|a|an|einer?|den|dem)\b", "", m.group(1), flags=re.I).strip(" ?").lower()
    if not skill or not ctx.cv_text:
        return None
    is_yes_no = f.type in ("radio", "checkbox") or (f.tag == "select" and choose_option(f.options, "yes"))
    if _has(f.label, r"how many years|wie viele jahre|years of"):
        return Decision("leave", why=f"Years of {skill}: please fill in (not stated on your CV)")
    if skill in ctx.cv_text.lower():
        return Decision("fill", "Yes" if is_yes_no else f"Yes - {skill} appears in my CV/projects", "cv")
    return Decision("leave", why=f"Your CV doesn't show {skill} - answer yourself (the app won't claim it)")


# ------------------------------------------------------------------- rules ---
CONTACT_ATTRS = {"first_name", "last_name", "full_name", "email", "phone", "linkedin", "github", "website",
                 "street", "postal_code", "city", "date_of_birth"}

# (patterns on the field's label/name/placeholder, profile attribute or handler)
RULES: list[tuple[tuple[str, ...], str]] = [
    ((r"first\s*name|vorname|given name|forename",), "first_name"),
    ((r"last\s*name|nachname|surname|family name|familienname",), "last_name"),
    ((r"^(full |your |your full )?name\s*[*:]?\s*$|full name|vollständiger name|^vor- und nachname",), "full_name"),
    ((r"e-?mail",), "email"),
    ((r"phone|telefon|mobil|handy|tel\.?\b",), "phone"),
    ((r"linkedin",), "linkedin"),
    ((r"github",), "github"),
    ((r"website|portfolio|homepage|personal (site|url)",), "website"),
    ((r"street|straße|strasse|address line|adresse|anschrift|address\b",), "street"),
    ((r"zip|postal|postcode|plz|postleitzahl",), "postal_code"),
    ((r"\bcity\b|stadt|wohnort|\bort\b|town|current location|where are you (based|located)",), "city"),
    ((r"country of residence|\bcountry\b|\bland\b",), "country"),
    ((r"date of birth|birth ?date|geburtsdatum|geburtstag",), "date_of_birth"),
    ((r"nationality|citizenship|staatsangehörigkeit|nationalität",), "nationality"),
    ((r"pronoun",), "pronouns"),
    ((r"university|universität|hochschule|school|college|institution",), "university"),
    ((r"field of study|major|studiengang|studienfach|fachrichtung|subject",), "field_of_study"),
    ((r"degree|abschluss(?!datum)|qualification",), "degree"),
    ((r"graduat|abschlussdatum|studienende|end of studies",), "graduation"),
    ((r"enrol+ed|immatrikul|eingeschrieben|currently a student|student status",), "enrolled_student"),
    ((r"sponsor",), "needs_sponsorship"),
    ((r"(authori[sz]ed|eligible|permitted|legally allowed|right) to work|work authori[sz]ation|arbeitsberechtigung|arbeiten zu dürfen",), "work_authorized"),
    ((r"work permit|arbeitserlaubnis|aufenthalts|residence permit|visa status|visum",), "work_permit"),
    ((r"german|deutsch",), "german_level"),
    ((r"english|englisch",), "english_level"),
    ((r"earliest|start date|starting date|available from|availability date|eintritt|frühest|verfügbar ab|beginn",), "earliest_start"),
    ((r"hours per week|hours/week|wochenstunden|stunden pro woche|weekly hours|wie viele stunden",), "hours_per_week"),
    ((r"duration|dauer|zeitraum|how long",), "internship_duration"),
    ((r"notice period|kündigungsfrist",), "notice_period"),
    ((r"relocat|umzug|umziehen",), "willing_to_relocate"),
    ((r"driver|führerschein",), "drivers_license"),
    ((r"how did you (hear|find)|hear about|where did you (see|find)|aufmerksam geworden|wie sind sie auf|quelle|referral source|source of",), "how_heard"),
]
CONSENT = r"privacy|datenschutz|consent|einwillig|agree|zustimm|terms|bedingungen|talent ?pool|gdpr|dsgvo|i confirm|bestätige|accept"
SALARY = r"salary|gehalt|vergütung|compensation|pay expectation|wage|stundenlohn|lohn|remuneration|verdienst"
OPEN = r"\bwhy\b|warum|weshalb|describe|beschreib|tell us|erzähl|motivation|cover letter text|what (makes|excites|interests)|additional information|anything else|zusätzliche|message to|nachricht an|introduce yourself"
GENDER = r"gender|geschlecht|anrede|salutation|title\b(?!.*job)"


SECURITY_CHECK = (r"captcha|security (query|question|check)|sicherheits(abfrage|frage|code)|spam ?(protection|schutz)|"
                  r"are you (a )?human|kein roboter|not a robot|sum of|summe (von|aus)|what is \d+\s*[-+x*]\s*\d+|"
                  r"wie viel ist|rechenaufgabe|type the (characters|code)|enter the code")


def decide(f: Field, ctx: Context, file_index: int = 0) -> Decision:
    t = f.text
    p = ctx.profile
    # anti-bot checks (math questions, captchas) are always yours - the app never answers them
    if _has(f"{f.label} {f.placeholder} {f.name}", SECURITY_CHECK):
        return Decision("leave", why="Security check – please answer this yourself (the app never answers these)")
    # --- files
    if f.type == "file":
        if _has(t, r"cover|anschreiben|motivation|letter"):
            return Decision("upload", ctx.letter_pdf, "file") if ctx.letter_pdf else Decision("leave", why="No cover letter file")
        if _has(t, r"resume|résumé|\bcv\b|lebenslauf|curriculum") or file_index == 0:
            return Decision("upload", ctx.cv_pdf, "file") if ctx.cv_pdf else Decision("leave", why="No CV file")
        if file_index == 1 and ctx.letter_pdf and not _has(t, r"zeugnis|certificate|transcript|other|weitere|sonstige"):
            return Decision("upload", ctx.letter_pdf, "file")
        return Decision("leave", why="Extra document (certificates, transcripts...) - add yourself if you want")
    # --- consent boxes are yours to tick
    if f.type == "checkbox" and _has(t, CONSENT):
        return Decision("consent", why="Consent / privacy box - tick it yourself after reading")
    if f.value.strip():
        return Decision("skip", f.value, why="already filled")
    # --- salary
    if _has(t, SALARY):
        return _salary(ctx, f)
    # --- gender / salutation: only if you set it, else "prefer not to say" when offered
    if _has(t, GENDER):
        if p.gender:
            opt = choose_option(f.options, p.gender) if f.options else p.gender
            return Decision("fill", opt, "details") if opt else Decision("leave", why="Gender / salutation")
        opt = _prefer_not(f.options)
        return Decision("fill", opt, "details") if opt else Decision("leave", why="Gender / salutation - optional, your choice")
    # --- typed contact fields: your details always win over an old saved answer
    #     (a saved "Phone" answer may come from a country-code dropdown like "Germany (+49)")
    if not f.options:
        for pats, attr in RULES:
            if attr in CONTACT_ATTRS and _has(t, *pats):
                val = p.full_name if attr == "full_name" else getattr(p, attr, "")
                if val:
                    return Decision("fill", val, "details")
                break
    # --- a question you answered before
    saved = find_answer(f.label) if len(f.label) > 3 else None
    if saved:
        val = choose_option(f.options, saved.answer) if f.options else saved.answer
        if val:
            return Decision("fill", val, "saved")
    # --- skill / experience yes-no
    exp = _experience(ctx, f)
    if exp:
        return exp
    # --- your details
    for pats, attr in RULES:
        if _has(t, *pats):
            val = p.full_name if attr == "full_name" else getattr(p, attr, "")
            if not val:
                return Decision("leave", why=f"'{f.label or attr}' - add it in My details to fill it next time")
            if f.options:
                opt = choose_option(f.options, val)
                return Decision("fill", opt, "details") if opt else Decision("leave", why=f"'{f.label}': none of the options match '{val}'")
            return Decision("fill", val, "details")
    # --- open question: Claude drafts from your CV and the job ad (you check it before Submit)
    is_open = f.tag == "textarea" or (len(f.label) > 45 and f.type in ("text", "")) or _has(f.label, OPEN)
    if is_open and f.label and ctx.drafter and not f.options:
        try:
            draft = (ctx.drafter(f.label) or "").strip()
        except Exception as e:  # pragma: no cover
            draft, why = "", str(e)
        if draft:
            return Decision("draft", draft, "claude", why="Draft by Claude from your CV - please read before Submit")
    if not f.label and not f.placeholder:
        return Decision("skip", why="field without a label")
    return Decision("leave", why=f"'{(f.label or f.placeholder)[:80]}' - please answer (saved for next time)")



def fit_length(text: str, limit: int) -> str:
    """Keep within the box's character limit, ending at a full sentence."""
    text = (text or "").strip()
    if not limit or len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "), cut.rfind(".\n"))
    return cut[:end + 1].strip() if end > limit * 0.5 else cut[:cut.rfind(" ")].strip()


def claude_rest(fields: list[Field], decisions: dict[str, Decision], ctx: Context) -> int:
    """Every question still open (not a security check, consent box or file) goes to Claude in ONE call.
    Answers come back as amber drafts for you to check; 'don't know' stays red."""
    if not ctx.answerer:
        return 0
    todo = []
    for f in fields:
        d = decisions.get(f.fid)
        if not d or d.action != "leave" or not (f.label or f.placeholder) or f.type == "file":
            continue
        if _has(f"{f.label} {f.placeholder} {f.name}", SECURITY_CHECK) or _has(f.text, CONSENT) and f.type == "checkbox":
            continue
        todo.append({"id": f.fid, "question": (f.label or f.placeholder)[:400], "type": f.type,
                     **({"max_chars": f.maxlength} if f.maxlength else {}),
                     "options": [o for o in f.options if o and not _has(o, r"^(please )?(select|choose|wählen|bitte)")][:40]})
    if not todo:
        return 0
    got = ctx.answerer(todo)
    n = 0
    by_id = {f.fid: f for f in fields}
    for fid, ans in got.items():
        f = by_id.get(fid)
        if not f or not ans:
            continue
        val = choose_option(f.options, ans) if f.options else fit_length(ans, f.maxlength)
        if not val:
            continue
        decisions[fid] = Decision("draft", val, "claude", why="Answered by Claude from your details/CV - please check")
        n += 1
    return n
