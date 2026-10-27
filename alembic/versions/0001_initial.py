"""Initial schema with explicit, reproducible table definitions."""

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "audio_files",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("storage_key", sa.String(100), unique=True, nullable=False),
        sa.Column("size", sa.Integer, nullable=False),
        sa.Column("metadata_json", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audio_files_created_at", "audio_files", ["created_at"])
    for name in ("audio_analyses", "audio_fingerprints"):
        op.create_table(
            name,
            sa.Column(
                "audio_id",
                sa.String(36),
                sa.ForeignKey("audio_files.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("data", sa.JSON, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    op.create_table(
        "processing_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "audio_id", sa.String(36), sa.ForeignKey("audio_files.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("params", sa.JSON, nullable=False),
        sa.Column("cache_key", sa.String(64)),
        sa.Column("progress", sa.Float, nullable=False),
        sa.Column("phase", sa.String(80), nullable=False),
        sa.Column("error", sa.String(500)),
        sa.Column("cancel_requested", sa.Boolean, nullable=False),
        sa.Column("version_id", sa.String(36)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("state IN ('pending','queued','processing','completed','failed','cancelled')"),
    )
    for field in ("audio_id", "state", "cache_key", "created_at", "updated_at"):
        op.create_index(f"ix_processing_jobs_{field}", "processing_jobs", [field])
    op.create_table(
        "processed_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "audio_id", sa.String(36), sa.ForeignKey("audio_files.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("processing_jobs.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("storage_key", sa.String(100), nullable=False, unique=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("params", sa.JSON, nullable=False),
        sa.Column("analysis", sa.JSON, nullable=False),
        sa.Column("fingerprint", sa.JSON, nullable=False),
        sa.Column("comparison", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_processed_versions_audio_id", "processed_versions", ["audio_id"])
    op.create_table(
        "processing_presets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("params", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    for table in (
        "processing_presets",
        "processed_versions",
        "processing_jobs",
        "audio_fingerprints",
        "audio_analyses",
        "audio_files",
    ):
        op.drop_table(table)
