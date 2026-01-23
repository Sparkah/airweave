"""Client management API endpoints."""

from typing import List, Optional
from uuid import UUID

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from airweave import crud
from airweave.api import deps
from airweave.api.context import ApiContext
from airweave.api.router import TrailingSlashRouter
from airweave.db.session import get_db
from airweave.schemas.client import (
    Client,
    ClientCreate,
    ClientUpdate,
    ClientWithSources,
    ClientWithStats,
)
from airweave.schemas.source_connection import SourceConnectionListItem

router = TrailingSlashRouter()


@router.post("/", response_model=Client)
async def create_client(
    *,
    db: AsyncSession = Depends(get_db),
    client_in: ClientCreate,
    ctx: ApiContext = Depends(deps.get_context),
) -> Client:
    """Create a new client.

    Creates a client under the current organization. Clients are used to
    organize source connections and identity clusters by customer/tenant.

    If `readable_id` is not provided, it will be auto-generated from the name.
    """
    client = await crud.client.create(db, obj_in=client_in, ctx=ctx)
    return Client.model_validate(client)


@router.get("/", response_model=List[ClientWithStats])
async def list_clients(
    *,
    db: AsyncSession = Depends(get_db),
    ctx: ApiContext = Depends(deps.get_context),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    include_inactive: bool = Query(False, description="Include inactive clients"),
) -> List[ClientWithStats]:
    """List all clients for the organization.

    Returns clients with statistics including source connection and identity
    cluster counts.
    """
    clients = await crud.client.get_multi_with_stats(
        db,
        ctx=ctx,
        skip=skip,
        limit=limit,
        include_inactive=include_inactive,
    )
    return [ClientWithStats.model_validate(c) for c in clients]


@router.get("/{client_id}", response_model=Client)
async def get_client(
    *,
    db: AsyncSession = Depends(get_db),
    client_id: UUID,
    ctx: ApiContext = Depends(deps.get_context),
) -> Client:
    """Get a client by ID."""
    client = await crud.client.get(db, id=client_id, ctx=ctx)
    return Client.model_validate(client)


@router.get("/by-readable-id/{readable_id}", response_model=Client)
async def get_client_by_readable_id(
    *,
    db: AsyncSession = Depends(get_db),
    readable_id: str,
    ctx: ApiContext = Depends(deps.get_context),
) -> Client:
    """Get a client by readable ID (slug)."""
    client = await crud.client.get_by_readable_id(db, readable_id=readable_id)
    if not client or client.organization_id != ctx.organization.id:
        from airweave.core.exceptions import NotFoundException

        raise NotFoundException("Client not found")
    return Client.model_validate(client)


@router.patch("/{client_id}", response_model=Client)
async def update_client(
    *,
    db: AsyncSession = Depends(get_db),
    client_id: UUID,
    client_in: ClientUpdate,
    ctx: ApiContext = Depends(deps.get_context),
) -> Client:
    """Update a client.

    Updateable fields: name, description, domain, metadata, is_active
    """
    client = await crud.client.get(db, id=client_id, ctx=ctx)
    client = await crud.client.update(db, db_obj=client, obj_in=client_in, ctx=ctx)
    return Client.model_validate(client)


@router.delete("/{client_id}", response_model=Client)
async def delete_client(
    *,
    db: AsyncSession = Depends(get_db),
    client_id: UUID,
    ctx: ApiContext = Depends(deps.get_context),
) -> Client:
    """Delete a client.

    Note: This will unassign all source connections and identity clusters
    from the client (they will not be deleted).
    """
    client = await crud.client.remove(db, id=client_id, ctx=ctx)
    return Client.model_validate(client)


# ===========================
# Source Connection Assignment
# ===========================


@router.get("/{client_id}/source-connections", response_model=List[SourceConnectionListItem])
async def list_client_source_connections(
    *,
    db: AsyncSession = Depends(get_db),
    client_id: UUID,
    ctx: ApiContext = Depends(deps.get_context),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
) -> List[SourceConnectionListItem]:
    """List all source connections assigned to a client."""
    source_connections = await crud.client.get_source_connections(
        db,
        client_id=client_id,
        ctx=ctx,
        skip=skip,
        limit=limit,
    )

    # Build response items
    items = []
    for sc in source_connections:
        items.append(
            SourceConnectionListItem(
                id=sc.id,
                name=sc.name,
                short_name=sc.short_name,
                readable_collection_id=sc.readable_collection_id,
                created_at=sc.created_at,
                modified_at=sc.modified_at,
                is_authenticated=sc.is_authenticated,
                entity_count=0,  # Would need to fetch this separately
            )
        )

    return items


@router.post("/{client_id}/source-connections/{source_connection_id}")
async def assign_source_connection(
    *,
    db: AsyncSession = Depends(get_db),
    client_id: UUID,
    source_connection_id: UUID,
    ctx: ApiContext = Depends(deps.get_context),
) -> dict:
    """Assign a source connection to a client.

    This associates the source connection with the client for organizational
    purposes and enables domain-based identity matching.
    """
    await crud.client.assign_source_connection(
        db,
        client_id=client_id,
        source_connection_id=source_connection_id,
        ctx=ctx,
    )
    return {"status": "success", "message": "Source connection assigned to client"}


@router.delete("/{client_id}/source-connections/{source_connection_id}")
async def unassign_source_connection(
    *,
    db: AsyncSession = Depends(get_db),
    client_id: UUID,
    source_connection_id: UUID,
    ctx: ApiContext = Depends(deps.get_context),
) -> dict:
    """Remove a source connection from a client.

    This does not delete the source connection, only removes the association.
    """
    await crud.client.unassign_source_connection(
        db,
        client_id=client_id,
        source_connection_id=source_connection_id,
        ctx=ctx,
    )
    return {"status": "success", "message": "Source connection removed from client"}


# ===========================
# Client Identities
# ===========================


@router.get("/{client_id}/identities")
async def list_client_identities(
    *,
    db: AsyncSession = Depends(get_db),
    client_id: UUID,
    ctx: ApiContext = Depends(deps.get_context),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
) -> List[dict]:
    """List all identity clusters for a client.

    Returns identity clusters that have been linked to this client through
    domain matching or manual assignment.
    """
    # Verify client exists
    await crud.client.get(db, id=client_id, ctx=ctx)

    clusters = await crud.identity_cluster.get_multi_with_stats(
        db,
        ctx=ctx,
        client_id=client_id,
        skip=skip,
        limit=limit,
    )
    return clusters
