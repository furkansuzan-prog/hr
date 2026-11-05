from datetime import date

from fastapi import HTTPException, Query

from app.analytics.dashboards import default_period as _default_period


def default_period() -> tuple[date, date]:
    """Last 12 complete months (single definition in app.analytics.dashboards)."""
    return _default_period()


class Filters:
    """Shared filter definition - identical for tables and charts (section 6.5)."""

    def __init__(
        self,
        period_start: date | None = Query(None, description="Inclusive, ISO date"),
        period_end: date | None = Query(None, description="Inclusive, ISO date"),
        entity_ids: str | None = Query(None, description="Comma-separated legal entity ids"),
        project_id: int | None = Query(None),
        occupation_group: str | None = Query(None),
    ):
        dps, dpe = default_period()
        self.ps = period_start or dps
        self.pe = period_end or dpe
        if self.pe < self.ps:
            raise HTTPException(422, "period_end must be >= period_start")
        if (self.pe - self.ps).days > 366 * 5:
            raise HTTPException(422, "Period longer than 5 years")
        try:
            self.entity_ids = {int(x) for x in entity_ids.split(",") if x.strip()} if entity_ids else None
        except ValueError:
            raise HTTPException(422, "entity_ids must be integers") from None
        self.project_id = project_id
        self.occupation_group = occupation_group

    def kw(self) -> dict:
        return {"entity_ids": self.entity_ids, "project_id": self.project_id, "occupation_group": self.occupation_group}
