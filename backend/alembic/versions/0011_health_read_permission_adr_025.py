"""health.read permission for health-derived KPIs (D-12 / PO-5, ADR-025) - data migration, no schema change

Inserts the permission and grants it to the existing SYSTEM role templates that receive it in
app.security.catalog.ROLE_TEMPLATES (SYSTEM_ADMIN, HR_DIRECTOR, HSE_SPECIALIST). Tenant-defined (non-system) roles are
not touched: an administrator grants health.read to them explicitly. New tenants get it from the seed.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-06 21:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None

_DESC = "View health-derived indicators (sickness, unplanned absence, protected leave; D-12)"
_ROLES = ("SYSTEM_ADMIN", "HR_DIRECTOR", "HSE_SPECIALIST")  # frozen copy: later catalog edits never change history


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("INSERT INTO permissions (code, description) VALUES ('health.read', :d) "
                         "ON CONFLICT (code) DO NOTHING"), {"d": _DESC})
    bind.execute(sa.text("INSERT INTO role_permissions (role_id, permission_code) "
                         "SELECT r.id, 'health.read' FROM roles r WHERE r.is_system IS TRUE AND r.code = ANY(:roles) "
                         "ON CONFLICT DO NOTHING"), {"roles": list(_ROLES)})


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM role_permissions WHERE permission_code = 'health.read'"))
    bind.execute(sa.text("DELETE FROM permissions WHERE code = 'health.read'"))
