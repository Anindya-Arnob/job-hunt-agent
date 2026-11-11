"""Reads your Gmail over IMAP (with a Google app password), without marking anything as read.

Only mails that look job-related (by sender and subject) are downloaded in full; everything else
is skipped after reading its header, and nothing is ever changed in your mailbox.
"""
from __future__ import annotations

import email
import imaplib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from typing import Callable, Optional

IMAP_HOST = "imap.gmail.com"
IMAP_FACTORY: Optional[Callable] = None          # test hook: () -> object with the imaplib API

# sender or subject must hint at a job topic, otherwise the mail is not downloaded
JOBISH = re.compile(
    r"bewerb|applica|apply|applied|interview|gespräch|gespraech|position|stelle|job|career|karriere|recruit|hiring|"
    r"talent|personio|recruitee|workday|successfactors|greenhouse|lever\.co|ashby|smartrecruiters|softgarden|rexx|"
    r"candidat|kandidat|eingangsbest|werkstudent|praktik|intern\b|internship|thesis|abschlussarbeit|offer\b|"
    r"angebot|absage|einladung|invitation|assessment|coding challenge|hackerrank|codility|onboarding|vertrag", re.I)


class AuthError(Exception):
    pass


@dataclass
class Mail:
    uid: int
    msg_id: str
    date: Optional[datetime]
    sender: str
    sender_name: str
    subject: str
    text: str = ""
    list_unsubscribe: bool = False
    headers_only: bool = False
    extra: dict = field(default_factory=dict)


def _dec(v) -> str:
    if not v:
        return ""
    try:
        return str(make_header(decode_header(v)))
    except Exception:
        return str(v)


class _Html2Text(HTMLParser):
    BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "table", "td", "section", "article"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip, self.href = [], 0, None
        self._a = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self.skip += 1
        if tag in self.BLOCK:
            self.out.append("\n")
        if tag == "a":
            self.href = dict(attrs).get("href")
            self._a = []

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head"):
            self.skip = max(0, self.skip - 1)
        if tag == "a" and self.href:
            if self.href.startswith("http"):
                self.out.append(f" [{self.href}]")
            self.href = None
        if tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(html: str) -> str:
    p = _Html2Text()
    try:
        p.feed(html)
    except Exception:
        return re.sub(r"<[^>]+>", " ", html)
    text = "".join(p.out).replace("\xa0", " ").replace("‌", "").replace("͏", "").replace("﻿", "")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def body_text(msg) -> str:
    plain, html = "", ""
    for part in (msg.walk() if msg.is_multipart() else [msg]):
        ctype = part.get_content_type()
        if part.get_content_maintype() == "multipart" or part.get("Content-Disposition", "").startswith("attachment"):
            continue
        try:
            payload = part.get_payload(decode=True) or b""
            txt = payload.decode(part.get_content_charset() or "utf-8", "replace")
        except Exception:
            continue
        if ctype == "text/plain" and not plain:
            plain = txt
        elif ctype == "text/html" and not html:
            html = txt
    if html and (not plain or ("http" not in plain and "http" in html)):
        return html_to_text(html)
    return plain.replace("\xa0", " ").strip()


def parse(raw: bytes, uid: int, headers_only: bool = False) -> Mail:
    msg = email.message_from_bytes(raw)
    name, addr = parseaddr(_dec(msg.get("From")))
    try:
        dt = parsedate_to_datetime(msg.get("Date")) if msg.get("Date") else None
        if dt and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        dt = None
    return Mail(uid=uid, msg_id=(msg.get("Message-ID") or f"uid-{uid}").strip(), date=dt, sender=addr.lower(),
                sender_name=name.strip(), subject=_dec(msg.get("Subject")).strip(),
                text="" if headers_only else body_text(msg),
                list_unsubscribe=bool(msg.get("List-Unsubscribe")), headers_only=headers_only)


def _connect(address: str, password: str):
    M = IMAP_FACTORY() if IMAP_FACTORY else imaplib.IMAP4_SSL(IMAP_HOST, 993, timeout=60)
    try:
        M.login(address, password.replace(" ", ""))
    except imaplib.IMAP4.error as e:
        raise AuthError("Gmail did not accept the login. Use a Google app password (16 letters), not your "
                        f"normal Gmail password. ({str(e)[:120]})")
    return M


def test_login(address: str, password: str) -> None:
    M = _connect(address, password)
    try:
        M.logout()
    except Exception:
        pass


def _uids(M, query: str) -> list[int]:
    typ, data = M.uid("search", None, query)
    if typ != "OK" or not data or not data[0]:
        return []
    return [int(x) for x in data[0].split()]


def fetch(address: str, password: str, last_uid: int = 0, uidvalidity: str = "", first_days: int = 60,
          limit: int = 5000, wanted: Callable[[Mail], bool] = None, backlog: Optional[list] = None,
          body_limit: int = 120) -> tuple[list[Mail], str, int, list[int]]:
    """New mails since last_uid (or the last `first_days` days on the first run), NEWEST FIRST.
    Only `body_limit` job mails are downloaded per check; older ones wait in the backlog for the next checks.
    Returns (mails, uidvalidity, highest uid seen, backlog of job-mail uids still to read)."""
    M = _connect(address, password)
    try:
        typ, _ = M.select("INBOX", readonly=True)            # read-only: nothing is marked as read
        if typ != "OK":
            raise RuntimeError("Could not open the inbox")
        uv = ""
        try:
            typ, data = M.status("INBOX", "(UIDVALIDITY)")
            m_ = re.search(rb"UIDVALIDITY (\d+)", data[0] if data else b"")
            uv = m_.group(1).decode() if m_ else ""
        except Exception:
            pass
        same_box = bool(last_uid and uv and uv == uidvalidity)
        if same_box:
            uids = [u for u in _uids(M, f"UID {last_uid + 1}:*") if u > last_uid]
        else:
            since = (datetime.now() - timedelta(days=first_days)).strftime("%d-%b-%Y")
            uids = _uids(M, f"SINCE {since}")
            backlog = []
        uids = sorted(uids)[-limit:]                         # the newest ones
        top = max(uids + [last_uid if same_box else 0])
        mails: list[Mail] = []
        for i in range(0, len(uids), 100):                  # headers first, in chunks
            chunk = ",".join(str(u) for u in uids[i:i + 100])
            typ, data = M.uid("fetch", chunk, "(UID BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID LIST-UNSUBSCRIBE)])")
            for item in data or []:
                if not isinstance(item, tuple):
                    continue
                m_ = re.search(rb"UID (\d+)", item[0])
                if m_:
                    mails.append(parse(item[1], int(m_.group(1)), headers_only=True))
        keep = [x.uid for x in mails if (wanted(x) if wanted else JOBISH.search(f"{x.sender} {x.sender_name} {x.subject}"))]
        todo = sorted(set(keep) | set(backlog or []), reverse=True)  # newest first, then what is left from before
        now_, later = todo[:body_limit], todo[body_limit:]
        full = []
        for uid in now_:
            typ, data = M.uid("fetch", str(uid), "(BODY.PEEK[])")
            raw = next((it[1] for it in data or [] if isinstance(it, tuple)), None)
            if raw:
                full.append(parse(raw, uid))
        full.sort(key=lambda x: (x.date or datetime.min.replace(tzinfo=timezone.utc)))
        return full, uv, top, later
    finally:
        try:
            M.logout()
        except Exception:
            pass
