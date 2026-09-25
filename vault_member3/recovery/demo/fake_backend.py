"""Disk-backed test double. Nodes share one process; faults are simulated."""
import asyncio
import hashlib
import os
import tempfile
import uuid
from pathlib import Path
from ..adapters import Unavailable, Missing, Conflict, Corrupt, Blocked
from ..models import StorageNode, ObjectMetadata, ReplicaInfo, ReplicaReceipt, CommitResult, HealthCheckResult, now


class FakeBackend:
    def __init__(self, config):
        self.config = config
        self.temp = tempfile.TemporaryDirectory(prefix="vault-demo-")
        self.root = Path(self.temp.name)
        self.registry = {}
        self.records = {}
        self.files = {}
        self.faults = {}
        self.observations = {}
        self.tokens = {}
        self.commit_log = []
        self.max_chunk_seen = 0
        self.transfer_failures = 0
        self.commit_conflicts = 0
        self.coordinator_available = True
        self.before_commit = None
        for i in range(1, 5):
            self.add_node(f"node-{i}")

    def add_node(self, node_id):
        if node_id in self.registry:
            raise Conflict("Node already registered")
        self.registry[node_id] = StorageNode(node_id=node_id, url="http://demo.invalid/" + node_id,
                                              capacity_bytes=1024 * 1024)
        self.faults[node_id] = {}

    def coordinator_check(self):
        if not self.coordinator_available:
            raise Unavailable("Metadata authority unavailable")

    async def guard(self, node_id):
        if node_id not in self.registry:
            raise Unavailable("Unknown demo node")
        fault = self.faults[node_id]
        if fault.get("stop") or fault.get("reject"):
            raise Unavailable("Demo node unreachable")
        if fault.get("timeout"):
            raise Unavailable("Simulated network timeout")
        if fault.get("delay"):
            await asyncio.sleep(fault["delay"])

    async def nodes(self):
        self.coordinator_check()
        return [n.model_copy(deep=True) for n in self.registry.values()]

    async def objects(self, cursor, limit):
        self.coordinator_check()
        keys = sorted(self.records)
        start = int(cursor or 0)
        page = keys[start:start + limit]
        return ([self.records[k].model_copy(deep=True) for k in page],
                str(start + limit) if start + limit < len(keys) else None)

    async def metadata(self, object_id):
        self.coordinator_check()
        if object_id not in self.records or self.records[object_id].deleted:
            raise Missing("Object not found or deleted")
        return self.records[object_id].model_copy(deep=True)

    async def health(self, node_id):
        await self.guard(node_id)
        if self.faults[node_id].get("invalid"):
            raise ValueError("Invalid demo health response")
        node = self.registry[node_id]
        full = self.faults[node_id].get("full")
        return HealthCheckResult(node_id=node_id, status="full" if full else "online",
                                  used_bytes=node.capacity_bytes if full else node.used_bytes,
                                  capacity_bytes=node.capacity_bytes)

    async def publish_health(self, node):
        self.coordinator_check()
        self.observations[node.node_id] = node.model_copy(deep=True)

    async def inspect(self, meta, replica):
        await self.guard(replica.node_id)
        key = (replica.node_id, replica.replica_id)
        if key not in self.files:
            raise Missing("Replica missing")
        path, version = self.files[key]
        digest, size = hashlib.sha256(), 0
        with path.open("rb") as stream:
            while chunk := stream.read(self.config.chunk_size):
                digest.update(chunk)
                size += len(chunk)
                await asyncio.sleep(0)
        return ReplicaReceipt(node_id=replica.node_id, replica_id=replica.replica_id,
                              version=version, size=size, checksum=digest.hexdigest(), durable=True)

    async def read(self, meta, replica):
        await self.guard(replica.node_id)
        key = (replica.node_id, replica.replica_id)
        if key not in self.files:
            raise Missing("Replica missing")
        path, version = self.files[key]
        if version != meta.version:
            raise Corrupt("Source version mismatch")
        with path.open("rb") as stream:
            while chunk := stream.read(self.config.chunk_size):
                await self.guard(replica.node_id)
                yield chunk
                await asyncio.sleep(0)

    async def write(self, meta, node_id, replica_id, chunks):
        await self.guard(node_id)
        if self.faults[node_id].get("full"):
            raise Blocked("Demo node full")
        node = self.registry[node_id]
        # UUID-based paths never include a user-supplied object key.
        final = self.root / hashlib.sha256((node_id + replica_id).encode()).hexdigest()
        temp = self.root / (uuid.uuid4().hex + ".partial")
        digest, size = hashlib.sha256(), 0
        try:
            with temp.open("wb") as stream:
                async for chunk in chunks:
                    await self.guard(node_id)
                    self.max_chunk_seen = max(self.max_chunk_seen, len(chunk))
                    if self.transfer_failures:
                        self.transfer_failures -= 1
                        raise Unavailable("Injected interrupted transfer")
                    size += len(chunk)
                    if size > meta.size or size + node.used_bytes > node.capacity_bytes:
                        raise Blocked("Destination capacity exceeded")
                    stream.write(chunk)
                    digest.update(chunk)
                if size != meta.size or digest.hexdigest() != meta.checksum:
                    raise Corrupt("Destination checksum mismatch")
                stream.flush()
                os.fsync(stream.fileno())
            if (node_id, replica_id) in self.files:
                existing = await self.inspect(meta, ReplicaInfo(node_id=node_id, replica_id=replica_id, version=meta.version))
                if existing.checksum != meta.checksum or existing.version != meta.version:
                    raise Conflict("Immutable replica ID already exists")
            else:
                os.replace(temp, final)
                self.files[(node_id, replica_id)] = (final, meta.version)
                node.used_bytes += size
            return ReplicaReceipt(node_id=node_id, replica_id=replica_id, version=meta.version,
                                  size=size, checksum=digest.hexdigest(), durable=True)
        finally:
            temp.unlink(missing_ok=True)

    async def commit(self, meta, receipt, remove=None):
        self.coordinator_check()
        if self.before_commit:
            callback, self.before_commit = self.before_commit, None
            callback(self, meta)
        if self.commit_conflicts:
            self.commit_conflicts -= 1
            raise Conflict("Injected metadata conflict")
        live = self.records.get(meta.object_id)
        if live is None or live.deleted or not live.committed or live.version != meta.version or live.revision != meta.revision:
            raise Conflict("Version, deletion or metadata revision changed")
        if receipt.version != live.version or receipt.checksum != live.checksum or receipt.size != live.size or not receipt.durable:
            raise Corrupt("Invalid durable receipt")
        replicas = [r for r in live.replicas if r.node_id != receipt.node_id]
        if remove:
            if remove not in live.replicas:
                raise Conflict("Source replica changed")
            replicas = [r for r in replicas if r != remove]
        replicas.append(ReplicaInfo(node_id=receipt.node_id, replica_id=receipt.replica_id, version=receipt.version))
        if remove and len(replicas) < live.required_replicas:
            raise Conflict("Move would violate target replication")
        live.replicas = replicas
        live.revision += 1
        live.updated_at = now()
        token = None
        if remove:
            token = uuid.uuid4().hex
            self.tokens[token] = (meta.object_id, remove.node_id, remove.replica_id)
        self.commit_log.append((meta.object_id, receipt.replica_id))
        return CommitResult(metadata=live.model_copy(deep=True), delete_token=token)

    async def delete(self, meta, replica, token):
        await self.guard(replica.node_id)
        expected = (meta.object_id, replica.node_id, replica.replica_id)
        if self.tokens.get(token) != expected:
            raise Conflict("Invalid retirement token")
        live = self.records.get(meta.object_id)
        if live and any(r.replica_id == replica.replica_id for r in live.replicas):
            raise Conflict("Replica still referenced")
        item = self.files.pop((replica.node_id, replica.replica_id), None)
        if item:
            path, _ = item
            self.registry[replica.node_id].used_bytes -= path.stat().st_size
            path.unlink()

    async def seed(self, object_id="file-123", size=262144, replicas=3):
        """Fake coordinator upload, with constant-size generated chunks."""
        digest = hashlib.sha256()
        block = b"v" * min(size, self.config.chunk_size)
        remaining = size
        while remaining:
            part = block[:remaining]
            digest.update(part)
            remaining -= len(part)
        meta = ObjectMetadata(object_id=object_id, object_key=object_id + ".bin", version="v1", revision=0,
                              size=size, checksum=digest.hexdigest(), required_replicas=self.config.replication_factor)
        self.records[object_id] = meta
        for node_id in list(self.registry)[:replicas]:
            async def chunks():
                left = size
                while left:
                    part = block[:left]
                    left -= len(part)
                    yield part
            receipt = await self.write(meta, node_id, uuid.uuid4().hex, chunks())
            result = await self.commit(meta.model_copy(deep=True), receipt)
            meta = result.metadata
        return await self.metadata(object_id)

    def inject(self, node_id, action, object_id=None, seconds=0):
        if node_id not in self.registry:
            raise Missing("Unknown demo node")
        if action == "restart":
            self.faults[node_id] = {}
        elif action in {"stop", "timeout", "reject", "full", "invalid"}:
            self.faults[node_id][action] = True
        elif action == "delay":
            if not 0 <= seconds <= 60:
                raise ValueError("Demo delay must be between 0 and 60 seconds")
            self.faults[node_id]["delay"] = seconds
        elif action in {"corrupt", "delete-replica", "outdated"}:
            meta = self.records.get(object_id)
            replica = next((r for r in meta.replicas if r.node_id == node_id), None) if meta else None
            if not replica or (node_id, replica.replica_id) not in self.files:
                raise Missing("No matching demo replica")
            path, version = self.files[(node_id, replica.replica_id)]
            if action == "corrupt":
                with path.open("r+b") as stream:
                    first = stream.read(1)
                    stream.seek(0)
                    stream.write(bytes([first[0] ^ 255]) if first else b"x")
            elif action == "outdated":
                self.files[(node_id, replica.replica_id)] = (path, "old-version")
            else:
                self.registry[node_id].used_bytes -= path.stat().st_size
                path.unlink()
                del self.files[(node_id, replica.replica_id)]
        else:
            raise ValueError("Unsupported demo action")

    async def close(self):
        self.temp.cleanup()
