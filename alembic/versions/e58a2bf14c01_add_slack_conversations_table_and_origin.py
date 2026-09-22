"""add slack_conversations table and origin column on conversations

Revision ID: e58a2bf14c01
Revises: d4a8e2f1c3b5
Create Date: 2026-04-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e58a2bf14c01'
down_revision: Union[str, Sequence[str], None] = 'd4a8e2f1c3b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add origin column on conversations and create slack_conversations table."""
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.add_column(sa.Column("origin", sa.String(length=20), nullable=True))

    op.create_table(
        "slack_conversations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("slack_channel_id", sa.String(length=32), nullable=False),
        sa.Column("slack_thread_ts", sa.String(length=32), nullable=False),
        sa.Column("slack_user_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id"),
    )

    op.create_index(
        "ix_slack_conversations_channel_thread",
        "slack_conversations",
        ["slack_channel_id", "slack_thread_ts"],
        unique=True,
    )
    op.create_index(
        "ix_slack_conversations_user_id",
        "slack_conversations",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop slack_conversations table and origin column from conversations."""
    op.drop_index(
        "ix_slack_conversations_user_id", table_name="slack_conversations"
    )
    op.drop_index(
        "ix_slack_conversations_channel_thread", table_name="slack_conversations"
    )
    op.drop_table("slack_conversations")

    with op.batch_alter_table("conversations") as batch_op:
        batch_op.drop_column("origin")
