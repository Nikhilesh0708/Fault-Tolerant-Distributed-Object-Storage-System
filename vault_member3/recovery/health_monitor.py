import asyncio
import time
from .adapters import BackendError
from .failure_detector import FailureDetector
from .models import NodeStatus


class HealthMonitor:
    def __init__(self, backend, config, metrics):
        self.backend, self.config, self.metrics = backend, config, metrics
        self.detector = FailureDetector(config, metrics)
        self.nodes = {}
        self.next_probe = {}
        self.lock = asyncio.Lock()
        self.last_error = None

    async def once(self, force=False):
        async with self.lock:
            registered = await self.backend.nodes()
            ids = {n.node_id for n in registered}
            for node_id in list(self.nodes):
                if node_id not in ids:
                    self.nodes.pop(node_id)
                    self.next_probe.pop(node_id, None)
                    self.detector.first_failure.pop(node_id, None)
            for node in registered:
                if node.node_id not in self.nodes:
                    node.status = NodeStatus.UNKNOWN
                    self.nodes[node.node_id] = node
                self.nodes[node.node_id].url = node.url
            # Fixed-size batches bound both live tasks and HTTP requests.
            for offset in range(0, len(registered), self.config.max_health_checks):
                await asyncio.gather(*(self._check(self.nodes[n.node_id], force)
                                       for n in registered[offset:offset + self.config.max_health_checks]))
            self.last_error = None

    async def _check(self, node, force):
        if not force and time.monotonic() < self.next_probe.get(node.node_id, 0):
            return
        started = time.monotonic()
        try:
            async with asyncio.timeout(self.config.heartbeat_timeout):
                response = await self.backend.health(node.node_id)
            if response.node_id != node.node_id or response.status.lower() not in {"online", "full"}:
                raise BackendError("Invalid health response")
            self.detector.success(node, response, (time.monotonic() - started) * 1000)
        except (BackendError, TimeoutError, ValueError) as exc:
            self.detector.failure(node, "Heartbeat timeout" if isinstance(exc, TimeoutError) else str(exc))
        interval = self.config.offline_probe_interval if node.status == NodeStatus.OFFLINE else self.config.heartbeat_interval
        self.next_probe[node.node_id] = time.monotonic() + interval
        # Coordinator must consume these observations for its placement policy.
        try:
            await self.backend.publish_health(node)
        except BackendError:
            self.metrics.event("health_publish_failed", node_id=node.node_id)
