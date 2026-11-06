"""HR_MANAGER role template gets health.read (D-15, ADR-028) - data migration, no schema change

Grants health.read to the existing SYSTEM role template HR_MANAGER of every tenant (is_system, code 'HR_MANAGER').
Tenant-defined (non-system) roles are not touched. New tenants get it from the seed (app.security.catalog).
Downgrade removes exactly these grants (the permission itself belongs to 0011).

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

_ROLE = "HR_MANAGER"  # frozen copy: later catalog edits never change history


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("INSERT INTO role_permissions (role_id, permission_code) "
                         "SELECT r.id, 'health.read' FROM roles r WHERE r.is_system IS TRUE AND r.code = :role "
                         "ON CONFLICT DO NOTHING"), {"role": _ROLE})


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM role_permissions WHERE permission_code = 'health.read' AND role_id IN "
                         "(SELECT id FROM roles WHERE is_system IS TRUE AND code = :role)"), {"role": _ROLE})
