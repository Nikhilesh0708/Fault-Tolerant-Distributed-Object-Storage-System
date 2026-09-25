"""The metadata authority.

This is the single source of truth for object metadata and node registry
state. It is intentionally a single in-process store guarded by one asyncio
lock: CONTRACT.md is explicit that this prototype does not implement
consensus or a distributed quorum, so we don't pretend to. Every mutation
that matters (replica add/remove, delete) goes through compare-and-swap on
`revision` so a stale caller (an old repair task, a race between two
requests) gets a 409/412 instead of silently corrupting state.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from .models import NodeObservation, ObjectMetadata, ReplicaRef, StorageNode, utcnow_iso


class ConflictError(Exception):
    """Raised on revision/version/tombstone mismatch. Maps to 409/412."""


class NotFoundError(Exception):
    """Raised when an object is absent or logically deleted."""


@dataclass
class NodeEntry:
    node_id: str
    url: str
    last_observation: Optional[NodeObservation] = None


@dataclass
class Store:
    nodes: dict[str, NodeEntry] = field(default_factory=dict)
    objects: dict[str, ObjectMetadata] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def register_node(self, node_id: str, url: str) -> None:
        self.nodes.setdefault(node_id, NodeEntry(node_id=node_id, url=url))

    async def record_observation(self, node_id: str, obs: NodeObservation) -> None:
        async with self._lock:
            if node_id not in self.nodes:
                raise NotFoundError(f"unknown node {node_id}")
            self.nodes[node_id].last_observation = obs

    def known_node_urls(self) -> dict[str, str]:
        return {nid: entry.url for nid, entry in self.nodes.items()}

    async def list_metadata(self, limit: int, cursor: Optional[str]) -> tuple[list[ObjectMetadata], Optional[str]]:
        async with self._lock:
            ids = sorted(self.objects.keys())
            start = 0
            if cursor:
                try:
                    start = ids.index(cursor) + 1
                except ValueError:
                    start = 0
            page_ids = ids[start : start + limit]
            page = [self.objects[i] for i in page_ids]
            next_cursor = page_ids[-1] if len(ids) > start + limit else None
            return page, next_cursor

    async def get_metadata(self, object_id: str, include_deleted: bool = False) -> ObjectMetadata:
        async with self._lock:
            meta = self.objects.get(object_id)
            if meta is None or (meta.deleted and not include_deleted):
                raise NotFoundError(object_id)
            return meta

    def new_object_id(self) -> str:
        return f"file-{uuid.uuid4().hex[:12]}"

    async def create_object(
        self,
        *,
        object_id: str,
        object_key: str,
        version: str,
        size: int,
        checksum: str,
        required_replicas: int,
        replicas: list[ReplicaRef],
    ) -> ObjectMetadata:
        async with self._lock:
            meta = ObjectMetadata(
                object_id=object_id,
                object_key=object_key,
                version=version,
                revision=1,
                size=size,
                checksum=checksum,
                required_replicas=required_replicas,
                committed=True,
                deleted=False,
                replicas=replicas,
                updated_at=utcnow_iso(),
            )
            self.objects[object_id] = meta
            return meta

    async def commit_replica_change(
        self,
        object_id: str,
        *,
        expected_revision: int,
        version: str,
        add: Optional[ReplicaRef],
        remove: Optional[ReplicaRef],
    ) -> ObjectMetadata:
        """Atomically add/replace a replica reference. Raises ConflictError or NotFoundError."""
        async with self._lock:
            meta = self.objects.get(object_id)
            if meta is None:
                raise NotFoundError(object_id)
            if meta.deleted:
                raise ConflictError("object has a deletion tombstone")
            if meta.revision != expected_revision:
                raise ConflictError(f"revision mismatch: have {meta.revision}, expected {expected_revision}")
            if meta.version != version:
                raise ConflictError(f"stale version: current committed version is {meta.version}")

            replicas = list(meta.replicas)

            if remove is not None:
                match = next(
                    (r for r in replicas if r.node_id == remove.node_id and r.replica_id == remove.replica_id and r.version == remove.version),
                    None,
                )
                if match is None:
                    raise ConflictError("replica to remove no longer matches authoritative state")
                replicas.remove(match)

            if add is not None:
                if any(r.node_id == add.node_id for r in replicas):
                    raise ConflictError(f"node {add.node_id} already holds a replica of this object")
                replicas.append(add)

            # Idempotent replay: nothing actually changed vs. current state.
            if replicas == meta.replicas:
                return meta

            new_meta = meta.model_copy(
                update={
                    "replicas": replicas,
                    "revision": meta.revision + 1,
                    "updated_at": utcnow_iso(),
                }
            )
            self.objects[object_id] = new_meta
            return new_meta

    async def tombstone(self, object_id: str) -> ObjectMetadata:
        async with self._lock:
            meta = self.objects.get(object_id)
            if meta is None or meta.deleted:
                raise NotFoundError(object_id)
            new_meta = meta.model_copy(
                update={"deleted": True, "revision": meta.revision + 1, "updated_at": utcnow_iso()}
            )
            self.objects[object_id] = new_meta
            return new_meta


STORE = Store()
