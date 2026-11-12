"""Morning run: search + score with the default filter profile (runs only while the app is open)."""
from __future__ import annotations

import logging

from sqlmodel import select

from . import db, models as m
from .config import settings

log = logging.getLogger(__name__)
_sched = None
JOB_ID = "morning_search"


async def _morning():
    from .api import runs
    with db.session_scope() as s:
        fp = s.exec(select(m.FilterProfile).where(m.FilterProfile.is_default == True)).first() or \
            s.exec(select(m.FilterProfile)).first()  # noqa: E712
    if fp:
        try:
            await runs.start_search(fp.id)
        except Exception as e:  # e.g. already running
            log.warning("morning run skipped: %s", e)


LOGIN_JOB = "claude_login_check"


async def _login_check():
    """Every 30 minutes: is Claude Code still logged in? (the free `claude auth status`)"""
    import asyncio
    from . import claude_code
    try:
        await asyncio.to_thread(claude_code.check_login, False)
    except Exception as e:
        log.info("login check: %s", e)


def start():
    global _sched
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
    except ImportError:  # pragma: no cover
        return
    if _sched is None:
        _sched = AsyncIOScheduler()
        _sched.start()
        from datetime import datetime, timedelta
        _sched.add_job(_login_check, "interval", minutes=30, id=LOGIN_JOB, replace_existing=True,
                       next_run_time=datetime.now() + timedelta(seconds=15))
    reschedule()


def reschedule():
    if _sched is None:
        return
    if _sched.get_job(JOB_ID):
        _sched.remove_job(JOB_ID)
    if settings.schedule_enabled:
        hh, mm = (settings.schedule_time or "07:10").split(":")
        _sched.add_job(_morning, "cron", hour=int(hh), minute=int(mm), id=JOB_ID, replace_existing=True)
    try:
        reschedule_mail()
    except Exception as e:  # the mail table may not exist yet in very old databases
        log.warning("mail schedule: %s", e)


MAIL_JOB = "mail_check"


def reschedule_mail():
    if _sched is None:
        return
    from .mail import service as mail
    if _sched.get_job(MAIL_JOB):
        _sched.remove_job(MAIL_JOB)
    cfg = mail.config()
    if cfg["enabled"] and cfg["app_password"]:
        from datetime import datetime, timedelta
        _sched.add_job(mail.scheduled_check, "interval", minutes=max(5, int(cfg["interval_min"] or 10)), id=MAIL_JOB,
                       replace_existing=True, next_run_time=datetime.now() + timedelta(seconds=20), max_instances=1,
                       coalesce=True)


def next_mail_check():
    if _sched and _sched.get_job(MAIL_JOB):
        return _sched.get_job(MAIL_JOB).next_run_time
    return None


def next_run():
    if _sched and _sched.get_job(JOB_ID):
        return _sched.get_job(JOB_ID).next_run_time
    return None


def stop():
    global _sched
    if _sched:
        _sched.shutdown(wait=False)
        _sched = None
