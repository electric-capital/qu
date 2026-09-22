"""create guides table

Revision ID: 4e8960c4dacc
Revises: a1b2c3d4e5f6
Create Date: 2026-02-14 18:35:18.816191

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4e8960c4dacc'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create guides table and migrate existing custom_system_prompt data."""
    # 1. Create the guides table
    op.create_table(
        'guides',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('is_default', sa.Boolean(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_guides_user_id'), 'guides', ['user_id'], unique=False)

    # 2. Composite unique index on (user_id, name) to prevent duplicate names per user
    op.create_index('ix_guides_user_id_name', 'guides', ['user_id', 'name'], unique=True)

    # 3. Data migration: create a default guide for each existing user,
    #    copying their custom_system_prompt content if present.
    conn = op.get_bind()

    users = conn.execute(sa.text("SELECT id, settings FROM users")).fetchall()
    for user_row in users:
        user_id = user_row[0]
        settings_raw = user_row[1]

        # Parse the settings JSON to extract custom_system_prompt
        content = ""
        if settings_raw:
            import json
            try:
                settings = json.loads(settings_raw) if isinstance(settings_raw, str) else settings_raw
                content = settings.get("custom_system_prompt", "") or ""
            except (json.JSONDecodeError, TypeError, AttributeError):
                pass

        guide_id = str(uuid.uuid4())
        conn.execute(
            sa.text(
                "INSERT INTO guides (id, user_id, name, content, is_default, created_at) "
                "VALUES (:id, :user_id, :name, :content, 1, datetime('now'))"
            ),
            {"id": guide_id, "user_id": user_id, "name": "Default", "content": content},
        )


def downgrade() -> None:
    """Drop guides table."""
    op.drop_index('ix_guides_user_id_name', table_name='guides')
    op.drop_index(op.f('ix_guides_user_id'), table_name='guides')
    op.drop_table('guides')
