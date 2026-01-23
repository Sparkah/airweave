"""Add client and identity matching tables.

Revision ID: 6baf8bb66eda
Revises: m6n7o8p9q0r1
Create Date: 2026-01-22 10:00:00.000000

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "6baf8bb66eda"
down_revision = "m6n7o8p9q0r1"
branch_labels = None
depends_on = None


def upgrade():
    # Create client table
    op.create_table(
        "client",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("readable_id", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("domain", sa.String(255), nullable=True),
        sa.Column("client_metadata", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("modified_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_email", sa.String(), nullable=True),
        sa.Column("modified_by_email", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organization.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("readable_id"),
    )
    op.create_index("ix_client_organization_id", "client", ["organization_id"])
    op.create_index("ix_client_readable_id", "client", ["readable_id"])
    op.create_index("ix_client_domain", "client", ["domain"])

    # Create identity_cluster table
    op.create_table(
        "identity_cluster",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("primary_email", sa.String(255), nullable=True),
        sa.Column("client_id", sa.UUID(), nullable=True),
        sa.Column("merged_attributes", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("modified_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organization.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["client_id"], ["client.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_identity_cluster_organization_id", "identity_cluster", ["organization_id"]
    )
    op.create_index(
        "ix_identity_cluster_client_id", "identity_cluster", ["client_id"]
    )
    op.create_index(
        "ix_identity_cluster_primary_email", "identity_cluster", ["primary_email"]
    )

    # Create identity_match table
    op.create_table(
        "identity_match",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("cluster_id", sa.UUID(), nullable=False),
        sa.Column("entity_id", sa.String(1024), nullable=False),
        sa.Column("sync_id", sa.UUID(), nullable=False),
        sa.Column("source_connection_id", sa.UUID(), nullable=False),
        sa.Column("match_method", sa.String(50), nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="'pending'"),
        sa.Column("matched_email", sa.String(255), nullable=True),
        sa.Column("matched_name", sa.String(255), nullable=True),
        sa.Column("reviewed_by_email", sa.String(255), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("modified_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organization.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["cluster_id"], ["identity_cluster.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["sync_id"], ["sync.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_connection_id"], ["source_connection.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_identity_match_organization_id", "identity_match", ["organization_id"]
    )
    op.create_index("ix_identity_match_cluster_id", "identity_match", ["cluster_id"])
    op.create_index("ix_identity_match_entity_id", "identity_match", ["entity_id"])
    op.create_index("ix_identity_match_sync_id", "identity_match", ["sync_id"])
    op.create_index(
        "ix_identity_match_source_connection_id",
        "identity_match",
        ["source_connection_id"],
    )
    op.create_index(
        "ix_identity_match_status", "identity_match", ["status"]
    )

    # Add client_id column to source_connection table
    op.add_column(
        "source_connection", sa.Column("client_id", sa.UUID(), nullable=True)
    )
    op.create_foreign_key(
        "fk_source_connection_client_id",
        "source_connection",
        "client",
        ["client_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_source_connection_client_id", "source_connection", ["client_id"]
    )


def downgrade():
    # Remove client_id from source_connection
    op.drop_index("ix_source_connection_client_id", table_name="source_connection")
    op.drop_constraint(
        "fk_source_connection_client_id", "source_connection", type_="foreignkey"
    )
    op.drop_column("source_connection", "client_id")

    # Drop identity_match table
    op.drop_index("ix_identity_match_status", table_name="identity_match")
    op.drop_index(
        "ix_identity_match_source_connection_id", table_name="identity_match"
    )
    op.drop_index("ix_identity_match_sync_id", table_name="identity_match")
    op.drop_index("ix_identity_match_entity_id", table_name="identity_match")
    op.drop_index("ix_identity_match_cluster_id", table_name="identity_match")
    op.drop_index("ix_identity_match_organization_id", table_name="identity_match")
    op.drop_table("identity_match")

    # Drop identity_cluster table
    op.drop_index("ix_identity_cluster_primary_email", table_name="identity_cluster")
    op.drop_index("ix_identity_cluster_client_id", table_name="identity_cluster")
    op.drop_index("ix_identity_cluster_organization_id", table_name="identity_cluster")
    op.drop_table("identity_cluster")

    # Drop client table
    op.drop_index("ix_client_domain", table_name="client")
    op.drop_index("ix_client_readable_id", table_name="client")
    op.drop_index("ix_client_organization_id", table_name="client")
    op.drop_table("client")
