"""add_resolution_packages_table

Revision ID: 6e9f689db057
Revises: 002
Create Date: 2025-11-24 21:06:05.883390+00:00

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '6e9f689db057'
down_revision = '002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create resolution_packages table
    op.create_table(
        'resolution_packages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('package_id', sa.String(length=100), nullable=False, comment='Unique package identifier (format: pkg_<random>)'),
        sa.Column('installation_id', sa.Integer(), nullable=False, comment='GitHub installation ID (not FK to avoid cascade)'),
        sa.Column('repository_owner', sa.String(length=255), nullable=False, comment='Repository owner (org or user)'),
        sa.Column('repository_name', sa.String(length=255), nullable=False, comment='Repository name'),
        sa.Column('source_branch', sa.String(length=255), nullable=False, comment='Source branch name (where push occurred)'),
        sa.Column('source_commit_sha', sa.String(length=40), nullable=False, comment='Source commit SHA (HEAD of source branch)'),
        sa.Column('target_branch', sa.String(length=255), nullable=False, comment='Target branch to merge into'),
        sa.Column('package_data', postgresql.JSON(astext_type=sa.Text()), nullable=False, comment='Full resolution package from Public API'),
        sa.Column('trigger_type', sa.String(length=50), nullable=False, server_default='push_event', comment='How this package was triggered (push_event, manual, scheduled)'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='Timestamp when package was created'),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False, comment='Timestamp when package expires (created_at + 24h)'),
        sa.Column('applied_at', sa.DateTime(timezone=True), nullable=True, comment='Timestamp when resolution was applied (NULL if pending)'),
        sa.Column('applied_by', sa.String(length=255), nullable=True, comment='GitHub username who applied the resolution'),
        sa.Column('check_run_id', sa.BigInteger(), nullable=True, comment='GitHub check run ID associated with this package'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('package_id', name='uq_resolution_packages_package_id')
    )

    # Create indexes
    op.create_index('idx_resolution_packages_package_id', 'resolution_packages', ['package_id'], unique=True)
    op.create_index('idx_resolution_packages_installation_id', 'resolution_packages', ['installation_id'], unique=False)
    op.create_index('idx_resolution_packages_source_commit_sha', 'resolution_packages', ['source_commit_sha'], unique=False)
    op.create_index('idx_resolution_packages_expires_at', 'resolution_packages', ['expires_at'], unique=False)
    op.create_index('idx_resolution_packages_repo', 'resolution_packages', ['installation_id', 'repository_owner', 'repository_name'], unique=False)


def downgrade() -> None:
    # Drop indexes
    op.drop_index('idx_resolution_packages_repo', table_name='resolution_packages')
    op.drop_index('idx_resolution_packages_expires_at', table_name='resolution_packages')
    op.drop_index('idx_resolution_packages_source_commit_sha', table_name='resolution_packages')
    op.drop_index('idx_resolution_packages_installation_id', table_name='resolution_packages')
    op.drop_index('idx_resolution_packages_package_id', table_name='resolution_packages')

    # Drop table
    op.drop_table('resolution_packages')
