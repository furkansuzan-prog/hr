"""Timekeeping rules (Module D). Badge events are PRESENCE evidence only - never productive time or pay entitlement."""
import hashlib
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import AttendanceEvent

NIGHT_START, NIGHT_END = time(20, 0), time(6, 0)  # 4857 s. Kanun md. 69: 20:00-06:00 (legal review flagged)


def dedup_key(person_id: int, device_id: str | None, ts: datetime, direction: str) -> str:
    """Same person, device and direction within the same minute = one physical event (reader double-reads)."""
    raw = f"{person_id}|{device_id or ''}|{ts.replace(second=0, microsecond=0).isoformat()}|{direction}"
    return hashlib.sha256(raw.encode()).hexdigest()


def ingest_attendance(db: Session, tenant_id: int, events: list[dict]) -> dict:
    """Idempotent ingestion: duplicates (in batch or already stored) are skipped, not double counted."""
    rows, seen = [], set()
    for e in events:
        k = dedup_key(e["person_id"], e.get("device_id"), e["event_time"], e["direction"])
        if k in seen:
            continue
        seen.add(k)
        rows.append({"tenant_id": tenant_id, "person_id": e["person_id"], "project_id": e.get("project_id"),
                     "event_time": e["event_time"], "direction": e["direction"], "device_id": e.get("device_id"),
                     "dedup_key": k, "source_system": e.get("source_system", "turnstile")})
    inserted = 0
    if rows:
        res = db.execute(insert(AttendanceEvent).values(rows).on_conflict_do_nothing(
            index_elements=["tenant_id", "dedup_key"]).returning(AttendanceEvent.id))
        inserted = len(res.all())
        db.commit()
    return {"received": len(events), "inserted": inserted, "duplicates": len(events) - inserted}


@dataclass
class PresenceInterval:
    start: datetime
    end: datetime


@dataclass
class PresenceResult:
    intervals: list[PresenceInterval]
    unmatched: list[dict]  # in without out / out without in -> data-quality issues, not assumed hours


def pair_events(events: list[dict], max_shift_hours: float = 16) -> PresenceResult:
    """Pair chronologically sorted in/out events of ONE person. Unpaired events are reported, never imputed."""
    evs = sorted(events, key=lambda e: e["event_time"])
    out, unmatched, open_in = [], [], None
    for e in evs:
        if e["direction"] == "in":
            if open_in is not None:
                unmatched.append(open_in)
            open_in = e
        else:
            if open_in is None:
                unmatched.append(e)
                continue
            dur = (e["event_time"] - open_in["event_time"]).total_seconds() / 3600
            if dur > max_shift_hours:
                unmatched += [open_in, e]
            else:
                out.append(PresenceInterval(open_in["event_time"], e["event_time"]))
            open_in = None
    if open_in is not None:
        unmatched.append(open_in)
    return PresenceResult(out, unmatched)


def split_by_day(iv: PresenceInterval) -> dict[date, dict]:
    """Allocate an interval to calendar days and compute night hours (20:00-06:00). A shift crossing midnight is
    split across both dates; the shift is still attributed to its start date by `shift_date`."""
    res: dict[date, dict] = {}
    cur = iv.start
    while cur < iv.end:
        nxt = min(iv.end, datetime.combine(cur.date() + timedelta(days=1), time(0), tzinfo=cur.tzinfo))
        hours = (nxt - cur).total_seconds() / 3600
        night = _night_overlap(cur, nxt)
        d = res.setdefault(cur.date(), {"hours": 0.0, "night_hours": 0.0})
        d["hours"] += round(hours, 4)
        d["night_hours"] += round(night, 4)
        cur = nxt
    return res


def _night_overlap(a: datetime, b: datetime) -> float:
    day = a.date()
    tz = a.tzinfo
    windows = [(datetime.combine(day, time(0), tzinfo=tz), datetime.combine(day, NIGHT_END, tzinfo=tz)),
               (datetime.combine(day, NIGHT_START, tzinfo=tz), datetime.combine(day + timedelta(days=1), time(0), tzinfo=tz))]
    total = 0.0
    for s, e in windows:
        lo, hi = max(a, s), min(b, e)
        if hi > lo:
            total += (hi - lo).total_seconds() / 3600
    return total


# ---------------------------------------------------------------- leave type normalisation (decision D-6)
# Every leave feed (HRIS, payroll, SGK, Excel) must map source leave labels through normalize_leave_type() so that
# maternity / parental and related protected leave lands in 'parental' and never inflates absenteeism (KPI-003/028/053).
# Order matters: protected leave is checked FIRST, because e.g. an SGK maternity report ("analık raporu") is formally a
# sick note and post-birth unpaid leave (4857 md.74) is formally unpaid leave.
SGK_REPORT_CODES = {"1": "sick", "2": "sick", "3": "sick", "4": "parental"}  # iş kazası, meslek hast., hastalık, analık

_LEAVE_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("parental", ("doğum", "dogum", "analık", "analik", "ebeveyn", "babalık", "babalik", "süt izni", "sut izni",
                  "gebelik", "hamilelik", "evlat edin", "maternity", "paternity", "parental", "nursing", "pregnan",
                  "adoption", "декрет", "беремен", "по уходу за ребенком", "родам")),
    ("public_holiday", ("resmi tatil", "resmî tatil", "bayram", "public holiday", "national holiday", "праздни")),
    ("unexcused", ("habersiz", "mazeretsiz", "devamsız", "devamsiz", "unexcused", "absent without", "awol", "прогул")),
    # work accident / occupational disease stay 'sick' until WP-1 (separate work_injury type) is approved
    ("sick", ("rapor", "hastalık", "hastalik", "istirahat", "iş kazası", "is kazasi", "meslek hastal", "sick",
              "medical", "illness", "work injur", "occupational", "больничн", "нетрудоспособ", "производственн")),
    ("annual", ("yıllık", "yillik", "annual", "vacation", "holiday leave", "ежегодн")),
    ("unpaid", ("ücretsiz", "ucretsiz", "unpaid", "without pay", "без сохранения")),
]


def _fold(text_: str) -> str:
    """Turkish-safe case/diacritic folding: 'İŞ KAZASI', 'iş kazası' and 'is kazasi' all compare equal.
    (str.lower() turns 'İ' into 'i' + combining dot, which broke matching of upper-case Turkish labels.)"""
    t = (text_ or "").strip().replace("İ", "i").replace("I", "i").replace("ı", "i").lower()
    return "".join(c for c in unicodedata.normalize("NFKD", t) if not unicodedata.combining(c))


_FOLDED_RULES = [(lt, tuple(_fold(n) for n in needles)) for lt, needles in _LEAVE_RULES]


def normalize_leave_type(label: str | None, sgk_report_code: str | None = None) -> str:
    """Map a source leave label (TR/EN/RU) or SGK report type code to the platform's leave_type.
    Unknown labels map to 'other' (counted by KPI-003, flagged for review by the import's data-quality rules)."""
    if sgk_report_code is not None and str(sgk_report_code).strip() in SGK_REPORT_CODES:
        return SGK_REPORT_CODES[str(sgk_report_code).strip()]
    text_ = _fold(label or "")
    if text_ in {"annual", "sick", "unpaid", "unexcused", "public_holiday", "parental", "other"}:
        return text_
    for leave_type, needles in _FOLDED_RULES:
        if any(n in text_ for n in needles):
            return leave_type
    return "other"
