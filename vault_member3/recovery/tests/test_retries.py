import asyncio
from ..models import RepairStatus
from .helpers import cluster, repair


def test_interrupted_transfer_retries_cleanly():
    """Interrupt the first transfer; next attempt succeeds with no temporary file leaks."""
    async def run():
        async with cluster() as (service, backend):
            backend.inject("node-3", "stop")
            backend.transfer_failures = 1
            task = await repair(service)
            assert task.status == RepairStatus.COMPLETED and task.attempts == 2
            assert not list(backend.root.glob("*.partial"))
    asyncio.run(run())


def test_metadata_conflict_retries():
    """Reject the first conditional commit; retry reads current metadata and succeeds."""
    async def run():
        async with cluster() as (service, backend):
            backend.inject("node-3", "stop")
            backend.commit_conflicts = 1
            task = await repair(service)
            assert task.status == RepairStatus.COMPLETED and task.attempts == 2
    asyncio.run(run())


def test_concurrent_delete_cannot_resurrect_object():
    """Delete immediately before commit; old repair cannot publish or resurrect the object."""
    async def run():
        async with cluster() as (service, backend):
            backend.inject("node-3", "stop")
            count = len(backend.commit_log)
            def delete(b, meta):
                b.records[meta.object_id].deleted = True
                b.records[meta.object_id].revision += 1
            backend.before_commit = delete
            task = await repair(service)
            assert task.status == RepairStatus.CANCELLED
            assert len(backend.commit_log) == count
    asyncio.run(run())


def test_concurrent_upload_fences_old_repair():
    """Publish a newer version before commit; old bytes must never enter its metadata."""
    async def run():
        async with cluster() as (service, backend):
            backend.inject("node-3", "stop")
            count = len(backend.commit_log)
            def update(b, meta):
                live = b.records[meta.object_id]
                live.version = "v2"
                live.revision += 1
                live.replicas = []
            backend.before_commit = update
            task = await repair(service)
            assert task.status == RepairStatus.BLOCKED
            assert backend.records["file-123"].version == "v2"
            assert not backend.records["file-123"].replicas
            assert len(backend.commit_log) == count
    asyncio.run(run())


def test_metadata_partition_never_commits():
    """Hide coordinator authority; repair must fail boundedly without using cached truth."""
    async def run():
        async with cluster() as (service, backend):
            count = len(backend.commit_log)
            backend.coordinator_available = False
            task = await repair(service)
            assert task.status == RepairStatus.FAILED
            assert task.attempts == service.config.retry_limit
            assert len(backend.commit_log) == count
    asyncio.run(run())


def test_source_disappears_after_inspection():
    """Remove the chosen source at read time; retry a surviving source, not cancel the object."""
    async def run():
        async with cluster() as (service, backend):
            backend.inject("node-3", "stop")
            read = backend.read
            disappeared = False
            async def race(meta, replica):
                nonlocal disappeared
                if not disappeared:
                    disappeared = True
                    backend.inject(replica.node_id, "delete-replica", meta.object_id)
                async for part in read(meta, replica):
                    yield part
            backend.read = race
            task = await repair(service)
            assert task.status == RepairStatus.COMPLETED
            assert task.attempts == 2
    asyncio.run(run())
