"""Timekeeping rules (Module D). Badge events are PRESENCE evidence only - never productive time or pay entitlement."""
import hashlib
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import AttendanceEvent
from app.models.operations import LEAVE_TYPES

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


# ---------------------------------------------------------------- leave type normalisation (decision D-6, ADR-019)
# Every leave feed (HRIS, payroll, SGK, Excel) must map source leave labels through normalize_leave_type() so that
# maternity / parental and related protected leave lands in 'parental' and never inflates absenteeism (KPI-003/028/053).
# Order matters: protected leave is checked FIRST, because e.g. an SGK maternity report ("analık raporu") is formally a
# sick note and post-birth unpaid leave (4857 md.74) is formally unpaid leave.
# The leave type set itself (LEAVE_TYPES) lives next to the model column (ADR-019).

# WP-1 3.2: work accident (5510 md.13) and occupational disease (md.14) -> work_injury; the report type decides.
SGK_REPORT_CODES = {"1": "work_injury", "2": "work_injury", "3": "sick", "4": "parental"}

_LEAVE_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("parental", ("doğum", "dogum", "analık", "analik", "ebeveyn", "babalık", "babalik", "süt izni", "sut izni",
                  "gebelik", "hamilelik", "evlat edin", "maternity", "paternity", "parental", "nursing", "pregnan",
                  "adoption", "декрет", "беремен", "по уходу за ребенком", "родам")),
    ("public_holiday", ("resmi tatil", "resmî tatil", "bayram", "public holiday", "national holiday", "праздни")),
    ("unexcused", ("habersiz", "mazeretsiz", "devamsız", "devamsiz", "unexcused", "absent without", "awol", "прогул")),
    # WP-1 3.2 + review condition 4: never a bare "kaza" / "accident" / "occupational" / "несчастн" (a traffic or home
    # accident is not a work accident) - only phrases that name the WORK accident / occupational disease.
    ("work_injury", ("iş kaza", "meslek hastal", "work injury", "work-related injury", "work related injury",
                     "occupational injury", "occupational accident", "work accident", "industrial accident",
                     "industrial injury", "occupational disease", "occupational illness", "injury on duty",
                     "производствен", "на производстве", "профзаболев", "профессиональное заболев")),
    ("sick", ("rapor", "hastalık", "hastalik", "istirahat", "sick", "medical", "illness", "больничн",
              "нетрудоспособ")),
    ("annual", ("yıllık", "yillik", "annual", "vacation", "holiday leave", "ежегодн")),
    ("unpaid", ("ücretsiz", "ucretsiz", "unpaid", "without pay", "без сохранения")),
]
# Needles that name a certified rest without saying why (fit both sick and work_injury): a label made only of these
# does not contradict an SGK work-injury code ("Rapor" + code 1 is consistent; "Hastalık raporu" + code 1 is not).
_GENERIC_REST = ("rapor", "istirahat", "medical", "больничн", "нетрудоспособ")

# WP-1 review condition 5: rule codes shared with the HSE import (WP-6)
LABEL_CODE_CONFLICT = "leave_label_code_conflict"
LABEL_AMBIGUOUS = "leave_label_ambiguous"
UNMAPPED_SGK_CODE = "unmapped_sgk_report_code"


def _fold(text_: str) -> str:
    """Turkish-safe case/diacritic folding: 'İŞ KAZASI', 'iş kazası' and 'is kazasi' all compare equal.
    (str.lower() turns 'İ' into 'i' + combining dot, which broke matching of upper-case Turkish labels.)"""
    t = (text_ or "").strip().replace("İ", "i").replace("I", "i").replace("ı", "i").lower()
    return "".join(c for c in unicodedata.normalize("NFKD", t) if not unicodedata.combining(c))


_FOLDED_RULES = [(lt, tuple(_fold(n) for n in needles)) for lt, needles in _LEAVE_RULES]
_FOLDED_GENERIC = frozenset(_fold(n) for n in _GENERIC_REST)
_FOLDED_SPECIFIC_SICK = tuple(n for lt, needles in _FOLDED_RULES if lt == "sick" for n in needles
                              if n not in _FOLDED_GENERIC)


def classify_leave(label: str | None, sgk_report_code: str | None = None) -> tuple[str, list[str]]:
    """Map a source leave label (TR/EN/RU) and/or SGK report type code to (leave_type, warning rule codes).
    1. A recognised SGK report code wins over the label; a label that names a different type ->
       leave_label_code_conflict (the official code is used, the contradiction becomes visible to HSE review).
    2. A non-empty unrecognised code -> unmapped_sgk_report_code, then the label decides.
    3. Label rules in order parental -> public_holiday -> unexcused -> work_injury -> sick -> annual -> unpaid; a label
       naming both protected leave and a work injury stays parental (D-6) with leave_label_ambiguous.
    Unknown labels map to 'other' (counted by KPI-003, flagged by the import's unmapped_leave_label rule)."""
    warnings: list[str] = []
    text_ = _fold(label or "")
    matches = [text_] if text_ in LEAVE_TYPES else [lt for lt, needles in _FOLDED_RULES
                                                    if any(n in text_ for n in needles)]
    label_type = matches[0] if matches else "other"
    code = str(sgk_report_code).strip() if sgk_report_code is not None else ""
    if code in SGK_REPORT_CODES:
        lt = SGK_REPORT_CODES[code]
        generic_rest = label_type == "sick" and not any(n in text_ for n in _FOLDED_SPECIFIC_SICK)
        if label_type not in ("other", lt) and not generic_rest:
            warnings.append(LABEL_CODE_CONFLICT)
        return lt, warnings
    if code:
        warnings.append(UNMAPPED_SGK_CODE)
    if label_type == "parental" and "work_injury" in matches:
        warnings.append(LABEL_AMBIGUOUS)
    return label_type, warnings


def normalize_leave_type(label: str | None, sgk_report_code: str | None = None) -> str:
    """Leave type only; classify_leave also returns the warnings the import records."""
    return classify_leave(label, sgk_report_code)[0]
