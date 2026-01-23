"""CRUD operations for identity matching."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from airweave.api.context import ApiContext
from airweave.core.datetime_utils import utc_now_naive
from airweave.core.exceptions import NotFoundException, ValidationException
from airweave.models.identity_match import (
    IdentityCluster,
    IdentityMatch,
    MatchMethod,
    MatchStatus,
)
from airweave.models.source_connection import SourceConnection
from airweave.schemas.identity_match import (
    IdentityClusterCreate,
    IdentityClusterUpdate,
    IdentityMatchCreate,
)

from ._base_organization import CRUDBaseOrganization


class CRUDIdentityCluster(
    CRUDBaseOrganization[IdentityCluster, IdentityClusterCreate, IdentityClusterUpdate]
):
    """CRUD operations for IdentityCluster model."""

    def __init__(self):
        super().__init__(IdentityCluster, track_user=False)

    async def get_by_email(
        self, db: AsyncSession, *, email: str, ctx: ApiContext
    ) -> Optional[IdentityCluster]:
        """Get a cluster by primary email."""
        query = select(IdentityCluster).where(
            and_(
                IdentityCluster.primary_email == email.lower(),
                IdentityCluster.organization_id == ctx.organization.id,
            )
        )
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def get_with_matches(
        self, db: AsyncSession, *, id: UUID, ctx: ApiContext
    ) -> IdentityCluster:
        """Get a cluster with all its matches."""
        query = (
            select(IdentityCluster)
            .options(selectinload(IdentityCluster.matches))
            .where(
                and_(
                    IdentityCluster.id == id,
                    IdentityCluster.organization_id == ctx.organization.id,
                )
            )
        )
        result = await db.execute(query)
        cluster = result.scalar_one_or_none()
        if not cluster:
            raise NotFoundException("Identity cluster not found")
        return cluster

    async def get_multi_with_stats(
        self,
        db: AsyncSession,
        *,
        ctx: ApiContext,
        client_id: Optional[UUID] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Get clusters with match counts."""
        # Base query with match count
        query = (
            select(
                IdentityCluster,
                func.count(IdentityMatch.id).label("match_count"),
            )
            .outerjoin(IdentityMatch, IdentityCluster.id == IdentityMatch.cluster_id)
            .where(IdentityCluster.organization_id == ctx.organization.id)
            .group_by(IdentityCluster.id)
        )

        if client_id:
            query = query.where(IdentityCluster.client_id == client_id)

        query = query.offset(skip).limit(limit).order_by(IdentityCluster.created_at.desc())
        result = await db.execute(query)

        results = []
        for row in result:
            cluster = row[0]
            match_count = row[1]
            results.append(
                {
                    "id": cluster.id,
                    "name": cluster.name,
                    "primary_email": cluster.primary_email,
                    "client_id": cluster.client_id,
                    "merged_attributes": cluster.merged_attributes,
                    "created_at": cluster.created_at,
                    "modified_at": cluster.modified_at,
                    "match_count": match_count,
                }
            )

        return results

    async def search_by_email_or_name(
        self,
        db: AsyncSession,
        *,
        query_str: str,
        ctx: ApiContext,
        client_id: Optional[UUID] = None,
        limit: int = 50,
    ) -> List[IdentityCluster]:
        """Search clusters by email or name."""
        search_pattern = f"%{query_str.lower()}%"

        query = select(IdentityCluster).where(
            and_(
                IdentityCluster.organization_id == ctx.organization.id,
                or_(
                    func.lower(IdentityCluster.primary_email).like(search_pattern),
                    func.lower(IdentityCluster.name).like(search_pattern),
                ),
            )
        )

        if client_id:
            query = query.where(IdentityCluster.client_id == client_id)

        query = query.limit(limit)
        result = await db.execute(query)
        return list(result.scalars().all())

    async def merge_clusters(
        self,
        db: AsyncSession,
        *,
        source_cluster_ids: List[UUID],
        target_cluster_id: Optional[UUID],
        name: Optional[str],
        primary_email: Optional[str],
        ctx: ApiContext,
    ) -> IdentityCluster:
        """Merge multiple clusters into one.

        All matches from source clusters are moved to the target cluster.
        Source clusters are deleted after merging.
        """
        if len(source_cluster_ids) < 2:
            raise ValidationException("At least 2 clusters are required for merging")

        # Determine target cluster
        if target_cluster_id:
            if target_cluster_id not in source_cluster_ids:
                raise ValidationException("Target cluster must be one of the source clusters")
            target_id = target_cluster_id
        else:
            target_id = source_cluster_ids[0]

        # Get all clusters
        query = select(IdentityCluster).where(
            and_(
                IdentityCluster.id.in_(source_cluster_ids),
                IdentityCluster.organization_id == ctx.organization.id,
            )
        )
        result = await db.execute(query)
        clusters = list(result.scalars().all())

        if len(clusters) != len(source_cluster_ids):
            raise NotFoundException("One or more clusters not found")

        target_cluster = next(c for c in clusters if c.id == target_id)
        source_clusters = [c for c in clusters if c.id != target_id]

        # Update target cluster properties
        if name:
            target_cluster.name = name
        if primary_email:
            target_cluster.primary_email = primary_email

        # Merge attributes from all clusters
        merged_attrs = target_cluster.merged_attributes or {}
        for cluster in source_clusters:
            if cluster.merged_attributes:
                for key, value in cluster.merged_attributes.items():
                    if key not in merged_attrs:
                        merged_attrs[key] = value
        target_cluster.merged_attributes = merged_attrs

        # Move all matches to target cluster
        source_ids = [c.id for c in source_clusters]
        await db.execute(
            update(IdentityMatch)
            .where(IdentityMatch.cluster_id.in_(source_ids))
            .values(cluster_id=target_id)
        )

        # Delete source clusters
        for cluster in source_clusters:
            await db.delete(cluster)

        await db.commit()
        await db.refresh(target_cluster)
        return target_cluster

    async def get_clusters_for_client(
        self,
        db: AsyncSession,
        *,
        client_id: UUID,
        ctx: ApiContext,
        skip: int = 0,
        limit: int = 100,
    ) -> List[IdentityCluster]:
        """Get all clusters for a specific client."""
        query = (
            select(IdentityCluster)
            .where(
                and_(
                    IdentityCluster.client_id == client_id,
                    IdentityCluster.organization_id == ctx.organization.id,
                )
            )
            .offset(skip)
            .limit(limit)
            .order_by(IdentityCluster.created_at.desc())
        )
        result = await db.execute(query)
        return list(result.scalars().all())


