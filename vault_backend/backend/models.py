"""Shared data models.

These mirror the schemas documented in CONTRACT.md (object metadata, replica
receipts, storage nodes) so the recovery service's HTTP adapter and the
frontend both see the shapes they were promised.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReplicaRef(BaseModel):
    node_id: str
    replica_id: str
    version: str


class ObjectMetadata(BaseModel):
    object_id: str
    object_key: str
    version: str
    revision: int
    size: int
    checksum: str
    required_replicas: int
    committed: bool
    deleted: bool
    replicas: list[ReplicaRef] = Field(default_factory=list)
    updated_at: str = Field(default_factory=utcnow_iso)


class ReplicaReceipt(BaseModel):
    node_id: str
    replica_id: str
    version: str
    size: int
    checksum: str
    durable: bool


class StorageNode(BaseModel):
    node_id: str
    url: str
    status: Literal["online", "full", "offline", "unknown"] = "unknown"
    used_bytes: int = 0
    capacity_bytes: int = 0
    response_ms: Optional[float] = None
    last_seen: Optional[str] = None
    failure_count: int = 0
    reason: Optional[str] = None


class NodeObservation(BaseModel):
    status: Literal["online", "full", "offline", "unknown"]
    last_seen: Optional[str] = None
    response_ms: Optional[float] = None
    failure_count: int = 0
    reason: Optional[str] = None


class ReplicaCommitRequest(BaseModel):
    expected_revision: int
    version: str
    receipt: Optional[ReplicaReceipt] = None
    remove: Optional[ReplicaRef] = None
