"""Add missing columns to github_installations

Revision ID: 002
Revises: 001
Create Date: 2025-11-20

Adds missing columns to github_installations table to match the SQLAlchemy model:
- account_id: GitHub account ID (required by model)
- target_type: Installation target type (required by model)
- events: JSON array of subscribed events
- suspended_at: Timestamp for suspended installations
- created_at: Renamed from installed_at for consistency

This migration fixes the schema mismatch that was causing webhook processing errors.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '002'
down_revision = '001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add missing columns to github_installations table."""

    # Add account_id column (required by model, will be populated from GitHub webhook data)
    # Using 0 as default for existing rows, but new installations will have actual account_id
    op.add_column(
        'github_installations',
        sa.Column('account_id', sa.Integer(), nullable=False, server_default='0', comment='GitHub account ID (org or user)')
    )
    op.create_index('ix_github_installations_account_id', 'github_installations', ['account_id'])

    # Add target_type column (required by model)
    # Default to 'User' for existing installations (can be updated later if needed)
    op.add_column(
        'github_installations',
        sa.Column('target_type', sa.String(length=50), nullable=False, server_default='User', comment='Installation target type')
    )

    # Add events column (optional JSON array)
    op.add_column(
        'github_installations',
        sa.Column('events', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='JSON array of webhook events this installation subscribes to')
    )

    # Add suspended_at column (nullable timestamp)
    op.add_column(
        'github_installations',
        sa.Column('suspended_at', sa.DateTime(timezone=True), nullable=True, comment='Timestamp when installation was suspended (if applicable)')
    )

    # Rename installed_at to created_at for consistency with model
    op.alter_column('github_installations', 'installed_at', new_column_name='created_at')


def downgrade() -> None:
    """Remove added columns and revert changes."""

    # Rename created_at back to installed_at
    op.alter_column('github_installations', 'created_at', new_column_name='installed_at')

    # Drop added columns in reverse order
    op.drop_column('github_installations', 'suspended_at')
    op.drop_column('github_installations', 'events')
    op.drop_column('github_installations', 'target_type')
    op.drop_index('ix_github_installations_account_id', table_name='github_installations')
    op.drop_column('github_installations', 'account_id')