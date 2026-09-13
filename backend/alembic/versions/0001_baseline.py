"""baseline schema

Represents the full current SQLModel schema. Implemented as
``SQLModel.metadata.create_all`` so it can never drift from the models; later
revisions use explicit ``op`` calls and autogenerate diffs against the models.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-09-06
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    import app.db.models  # noqa: F401  (register tables)
    from sqlmodel import SQLModel

    SQLModel.metadata.create_all(op.get_bind())


def downgrade() -> None:
    import app.db.models  # noqa: F401

    from sqlmodel import SQLModel

    SQLModel.metadata.drop_all(op.get_bind())
