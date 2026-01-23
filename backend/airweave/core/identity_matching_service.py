"""Identity matching service for cross-source entity linking.

This service handles automatic and manual identity matching across different
data sources. It creates identity clusters that group entities representing
the same person across sources.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from airweave.api.context import ApiContext
from airweave.core.identity_signals import (
    IdentitySignals,
    calculate_name_similarity,
    extract_domain_from_email,
    extract_identity_signals,
    is_identity_entity_type,
)
from airweave.models.identity_match import (
    IdentityCluster,
    IdentityMatch,
    MatchMethod,
    MatchStatus,
)

logger = logging.getLogger(__name__)

# Confidence thresholds
EMAIL_EXACT_CONFIDENCE = 1.0
EMAIL_DOMAIN_CONFIDENCE = 0.8
NAME_FUZZY_HIGH_CONFIDENCE = 0.7
NAME_FUZZY_LOW_CONFIDENCE = 0.5
AI_SUGGESTION_CONFIDENCE = 0.6

# Minimum confidence for auto-approval
AUTO_APPROVE_THRESHOLD = 0.8


@dataclass
class MatchResult:
    """Result of an identity match attempt."""

    matched: bool
    cluster_id: Optional[UUID] = None
    match_id: Optional[UUID] = None
    match_method: Optional[MatchMethod] = None
    confidence_score: float = 0.0
    status: MatchStatus = MatchStatus.PENDING
    is_new_cluster: bool = False


class IdentityMatchingService:
    """Service for identity matching across data sources."""

    async def match_entity(
        self,
        db: AsyncSession,
        *,
        entity_id: str,
        entity_type: str,
        entity_data: Dict[str, Any],
        sync_id: UUID,
        source_connection_id: Optional[UUID],
        organization_id: UUID,
        client_id: Optional[UUID] = None,
    ) -> Optional[MatchResult]:
        """Attempt to match an entity to an identity cluster.

        This is the main entry point for identity matching. It:
        1. Checks if the entity type is relevant for identity matching
        2. Extracts identity signals (email, name) from the entity data
        3. Tries to match to an existing cluster by email (exact match)
        4. If client_id is set, tries domain-based matching
        5. Falls back to fuzzy name matching
        6. Creates a new cluster if no match found

        Args:
            db: Database session
            entity_id: The entity's ID
            entity_type: The entity type name (e.g., "SlackUser")
            entity_data: The entity's data as a dictionary
            sync_id: The sync ID
            source_connection_id: The source connection ID
            organization_id: The organization ID
            client_id: Optional client ID for domain matching

        Returns:
            MatchResult with match details, or None if entity type is not relevant
        """
        from airweave import crud

        # Check if source connection is available
        if source_connection_id is None:
            logger.debug(
                f"No source_connection_id for {entity_type}[{entity_id}], skipping identity matching"
            )
            return None

        # Check if this entity type is relevant for identity matching
        if not is_identity_entity_type(entity_type):
            return None

        # Extract identity signals from entity data
        signals = extract_identity_signals(entity_data, entity_type)
        if not signals.has_signals:
            logger.debug(
                f"No identity signals found for {entity_type}[{entity_id}]"
            )
            return None

        # Create a minimal context for CRUD operations
        # This is a simplified context since we're running in the sync pipeline
        from airweave.models.organization import Organization

        org = await db.get(Organization, organization_id)
        if not org:
            logger.error(f"Organization {organization_id} not found")
            return None

        class MinimalContext:
            def __init__(self, organization):
                self.organization = organization
                self.has_user_context = False
                self.tracking_email = None

        ctx = MinimalContext(org)

        # Check if this entity is already matched
        existing_match = await crud.identity_match.get_by_entity_and_sync(
            db, entity_id=entity_id, sync_id=sync_id, ctx=ctx
        )
        if existing_match:
            logger.debug(
                f"Entity {entity_type}[{entity_id}] already matched to cluster {existing_match.cluster_id}"
            )
            return MatchResult(
                matched=True,
                cluster_id=existing_match.cluster_id,
                match_id=existing_match.id,
                match_method=MatchMethod(existing_match.match_method),
                confidence_score=existing_match.confidence_score,
                status=MatchStatus(existing_match.status),
                is_new_cluster=False,
            )

        # Try matching strategies in order of confidence
        result = None

        # 1. Email exact match (highest confidence)
        if signals.primary_email:
            result = await self._try_email_exact_match(
                db,
                signals=signals,
                entity_id=entity_id,
                sync_id=sync_id,
                source_connection_id=source_connection_id,
                organization_id=organization_id,
                client_id=client_id,
                ctx=ctx,
            )

        # 2. Domain-based matching (if client has a domain)
        if not result and signals.primary_email and client_id:
            result = await self._try_domain_match(
                db,
                signals=signals,
                entity_id=entity_id,
                sync_id=sync_id,
                source_connection_id=source_connection_id,
                organization_id=organization_id,
                client_id=client_id,
                ctx=ctx,
            )

        # 3. Fuzzy name matching (lower confidence, may need review)
        if not result and signals.primary_name:
            result = await self._try_name_fuzzy_match(
                db,
                signals=signals,
                entity_id=entity_id,
                sync_id=sync_id,
                source_connection_id=source_connection_id,
                organization_id=organization_id,
                client_id=client_id,
                ctx=ctx,
            )

        # 4. Create new cluster if no match found
        if not result:
            result = await self._create_new_cluster(
                db,
                signals=signals,
                entity_id=entity_id,
                sync_id=sync_id,
                source_connection_id=source_connection_id,
                organization_id=organization_id,
                client_id=client_id,
                ctx=ctx,
            )

        return result

    async def _try_email_exact_match(
        self,
        db: AsyncSession,
        *,
        signals: IdentitySignals,
        entity_id: str,
        sync_id: UUID,
        source_connection_id: UUID,
        organization_id: UUID,
        client_id: Optional[UUID],
        ctx: Any,
    ) -> Optional[MatchResult]:
        """Try to match by exact email."""
        from airweave import crud

        # Look for existing cluster with this email
        cluster = await crud.identity_cluster.get_by_email(
            db, email=signals.primary_email, ctx=ctx
        )

        if cluster:
            # Found existing cluster - add match
            match = await self._create_match(
                db,
                cluster_id=cluster.id,
                entity_id=entity_id,
                sync_id=sync_id,
                source_connection_id=source_connection_id,
                organization_id=organization_id,
                match_method=MatchMethod.EMAIL_EXACT,
                confidence_score=EMAIL_EXACT_CONFIDENCE,
                matched_email=signals.primary_email,
                matched_name=signals.primary_name,
                auto_approve=True,  # High confidence, auto-approve
            )

            logger.info(
                f"Email exact match: {entity_id} -> cluster {cluster.id} "
                f"(email: {signals.primary_email})"
            )

            return MatchResult(
                matched=True,
                cluster_id=cluster.id,
                match_id=match.id,
                match_method=MatchMethod.EMAIL_EXACT,
                confidence_score=EMAIL_EXACT_CONFIDENCE,
                status=MatchStatus.APPROVED,
                is_new_cluster=False,
            )

        return None

    async def _try_domain_match(
        self,
        db: AsyncSession,
        *,
        signals: IdentitySignals,
        entity_id: str,
        sync_id: UUID,
        source_connection_id: UUID,
        organization_id: UUID,
        client_id: UUID,
        ctx: Any,
    ) -> Optional[MatchResult]:
        """Try to match by email domain to client."""
        from airweave import crud

        # Get the client's domain
        client = await crud.client.get(db, id=client_id, ctx=ctx)
        if not client or not client.domain:
            return None

        email_domain = extract_domain_from_email(signals.primary_email)
        if email_domain and email_domain == client.domain:
            # Email domain matches client domain - create new cluster linked to client
            cluster = await self._create_cluster(
                db,
                name=signals.primary_name,
                primary_email=signals.primary_email,
                organization_id=organization_id,
                client_id=client_id,
            )

            match = await self._create_match(
                db,
                cluster_id=cluster.id,
                entity_id=entity_id,
                sync_id=sync_id,
                source_connection_id=source_connection_id,
                organization_id=organization_id,
                match_method=MatchMethod.EMAIL_DOMAIN,
                confidence_score=EMAIL_DOMAIN_CONFIDENCE,
                matched_email=signals.primary_email,
                matched_name=signals.primary_name,
                auto_approve=True,  # Domain match is high confidence
            )

            logger.info(
                f"Domain match: {entity_id} -> new cluster {cluster.id} "
                f"(domain: {email_domain}, client: {client.name})"
            )

            return MatchResult(
                matched=True,
                cluster_id=cluster.id,
                match_id=match.id,
                match_method=MatchMethod.EMAIL_DOMAIN,
                confidence_score=EMAIL_DOMAIN_CONFIDENCE,
                status=MatchStatus.APPROVED,
                is_new_cluster=True,
            )

        return None

    async def _try_name_fuzzy_match(
        self,
        db: AsyncSession,
        *,
        signals: IdentitySignals,
        entity_id: str,
        sync_id: UUID,
        source_connection_id: UUID,
        organization_id: UUID,
        client_id: Optional[UUID],
        ctx: Any,
    ) -> Optional[MatchResult]:
        """Try to match by fuzzy name comparison."""
        from airweave import crud

        # Search for clusters with similar names
        clusters = await crud.identity_cluster.search_by_email_or_name(
            db,
            query_str=signals.primary_name,
            ctx=ctx,
            client_id=client_id,
            limit=10,
        )

        best_match = None
        best_score = 0.0

        for cluster in clusters:
            if cluster.name:
                score = calculate_name_similarity(signals.primary_name, cluster.name)
                if score > best_score and score >= NAME_FUZZY_LOW_CONFIDENCE:
                    best_match = cluster
                    best_score = score

        if best_match and best_score >= NAME_FUZZY_LOW_CONFIDENCE:
            # Determine if this should be auto-approved
            auto_approve = best_score >= AUTO_APPROVE_THRESHOLD
            status = MatchStatus.APPROVED if auto_approve else MatchStatus.PENDING

            match = await self._create_match(
                db,
                cluster_id=best_match.id,
                entity_id=entity_id,
                sync_id=sync_id,
                source_connection_id=source_connection_id,
                organization_id=organization_id,
                match_method=MatchMethod.NAME_FUZZY,
                confidence_score=best_score,
                matched_email=signals.primary_email,
                matched_name=signals.primary_name,
                auto_approve=auto_approve,
            )

            logger.info(
                f"Name fuzzy match: {entity_id} -> cluster {best_match.id} "
                f"(name: {signals.primary_name}, score: {best_score:.2f}, "
                f"status: {status.value})"
            )

            return MatchResult(
                matched=True,
                cluster_id=best_match.id,
                match_id=match.id,
                match_method=MatchMethod.NAME_FUZZY,
                confidence_score=best_score,
                status=status,
                is_new_cluster=False,
            )

        return None

    async def _create_new_cluster(
        self,
        db: AsyncSession,
        *,
        signals: IdentitySignals,
        entity_id: str,
        sync_id: UUID,
        source_connection_id: UUID,
        organization_id: UUID,
        client_id: Optional[UUID],
        ctx: Any,
    ) -> MatchResult:
        """Create a new identity cluster for an unmatched entity."""
        # Create the cluster
        cluster = await self._create_cluster(
            db,
            name=signals.primary_name,
            primary_email=signals.primary_email,
            organization_id=organization_id,
            client_id=client_id,
        )

        # Create the match
        match = await self._create_match(
            db,
            cluster_id=cluster.id,
            entity_id=entity_id,
            sync_id=sync_id,
            source_connection_id=source_connection_id,
            organization_id=organization_id,
            match_method=MatchMethod.EMAIL_EXACT if signals.primary_email else MatchMethod.MANUAL,
            confidence_score=EMAIL_EXACT_CONFIDENCE if signals.primary_email else 1.0,
            matched_email=signals.primary_email,
            matched_name=signals.primary_name,
            auto_approve=True,  # First entity in cluster is auto-approved
        )

        logger.info(
            f"Created new cluster {cluster.id} for {entity_id} "
            f"(email: {signals.primary_email}, name: {signals.primary_name})"
        )

        return MatchResult(
            matched=True,
            cluster_id=cluster.id,
            match_id=match.id,
            match_method=MatchMethod.EMAIL_EXACT if signals.primary_email else MatchMethod.MANUAL,
            confidence_score=EMAIL_EXACT_CONFIDENCE if signals.primary_email else 1.0,
            status=MatchStatus.APPROVED,
            is_new_cluster=True,
        )

    async def _create_cluster(
        self,
        db: AsyncSession,
        *,
        name: Optional[str],
        primary_email: Optional[str],
        organization_id: UUID,
        client_id: Optional[UUID],
    ) -> IdentityCluster:
        """Create a new identity cluster."""
        from airweave.core.datetime_utils import utc_now_naive

        cluster = IdentityCluster(
            organization_id=organization_id,
            name=name,
            primary_email=primary_email.lower() if primary_email else None,
            client_id=client_id,
            merged_attributes={},
            created_at=utc_now_naive(),
            modified_at=utc_now_naive(),
        )
        db.add(cluster)
        await db.flush()
        return cluster

    async def _create_match(
        self,
        db: AsyncSession,
        *,
        cluster_id: UUID,
        entity_id: str,
        sync_id: UUID,
        source_connection_id: UUID,
        organization_id: UUID,
        match_method: MatchMethod,
        confidence_score: float,
        matched_email: Optional[str],
        matched_name: Optional[str],
        auto_approve: bool,
    ) -> IdentityMatch:
        """Create a new identity match."""
        from airweave.core.datetime_utils import utc_now_naive

        status = MatchStatus.APPROVED if auto_approve else MatchStatus.PENDING

        match = IdentityMatch(
            organization_id=organization_id,
            cluster_id=cluster_id,
            entity_id=entity_id,
            sync_id=sync_id,
            source_connection_id=source_connection_id,
            match_method=match_method.value,
            confidence_score=confidence_score,
            status=status.value,
            matched_email=matched_email.lower() if matched_email else None,
            matched_name=matched_name,
            created_at=utc_now_naive(),
            modified_at=utc_now_naive(),
        )
        db.add(match)
        await db.flush()
        return match

    async def suggest_matches_for_source(
        self,
        db: AsyncSession,
        *,
        source_connection_id: UUID,
        ctx: ApiContext,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Generate AI-suggested matches for entities in a source connection.

        This can be called to generate suggestions for entities that haven't
        been matched yet, using lower confidence thresholds.

        Args:
            db: Database session
            source_connection_id: Source connection to analyze
            ctx: API context
            limit: Maximum suggestions to generate

        Returns:
            List of suggested matches
        """
        from airweave import crud
        from airweave.models.entity import Entity

        # Get unmatched entities from this source connection
        # This is a simplified query - in production you'd want to optimize this
        from sqlalchemy import and_, select

        # Get the sync_id for this source connection
        source_conn = await crud.source_connection.get(
            db, id=source_connection_id, ctx=ctx
        )
        if not source_conn or not source_conn.sync_id:
            return []

        # Find entities that don't have identity matches yet
        subquery = select(IdentityMatch.entity_id).where(
            IdentityMatch.sync_id == source_conn.sync_id
        )

        query = (
            select(Entity)
            .where(
                and_(
                    Entity.sync_id == source_conn.sync_id,
                    ~Entity.entity_id.in_(subquery),
                )
            )
            .limit(limit)
        )

        result = await db.execute(query)
        entities = result.scalars().all()

        suggestions = []
        for entity in entities:
            # Extract identity signals
            entity_data = entity.data if hasattr(entity, "data") else {}
            signals = extract_identity_signals(entity_data)

            if not signals.has_signals:
                continue

            # Look for potential cluster matches
            if signals.primary_email:
                cluster = await crud.identity_cluster.get_by_email(
                    db, email=signals.primary_email, ctx=ctx
                )
                if cluster:
                    suggestions.append(
                        {
                            "entity_id": entity.entity_id,
                            "sync_id": source_conn.sync_id,
                            "source_connection_id": source_connection_id,
                            "suggested_cluster_id": cluster.id,
                            "confidence_score": AI_SUGGESTION_CONFIDENCE,
                            "matched_email": signals.primary_email,
                            "matched_name": signals.primary_name,
                            "reason": f"Email matches cluster '{cluster.name or cluster.primary_email}'",
                        }
                    )
                    continue

            # Try fuzzy name matching for suggestions
            if signals.primary_name:
                clusters = await crud.identity_cluster.search_by_email_or_name(
                    db,
                    query_str=signals.primary_name,
                    ctx=ctx,
                    limit=5,
                )

                for cluster in clusters:
                    if cluster.name:
                        score = calculate_name_similarity(
                            signals.primary_name, cluster.name
                        )
                        if score >= NAME_FUZZY_LOW_CONFIDENCE:
                            suggestions.append(
                                {
                                    "entity_id": entity.entity_id,
                                    "sync_id": source_conn.sync_id,
                                    "source_connection_id": source_connection_id,
                                    "suggested_cluster_id": cluster.id,
                                    "confidence_score": score * AI_SUGGESTION_CONFIDENCE,
                                    "matched_email": signals.primary_email,
                                    "matched_name": signals.primary_name,
                                    "reason": f"Name similar to cluster '{cluster.name}' "
                                    f"(score: {score:.2f})",
                                }
                            )
                            break

        return suggestions


# Singleton instance
identity_matching_service = IdentityMatchingService()
