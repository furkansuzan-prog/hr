"""data feed coverage metadata (B-20, ADR-020)

New table data_feed_coverage (feed type x employer x optional project x month; a missing row = 'undeclared')
and import_batches.coverage (declaration sent with an upload). Unique key treats NULL project as one value
(PostgreSQL >= 15 NULLS NOT DISTINCT).

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-06 00:20:39.751919
"""
from alembic import op
import sqlalchemy as sa


revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('data_feed_coverage',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('feed_type', sa.String(length=20), nullable=False),
    sa.Column('legal_entity_id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=True),
    sa.Column('period_month', sa.Date(), nullable=False),
    sa.Column('completeness', sa.String(length=10), nullable=False),
    sa.Column('coverage_ratio', sa.Numeric(precision=4, scale=3), nullable=True),
    sa.Column('confirmed_empty', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('planned_on_leave_days', sa.String(length=10), nullable=True),
    sa.Column('attributes', sa.JSON(), nullable=True),
    sa.Column('basis', sa.String(length=20), nullable=False),
    sa.Column('import_batch_id', sa.Integer(), nullable=True),
    sa.Column('data_source_id', sa.Integer(), nullable=True),
    sa.Column('declared_by', sa.BigInteger(), nullable=True),
    sa.Column('declared_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['data_source_id'], ['data_sources.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['import_batch_id'], ['import_batches.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['legal_entity_id'], ['legal_entities.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_data_feed_coverage_month', 'data_feed_coverage', ['tenant_id', 'period_month'], unique=False)
    op.create_index(op.f('ix_data_feed_coverage_tenant_id'), 'data_feed_coverage', ['tenant_id'], unique=False)
    op.create_index('uq_data_feed_coverage', 'data_feed_coverage', ['tenant_id', 'feed_type', 'legal_entity_id', 'project_id', 'period_month'], unique=True, postgresql_nulls_not_distinct=True)
    op.add_column('import_batches', sa.Column('coverage', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('import_batches', 'coverage')
    op.drop_index('uq_data_feed_coverage', table_name='data_feed_coverage', postgresql_nulls_not_distinct=True)
    op.drop_index(op.f('ix_data_feed_coverage_tenant_id'), table_name='data_feed_coverage')
    op.drop_index('ix_data_feed_coverage_month', table_name='data_feed_coverage')
    op.drop_table('data_feed_coverage')
