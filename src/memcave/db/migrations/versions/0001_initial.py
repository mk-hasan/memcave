"""Initial schema."""

from collections.abc import Sequence

from alembic import op

from memcave.db.tables import Base

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind)
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql(
            "CREATE VIRTUAL TABLE IF NOT EXISTS text_search USING fts5("
            "owner_kind UNINDEXED, owner_id UNINDEXED, tenant_id UNINDEXED, body)"
        )
    elif bind.dialect.name == "postgresql":
        bind.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
        bind.exec_driver_sql(
            "ALTER TABLE memory_embeddings ADD COLUMN IF NOT EXISTS embedding vector(64)"
        )


def downgrade() -> None:
    Base.metadata.drop_all(op.get_bind())
