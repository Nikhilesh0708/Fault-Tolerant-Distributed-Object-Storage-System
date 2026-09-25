"""A timeout means unreachable from here, never proof of permanent data loss."""
import time
from .models import NodeStatus, StorageNode, HealthCheckResult, now


class FailureDetector:
    def __init__(self, config, metrics):
        self.config, self.metrics = config, metrics
        self.first_failure = {}

    def success(self, node: StorageNode, response: HealthCheckResult, elapsed_ms: float):
        old = node.status
        node.used_bytes = response.used_bytes
        node.capacity_bytes = response.capacity_bytes
        node.response_ms = elapsed_ms
        node.last_seen = now()
        node.failure_count = 0
        node.reason = ""
        self.first_failure.pop(node.node_id, None)
        if response.status.lower() == "full" or node.used_bytes >= node.capacity_bytes:
            node.status = NodeStatus.FULL
        else:
            node.status = NodeStatus.RECOVERING if old == NodeStatus.OFFLINE else NodeStatus.ONLINE
        if node.status != old:
            self.metrics.event("node_state", node_id=node.node_id, status=node.status.value)

    def failure(self, node: StorageNode, reason: str):
        old = node.status
        self.first_failure.setdefault(node.node_id, time.monotonic())
        node.failure_count += 1
        node.reason = reason
        node.status = (NodeStatus.OFFLINE if node.failure_count >= self.config.failure_threshold else NodeStatus.SUSPECTED)
        if old != node.status:
            if node.status == NodeStatus.OFFLINE:
                self.metrics.detection_times.append(time.monotonic() - self.first_failure[node.node_id])
            self.metrics.event("node_offline" if node.status == NodeStatus.OFFLINE else "node_suspected",
                               node_id=node.node_id, reason=reason, failure_count=node.failure_count,
                               last_seen=node.last_seen)
