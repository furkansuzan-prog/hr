"""SGK exit code import + turnover v2 (ADR-023, wave-2 items 13-14)

employment_relationships: termination_reason_source (sgk | hris | manual), planned_end_date, planned_end_verified
(True / False / NULL = could not be evaluated). contract_type gains the value 'unknown' (no DB CHECK - the value set
lives in app.models.CONTRACT_TYPES like LEAVE_TYPES). New tables:
- termination_code_map: per-tenant SGK exit code -> termination category mapping (data, not code); rows with
  verified_at NULL are never used for the category. Unique (tenant, code_system, code, valid_from) NULLS NOT DISTINCT.
- termination_counters: legal entity x month counts of exits whose raw code is not stored (code 11 ->
  work_accident_death_exits, WP-5 review condition 3) - no person link.
Existing rows are not touched: their planned_end_verified stays NULL, i.e. they are never excluded as planned ends.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-06 18:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('employment_relationships', sa.Column('termination_reason_source', sa.String(length=10),
                                                        nullable=True))
    op.add_column('employment_relationships', sa.Column('planned_end_date', sa.Date(), nullable=True))
    op.add_column('employment_relationships', sa.Column('planned_end_verified', sa.Boolean(), nullable=True))
    op.create_table(
        'termination_code_map',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code_system', sa.String(length=30), nullable=False),
        sa.Column('code', sa.String(length=4), nullable=False),
        sa.Column('label_tr', sa.String(length=200), nullable=False),
        sa.Column('category', sa.String(length=30), nullable=False),
        sa.Column('planned_end_eligible', sa.Boolean(), nullable=False),
        sa.Column('health_related', sa.Boolean(), nullable=False),
        sa.Column('retain_code', sa.Boolean(), nullable=False),
        sa.Column('counter', sa.String(length=40), nullable=True),
        sa.Column('valid_from', sa.Date(), nullable=True),
        sa.Column('valid_to', sa.Date(), nullable=True),
        sa.Column('verified_by', sa.String(length=120), nullable=True),
        sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('note', sa.String(length=300), nullable=True),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'code_system', 'code', 'valid_from', name='uq_termination_code',
                            postgresql_nulls_not_distinct=True),
    )
    op.create_index(op.f('ix_termination_code_map_tenant_id'), 'termination_code_map', ['tenant_id'], unique=False)
    op.create_table(
        'termination_counters',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('legal_entity_id', sa.Integer(), nullable=False),
        sa.Column('period_month', sa.Date(), nullable=False),
        sa.Column('counter', sa.String(length=40), nullable=False),
        sa.Column('count', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['legal_entity_id'], ['legal_entities.id']),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'legal_entity_id', 'period_month', 'counter', name='uq_termination_counter'),
    )
    op.create_index(op.f('ix_termination_counters_legal_entity_id'), 'termination_counters', ['legal_entity_id'],
                    unique=False)
    op.create_index(op.f('ix_termination_counters_tenant_id'), 'termination_counters', ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_termination_counters_tenant_id'), table_name='termination_counters')
    op.drop_index(op.f('ix_termination_counters_legal_entity_id'), table_name='termination_counters')
    op.drop_table('termination_counters')
    op.drop_index(op.f('ix_termination_code_map_tenant_id'), table_name='termination_code_map')
    op.drop_table('termination_code_map')
    op.drop_column('employment_relationships', 'planned_end_verified')
    op.drop_column('employment_relationships', 'planned_end_date')
    op.drop_column('employment_relationships', 'termination_reason_source')
