"""Identity matching models for cross-source entity linking."""

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, List, Optional
from uuid import UUID

from sqlalchemy import DateTime, Float, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from airweave.models._base import OrganizationBase

if TYPE_CHECKING:
    from airweave.models.client import Client
    from airweave.models.source_connection import SourceConnection


class MatchMethod(str, Enum):
    """Methods used to match entities across sources."""

    EMAIL_EXACT = "email_exact"
    EMAIL_DOMAIN = "email_domain"
    NAME_FUZZY = "name_fuzzy"
    MANUAL = "manual"
    AI_SUGGESTED = "ai_suggested"


class MatchStatus(str, Enum):
    """Status of an identity match."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class IdentityCluster(OrganizationBase):
    """A cluster of matched identities across different sources.

    An IdentityCluster groups together entities from different sources that
    represent the same person/identity. For example, a Slack user, Gmail contact,
    and GitHub user that all belong to the same person.
    """

    __tablename__ = "identity_cluster"

    # Display name for the cluster (usually the person's name)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Primary email for the identity
    primary_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)

    # Optional client association
    client_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("client.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Merged/consolidated attributes from all matched entities
    merged_attributes: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Relationships
    client: Mapped[Optional["Client"]] = relationship(
        "Client",
        back_populates="identity_clusters",
        lazy="noload",
    )

    matches: Mapped[List["IdentityMatch"]] = relationship(
        "IdentityMatch",
        back_populates="cluster",
        cascade="all, delete-orphan",
        lazy="noload",
    )


class IdentityMatch(OrganizationBase):
    """An individual entity matched to an identity cluster.

    Each IdentityMatch represents a single entity from a source connection
    that has been linked to an IdentityCluster.
    """

    __tablename__ = "identity_match"

    # Reference to the cluster this match belongs to
    cluster_id: Mapped[UUID] = mapped_column(
        ForeignKey("identity_cluster.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Entity reference (from the Entity table)
    entity_id: Mapped[str] = mapped_column(String(1024), nullable=False, index=True)
    sync_id: Mapped[UUID] = mapped_column(
        ForeignKey("sync.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Source connection reference
    source_connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_connection.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Match details
    match_method: Mapped[str] = mapped_column(String(50), nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=MatchStatus.PENDING.value)

    # Data used for matching (for audit trail)
    matched_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    matched_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Review information
    reviewed_by_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    cluster: Mapped["IdentityCluster"] = relationship(
        "IdentityCluster",
        back_populates="matches",
        lazy="noload",
    )

    source_connection: Mapped["SourceConnection"] = relationship(
        "SourceConnection",
        lazy="noload",
    )
