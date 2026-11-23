"""Add database-backed file storage

Revision ID: 95844e03bb2b
Revises: a9e905ef5dde
Create Date: 2026-11-23 14:55:28.417500
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# Revision identifiers, used by Alembic.
revision = "95844e03bb2b"
down_revision = "a9e905ef5dde"
branch_labels = None
depends_on = None


def upgrade():
    # Create the table for storing private uploaded files.
    op.create_table(
        "stored_files",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("file_data", mysql.MEDIUMBLOB(), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # Add database file reference to candidate documents.
    with op.batch_alter_table("candidate_documents", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("stored_file_id", sa.Integer(), nullable=True)
        )

        batch_op.alter_column(
            "file_path",
            existing_type=mysql.VARCHAR(length=500),
            nullable=True,
        )

        batch_op.create_foreign_key(
            "fk_candidate_documents_stored_file_id",
            "stored_files",
            ["stored_file_id"],
            ["id"],
        )

    # Add database file reference to candidate resumes.
    with op.batch_alter_table("candidates", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("resume_file_id", sa.Integer(), nullable=True)
        )

        batch_op.create_foreign_key(
            "fk_candidates_resume_file_id",
            "stored_files",
            ["resume_file_id"],
            ["id"],
        )


def downgrade():
    # Remove the candidate resume reference.
    with op.batch_alter_table("candidates", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fk_candidates_resume_file_id",
            type_="foreignkey",
        )

        batch_op.drop_column("resume_file_id")

    # Remove the candidate document reference.
    with op.batch_alter_table("candidate_documents", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fk_candidate_documents_stored_file_id",
            type_="foreignkey",
        )

        batch_op.alter_column(
            "file_path",
            existing_type=mysql.VARCHAR(length=500),
            nullable=False,
        )

        batch_op.drop_column("stored_file_id")

    # Drop the stored files table.
    op.drop_table("stored_files")