"""Team integration boundary. Coordinator, not this worker, owns object truth."""
from collections.abc import AsyncIterator
from typing import Protocol
from .models import ObjectMetadata, StorageNode, ReplicaInfo, ReplicaReceipt, CommitResult, HealthCheckResult


class BackendError(Exception):
    """Sanitized upstream error; safe to display."""


class Unavailable(BackendError):
    pass


class Missing(BackendError):
    pass


class Conflict(BackendError):
    pass


class Corrupt(BackendError):
    pass


class Blocked(BackendError):
    pass


class Backend(Protocol):
    async def nodes(self) -> list[StorageNode]: ...
    async def objects(self, cursor: str | None, limit: int) -> tuple[list[ObjectMetadata], str | None]: ...
    async def metadata(self, object_id: str) -> ObjectMetadata: ...
    async def health(self, node_id: str) -> HealthCheckResult: ...
    async def publish_health(self, node: StorageNode) -> None: ...
    async def inspect(self, meta: ObjectMetadata, replica: ReplicaInfo) -> ReplicaReceipt: ...
    def read(self, meta: ObjectMetadata, replica: ReplicaInfo) -> AsyncIterator[bytes]: ...
    async def write(self, meta: ObjectMetadata, node_id: str, replica_id: str,
                    chunks: AsyncIterator[bytes]) -> ReplicaReceipt: ...
    async def commit(self, meta: ObjectMetadata, receipt: ReplicaReceipt,
                     remove: ReplicaInfo | None = None) -> CommitResult: ...
    async def delete(self, meta: ObjectMetadata, replica: ReplicaInfo, token: str) -> None: ...
    async def close(self) -> None: ...
