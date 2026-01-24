"""Pydantic schemas for Client management."""

from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator
import re

if TYPE_CHECKING:
    from airweave.schemas.source_connection import SourceConnectionListItem


class ClientBase(BaseModel):
    """Base schema for Client."""

    name: str = Field(..., min_length=1, max_length=255, description="Client name")
    description: Optional[str] = Field(None, max_length=1000, description="Client description")
    domain: Optional[str] = Field(
        None,
        max_length=255,
        description="Email domain for automatic matching (e.g., 'acme.com')",
    )
    client_metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")


class ClientCreate(ClientBase):
    """Schema for creating a Client."""

    readable_id: Optional[str] = Field(
        None,
        min_length=2,
        max_length=255,
        description="Unique slug identifier (auto-generated from name if not provided)",
    )

    @field_validator("readable_id")
    @classmethod
    def validate_readable_id(cls, v: Optional[str]) -> Optional[str]:
        """Validate readable_id format."""
        if v is None:
            return v
        # Allow only lowercase alphanumeric and hyphens
        if not re.match(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$", v):
            raise ValueError(
                "readable_id must contain only lowercase letters, numbers, and hyphens, "
                "and cannot start or end with a hyphen"
            )
        return v

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, v: Optional[str]) -> Optional[str]:
        """Validate domain format."""
        if v is None:
            return v
        # Basic domain validation
        v = v.lower().strip()
        if not re.match(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", v):
            raise ValueError("Invalid domain format")
        return v


class ClientUpdate(BaseModel):
    """Schema for updating a Client."""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=1000)
    domain: Optional[str] = Field(None, max_length=255)
    client_metadata: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, v: Optional[str]) -> Optional[str]:
        """Validate domain format."""
        if v is None:
            return v
        v = v.lower().strip()
        if not re.match(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", v):
            raise ValueError("Invalid domain format")
        return v


class Client(ClientBase):
    """Schema for Client response."""

    id: UUID
    readable_id: str
    is_active: bool
    created_at: datetime
    modified_at: datetime
    created_by_email: Optional[str] = None
    modified_by_email: Optional[str] = None

    model_config = {"from_attributes": True}


class ClientWithStats(Client):
    """Client response with statistics."""

    source_connection_count: int = Field(0, description="Number of source connections")
    identity_cluster_count: int = Field(0, description="Number of identity clusters")


class ClientWithSources(Client):
    """Client response with source connections."""

    source_connections: List["SourceConnectionListItem"] = Field(
        default_factory=list, description="Source connections assigned to this client"
    )


# Rebuild models for forward references
ClientWithSources.model_rebuild()
