import asyncio
import hashlib
import random
import time
import uuid
from collections import OrderedDict
from .adapters import BackendError, Blocked, Missing, Conflict
from .integrity_checker import verified_chunks, validate_receipt
from .models import RepairTask, RepairStatus, ReplicaInfo, NodeStatus, now


class RepairManager:
    def __init__(self, backend, monitor, checker, config, metrics):
        self.backend, self.monitor, self.checker = backend, monitor, checker
        self.config, self.metrics = config, metrics
        self.queue = asyncio.Queue(maxsize=config.queue_size)
        self.pending = {}
        self.tasks = OrderedDict()
        self.slots = asyncio.Semaphore(config.max_concurrent_repairs)
        self.locks = [asyncio.Lock() for _ in range(128)]
        self.workers = []
        self.reserved = {}

    def lock_for(self, object_id):
        return self.locks[int(hashlib.sha256(object_id.encode()).hexdigest()[:8], 16) % len(self.locks)]

    def enqueue(self, object_id):
        if object_id in self.pending:
            return self.tasks[self.pending[object_id]], False
        if self.queue.full():
            self.metrics.counts["queue_backpressure"] += 1
            return None, False
        task = RepairTask(task_id=uuid.uuid4().hex, object_id=object_id)
        self.pending[object_id] = task.task_id
        self.tasks[task.task_id] = task
        self.queue.put_nowait(task)
        self._trim_history()
        return task, True

    def _trim_history(self):
        if len(self.tasks) <= self.config.history_limit + len(self.pending):
            return
        active_ids = set(self.pending.values())
        for key in list(self.tasks):
            if key not in active_ids:
                self.tasks.pop(key)
            if len(self.tasks) <= self.config.history_limit + len(self.pending):
                break

    async def start(self):
        if not self.workers:
            self.workers = [asyncio.create_task(self.worker()) for _ in range(self.config.max_concurrent_repairs)]

    async def stop(self):
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.workers.clear()

    async def worker(self):
        while True:
            task = await self.queue.get()
            try:
                async with self.lock_for(task.object_id), self.slots:
                    await self.run(task)
            except asyncio.CancelledError:
                task.status = RepairStatus.CANCELLED
                task.message = "Shutdown; reconciliation will rediscover incomplete repairs"
                raise
            except Exception:
                task.status = RepairStatus.FAILED
                task.message = "Unexpected recovery error; see service logs"
                self.metrics.event("repair_failed", task_id=task.task_id, reason="unexpected_error")
            finally:
                task.updated_at = now()
                self.pending.pop(task.object_id, None)
                self.queue.task_done()

    async def run(self, task):
        started = time.monotonic()
        task.status = RepairStatus.REPAIRING
        for attempt in range(1, self.config.retry_limit + 1):
            task.attempts = attempt
            try:
                async with asyncio.timeout(self.config.operation_timeout):
                    await self.heal(task)
                task.status = RepairStatus.COMPLETED
                task.progress = 100
                task.message = "Required replicas verified"
                self.metrics.repair_times.append(time.monotonic() - started)
                self.metrics.event("repair_completed", object_id=task.object_id, attempts=attempt)
                return
            except Missing:
                task.status = RepairStatus.CANCELLED
                task.message = "Object no longer exists; repair cancelled"
                return
            except Blocked as exc:
                task.status = RepairStatus.BLOCKED
                task.message = str(exc)
                self.metrics.event("repair_blocked", object_id=task.object_id, reason=str(exc))
                return
            except (BackendError, TimeoutError) as exc:
                task.message = str(exc) or "Repair deadline exceeded"
                task.updated_at = now()
                if attempt < self.config.retry_limit:
                    self.metrics.event("repair_retry", object_id=task.object_id, attempt=attempt)
                    await asyncio.sleep(self.config.retry_delay * 2**(attempt - 1) * random.uniform(0.8, 1.2))
        task.status = RepairStatus.FAILED
        self.metrics.event("repair_failed", object_id=task.object_id, reason=task.message)

    def destinations(self, meta, healthy):
        healthy_ids = {r.node_id for r in healthy}
        choices = []
        for node in self.monitor.nodes.values():
            free = node.capacity_bytes - node.used_bytes - self.reserved.get(node.node_id, 0)
            if node.status == NodeStatus.ONLINE and node.node_id not in healthy_ids and free >= meta.size:
                choices.append(node)
        return sorted(choices, key=lambda n: ((n.used_bytes + self.reserved.get(n.node_id, 0)) / n.capacity_bytes, n.node_id))

    async def transfer(self, meta, source, destination, task=None):
        if meta.size > self.config.max_object_bytes:
            raise Blocked("Object exceeds configured transfer-size limit")
        # New immutable generation per attempt; never overwrite a live replica.
        replica_id = (uuid.uuid5(uuid.UUID(task.task_id), meta.version + ":" + meta.checksum + ":" + destination).hex
                      if task else uuid.uuid4().hex)
        replica = ReplicaInfo(node_id=destination, replica_id=replica_id, version=meta.version)
        self.reserved[destination] = self.reserved.get(destination, 0) + meta.size
        try:
            # A previous commit may have timed out after a successful copy.
            try:
                existing = await self.backend.inspect(meta, replica)
                validate_receipt(meta, replica, existing)
                return existing
            except Missing:
                pass
            def progress(count):
                if task:
                    task.progress = min(95, 95 * count / max(1, meta.size))
                    task.updated_at = now()
            receipt = await self.backend.write(meta, destination, replica_id,
                         verified_chunks(self.backend, self.config, meta, source, progress))
            validate_receipt(meta, replica, receipt)
            # Recompute the actual destination bytes, not just its cached metadata.
            validate_receipt(meta, replica, await self.backend.inspect(meta, replica))
            return receipt
        finally:
            self.reserved[destination] -= meta.size

    async def heal(self, task):
        meta = await self.backend.metadata(task.object_id)
        if not meta.committed or meta.deleted:
            raise Missing("Object is not committed")
        task.version = meta.version
        result = await self.checker.check(meta)
        if len(result.healthy) >= meta.required_replicas:
            return
        if not result.healthy:
            raise Blocked("No verified readable source; data may be temporarily unreachable")
        # One refresh per attempt, not one registry request per candidate.
        await self.monitor.once(force=True)
        while len(result.healthy) < meta.required_replicas:
            choices = self.destinations(meta, result.healthy)
            if not choices:
                raise Blocked("Not enough distinct healthy nodes or free capacity")
            source = result.healthy[0]
            destination = choices[0].node_id
            task.source_node, task.destination_node = source.node_id, destination
            task.failed_node = next(iter(result.problems), None)
            task.message = "Copying and verifying immutable replica"
            try:
                receipt = await self.transfer(meta, source, destination, task)
            except Missing as exc:
                raise Conflict("Selected replica disappeared; refresh authoritative metadata") from exc
            # CAS also checks tombstones and immutable object version on the server.
            bad = next((r for r in meta.replicas if r.node_id in result.problems), None)
            # Retire an invalid location when the metadata already has its target count.
            remove = bad if len(meta.replicas) >= meta.required_replicas else None
            commit = await self.backend.commit(meta, receipt, remove=remove)
            if remove and commit.delete_token:
                try:
                    await self.backend.delete(meta, remove, commit.delete_token)
                except BackendError:
                    self.metrics.event("cleanup_pending", object_id=meta.object_id,
                                       replica_id=remove.replica_id)
            meta = commit.metadata
            result = await self.checker.check(meta)
            if not result.healthy:
                raise Conflict("All sources became unreachable during repair")
