import asyncio
from ..models import RepairStatus
from .helpers import cluster, repair


def test_failure_automatically_enqueues_repair():
    """Stop one of three replicas; reconciliation queues it and restores three copies."""
    async def run():
        async with cluster() as (service, backend):
            backend.inject("node-3", "stop")
            await service.repairs.start()
            scan = await service.scrub()
            assert scan["degraded_objects"] == 1
            await asyncio.wait_for(service.repairs.queue.join(), 5)
            result = await service.checker.check(await backend.metadata("file-123"))
            assert result.status == "HEALTHY"
            assert {r.node_id for r in result.healthy} == {"node-1", "node-2", "node-4"}
    asyncio.run(run())


def test_multiple_failures_block_until_capacity_returns():
    """Stop two nodes; one source survives but only two eligible nodes exist, so repair blocks."""
    async def run():
        async with cluster() as (service, backend):
            backend.inject("node-2", "stop")
            backend.inject("node-3", "stop")
            task = await repair(service)
            assert task.status == RepairStatus.BLOCKED
            assert (await service.checker.check(await backend.metadata("file-123"))).healthy
            backend.inject("node-2", "restart")
            await service.monitor.once(force=True)
            task = await repair(service)
            assert task.status == RepairStatus.COMPLETED
    asyncio.run(run())


def test_no_source_does_not_claim_permanent_loss():
    """Partition every source; report UNAVAILABLE/BLOCKED, never fabricate a good copy."""
    async def run():
        async with cluster() as (service, backend):
            for node in ("node-1", "node-2", "node-3"):
                backend.inject(node, "stop")
            task = await repair(service)
            assert task.status == RepairStatus.BLOCKED
            await service.checker.scan()
            assert service.status()["system_status"] == "UNAVAILABLE"
    asyncio.run(run())
