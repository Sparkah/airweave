"""CRUD operations for Client management."""

import re
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from airweave.api.context import ApiContext
from airweave.core.exceptions import NotFoundException, ValidationException
from airweave.models.client import Client
from airweave.models.identity_match import IdentityCluster
from airweave.models.source_connection import SourceConnection
from airweave.schemas.client import ClientCreate, ClientUpdate

from ._base_organization import CRUDBaseOrganization


def generate_readable_id(name: str) -> str:
    """Generate a readable_id from a name.

    Converts name to lowercase, replaces spaces and special chars with hyphens,
    and removes consecutive hyphens.
    """
    # Convert to lowercase and replace spaces/special chars with hyphens
    readable_id = re.sub(r"[^a-z0-9]+", "-", name.lower())
    # Remove leading/trailing hyphens
    readable_id = readable_id.strip("-")
    # Remove consecutive hyphens
    readable_id = re.sub(r"-+", "-", readable_id)
    return readable_id


class CRUDClient(CRUDBaseOrganization[Client, ClientCreate, ClientUpdate]):
    """CRUD operations for Client model."""

    async def create(
        self,
        db: AsyncSession,
        *,
        obj_in: ClientCreate,
        ctx: ApiContext,
        **kwargs,
    ) -> Client:
        """Create a new client.

        Auto-generates readable_id from name if not provided.
        Ensures readable_id uniqueness by appending a number if needed.
        """
        obj_data = obj_in.model_dump(exclude_unset=True)

        # Generate readable_id if not provided
        if not obj_data.get("readable_id"):
            base_readable_id = generate_readable_id(obj_data["name"])
            obj_data["readable_id"] = await self._ensure_unique_readable_id(
                db, base_readable_id
            )
        else:
            # Check if the provided readable_id is unique
            existing = await self.get_by_readable_id(
                db, readable_id=obj_data["readable_id"]
            )
            if existing:
                raise ValidationException(
                    f"Client with readable_id '{obj_data['readable_id']}' already exists"
                )

        return await super().create(db, obj_in=obj_data, ctx=ctx, **kwargs)

    async def _ensure_unique_readable_id(
        self, db: AsyncSession, base_readable_id: str
    ) -> str:
        """Ensure readable_id is unique by appending a number if needed."""
        readable_id = base_readable_id
        counter = 1

        while True:
            existing = await self.get_by_readable_id(db, readable_id=readable_id)
            if not existing:
                return readable_id
            readable_id = f"{base_readable_id}-{counter}"
            counter += 1
            if counter > 100:
                raise ValidationException("Could not generate unique readable_id")

    async def get_by_readable_id(
        self, db: AsyncSession, *, readable_id: str
    ) -> Optional[Client]:
        """Get a client by readable_id (globally unique)."""
        query = select(Client).where(Client.readable_id == readable_id)
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def get_by_domain(
        self, db: AsyncSession, *, domain: str, ctx: ApiContext
    ) -> Optional[Client]:
        """Get a client by domain within the organization."""
        query = select(Client).where(
            and_(
                Client.domain == domain.lower(),
                Client.organization_id == ctx.organization.id,
            )
        )
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def get_multi_with_stats(
        self,
        db: AsyncSession,
        *,
        ctx: ApiContext,
        skip: int = 0,
        limit: int = 100,
        include_inactive: bool = False,
    ) -> List[Dict[str, Any]]:
        """Get clients with statistics (source connection and cluster counts)."""
        # Base query
        query = select(Client).where(Client.organization_id == ctx.organization.id)

        if not include_inactive:
            query = query.where(Client.is_active == True)

        query = query.offset(skip).limit(limit).order_by(Client.created_at.desc())
        result = await db.execute(query)
        clients = list(result.scalars().all())

        if not clients:
            return []

        client_ids = [c.id for c in clients]

        # Get source connection counts
        sc_counts = await self._get_source_connection_counts(db, client_ids)

        # Get identity cluster counts
        cluster_counts = await self._get_cluster_counts(db, client_ids)

        # Build response
        results = []
        for client in clients:
            results.append(
                {
                    "id": client.id,
                    "name": client.name,
                    "readable_id": client.readable_id,
                    "description": client.description,
                    "domain": client.domain,
                    "client_metadata": client.client_metadata,
                    "is_active": client.is_active,
                    "created_at": client.created_at,
                    "modified_at": client.modified_at,
                    "created_by_email": client.created_by_email,
                    "modified_by_email": client.modified_by_email,
                    "source_connection_count": sc_counts.get(client.id, 0),
                    "identity_cluster_count": cluster_counts.get(client.id, 0),
                }
            )

        return results

    async def _get_source_connection_counts(
        self, db: AsyncSession, client_ids: List[UUID]
    ) -> Dict[UUID, int]:
        """Get source connection counts for clients."""
        query = (
            select(
                SourceConnection.client_id,
                func.count(SourceConnection.id).label("count"),
            )
            .where(SourceConnection.client_id.in_(client_ids))
            .group_by(SourceConnection.client_id)
        )
        result = await db.execute(query)
        return {row.client_id: row.count for row in result}

    async def _get_cluster_counts(
        self, db: AsyncSession, client_ids: List[UUID]
    ) -> Dict[UUID, int]:
        """Get identity cluster counts for clients."""
        query = (
            select(
                IdentityCluster.client_id,
                func.count(IdentityCluster.id).label("count"),
            )
            .where(IdentityCluster.client_id.in_(client_ids))
            .group_by(IdentityCluster.client_id)
        )
        result = await db.execute(query)
        return {row.client_id: row.count for row in result}

    async def get_with_source_connections(
        self, db: AsyncSession, *, id: UUID, ctx: ApiContext
    ) -> Client:
        """Get a client with its source connections."""
        query = (
            select(Client)
            .options(selectinload(Client.source_connections))
            .where(
                and_(
                    Client.id == id,
                    Client.organization_id == ctx.organization.id,
                )
            )
        )
        result = await db.execute(query)
        client = result.scalar_one_or_none()
        if not client:
            raise NotFoundException("Client not found")
        return client

    async def assign_source_connection(
        self,
        db: AsyncSession,
        *,
        client_id: UUID,
        source_connection_id: UUID,
        ctx: ApiContext,
    ) -> SourceConnection:
        """Assign a source connection to a client."""
        # Verify client exists and belongs to org
        client = await self.get(db, id=client_id, ctx=ctx)
        if not client:
            raise NotFoundException("Client not found")

        # Get and update source connection
        from airweave import crud

        source_conn = await crud.source_connection.get(
            db, id=source_connection_id, ctx=ctx
        )
        if not source_conn:
            raise NotFoundException("Source connection not found")

        source_conn.client_id = client_id
        await db.commit()
        await db.refresh(source_conn)
        return source_conn

    async def unassign_source_connection(
        self,
        db: AsyncSession,
        *,
        client_id: UUID,
        source_connection_id: UUID,
        ctx: ApiContext,
    ) -> SourceConnection:
        """Remove a source connection from a client."""
        # Verify client exists and belongs to org
        client = await self.get(db, id=client_id, ctx=ctx)
        if not client:
            raise NotFoundException("Client not found")

        # Get source connection
        from airweave import crud

        source_conn = await crud.source_connection.get(
            db, id=source_connection_id, ctx=ctx
        )
        if not source_conn:
            raise NotFoundException("Source connection not found")

        if source_conn.client_id != client_id:
            raise ValidationException(
                "Source connection is not assigned to this client"
            )

        source_conn.client_id = None
        await db.commit()
        await db.refresh(source_conn)
        return source_conn

    async def get_source_connections(
        self,
        db: AsyncSession,
        *,
        client_id: UUID,
        ctx: ApiContext,
        skip: int = 0,
        limit: int = 100,
    ) -> List[SourceConnection]:
        """Get all source connections for a client."""
        # Verify client exists
        await self.get(db, id=client_id, ctx=ctx)

        query = (
            select(SourceConnection)
            .where(
                and_(
                    SourceConnection.client_id == client_id,
                    SourceConnection.organization_id == ctx.organization.id,
                )
            )
            .offset(skip)
            .limit(limit)
            .order_by(SourceConnection.created_at.desc())
        )
        result = await db.execute(query)
        return list(result.scalars().all())


# Singleton instance
client = CRUDClient(Client)
