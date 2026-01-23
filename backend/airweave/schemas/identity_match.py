"""Pydantic schemas for identity matching."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


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


# ===========================
# Identity Match Schemas
# ===========================


class IdentityMatchBase(BaseModel):
    """Base schema for IdentityMatch."""

    entity_id: str = Field(..., description="Entity ID from the source")
    sync_id: UUID = Field(..., description="Sync ID the entity belongs to")
    source_connection_id: UUID = Field(..., description="Source connection ID")
    match_method: MatchMethod = Field(..., description="Method used to match")
    confidence_score: float = Field(
        1.0, ge=0.0, le=1.0, description="Confidence score (0-1)"
    )
    matched_email: Optional[str] = Field(None, description="Email used for matching")
    matched_name: Optional[str] = Field(None, description="Name used for matching")


class IdentityMatchCreate(IdentityMatchBase):
    """Schema for creating an IdentityMatch."""

    cluster_id: UUID = Field(..., description="Cluster to add the match to")
    status: MatchStatus = Field(
        MatchStatus.PENDING, description="Initial status of the match"
    )


class IdentityMatch(IdentityMatchBase):
    """Schema for IdentityMatch response."""

    id: UUID
    cluster_id: UUID
    status: MatchStatus
    reviewed_by_email: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    created_at: datetime
    modified_at: datetime

    model_config = {"from_attributes": True}


class IdentityMatchWithSource(IdentityMatch):
    """IdentityMatch with source connection details."""

    source_name: Optional[str] = Field(None, description="Name of the source connection")
    source_short_name: Optional[str] = Field(None, description="Short name of the source")


# ===========================
# Identity Cluster Schemas
# ===========================


class IdentityClusterBase(BaseModel):
    """Base schema for IdentityCluster."""

    name: Optional[str] = Field(None, max_length=255, description="Display name")
    primary_email: Optional[str] = Field(None, max_length=255, description="Primary email")
    client_id: Optional[UUID] = Field(None, description="Associated client ID")
    merged_attributes: Optional[Dict[str, Any]] = Field(
        None, description="Merged attributes from all matches"
    )


class IdentityClusterCreate(IdentityClusterBase):
    """Schema for creating an IdentityCluster."""

    pass


class IdentityClusterUpdate(BaseModel):
    """Schema for updating an IdentityCluster."""

    name: Optional[str] = Field(None, max_length=255)
    primary_email: Optional[str] = Field(None, max_length=255)
    client_id: Optional[UUID] = None
    merged_attributes: Optional[Dict[str, Any]] = None


class IdentityCluster(IdentityClusterBase):
    """Schema for IdentityCluster response."""

    id: UUID
    created_at: datetime
    modified_at: datetime

    model_config = {"from_attributes": True}


class IdentityClusterWithMatches(IdentityCluster):
    """IdentityCluster with its matches."""

    matches: List[IdentityMatchWithSource] = Field(
        default_factory=list, description="All matches in this cluster"
    )
    match_count: int = Field(0, description="Number of matches in the cluster")


class IdentityClusterWithClient(IdentityCluster):
    """IdentityCluster with client information."""

    client_name: Optional[str] = Field(None, description="Name of the associated client")
    client_readable_id: Optional[str] = Field(
        None, description="Readable ID of the associated client"
    )


# ===========================
# Batch Operations
# ===========================


class PendingMatchReview(BaseModel):
    """Schema for reviewing pending matches."""

    match_ids: List[UUID] = Field(..., min_length=1, description="Match IDs to review")
    action: MatchStatus = Field(
        ..., description="Action to take (approved or rejected)"
    )


class MatchSuggestion(BaseModel):
    """Schema for AI-suggested matches."""

    entity_id: str
    sync_id: UUID
    source_connection_id: UUID
    suggested_cluster_id: Optional[UUID] = Field(
        None, description="Existing cluster to match to (None for new cluster)"
    )
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    matched_email: Optional[str] = None
    matched_name: Optional[str] = None
    reason: str = Field(..., description="Reason for the suggestion")


class ClusterMergeRequest(BaseModel):
    """Schema for merging clusters."""

    source_cluster_ids: List[UUID] = Field(
        ..., min_length=2, description="Cluster IDs to merge"
    )
    target_cluster_id: Optional[UUID] = Field(
        None,
        description="Target cluster ID (if None, uses the first source cluster)",
    )
    name: Optional[str] = Field(None, description="Name for the merged cluster")
    primary_email: Optional[str] = Field(
        None, description="Primary email for the merged cluster"
    )


# ===========================
# Search and Filter
# ===========================


class IdentitySearchRequest(BaseModel):
    """Schema for searching identities."""

    query: str = Field(..., min_length=1, description="Search query (email or name)")
    client_id: Optional[UUID] = Field(None, description="Filter by client")
    source_connection_id: Optional[UUID] = Field(
        None, description="Filter by source connection"
    )
    status: Optional[MatchStatus] = Field(None, description="Filter by match status")
    limit: int = Field(50, ge=1, le=500, description="Maximum results to return")


class IdentitySearchResult(BaseModel):
    """Schema for identity search result."""

    cluster: IdentityCluster
    matches: List[IdentityMatchWithSource]
    relevance_score: float = Field(..., description="Search relevance score")
