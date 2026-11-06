"""timesheet import (ADR-022, wave-2 item 12)

timesheets.day_type (source day type, NULL = not supplied) and timesheets.period_days (1 = daily row, > 1 = period
total starting at work_date; CHECK >= 1). uq_timesheet_day becomes NULLS NOT DISTINCT (PostgreSQL >= 15) so the
import's natural key (employment, work_date, project) also holds for rows without a project. import_batches.options
keeps upload options (mode skip|replace, approved_source) and file-level findings - no row data.
The timesheet_monthly summary and its dirty-tracking triggers (0005) are unchanged: they aggregate approved rows by
work_date month, which is still the contract for daily and period rows.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-06 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('import_batches', sa.Column('options', sa.JSON(), nullable=True))
    op.add_column('timesheets', sa.Column('day_type', sa.String(length=20), nullable=True))
    op.add_column('timesheets', sa.Column('period_days', sa.Integer(), server_default=sa.text('1'), nullable=False))
    op.create_check_constraint('ck_ts_period_days', 'timesheets', 'period_days >= 1')
    dup = op.get_bind().execute(sa.text(
        "SELECT count(*) FROM (SELECT 1 FROM timesheets WHERE project_id IS NULL "
        "GROUP BY employment_id, work_date HAVING count(*) > 1) d")).scalar()
    if dup:
        raise RuntimeError(f"{dup} employment-day(s) have more than one timesheet row without a project; merge them "
                           "before upgrading (uq_timesheet_day becomes NULLS NOT DISTINCT, ADR-022)")
    op.drop_constraint('uq_timesheet_day', 'timesheets', type_='unique')
    op.create_unique_constraint('uq_timesheet_day', 'timesheets', ['employment_id', 'work_date', 'project_id'],
                                postgresql_nulls_not_distinct=True)


def downgrade() -> None:
    op.drop_constraint('uq_timesheet_day', 'timesheets', type_='unique')
    op.create_unique_constraint('uq_timesheet_day', 'timesheets', ['employment_id', 'work_date', 'project_id'])
    op.drop_constraint('ck_ts_period_days', 'timesheets', type_='check')
    op.drop_column('timesheets', 'period_days')
    op.drop_column('timesheets', 'day_type')
    op.drop_column('import_batches', 'options')
