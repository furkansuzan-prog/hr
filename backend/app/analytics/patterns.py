"""Work patterns, public holidays and leave-day conversion (WP-4 3.1-3.2, ADR-024).

Shared by the leave / timesheet imports (A08) and the KPI calculators (A09). Pure reads, no writes:
- `Pattern`: a weekly pattern (working ISO weekdays, daily hours, holiday country);
- `Holidays`: country calendar with fractions (0.5 = eve half day); a country-year without rows = calendar missing;
- `PatternResolver`: pattern of an employment on a date - employment -> project of the primary assignment on that date
  -> legal entity -> None (`pattern_unknown`). The tenant's work_calendars row (45 h) is never a known pattern;
- `working_days`: leave working days of a pattern in [sd, ed] minus public-holiday fractions on working days
  (4857 md.56 last paragraph: weekly rest and public holidays are not leave days - L-1 / L-2 safe defaults).
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    DEFAULT_HOURS_PER_DAY,
    Assignment,
    PublicHoliday,
    WorkPattern,
    WorkPatternAssignment,
)

DEFAULT_WEEKDAYS = (1, 2, 3, 4, 5)  # fallback day counting when no pattern is known (today's Mon-Fri behaviour)


@dataclass(frozen=True)
class Pattern:
    id: int
    code: str
    weekdays: frozenset[int]
    daily_hours: float
    weekly_hours: float
    country: str

    @property
    def days_per_week(self) -> int:
        return len(self.weekdays)

    @staticmethod
    def of(p: WorkPattern) -> "Pattern":
        return Pattern(p.id, p.code, frozenset(int(x) for x in p.working_weekdays), float(p.daily_hours),
                       float(p.weekly_hours), p.country)


class Holidays:
    """Lazy per country-year cache of public_holidays (memoised on the session per request)."""

    def __init__(self, db: Session):
        self.db = db
        self._years: dict[tuple[str, int], dict[date, float]] = db.info.setdefault("holiday_memo", {})

    def _year(self, country: str, year: int) -> dict[date, float]:
        k = (country, year)
        if k not in self._years:
            rows = self.db.execute(select(PublicHoliday.holiday_date, PublicHoliday.fraction).where(
                PublicHoliday.country == country, PublicHoliday.holiday_date >= date(year, 1, 1),
                PublicHoliday.holiday_date <= date(year, 12, 31))).all()
            self._years[k] = {d: float(f) for d, f in rows}
        return self._years[k]

    def fraction(self, country: str | None, d: date) -> float:
        return self._year(country, d.year).get(d, 0.0) if country else 0.0

    def missing_years(self, country: str | None, sd: date, ed: date) -> list[int]:
        if not country:
            return []
        return [y for y in range(sd.year, ed.year + 1) if not self._year(country, y)]


def day_weights(pattern: Pattern | None, holidays: Holidays | None, sd: date, ed: date) -> dict[date, float]:
    """Working-day weight of each day in [sd, ed]: 1 - holiday fraction on the pattern's working weekdays, 0 elsewhere.
    No pattern -> Mon-Fri without holidays (the pre-ADR-024 behaviour, `default_9h`)."""
    weekdays = pattern.weekdays if pattern else frozenset(DEFAULT_WEEKDAYS)
    country = pattern.country if pattern else None
    out: dict[date, float] = {}
    d = sd
    while d <= ed:
        if d.isoweekday() in weekdays:
            w = 1.0 - (holidays.fraction(country, d) if holidays is not None and pattern else 0.0)
            if w > 0:
                out[d] = w
        d += timedelta(days=1)
    return out


def working_days(pattern: Pattern | None, holidays: Holidays | None, sd: date, ed: date) -> float:
    return round(sum(day_weights(pattern, holidays, sd, ed).values()), 2)


def hours_per_day(pattern: Pattern | None) -> float:
    return pattern.daily_hours if pattern else float(DEFAULT_HOURS_PER_DAY)


class PatternResolver:
    """Effective-dated pattern resolution for a set of employments (one load, then pure lookups).
    employment -> project of the primary assignment on the date -> legal entity -> None."""

    def __init__(self, db: Session, tenant_id: int, employment_ids=None):
        self.patterns = {p.id: Pattern.of(p) for p in db.execute(
            select(WorkPattern).where(WorkPattern.tenant_id == tenant_id)).scalars()}
        self.by_emp: dict[int, list] = defaultdict(list)
        self.by_project: dict[int, list] = defaultdict(list)
        self.by_entity: dict[int, list] = defaultdict(list)
        self.primary: dict[int, list] = defaultdict(list)
        if not self.patterns:
            return
        q = select(WorkPatternAssignment).where(WorkPatternAssignment.tenant_id == tenant_id)
        for a in db.execute(q).scalars():
            if a.level == "employment" and (employment_ids is None or a.employment_id in employment_ids):
                self.by_emp[a.employment_id].append(a)
            elif a.level == "project":
                self.by_project[a.project_id].append(a)
            elif a.level == "legal_entity":
                self.by_entity[a.legal_entity_id].append(a)
        if self.by_project:
            aq = select(Assignment.employment_id, Assignment.project_id, Assignment.valid_from, Assignment.valid_to,
                        Assignment.is_primary).where(Assignment.tenant_id == tenant_id,
                                                     Assignment.project_id.is_not(None))
            if employment_ids is not None:
                aq = aq.where(Assignment.employment_id.in_(list(employment_ids)))
            for emp, prj, vf, vt, prim in db.execute(aq):
                self.primary[emp].append((prj, vf, vt, bool(prim)))

    @property
    def empty(self) -> bool:
        return not self.patterns

    @staticmethod
    def _valid(items, on: date):
        for a in sorted(items, key=lambda x: x.valid_from, reverse=True):
            if a.valid_from <= on and (a.valid_to is None or a.valid_to > on):
                return a
        return None

    def project_on(self, employment_id: int, on: date) -> int | None:
        """Project of the employment's primary assignment valid on `on` (a single project assignment also counts)."""
        valid = [(prj, prim) for prj, vf, vt, prim in self.primary.get(employment_id, ())
                 if vf <= on and (vt is None or vt > on)]
        prim = sorted({p for p, is_p in valid if is_p})
        if len(prim) == 1:
            return prim[0]
        allp = sorted({p for p, _ in valid})
        return allp[0] if len(allp) == 1 else None

    def resolve(self, employment_id: int | None, legal_entity_id: int | None, on: date) -> Pattern | None:
        if not self.patterns:
            return None
        if employment_id is not None:
            a = self._valid(self.by_emp.get(employment_id, ()), on)
            if a is not None:
                return self.patterns.get(a.work_pattern_id)
            prj = self.project_on(employment_id, on) if self.by_project else None
            if prj is not None:
                a = self._valid(self.by_project.get(prj, ()), on)
                if a is not None:
                    return self.patterns.get(a.work_pattern_id)
        if legal_entity_id is not None:
            a = self._valid(self.by_entity.get(legal_entity_id, ()), on)
            if a is not None:
                return self.patterns.get(a.work_pattern_id)
        return None

    def by_id(self, pattern_id: int | None) -> Pattern | None:
        return self.patterns.get(pattern_id) if pattern_id else None


def leave_days_in_window(*, days: float | None, hours: float, sd: date, ed: date, ps: date, pe: date,
                         pattern: Pattern | None, holidays: Holidays | None, fallback_hpd: float) -> tuple[float, str]:
    """Leave days of one record that fall into [ps, pe] (KPI-055/056 new version, WP-4 3.4).
    Total days: stored `days`, else hours / pattern daily hours (`hours_divided`), else hours / fallback (`default_9h`).
    Window share: pattern known -> working-day share (holidays weighted), otherwise calendar-day share (v1 rule)."""
    if days is not None:
        total, basis = float(days), None
    elif pattern is not None:
        total, basis = float(hours) / pattern.daily_hours, "hours_divided"
    else:
        total, basis = float(hours) / fallback_hpd, "default_9h"
    if sd >= ps and ed <= pe:
        return total, basis
    lo, hi = max(sd, ps), min(ed, pe)
    if hi < lo:
        return 0.0, basis
    if pattern is not None:
        w = day_weights(pattern, holidays, sd, ed)
        tot = sum(w.values())
        if tot > 0:
            return total * sum(v for d, v in w.items() if lo <= d <= hi) / tot, basis
    return total * ((hi - lo).days + 1) / ((ed - sd).days + 1), basis
