"""safety case-level import (ADR-021, wave-2 item 9)

safety_events becomes case level (one row = outcome of one affected person, no identity): event_ref / case_no,
worker_category, reported_date, restricted_days, case_status, SGK notification status/date, high_consequence and the
fatality reclassification marker (WP-2 example L). lost_days becomes nullable (open case: empty is not 0). Partial
unique index on (tenant, source_system, source_record_id) = re-import key "<event_ref>#<case_no>" (seed rows have no
source_record_id). import_batches.dropped_columns keeps the NAMES of prohibited columns dropped on upload.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-06 00:51:30.688000
"""
from alembic import op
import sqlalchemy as sa


revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('import_batches', sa.Column('dropped_columns', sa.JSON(), nullable=True))
    op.add_column('safety_events', sa.Column('event_ref', sa.String(length=100), nullable=True))
    op.add_column('safety_events', sa.Column('case_no', sa.Integer(), nullable=True))
    op.add_column('safety_events', sa.Column('worker_category', sa.String(length=30), nullable=True))
    op.add_column('safety_events', sa.Column('reported_date', sa.Date(), nullable=True))
    op.add_column('safety_events', sa.Column('restricted_days', sa.Integer(), nullable=True))
    op.add_column('safety_events', sa.Column('case_status', sa.String(length=10), nullable=True))
    op.add_column('safety_events', sa.Column('sgk_notification', sa.String(length=20), nullable=True))
    op.add_column('safety_events', sa.Column('sgk_notification_date', sa.Date(), nullable=True))
    op.add_column('safety_events', sa.Column('high_consequence', sa.Boolean(), nullable=True))
    op.add_column('safety_events', sa.Column('reclassified_from_fatality', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.alter_column('safety_events', 'lost_days',
               existing_type=sa.INTEGER(),
               nullable=True)
    op.create_index('uq_safety_case_source', 'safety_events', ['tenant_id', 'source_system', 'source_record_id'], unique=True, postgresql_where=sa.text('source_record_id IS NOT NULL'))


def downgrade() -> None:
    op.execute("UPDATE safety_events SET lost_days = 0 WHERE lost_days IS NULL")
    op.drop_index('uq_safety_case_source', table_name='safety_events', postgresql_where=sa.text('source_record_id IS NOT NULL'))
    op.alter_column('safety_events', 'lost_days',
               existing_type=sa.INTEGER(),
               nullable=False)
    op.drop_column('safety_events', 'reclassified_from_fatality')
    op.drop_column('safety_events', 'high_consequence')
    op.drop_column('safety_events', 'sgk_notification_date')
    op.drop_column('safety_events', 'sgk_notification')
    op.drop_column('safety_events', 'case_status')
    op.drop_column('safety_events', 'restricted_days')
    op.drop_column('safety_events', 'reported_date')
    op.drop_column('safety_events', 'worker_category')
    op.drop_column('safety_events', 'case_no')
    op.drop_column('safety_events', 'event_ref')
    op.drop_column('import_batches', 'dropped_columns')
