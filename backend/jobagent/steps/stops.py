"""Builds a knock-out stopped: which job, why, when. Shown on the Job board's Stopped tab until the CV is built
or the job is deleted."""
from __future__ import annotations

import json
from datetime import datetime

from sqlmodel import select

from .. import db
from .. import models as m

_P = "stopped:"


def record(job_id: int, reasons: list[str]) -> None:
    with db.session_scope() as s:
        row = s.get(m.AppSetting, _P + str(job_id)) or m.AppSetting(key=_P + str(job_id))
        row.value = json.dumps({"reasons": list(reasons), "at": datetime.now().astimezone().isoformat(timespec="seconds")},
                               ensure_ascii=False)
        s.add(row)


def clear(job_id: int) -> None:
    with db.session_scope() as s:
        row = s.get(m.AppSetting, _P + str(job_id))
        if row:
            s.delete(row)


def all_stops() -> dict[int, dict]:
    """job id -> {"reasons": [...], "at": iso}"""
    out = {}
    with db.session_scope() as s:
        for row in s.exec(select(m.AppSetting).where(m.AppSetting.key.startswith(_P))).all():
            try:
                out[int(row.key[len(_P):])] = json.loads(row.value)
            except (ValueError, TypeError):
                pass
    return out
