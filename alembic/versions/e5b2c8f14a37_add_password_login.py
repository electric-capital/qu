"""add email/password sign-in: users.password_hash + password_tokens

Revision ID: e5b2c8f14a37
Revises: b8e4d2a7c1f5
Create Date: 2026-09-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5b2c8f14a37'
down_revision: Union[str, Sequence[str], None] = 'b8e4d2a7c1f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('password_hash', sa.String(length=255), nullable=True))
    op.create_table(
        'password_tokens',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('purpose', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('used_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_password_tokens_email', 'password_tokens', ['email'], unique=False)
    op.create_index('ix_password_tokens_token_hash', 'password_tokens', ['token_hash'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_password_tokens_token_hash', table_name='password_tokens')
    op.drop_index('ix_password_tokens_email', table_name='password_tokens')
    op.drop_table('password_tokens')
    op.drop_column('users', 'password_hash')
