"""Client model for multi-tenant client management."""

from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from airweave.models._base import OrganizationBase, UserMixin

if TYPE_CHECKING:
    from airweave.models.identity_match import IdentityCluster
    from airweave.models.source_connection import SourceConnection


class Client(OrganizationBase, UserMixin):
    """Client model for managing agency clients.

    A Client represents a customer/client of an organization (agency).
    Source connections can be mapped to specific clients for data organization
    and identity matching purposes.
    """

    __tablename__ = "client"

    # Basic information
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    readable_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Domain for automatic email-based matching
    domain: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)

    # Flexible metadata storage
    client_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Active status
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Relationships
    source_connections: Mapped[List["SourceConnection"]] = relationship(
        "SourceConnection",
        back_populates="client",
        lazy="noload",
    )

    identity_clusters: Mapped[List["IdentityCluster"]] = relationship(
        "IdentityCluster",
        back_populates="client",
        lazy="noload",
    )
