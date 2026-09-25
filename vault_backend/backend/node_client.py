"""Talks to storage nodes over HTTP using the CONTRACT.md node endpoints."""
from __future__ import annotations

import hashlib
import time
import uuid
from pathlib import Path
from typing import AsyncIterator, Optional

import httpx

from .models import ReplicaReceipt, StorageNode
from .store import STORE

NODE_TIMEOUT = httpx.Timeout(10.0, read=30.0)
CHUNK_SIZE = 65536


async def probe_node_health(client: httpx.AsyncClient, node_id: str, url: str) -> StorageNode:
    started = time.monotonic()
    try:
        resp = await client.get(f"{url}/health", timeout=2.0)
        resp.raise_for_status()
        data = resp.json()
        elapsed_ms = (time.monotonic() - started) * 1000
        return StorageNode(
            node_id=node_id,
            url=url,
            status=data.get("status", "unknown"),
            used_bytes=data.get("used_bytes", 0),
            capacity_bytes=data.get("capacity_bytes", 0),
            response_ms=round(elapsed_ms, 2),
            last_seen=None,
        )
    except Exception as exc:  # noqa: BLE001 - node may simply be down
        obs = STORE.nodes.get(node_id)
        last_obs = obs.last_observation if obs else None
        return StorageNode(
            node_id=node_id,
            url=url,
            status="offline",
            used_bytes=0,
            capacity_bytes=0,
            response_ms=None,
            last_seen=last_obs.last_seen if last_obs else None,
            failure_count=(last_obs.failure_count if last_obs else 0) + 1,
            reason=str(exc),
        )


async def list_live_nodes() -> list[StorageNode]:
    urls = STORE.known_node_urls()
    async with httpx.AsyncClient() as client:
        results = []
        for node_id, url in urls.items():
            results.append(await probe_node_health(client, node_id, url))
        return results


async def healthy_nodes_for_write(exclude: Optional[set[str]] = None) -> list[StorageNode]:
    exclude = exclude or set()
    nodes = await list_live_nodes()
    candidates = [n for n in nodes if n.status == "online" and n.node_id not in exclude]
    candidates.sort(key=lambda n: (n.used_bytes / n.capacity_bytes) if n.capacity_bytes else 0)
    return candidates


async def replicate_file_to_node(
    client: httpx.AsyncClient, node: StorageNode, object_id: str, version: str, file_path: Path, expected_hash: str, expected_size: int
) -> ReplicaReceipt:
    replica_id = f"gen-{uuid.uuid4().hex[:12]}"

    async def _chunks() -> AsyncIterator[bytes]:
        with file_path.open("rb") as fh:
            while True:
                chunk = fh.read(CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk

    resp = await client.post(
        f"{node.url}/objects/{object_id}/replicate",
        params={"version": version, "replica_id": replica_id},
        headers={
            "Content-Type": "application/octet-stream",
            "X-Expected-SHA256": expected_hash,
            "X-Expected-Size": str(expected_size),
            "Idempotency-Key": replica_id,
        },
        content=_chunks(),
        timeout=NODE_TIMEOUT,
    )
    resp.raise_for_status()
    return ReplicaReceipt(**resp.json())


def hash_file(file_path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with file_path.open("rb") as fh:
        while True:
            chunk = fh.read(CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size
