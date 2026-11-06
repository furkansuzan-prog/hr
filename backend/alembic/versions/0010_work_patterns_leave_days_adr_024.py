"""Work patterns, public holidays, leave entitlement rules, leave days (ADR-024, wave-2 items 15-17; WP-4 3.1)

New tables:
- work_patterns: weekly pattern catalogue per tenant (working ISO weekdays, daily / weekly hours, holiday country);
- work_pattern_assignments: effective-dated binding (employment | project | legal_entity, exactly one target - CHECK);
- public_holidays: country x date x fraction (1.0 full day, 0.5 eve), shared by all tenants;
- leave_entitlement_rules: contractual / collective-agreement entitlement above the statutory floor; active only with
  source_document + approval (pending_reason marks e.g. the unconfirmed underground +4 days, AL-02).
New columns: employment_relationships.collective_agreement_code / collective_agreement_known / leave_seniority_date;
leave_records.days / day_basis / work_pattern_id. Existing rows are not touched (days NULL = converted at calculation
time; older KPI formula versions never read the new columns).

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-06 19:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('public_holidays',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('country', sa.String(length=2), nullable=False),
    sa.Column('holiday_date', sa.Date(), nullable=False),
    sa.Column('fraction', sa.Numeric(precision=3, scale=2), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('source', sa.String(length=120), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('fraction > 0 AND fraction <= 1', name='ck_public_holiday_fraction'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('country', 'holiday_date', name='uq_public_holiday')
    )
    op.create_table('work_patterns',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('code', sa.String(length=40), nullable=False),
    sa.Column('days_per_week', sa.Integer(), nullable=False),
    sa.Column('working_weekdays', sa.JSON(), nullable=False),
    sa.Column('daily_hours', sa.Numeric(precision=4, scale=2), nullable=False),
    sa.Column('weekly_hours', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.Column('country', sa.String(length=2), nullable=False),
    sa.Column('pattern_type', sa.String(length=10), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('source_system', sa.String(length=50), nullable=False),
    sa.Column('source_record_id', sa.String(length=120), nullable=True),
    sa.Column('record_version', sa.Integer(), nullable=False),
    sa.Column('updated_by', sa.BigInteger(), nullable=True),
    sa.CheckConstraint('daily_hours > 0 AND daily_hours <= 24', name='ck_work_pattern_daily_hours'),
    sa.CheckConstraint('days_per_week BETWEEN 1 AND 7', name='ck_work_pattern_days'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'code', name='uq_work_pattern_code')
    )
    op.create_index(op.f('ix_work_patterns_tenant_id'), 'work_patterns', ['tenant_id'], unique=False)
    op.create_table('leave_entitlement_rules',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('scope_level', sa.String(length=25), nullable=False),
    sa.Column('employment_id', sa.Integer(), nullable=True),
    sa.Column('collective_agreement_code', sa.String(length=40), nullable=True),
    sa.Column('legal_entity_id', sa.Integer(), nullable=True),
    sa.Column('project_id', sa.Integer(), nullable=True),
    sa.Column('rule_type', sa.String(length=20), nullable=False),
    sa.Column('value', sa.JSON(), nullable=False),
    sa.Column('source_document', sa.String(length=200), nullable=False),
    sa.Column('approved_by', sa.String(length=120), nullable=True),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('pending_reason', sa.String(length=40), nullable=True),
    sa.Column('note', sa.String(length=300), nullable=True),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('valid_from', sa.Date(), nullable=False),
    sa.Column('valid_to', sa.Date(), nullable=True),
    sa.CheckConstraint("(scope_level = 'employment' AND employment_id IS NOT NULL) OR (scope_level = 'collective_agreement' AND collective_agreement_code IS NOT NULL) OR (scope_level = 'legal_entity' AND legal_entity_id IS NOT NULL) OR (scope_level = 'project' AND project_id IS NOT NULL)", name='ck_entitlement_scope'),
    sa.CheckConstraint('length(source_document) > 0', name='ck_entitlement_source_document'),
    sa.ForeignKeyConstraint(['employment_id'], ['employment_relationships.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['legal_entity_id'], ['legal_entities.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_leave_entitlement_rules_tenant_id'), 'leave_entitlement_rules', ['tenant_id'], unique=False)
    op.create_table('work_pattern_assignments',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('work_pattern_id', sa.Integer(), nullable=False),
    sa.Column('level', sa.String(length=20), nullable=False),
    sa.Column('employment_id', sa.Integer(), nullable=True),
    sa.Column('project_id', sa.Integer(), nullable=True),
    sa.Column('legal_entity_id', sa.Integer(), nullable=True),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('valid_from', sa.Date(), nullable=False),
    sa.Column('valid_to', sa.Date(), nullable=True),
    sa.Column('source_system', sa.String(length=50), nullable=False),
    sa.Column('source_record_id', sa.String(length=120), nullable=True),
    sa.Column('record_version', sa.Integer(), nullable=False),
    sa.Column('updated_by', sa.BigInteger(), nullable=True),
    sa.CheckConstraint("(level = 'employment' AND employment_id IS NOT NULL AND project_id IS NULL AND legal_entity_id IS NULL) OR (level = 'project' AND project_id IS NOT NULL AND employment_id IS NULL AND legal_entity_id IS NULL) OR (level = 'legal_entity' AND legal_entity_id IS NOT NULL AND employment_id IS NULL AND project_id IS NULL)", name='ck_wpa_target'),
    sa.CheckConstraint('valid_to IS NULL OR valid_to > valid_from', name='ck_wpa_dates'),
    sa.ForeignKeyConstraint(['employment_id'], ['employment_relationships.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['legal_entity_id'], ['legal_entities.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['work_pattern_id'], ['work_patterns.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_work_pattern_assignments_employment_id'), 'work_pattern_assignments', ['employment_id'], unique=False)
    op.create_index(op.f('ix_work_pattern_assignments_tenant_id'), 'work_pattern_assignments', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_work_pattern_assignments_work_pattern_id'), 'work_pattern_assignments', ['work_pattern_id'], unique=False)
    op.create_index('ix_wpa_tenant_level', 'work_pattern_assignments', ['tenant_id', 'level'], unique=False)
    op.add_column('employment_relationships', sa.Column('collective_agreement_code', sa.String(length=40), nullable=True))
    op.add_column('employment_relationships', sa.Column('collective_agreement_known', sa.Boolean(), nullable=True))
    op.add_column('employment_relationships', sa.Column('leave_seniority_date', sa.Date(), nullable=True))
    op.add_column('leave_records', sa.Column('days', sa.Numeric(precision=6, scale=2), nullable=True))
    op.add_column('leave_records', sa.Column('day_basis', sa.String(length=20), nullable=True))
    op.add_column('leave_records', sa.Column('work_pattern_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_leave_records_work_pattern', 'leave_records', 'work_patterns', ['work_pattern_id'], ['id'])


def downgrade() -> None:
    op.drop_constraint('fk_leave_records_work_pattern', 'leave_records', type_='foreignkey')
    op.drop_column('leave_records', 'work_pattern_id')
    op.drop_column('leave_records', 'day_basis')
    op.drop_column('leave_records', 'days')
    op.drop_column('employment_relationships', 'leave_seniority_date')
    op.drop_column('employment_relationships', 'collective_agreement_known')
    op.drop_column('employment_relationships', 'collective_agreement_code')
    op.drop_index('ix_wpa_tenant_level', table_name='work_pattern_assignments')
    op.drop_index(op.f('ix_work_pattern_assignments_work_pattern_id'), table_name='work_pattern_assignments')
    op.drop_index(op.f('ix_work_pattern_assignments_tenant_id'), table_name='work_pattern_assignments')
    op.drop_index(op.f('ix_work_pattern_assignments_employment_id'), table_name='work_pattern_assignments')
    op.drop_table('work_pattern_assignments')
    op.drop_index(op.f('ix_leave_entitlement_rules_tenant_id'), table_name='leave_entitlement_rules')
    op.drop_table('leave_entitlement_rules')
    op.drop_index(op.f('ix_work_patterns_tenant_id'), table_name='work_patterns')
    op.drop_table('work_patterns')
    op.drop_table('public_holidays')
