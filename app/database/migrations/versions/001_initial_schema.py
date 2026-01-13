"""Initial database schema - Workstream 1 v3.0

Revision ID: 001
Revises:
Create Date: 2025-11-17

Creates all initial tables for the MergeWeave GitHub App per Workstream 1 v3.0 specification:
- users: User accounts
- github_installations: GitHub App installations (with BigInteger for installation_id)
- github_repositories: Repositories under installations (with BigInteger for github_repo_id)
- github_branches: Branches within repositories
- conflict_detections: Detected merge conflicts (with enhanced schema)
- resolution_results: CRE resolution results (with PENDING/SUCCESS/FAILED/APPLIED/EXPIRED states)
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create all initial tables per Workstream 1 v3.0 spec."""

    # ========================================================================
    # Users table - SKIPPED
    # The 'users' table already exists from the Public API (with UUID primary key).
    # The GitHub App will reference the existing users table.
    # ========================================================================
    # NOTE: users table creation skipped - table already exists in shared database

    # ========================================================================
    # GitHub Installations table
    # BigInteger used for installation_id per spec to prevent overflow
    # UUID used for user_id to match existing Public API users table
    # ========================================================================
    op.create_table(
        'github_installations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('installation_id', sa.BigInteger(), nullable=False),  # BigInteger per spec
        sa.Column('account_login', sa.String(length=255), nullable=False),
        sa.Column('account_type', sa.String(length=50), nullable=False),  # "Organization" or "User"
        sa.Column('account_avatar_url', sa.String(length=512), nullable=True),
        sa.Column('permissions', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('installed_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_github_installations_user_id', 'github_installations', ['user_id'])
    op.create_index('ix_github_installations_installation_id', 'github_installations', ['installation_id'], unique=True)

    # ========================================================================
    # GitHub Repositories table
    # BigInteger used for github_repo_id per spec to prevent overflow
    # ========================================================================
    op.create_table(
        'github_repositories',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('installation_id', sa.Integer(), nullable=False),
        sa.Column('github_repo_id', sa.BigInteger(), nullable=False),  # BigInteger per spec
        sa.Column('owner', sa.String(length=255), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('full_name', sa.String(length=512), nullable=False),  # "owner/repo"
        sa.Column('default_branch', sa.String(length=255), nullable=True),
        sa.Column('is_private', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('cached_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['installation_id'], ['github_installations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_github_repositories_installation_id', 'github_repositories', ['installation_id'])
    op.create_index('ix_github_repositories_github_repo_id', 'github_repositories', ['github_repo_id'], unique=True)
    op.create_index('ix_github_repositories_full_name', 'github_repositories', ['full_name'])

    # ========================================================================
    # GitHub Branches table
    # Field named 'name' per spec (not 'branch_name')
    # ========================================================================
    op.create_table(
        'github_branches',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('repository_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),  # 'name' per spec
        sa.Column('commit_sha', sa.String(length=40), nullable=False),  # 'commit_sha' per spec
        sa.Column('is_protected', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('cached_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['repository_id'], ['github_repositories.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_github_branches_repository_id', 'github_branches', ['repository_id'])
    op.create_index('ix_github_branches_name', 'github_branches', ['name'])

    # ========================================================================
    # Conflict Detections table
    # Enhanced schema per Workstream 1 v3.0 spec with all required fields
    # ========================================================================
    op.create_table(
        'conflict_detections',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('installation_id', sa.Integer(), nullable=False),
        sa.Column('repository_full_name', sa.String(length=512), nullable=False),
        sa.Column('source_ref', sa.String(length=255), nullable=False),
        sa.Column('target_ref', sa.String(length=255), nullable=False),
        sa.Column('base_sha', sa.String(length=40), nullable=True),  # Base commit SHA for merge simulation
        sa.Column('commit_sha', sa.String(length=40), nullable=False),
        # Detection results
        sa.Column('has_conflicts', sa.Boolean(), nullable=False),
        sa.Column('conflicted_files', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        # Resolution
        sa.Column('resolution_generated', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('resolution_confidence', sa.Numeric(precision=3, scale=2), nullable=True),
        sa.Column('resolution_strategy', sa.String(length=100), nullable=True),
        # User action
        sa.Column('user_action', sa.String(length=50), nullable=True),
        sa.Column('user_action_at', sa.DateTime(timezone=True), nullable=True),
        # Metadata
        sa.Column('detected_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('webhook_delivery_id', sa.String(length=255), nullable=True),
        sa.Column('event_type', sa.String(length=50), nullable=True),  # "push", "pull_request", etc.
        sa.Column('processing_duration_ms', sa.Integer(), nullable=True),  # Time to detect conflicts
        sa.ForeignKeyConstraint(['installation_id'], ['github_installations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_conflict_detections_installation_id', 'conflict_detections', ['installation_id'])
    op.create_index('ix_conflict_detections_repository_full_name', 'conflict_detections', ['repository_full_name'])
    op.create_index('ix_conflict_detections_detected_at', 'conflict_detections', ['detected_at'])

    # ========================================================================
    # Resolution Results table
    # Per Workstream 1 v3.0 spec with correct status enum (PENDING/SUCCESS/FAILED/APPLIED/EXPIRED)
    # ========================================================================
    op.create_table(
        'resolution_results',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('conflict_detection_id', sa.Integer(), nullable=False),
        # Resolution metadata
        sa.Column('resolution_id', sa.String(length=255), nullable=False),  # UUID from CRE
        sa.Column('status', sa.String(length=50), nullable=False, server_default='pending'),
        # Package details
        sa.Column('package_url', sa.String(length=1024), nullable=True),  # S3 presigned URL
        sa.Column('package_expires_at', sa.DateTime(timezone=True), nullable=True),
        # Resolution quality
        sa.Column('confidence_score', sa.Numeric(precision=3, scale=2), nullable=True),  # 0.00-1.00
        sa.Column('strategy_used', sa.String(length=100), nullable=True),
        # Error handling
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('error_code', sa.String(length=100), nullable=True),
        # Timing
        sa.Column('requested_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('applied_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['conflict_detection_id'], ['conflict_detections.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_resolution_results_conflict_detection_id', 'resolution_results', ['conflict_detection_id'])
    op.create_index('ix_resolution_results_resolution_id', 'resolution_results', ['resolution_id'], unique=True)
    op.create_index('ix_resolution_results_requested_at', 'resolution_results', ['requested_at'])


def downgrade() -> None:
    """Drop all tables created by this migration."""
    op.drop_table('resolution_results')
    op.drop_table('conflict_detections')
    op.drop_table('github_branches')
    op.drop_table('github_repositories')
    op.drop_table('github_installations')
    # NOTE: users table not dropped - it's owned by Public API
