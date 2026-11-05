"""KPI formula versions are immutable at the database level (audit finding #7)

Revision ID: 0003
Revises: 0002
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE OR REPLACE FUNCTION kpi_formula_versions_immutable() RETURNS trigger AS $$
    BEGIN
      IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'kpi_formula_versions rows cannot be deleted';
      END IF;
      IF NEW.kpi_id IS DISTINCT FROM OLD.kpi_id OR NEW.version IS DISTINCT FROM OLD.version
         OR NEW.calculator_key IS DISTINCT FROM OLD.calculator_key OR NEW.formula_text IS DISTINCT FROM OLD.formula_text
         OR NEW.numerator_def IS DISTINCT FROM OLD.numerator_def OR NEW.denominator_def IS DISTINCT FROM OLD.denominator_def
         OR NEW.parameters::text IS DISTINCT FROM OLD.parameters::text THEN
        RAISE EXCEPTION 'kpi_formula_versions are immutable; create a new version instead';
      END IF;
      RETURN NEW;  -- only is_current / change_note may change
    END $$ LANGUAGE plpgsql;
    CREATE TRIGGER trg_kpi_formula_versions_immutable BEFORE UPDATE OR DELETE ON kpi_formula_versions
      FOR EACH ROW EXECUTE FUNCTION kpi_formula_versions_immutable();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_kpi_formula_versions_immutable ON kpi_formula_versions")
    op.execute("DROP FUNCTION IF EXISTS kpi_formula_versions_immutable()")
