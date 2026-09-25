"""Chunked verification and bounded, paginated scrubbing."""
import asyncio
import hashlib
import tempfile
from contextlib import asynccontextmanager
from .adapters import BackendError, Missing, Unavailable, Corrupt
from .models import IntegrityCheckResult, now


def validate_receipt(meta, replica, receipt):
    if receipt.node_id != replica.node_id or receipt.replica_id != replica.replica_id:
        raise Corrupt("Replica identity mismatch")
    if receipt.version != meta.version:
        raise Corrupt("Outdated replica")
    if receipt.size != meta.size or receipt.checksum != meta.checksum or not receipt.durable:
        raise Corrupt("Replica checksum, size or durability mismatch")


async def verified_chunks(backend, config, meta, replica, progress=None):
    """Destination MUST keep these bytes provisional until this iterator finishes."""
    digest, total = hashlib.sha256(), 0
    async for chunk in backend.read(meta, replica):
        # Defensive slicing also protects adapters that return oversized buffers.
        for offset in range(0, len(chunk), config.chunk_size):
            part = chunk[offset:offset + config.chunk_size]
            total += len(part)
            if total > meta.size or total > config.max_object_bytes:
                raise Corrupt("Source exceeds expected or permitted size")
            digest.update(part)
            if progress:
                progress(total)
            yield part
            if config.transfer_bytes_per_second:
                await asyncio.sleep(len(part) / config.transfer_bytes_per_second)
    if total != meta.size or digest.hexdigest() != meta.checksum:
        raise Corrupt("Source stream checksum mismatch")


@asynccontextmanager
async def verified_download(backend, config, meta, replica):
    """Coordinator integration helper: verify on disk BEFORE serving any bytes."""
    with tempfile.TemporaryFile("w+b") as stream:
        async for part in verified_chunks(backend, config, meta, replica):
            await asyncio.to_thread(stream.write, part)
        stream.seek(0)
        yield stream


class IntegrityChecker:
    def __init__(self, backend, config, metrics):
        self.backend, self.config, self.metrics = backend, config, metrics
        self.summary = {"status": "UNKNOWN", "objects_checked": 0}
        self.samples = []
        self.lock = asyncio.Lock()

    async def check(self, meta):
        result = IntegrityCheckResult(object_id=meta.object_id, version=meta.version,
                                       required_replicas=meta.required_replicas)
        if not meta.committed or meta.deleted:
            result.status = "SKIPPED"
            return result
        for replica in meta.replicas:
            try:
                async with asyncio.timeout(self.config.operation_timeout):
                    receipt = await self.backend.inspect(meta, replica)
                if receipt.version != meta.version or replica.version != meta.version:
                    result.problems[replica.node_id] = "OUTDATED"
                else:
                    validate_receipt(meta, replica, receipt)
                    result.healthy.append(replica)
            except Missing:
                result.problems[replica.node_id] = "MISSING"
            except Corrupt:
                result.problems[replica.node_id] = "CORRUPTED"
            except (Unavailable, TimeoutError):
                result.problems[replica.node_id] = "UNAVAILABLE"
            except (BackendError, ValueError):
                result.problems[replica.node_id] = "INVALID_RESPONSE"
        if len(result.healthy) >= meta.required_replicas:
            result.status = "HEALTHY"
        elif result.healthy:
            result.status = "DEGRADED"
        else:
            result.status = "UNAVAILABLE"
        return result

    async def scan(self, enqueue=None):
        async with self.lock:
            summary = {"status": "RUNNING", "objects_checked": 0, "healthy_objects": 0,
                       "degraded_objects": 0, "unavailable_objects": 0, "healthy_replicas": 0,
                       "missing_replicas": 0, "corrupted_replicas": 0, "outdated_replicas": 0,
                       "unavailable_replicas": 0, "invalid_responses": 0,
                       "repair_tasks_created": 0, "logical_bytes": 0, "started_at": now()}
            self.summary = summary
            self.samples = []
            cursor = None
            try:
                while True:
                    page, next_cursor = await self.backend.objects(cursor, self.config.page_size)
                    for meta in page:
                        result = await self.check(meta)
                        if result.status == "SKIPPED":
                            continue
                        summary["objects_checked"] += 1
                        summary["logical_bytes"] += meta.size
                        summary[result.status.lower() + "_objects"] += 1
                        summary["healthy_replicas"] += len(result.healthy)
                        for problem in result.problems.values():
                            key = "invalid_responses" if problem == "INVALID_RESPONSE" else problem.lower() + "_replicas"
                            summary[key] += 1
                        if result.problems and len(self.samples) < self.config.history_limit:
                            self.samples.append(result.model_dump(mode="json"))
                        if result.status != "HEALTHY" and enqueue:
                            _, created = enqueue(meta.object_id)
                            summary["repair_tasks_created"] += int(created)
                    if next_cursor is None:
                        break
                    if next_cursor == cursor:
                        raise BackendError("Metadata pagination did not advance")
                    cursor = next_cursor
                summary["status"] = "COMPLETED"
                summary["completed_at"] = now()
                self.metrics.event("scrub_completed", **summary)
            except Exception:
                summary["status"] = "FAILED"
                raise
            return summary.copy()
