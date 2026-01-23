"""Identity matching handler for cross-source entity linking.

Runs after entities are persisted to PostgreSQL to create/update identity
matches and clusters.
"""

import logging
from typing import TYPE_CHECKING, List

from airweave.core.identity_matching_service import identity_matching_service
from airweave.core.identity_signals import is_identity_entity_type
from airweave.db.session import get_db_context
from airweave.platform.sync.actions.entity.types import (
    EntityActionBatch,
    EntityDeleteAction,
    EntityInsertAction,
    EntityUpdateAction,
)
from airweave.platform.sync.handlers.protocol import EntityActionHandler

if TYPE_CHECKING:
    from airweave.platform.contexts import SyncContext

logger = logging.getLogger(__name__)


class IdentityMatchingHandler(EntityActionHandler):
    """Handler for identity matching of entities.

    Processes newly inserted entities to create or update identity clusters
    and matches. This enables cross-source identity resolution.

    This handler is designed to be non-blocking - failures in identity
    matching should not fail the sync. Identity matches can always be
    created/corrected later through the API.
    """

    @property
    def name(self) -> str:
        """Handler name."""
        return "identity_matching"

    async def handle_batch(
        self,
        batch: EntityActionBatch,
        sync_context: "SyncContext",
    ) -> None:
        """Handle batch - process inserts for identity matching.

        Only processes INSERT actions since those represent new entities
        that need to be matched to identity clusters.

        Args:
            batch: Entity action batch
            sync_context: Sync context
        """
        if not batch.inserts:
            return

        # Filter for identity-relevant entity types
        identity_inserts = [
            action for action in batch.inserts
            if is_identity_entity_type(action.entity_type)
        ]

        if not identity_inserts:
            sync_context.logger.debug(
                "[IdentityMatching] No identity-relevant entities in batch"
            )
            return

        sync_context.logger.debug(
            f"[IdentityMatching] Processing {len(identity_inserts)} entities for identity matching"
        )

        # Get source connection to check for client_id
        client_id = None
        source_connection_id = None

        async with get_db_context() as db:
            # Fetch source connection from database using sync_id
            from airweave import crud

            source_connection = await crud.source_connection.get_by_sync_id(
                db,
                sync_id=sync_context.sync.id,
                ctx=sync_context.ctx,
            )
            if source_connection:
                source_connection_id = source_connection.id
                client_id = source_connection.client_id

        # Process each entity for identity matching
        matched_count = 0
        new_cluster_count = 0
        error_count = 0

        async with get_db_context() as db:
            for action in identity_inserts:
                try:
                    # Convert entity to dict for signal extraction
                    entity_data = self._entity_to_dict(action.entity)

                    result = await identity_matching_service.match_entity(
                        db,
                        entity_id=action.entity_id,
                        entity_type=action.entity_type,
                        entity_data=entity_data,
                        sync_id=sync_context.sync.id,
                        source_connection_id=source_connection_id,
                        organization_id=sync_context.sync.organization_id,
                        client_id=client_id,
                    )

                    if result and result.matched:
                        matched_count += 1
                        if result.is_new_cluster:
                            new_cluster_count += 1

                except Exception as e:
                    # Log but don't fail - identity matching is non-critical
                    error_count += 1
                    sync_context.logger.warning(
                        f"[IdentityMatching] Error matching entity {action.entity_id}: {e}"
                    )

            # Commit all identity matches in one transaction
            await db.commit()

        sync_context.logger.info(
            f"[IdentityMatching] Matched {matched_count} entities "
            f"({new_cluster_count} new clusters, {error_count} errors)"
        )

    async def handle_inserts(
        self,
        actions: List[EntityInsertAction],
        sync_context: "SyncContext",
    ) -> None:
        """Handle inserts - main identity matching logic.

        This is called when using the individual action methods instead of
        handle_batch.
        """
        # Delegate to batch handling
        batch = EntityActionBatch(
            inserts=actions,
            updates=[],
            deletes=[],
            keeps=[],
            existing_map={},
        )
        await self.handle_batch(batch, sync_context)

    async def handle_updates(
        self,
        actions: List[EntityUpdateAction],
        sync_context: "SyncContext",
    ) -> None:
        """Handle updates - no-op for identity matching.

        Identity matches are based on the entity's identifying fields which
        typically don't change on updates. If they do change, the entity
        would need to be re-evaluated manually.
        """
        pass

    async def handle_deletes(
        self,
        actions: List[EntityDeleteAction],
        sync_context: "SyncContext",
    ) -> None:
        """Handle deletes - no-op for identity matching.

        When entities are deleted, their identity matches are orphaned but
        not automatically deleted. This allows historical tracking and
        manual cleanup if needed.
        """
        pass

    async def handle_orphan_cleanup(
        self,
        orphan_entity_ids: List[str],
        sync_context: "SyncContext",
    ) -> None:
        """Handle orphan cleanup - no-op for identity matching.

        Identity matches for orphaned entities are left in place for
        historical tracking.
        """
        pass

    def _entity_to_dict(self, entity) -> dict:
        """Convert entity to dictionary for signal extraction.

        Args:
            entity: BaseEntity instance

        Returns:
            Dictionary representation of entity data
        """
        try:
            # Try pydantic model_dump first
            if hasattr(entity, "model_dump"):
                return entity.model_dump(exclude_none=True)
            # Fall back to __dict__
            elif hasattr(entity, "__dict__"):
                return {
                    k: v for k, v in entity.__dict__.items()
                    if not k.startswith("_") and v is not None
                }
            else:
                return {}
        except Exception:
            return {}
