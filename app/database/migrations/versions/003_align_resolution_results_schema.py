"""Align resolution_results table with model

Revision ID: 003
Revises: 6e9f689db057
Create Date: 2025-11-25

Aligns the resolution_results table schema with the ResolutionResult model:
- Rename conflict_detection_id to conflict_id
- Add missing columns
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '003'
down_revision = '6e9f689db057'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Align resolution_results schema with model."""

    # Rename conflict_detection_id to conflict_id
    op.alter_column(
        'resolution_results',
        'conflict_detection_id',
        new_column_name='conflict_id'
    )

    # Update index name to match
    op.drop_index('ix_resolution_results_conflict_detection_id', table_name='resolution_results')
    op.create_index('ix_resolution_results_conflict_id', 'resolution_results', ['conflict_id'])

    # Add missing columns from model
    op.add_column('resolution_results', sa.Column(
        'resolved_files',
        postgresql.JSONB(astext_type=sa.Text()),
        nullable=True
    ))

    op.add_column('resolution_results', sa.Column(
        'unresolved_files',
        postgresql.JSONB(astext_type=sa.Text()),
        nullable=True
    ))

    op.add_column('resolution_results', sa.Column(
        'resolution_dag',
        postgresql.JSONB(astext_type=sa.Text()),
        nullable=True
    ))

    op.add_column('resolution_results', sa.Column(
        'processing_time_ms',
        sa.Integer(),
        nullable=True
    ))

    op.add_column('resolution_results', sa.Column(
        'resolution_package_url',
        sa.String(length=1024),
        nullable=True
    ))

    op.add_column('resolution_results', sa.Column(
        'resolution_package_size',
        sa.Integer(),
        nullable=True
    ))

    op.add_column('resolution_results', sa.Column(
        'comment_url',
        sa.String(length=1024),
        nullable=True
    ))

    op.add_column('resolution_results', sa.Column(
        'created_at',
        sa.DateTime(timezone=True),
        server_default=sa.text('now()'),
        nullable=False
    ))

    op.add_column('resolution_results', sa.Column(
        'updated_at',
        sa.DateTime(timezone=True),
        server_default=sa.text('now()'),
        nullable=False
    ))

    # Make resolution_id nullable (model allows null)
    op.alter_column('resolution_results', 'resolution_id', nullable=True)

    # Change confidence_score from numeric to varchar to match model
    op.alter_column(
        'resolution_results',
        'confidence_score',
        type_=sa.String(length=20),
        existing_type=sa.Numeric(precision=3, scale=2),
        postgresql_using='confidence_score::text'
    )


def downgrade() -> None:
    """Revert schema changes."""

    # Remove added columns
    op.drop_column('resolution_results', 'updated_at')
    op.drop_column('resolution_results', 'created_at')
    op.drop_column('resolution_results', 'comment_url')
    op.drop_column('resolution_results', 'resolution_package_size')
    op.drop_column('resolution_results', 'resolution_package_url')
    op.drop_column('resolution_results', 'processing_time_ms')
    op.drop_column('resolution_results', 'resolution_dag')
    op.drop_column('resolution_results', 'unresolved_files')
    op.drop_column('resolution_results', 'resolved_files')

    # Make resolution_id not nullable
    op.alter_column('resolution_results', 'resolution_id', nullable=False)

    # Revert confidence_score type
    op.alter_column(
        'resolution_results',
        'confidence_score',
        type_=sa.Numeric(precision=3, scale=2),
        postgresql_using='confidence_score::numeric(3,2)'
    )

    # Rename conflict_id back to conflict_detection_id
    op.drop_index('ix_resolution_results_conflict_id', table_name='resolution_results')
    op.alter_column(
        'resolution_results',
        'conflict_id',
        new_column_name='conflict_detection_id'
    )
    op.create_index('ix_resolution_results_conflict_detection_id', 'resolution_results', ['conflict_detection_id'])