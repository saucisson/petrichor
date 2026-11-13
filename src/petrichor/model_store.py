"""In-memory model store with content-hash idempotence (spec §5, §13).

Models are stored by id and indexed by content hash: saving a model whose
hash is already known returns the stored model instead of a duplicate, which
is what makes `POST /v1/models` idempotent.
"""

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from petrichor.petri_canonical import CanonicalModel, ContentHash, ModelId

logger = structlog.get_logger(__name__)


class ModelStore:
    """Stores canonical models by id, indexed by content hash for idempotent import."""

    def __init__(self) -> None:
        """Start with an empty store."""
        self._by_id: dict[ModelId, CanonicalModel] = {}
        self._by_hash: dict[ContentHash, ModelId] = {}

    def save(self, *, model: CanonicalModel) -> tuple[CanonicalModel, bool]:
        """Store a model; return it with ``created=False`` if its hash is already known."""
        existing_id = self._by_hash.get(model.content_hash)
        if existing_id is not None:
            logger.debug(
                "model_store_hash_hit",
                content_hash=model.content_hash,
                model_id=existing_id,
            )
            return self._by_id[existing_id], False
        self._by_id[model.id] = model
        self._by_hash[model.content_hash] = model.id
        logger.debug("model_store_saved", model_id=model.id, content_hash=model.content_hash)
        return model, True

    def get(self, *, model_id: ModelId) -> CanonicalModel | None:
        """Return the stored model for an id, or None."""
        return self._by_id.get(model_id)

    def replace(self, *, model: CanonicalModel) -> None:
        """Overwrite the stored model for its id (same content hash: annotations only)."""
        self._by_id[model.id] = model
        logger.debug("model_store_replaced", model_id=model.id, content_hash=model.content_hash)

    def __len__(self) -> int:
        """The number of stored models."""
        return len(self._by_id)