class CRUDIdentityMatch(
    CRUDBaseOrganization[IdentityMatch, IdentityMatchCreate, Dict[str, Any]]
):
    """CRUD operations for IdentityMatch model."""

    def __init__(self):
        super().__init__(IdentityMatch, track_user=False)

    async def get_by_entity_and_sync(
        self,
        db: AsyncSession,
        *,
        entity_id: str,
        sync_id: UUID,
        ctx: ApiContext,
    ) -> Optional[IdentityMatch]:
        """Get a match by entity_id and sync_id."""
        query = select(IdentityMatch).where(
            and_(
                IdentityMatch.entity_id == entity_id,
                IdentityMatch.sync_id == sync_id,
                IdentityMatch.organization_id == ctx.organization.id,
            )
        )
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def get_pending_matches(
        self,
        db: AsyncSession,
        *,
        ctx: ApiContext,
        source_connection_id: Optional[UUID] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> List[IdentityMatch]:
        """Get all pending matches for review."""
        query = select(IdentityMatch).where(
            and_(
                IdentityMatch.status == MatchStatus.PENDING.value,
                IdentityMatch.organization_id == ctx.organization.id,
            )
        )

        if source_connection_id:
            query = query.where(IdentityMatch.source_connection_id == source_connection_id)

        query = query.offset(skip).limit(limit).order_by(IdentityMatch.created_at.desc())
        result = await db.execute(query)
        return list(result.scalars().all())

    async def bulk_update_status(
        self,
        db: AsyncSession,
        *,
        match_ids: List[UUID],
        status: MatchStatus,
        reviewed_by_email: Optional[str],
        ctx: ApiContext,
    ) -> int:
        """Bulk update match status (approve/reject)."""
        # Verify all matches belong to the organization
        query = select(IdentityMatch).where(
            and_(
                IdentityMatch.id.in_(match_ids),
                IdentityMatch.organization_id == ctx.organization.id,
            )
        )
        result = await db.execute(query)
        matches = list(result.scalars().all())

        if len(matches) != len(match_ids):
            raise NotFoundException("One or more matches not found")

        # Update all matches
        await db.execute(
            update(IdentityMatch)
            .where(IdentityMatch.id.in_(match_ids))
            .values(
                status=status.value,
                reviewed_by_email=reviewed_by_email,
                reviewed_at=utc_now_naive(),
            )
        )

        await db.commit()
        return len(matches)

    async def get_matches_for_cluster(
        self,
        db: AsyncSession,
        *,
        cluster_id: UUID,
        ctx: ApiContext,
    ) -> List[Dict[str, Any]]:
        """Get all matches for a cluster with source connection details."""
        query = (
            select(IdentityMatch, SourceConnection.name, SourceConnection.short_name)
            .outerjoin(
                SourceConnection,
                IdentityMatch.source_connection_id == SourceConnection.id,
            )
            .where(
                and_(
                    IdentityMatch.cluster_id == cluster_id,
                    IdentityMatch.organization_id == ctx.organization.id,
                )
            )
            .order_by(IdentityMatch.created_at.desc())
        )
        result = await db.execute(query)

        matches = []
        for row in result:
            match = row[0]
            source_name = row[1]
            source_short_name = row[2]
            matches.append(
                {
                    "id": match.id,
                    "cluster_id": match.cluster_id,
                    "entity_id": match.entity_id,
                    "sync_id": match.sync_id,
                    "source_connection_id": match.source_connection_id,
                    "match_method": match.match_method,
                    "confidence_score": match.confidence_score,
                    "status": match.status,
                    "matched_email": match.matched_email,
                    "matched_name": match.matched_name,
                    "reviewed_by_email": match.reviewed_by_email,
                    "reviewed_at": match.reviewed_at,
                    "created_at": match.created_at,
                    "modified_at": match.modified_at,
                    "source_name": source_name,
                    "source_short_name": source_short_name,
                }
            )

        return matches

    async def find_by_email(
        self,
        db: AsyncSession,
        *,
        email: str,
        ctx: ApiContext,
    ) -> List[IdentityMatch]:
        """Find matches by email."""
        query = select(IdentityMatch).where(
            and_(
                IdentityMatch.matched_email == email.lower(),
                IdentityMatch.organization_id == ctx.organization.id,
            )
        )
        result = await db.execute(query)
        return list(result.scalars().all())

    async def get_matches_for_source_connection(
        self,
        db: AsyncSession,
        *,
        source_connection_id: UUID,
        ctx: ApiContext,
        status: Optional[MatchStatus] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> List[IdentityMatch]:
        """Get all matches for a source connection."""
        query = select(IdentityMatch).where(
            and_(
                IdentityMatch.source_connection_id == source_connection_id,
                IdentityMatch.organization_id == ctx.organization.id,
            )
        )

        if status:
            query = query.where(IdentityMatch.status == status.value)

        query = query.offset(skip).limit(limit).order_by(IdentityMatch.created_at.desc())
        result = await db.execute(query)
        return list(result.scalars().all())


# Singleton instances
identity_cluster = CRUDIdentityCluster()
identity_match = CRUDIdentityMatch()
