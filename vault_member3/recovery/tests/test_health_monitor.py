import asyncio
import pytest
from ..models import NodeStatus
from .helpers import cluster


def test_normal_heartbeat():
    """Setup online nodes; probe them; expect online state and a timestamp."""
    async def run():
        async with cluster() as (service, backend):
            assert all(n.status == NodeStatus.ONLINE and n.last_seen for n in service.monitor.nodes.values())
            assert backend.observations["node-1"].status == NodeStatus.ONLINE
    asyncio.run(run())


@pytest.mark.parametrize("action", ["stop", "reject", "timeout", "invalid"])
def test_failure_threshold_and_recovery(action):
    """Inject unreachable/invalid responses; require threshold, then two healthy probes."""
    async def run():
        async with cluster() as (service, backend):
            backend.inject("node-3", action)
            await service.monitor.once(force=True)
            assert service.monitor.nodes["node-3"].status == NodeStatus.SUSPECTED
            await service.monitor.once(force=True)
            await service.monitor.once(force=True)
            assert service.monitor.nodes["node-3"].status == NodeStatus.OFFLINE
            backend.inject("node-3", "restart")
            await service.monitor.once(force=True)
            assert service.monitor.nodes["node-3"].status == NodeStatus.RECOVERING
            await service.monitor.once(force=True)
            assert service.monitor.nodes["node-3"].status == NodeStatus.ONLINE
            assert service.metrics.detection_times
    asyncio.run(run())


def test_real_heartbeat_deadline():
    """Delay a fake node beyond the configured deadline; expect a timeout observation."""
    async def run():
        async with cluster(heartbeat_timeout=0.01) as (service, backend):
            backend.inject("node-1", "delay", seconds=0.1)
            await service.monitor.once(force=True)
            assert service.monitor.nodes["node-1"].reason == "Heartbeat timeout"
    asyncio.run(run())


def test_full_join_leave_and_probe_backoff():
    """Add/remove nodes and simulate full storage; monitor tracks eligibility without deleting data."""
    async def run():
        async with cluster() as (service, backend):
            backend.inject("node-4", "full")
            backend.add_node("node-5")
            await service.monitor.once(force=True)
            assert service.monitor.nodes["node-4"].status == NodeStatus.FULL
            assert "node-5" in service.monitor.nodes
            backend.inject("node-5", "stop")
            for _ in range(3):
                await service.monitor.once(force=True)
            failures = service.monitor.nodes["node-5"].failure_count
            await service.monitor.once()
            assert service.monitor.nodes["node-5"].failure_count == failures
            del backend.registry["node-5"]
            await service.monitor.once(force=True)
            assert "node-5" not in service.monitor.nodes
    asyncio.run(run())
