import asyncio
from collections import deque
from .adapters import BackendError, Blocked
from .models import RebalanceTask, NodeStatus


class Rebalancer:
    def __init__(self, backend, monitor, checker, repairs, config, metrics):
        self.backend, self.monitor, self.checker, self.repairs = backend, monitor, checker, repairs
        self.config, self.metrics = config, metrics
        self.paused = not config.enable_rebalance
        self.history = deque(maxlen=config.history_limit)
        self.lock = asyncio.Lock()

    async def once(self):
        if self.paused:
            return {"status": "PAUSED"}
        if self.repairs.pending:
            return {"status": "DEFERRED", "reason": "Repairs take priority"}
        async with self.lock:
            await self.monitor.once(force=True)
            cursor = None
            while True:
                page, next_cursor = await self.backend.objects(cursor, self.config.page_size)
                for item in page:
                    async with self.repairs.lock_for(item.object_id), self.repairs.slots:
                        try:
                            async with asyncio.timeout(self.config.operation_timeout):
                                task = await self.move(item.object_id)
                            if task:
                                self.history.append(task)
                                self.metrics.event("rebalance_completed", **task.model_dump())
                                return task.model_dump()
                        except (BackendError, TimeoutError) as exc:
                            self.metrics.event("rebalance_failed", object_id=item.object_id, reason=str(exc))
                            return {"status": "FAILED", "message": str(exc)}
                if next_cursor is None:
                    return {"status": "IDLE", "reason": "No beneficial safe move"}
                if cursor == next_cursor:
                    raise BackendError("Metadata pagination did not advance")
                cursor = next_cursor

    async def move(self, object_id):
        meta = await self.backend.metadata(object_id)
        result = await self.checker.check(meta)
        if result.status != "HEALTHY" or meta.size == 0:
            return None
        choices = self.repairs.destinations(meta, result.healthy)
        sources = [r for r in result.healthy if r.node_id in self.monitor.nodes and
                   self.monitor.nodes[r.node_id].status in {NodeStatus.ONLINE, NodeStatus.FULL}]
        if not choices or not sources:
            return None
        source = max(sources, key=lambda r: self.monitor.nodes[r.node_id].used_bytes / self.monitor.nodes[r.node_id].capacity_bytes)
        high, low = self.monitor.nodes[source.node_id], choices[0]
        before = high.used_bytes / high.capacity_bytes - low.used_bytes / low.capacity_bytes
        after = abs((high.used_bytes - meta.size) / high.capacity_bytes - (low.used_bytes + meta.size) / low.capacity_bytes)
        if before < self.config.rebalance_threshold or after >= before:
            return None
        receipt = await self.repairs.transfer(meta, source, low.node_id)
        # Recheck remaining copies just before requesting a conditional move.
        checked = await self.checker.check(meta)
        if len([r for r in checked.healthy if r != source]) + 1 < meta.required_replicas:
            raise Blocked("Replica health changed; source will not be removed")
        committed = await self.backend.commit(meta, receipt, remove=source)
        task = RebalanceTask(object_id=object_id, source_node=source.node_id,
                             destination_node=low.node_id, status="COMPLETED")
        if not committed.delete_token:
            task.status = "CLEANUP_PENDING"
            task.message = "Metadata moved; old bytes retained because no retirement token was returned"
            return task
        try:
            await self.backend.delete(meta, source, committed.delete_token)
        except BackendError:
            task.status = "CLEANUP_PENDING"
            task.message = "Metadata moved; coordinator garbage collection must retry old-replica cleanup"
        return task
