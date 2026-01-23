"""Identity management API endpoints."""

from typing import List, Optional
from uuid import UUID

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from airweave import crud
from airweave.api import deps
from airweave.api.context import ApiContext
from airweave.api.router import TrailingSlashRouter
from airweave.core.identity_matching_service import identity_matching_service
from airweave.db.session import get_db
from airweave.schemas.identity_match import (
    ClusterMergeRequest,
    IdentityCluster,
    IdentityClusterCreate,
    IdentityClusterUpdate,
    IdentityClusterWithMatches,
    IdentityMatch,
    IdentityMatchCreate,
    IdentityMatchWithSource,
    MatchStatus,
    PendingMatchReview,
)

router = TrailingSlashRouter()


# ===========================
# Identity Clusters
# ===========================


@router.get("/clusters", response_model=List[dict])
async def list_clusters(
    *,
    db: AsyncSession = Depends(get_db),
    ctx: ApiContext = Depends(deps.get_context),
    client_id: Optional[UUID] = Query(None, description="Filter by client ID"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
) -> List[dict]:
    """List identity clusters.

    Returns clusters with match counts. Optionally filter by client ID.
    """
    clusters = await crud.identity_cluster.get_multi_with_stats(
        db,
        ctx=ctx,
        client_id=client_id,
        skip=skip,
        limit=limit,
    )
    return clusters


@router.post("/clusters", response_model=IdentityCluster)
async def create_cluster(
    *,
    db: AsyncSession = Depends(get_db),
    cluster_in: IdentityClusterCreate,
    ctx: ApiContext = Depends(deps.get_context),
) -> IdentityCluster:
    """Create a new identity cluster manually.

    This creates an empty cluster that can be populated with manual matches.
    """
    cluster = await crud.identity_cluster.create(db, obj_in=cluster_in, ctx=ctx)
    return IdentityCluster.model_validate(cluster)


@router.get("/clusters/{cluster_id}", response_model=IdentityClusterWithMatches)
async def get_cluster(
    *,
    db: AsyncSession = Depends(get_db),
    cluster_id: UUID,
    ctx: ApiContext = Depends(deps.get_context),
) -> IdentityClusterWithMatches:
    """Get an identity cluster with all its matches.

    Returns the cluster along with all matched entities and their source
    connection information.
    """
    cluster = await crud.identity_cluster.get_with_matches(
        db, id=cluster_id, ctx=ctx
    )

    # Get matches with source details
    matches = await crud.identity_match.get_matches_for_cluster(
        db, cluster_id=cluster_id, ctx=ctx
    )

    return IdentityClusterWithMatches(
        id=cluster.id,
        name=cluster.name,
        primary_email=cluster.primary_email,
        client_id=cluster.client_id,
        merged_attributes=cluster.merged_attributes,
        created_at=cluster.created_at,
        modified_at=cluster.modified_at,
        matches=[IdentityMatchWithSource.model_validate(m) for m in matches],
        match_count=len(matches),
    )


@router.patch("/clusters/{cluster_id}", response_model=IdentityCluster)
async def update_cluster(
    *,
    db: AsyncSession = Depends(get_db),
    cluster_id: UUID,
    cluster_in: IdentityClusterUpdate,
    ctx: ApiContext = Depends(deps.get_context),
) -> IdentityCluster:
    """Update an identity cluster.

    Updateable fields: name, primary_email, client_id, merged_attributes
    """
    cluster = await crud.identity_cluster.get(db, id=cluster_id, ctx=ctx)
    cluster = await crud.identity_cluster.update(
        db, db_obj=cluster, obj_in=cluster_in, ctx=ctx
    )
    return IdentityCluster.model_validate(cluster)


@router.delete("/clusters/{cluster_id}", response_model=IdentityCluster)
async def delete_cluster(
    *,
    db: AsyncSession = Depends(get_db),
    cluster_id: UUID,
    ctx: ApiContext = Depends(deps.get_context),
) -> IdentityCluster:
    """Delete an identity cluster.

    This will also delete all matches associated with the cluster.
    """
    cluster = await crud.identity_cluster.remove(db, id=cluster_id, ctx=ctx)
    return IdentityCluster.model_validate(cluster)


@router.post("/clusters/merge", response_model=IdentityCluster)
async def merge_clusters(
    *,
    db: AsyncSession = Depends(get_db),
    merge_request: ClusterMergeRequest,
    ctx: ApiContext = Depends(deps.get_context),
) -> IdentityCluster:
    """Merge multiple identity clusters into one.

    All matches from source clusters are moved to the target cluster.
    Source clusters are deleted after merging.

    If target_cluster_id is not provided, the first source cluster is used
    as the target.
    """
    cluster = await crud.identity_cluster.merge_clusters(
        db,
        source_cluster_ids=merge_request.source_cluster_ids,
        target_cluster_id=merge_request.target_cluster_id,
        name=merge_request.name,
        primary_email=merge_request.primary_email,
        ctx=ctx,
    )
    return IdentityCluster.model_validate(cluster)


# ===========================
# Identity Matches
# ===========================


@router.post("/clusters/{cluster_id}/matches", response_model=IdentityMatch)
async def add_manual_match(
    *,
    db: AsyncSession = Depends(get_db),
    cluster_id: UUID,
    match_in: IdentityMatchCreate,
    ctx: ApiContext = Depends(deps.get_context),
) -> IdentityMatch:
    """Add a manual match to a cluster.

    This creates a new identity match linking an entity to the specified cluster.
    Manual matches are automatically approved.
    """
    from airweave.models.identity_match import MatchMethod, MatchStatus as ModelMatchStatus

    # Verify cluster exists
    await crud.identity_cluster.get(db, id=cluster_id, ctx=ctx)

    # Override cluster_id and set manual match properties
    match_data = match_in.model_dump()
    match_data["cluster_id"] = cluster_id
    match_data["match_method"] = MatchMethod.MANUAL.value
    match_data["status"] = ModelMatchStatus.APPROVED.value
    match_data["confidence_score"] = 1.0

    match = await crud.identity_match.create(db, obj_in=match_data, ctx=ctx)
    return IdentityMatch.model_validate(match)


@router.delete("/matches/{match_id}", response_model=IdentityMatch)
async def delete_match(
    *,
    db: AsyncSession = Depends(get_db),
    match_id: UUID,
    ctx: ApiContext = Depends(deps.get_context),
) -> IdentityMatch:
    """Delete an identity match.

    Removes the link between an entity and its cluster. The entity can then
    be re-matched to a different cluster.
    """
    match = await crud.identity_match.remove(db, id=match_id, ctx=ctx)
    return IdentityMatch.model_validate(match)


# ===========================
# Pending Reviews
# ===========================


@router.get("/pending-reviews", response_model=List[IdentityMatch])
async def list_pending_reviews(
    *,
    db: AsyncSession = Depends(get_db),
    ctx: ApiContext = Depends(deps.get_context),
    source_connection_id: Optional[UUID] = Query(
        None, description="Filter by source connection"
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
) -> List[IdentityMatch]:
    """List matches pending review.

    Returns matches with status "pending" that need human approval or rejection.
    These are typically lower-confidence matches from fuzzy name matching.
    """
    matches = await crud.identity_match.get_pending_matches(
        db,
        ctx=ctx,
        source_connection_id=source_connection_id,
        skip=skip,
        limit=limit,
    )
    return [IdentityMatch.model_validate(m) for m in matches]


@router.post("/pending-reviews/batch")
async def batch_review_matches(
    *,
    db: AsyncSession = Depends(get_db),
    review: PendingMatchReview,
    ctx: ApiContext = Depends(deps.get_context),
) -> dict:
    """Batch approve or reject pending matches.

    Allows reviewing multiple matches at once. The action must be either
    "approved" or "rejected".
    """
    if review.action not in (MatchStatus.APPROVED, MatchStatus.REJECTED):
        from airweave.core.exceptions import ValidationException

        raise ValidationException("Action must be 'approved' or 'rejected'")

    reviewer_email = ctx.tracking_email if ctx.has_user_context else None

    count = await crud.identity_match.bulk_update_status(
        db,
        match_ids=review.match_ids,
        status=review.action,
        reviewed_by_email=reviewer_email,
        ctx=ctx,
    )

    return {
        "status": "success",
        "message": f"{count} matches {review.action.value}",
        "count": count,
    }


# ===========================
# AI Suggestions
# ===========================


@router.post("/suggest/{source_connection_id}")
async def generate_suggestions(
    *,
    db: AsyncSession = Depends(get_db),
    source_connection_id: UUID,
    ctx: ApiContext = Depends(deps.get_context),
    limit: int = Query(100, ge=1, le=500),
) -> List[dict]:
    """Generate AI-suggested matches for a source connection.

    Analyzes unmatched entities in the source connection and suggests
    potential cluster matches based on email and name similarity.

    Suggestions are returned but not automatically applied. Use the
    manual match endpoint to apply suggestions.
    """
    suggestions = await identity_matching_service.suggest_matches_for_source(
        db,
        source_connection_id=source_connection_id,
        ctx=ctx,
        limit=limit,
    )
    return suggestions


# ===========================
# Search
# ===========================


@router.get("/search")
async def search_identities(
    *,
    db: AsyncSession = Depends(get_db),
    ctx: ApiContext = Depends(deps.get_context),
    q: str = Query(..., min_length=1, description="Search query (email or name)"),
    client_id: Optional[UUID] = Query(None, description="Filter by client"),
    limit: int = Query(50, ge=1, le=500),
) -> List[dict]:
    """Search for identity clusters by email or name.

    Searches cluster primary_email and name fields. Returns matching clusters
    with their match counts.
    """
    clusters = await crud.identity_cluster.search_by_email_or_name(
        db,
        query_str=q,
        ctx=ctx,
        client_id=client_id,
        limit=limit,
    )

    results = []
    for cluster in clusters:
        results.append(
            {
                "id": cluster.id,
                "name": cluster.name,
                "primary_email": cluster.primary_email,
                "client_id": cluster.client_id,
                "created_at": cluster.created_at,
                "modified_at": cluster.modified_at,
            }
        )

    return results
