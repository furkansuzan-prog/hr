"""Training and payroll cost imports (wave-2 items 18/19, ADR-027)

Schema: training_records.legal_basis / start_date / delivery (WP-6 data 3.3.1), payroll_costs.is_correction (3.7.1),
lookup indexes on the natural keys of both imports. No unique constraint: existing tenants may hold legacy rows that
repeat a key; the import enforces the key (validation + commit re-check).
Data: the cost.write permission, granted to the SYSTEM_ADMIN system role templates only (frozen copy). Tenant roles
are not touched; an administrator grants cost.write explicitly. New tenants get it from the seed.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-06 23:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None

_DESC = "Import / correct payroll cost data (with import.write; amounts shown only with cost.read - ADR-027)"
_ROLES = ("SYSTEM_ADMIN",)  # frozen copy: later catalog edits never change history


def upgrade() -> None:
    op.add_column('training_records', sa.Column('legal_basis', sa.String(length=30), nullable=True))
    op.add_column('training_records', sa.Column('start_date', sa.Date(), nullable=True))
    op.add_column('training_records', sa.Column('delivery', sa.String(length=20), nullable=True))
    op.create_index('ix_training_employment_course', 'training_records',
                    ['employment_id', 'course_code', 'completed_on'], unique=False)
    op.add_column('payroll_costs', sa.Column('is_correction', sa.Boolean(), server_default=sa.text('false'),
                                             nullable=False))
    op.create_index('ix_cost_employment_period', 'payroll_costs', ['employment_id', 'period_month'], unique=False)
    bind = op.get_bind()
    bind.execute(sa.text("INSERT INTO permissions (code, description) VALUES ('cost.write', :d) "
                         "ON CONFLICT (code) DO NOTHING"), {"d": _DESC})
    bind.execute(sa.text("INSERT INTO role_permissions (role_id, permission_code) "
                         "SELECT r.id, 'cost.write' FROM roles r WHERE r.is_system IS TRUE AND r.code = ANY(:roles) "
                         "ON CONFLICT DO NOTHING"), {"roles": list(_ROLES)})


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM role_permissions WHERE permission_code = 'cost.write'"))
    bind.execute(sa.text("DELETE FROM permissions WHERE code = 'cost.write'"))
    op.drop_index('ix_cost_employment_period', table_name='payroll_costs')
    op.drop_column('payroll_costs', 'is_correction')
    op.drop_index('ix_training_employment_course', table_name='training_records')
    op.drop_column('training_records', 'delivery')
    op.drop_column('training_records', 'start_date')
    op.drop_column('training_records', 'legal_basis')
