"""Dashboard Tables Migration.

Creates tables for MergeContext storage and management.

Revision ID: 002
Revises: 001
Create Date: 2025-12-01
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
    """Create merge_contexts table and related infrastructure."""

    # Create enum types
    merge_context_status = postgresql.ENUM(
        'pending',
        'analyzing',
        'conflict_detected',
        'resolving',
        'resolved',
        'applied',
        'failed',
        name='merge_context_status',
        create_type=False
    )

    source_type = postgresql.ENUM(
        'github',
        'local',
        'manual',
        name='source_type',
        create_type=False
    )

    # Create enum types if they don't exist
    op.execute("CREATE TYPE merge_context_status AS ENUM ('pending', 'analyzing', 'conflict_detected', 'resolving', 'resolved', 'applied', 'failed')")
    op.execute("CREATE TYPE source_type AS ENUM ('github', 'local', 'manual')")

    # Create merge_contexts table
    op.create_table(
        'merge_contexts',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False,
                  server_default=sa.text('gen_random_uuid()')),

        # Source information
        sa.Column('source_type', sa.Enum('github', 'local', 'manual',
                  name='source_type', create_type=False), nullable=False),
        sa.Column('installation_id', sa.BigInteger(), nullable=True,
                  comment='GitHub installation ID (null for local/manual)'),
        sa.Column('repository_full_name', sa.String(length=255), nullable=True,
                  comment='Full repository name (owner/repo)'),

        # User ownership
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=True,
                  comment='User ID from Public API'),

        # Branch configuration (JSONB)
        sa.Column('branch_config', postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  comment='Branch selection and merge-base info'),

        # Commit graph (3-way diff data)
        sa.Column('commit_graph', postgresql.JSONB(astext_type=sa.Text()), nullable=True,
                  comment='CommitGraph with base, source, target ancestry'),

        # Conflict information
        sa.Column('conflict_set', postgresql.JSONB(astext_type=sa.Text()), nullable=True,
                  comment='Detected conflicts grouped for navigation'),

        # Resolution (populated after CRE)
        sa.Column('resolution', postgresql.JSONB(astext_type=sa.Text()), nullable=True,
                  comment='CRE resolution result'),

        # Version manifest (always present for debugging)
        sa.Column('version_manifest', postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  comment='Complete version information for debugging'),

        # Status tracking
        sa.Column('status', sa.Enum('pending', 'analyzing', 'conflict_detected', 'resolving',
                  'resolved', 'applied', 'failed', name='merge_context_status',
                  create_type=False), nullable=False, server_default='pending'),
        sa.Column('status_message', sa.Text(), nullable=True,
                  comment='Human-readable status message'),
        sa.Column('error_details', postgresql.JSONB(astext_type=sa.Text()), nullable=True,
                  comment='Error information if status is failed'),

        # Processing metadata
        sa.Column('processing_started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('processing_completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('processing_duration_ms', sa.Integer(), nullable=True),

        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('now()')),

        # Soft delete
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),

        # Primary key
        sa.PrimaryKeyConstraint('id')
    )

    # Create indexes for common queries
    op.create_index('ix_merge_contexts_installation_id', 'merge_contexts', ['installation_id'])
    op.create_index('ix_merge_contexts_user_id', 'merge_contexts', ['user_id'])
    op.create_index('ix_merge_contexts_status', 'merge_contexts', ['status'])
    op.create_index('ix_merge_contexts_created_at', 'merge_contexts', ['created_at'])
    op.create_index('ix_merge_contexts_repository', 'merge_contexts', ['repository_full_name'])

    # Composite index for user's active contexts
    op.create_index(
        'ix_merge_contexts_user_active',
        'merge_contexts',
        ['user_id', 'status'],
        postgresql_where=sa.text("deleted_at IS NULL")
    )

    # Index for version debugging queries (CRE version lookup)
    op.execute("""
        CREATE INDEX ix_merge_contexts_cre_version
        ON merge_contexts ((version_manifest->'cre'->>'engine'))
    """)

    # Create or replace trigger function for updated_at
    op.execute("""
        CREATE OR REPLACE FUNCTION update_merge_contexts_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ language 'plpgsql';
    """)

    # Create trigger
    op.execute("""
        CREATE TRIGGER trigger_merge_contexts_updated_at
        BEFORE UPDATE ON merge_contexts
        FOR EACH ROW
        EXECUTE FUNCTION update_merge_contexts_updated_at();
    """)


def downgrade() -> None:
    """Drop merge_contexts table and related infrastructure."""

    # Drop trigger
    op.execute("DROP TRIGGER IF EXISTS trigger_merge_contexts_updated_at ON merge_contexts")
    op.execute("DROP FUNCTION IF EXISTS update_merge_contexts_updated_at")

    # Drop indexes
    op.drop_index('ix_merge_contexts_cre_version', table_name='merge_contexts')
    op.drop_index('ix_merge_contexts_user_active', table_name='merge_contexts')
    op.drop_index('ix_merge_contexts_repository', table_name='merge_contexts')
    op.drop_index('ix_merge_contexts_created_at', table_name='merge_contexts')
    op.drop_index('ix_merge_contexts_status', table_name='merge_contexts')
    op.drop_index('ix_merge_contexts_user_id', table_name='merge_contexts')
    op.drop_index('ix_merge_contexts_installation_id', table_name='merge_contexts')

    # Drop table
    op.drop_table('merge_contexts')

    # Drop enum types
    op.execute("DROP TYPE IF EXISTS merge_context_status")
    op.execute("DROP TYPE IF EXISTS source_type")
