import asyncio
from .helpers import cluster


def test_rebalance_copies_before_delete():
    """Seed enough files for a beneficial move; old bytes disappear only after safe commit."""
    async def run():
        async with cluster(enable_rebalance=True, rebalance_threshold=0.001) as (service, backend):
            for i in range(3):
                await backend.seed(f"extra-{i}", size=8192)
            backend.add_node("node-5")
            files_before = set(backend.files)
            result = await service.rebalancer.once()
            assert result["status"] == "COMPLETED"
            meta = await backend.metadata(result["object_id"])
            verified = await service.checker.check(meta)
            assert verified.status == "HEALTHY" and len(verified.healthy) == 3
            assert len(files_before - set(backend.files)) == 1
            assert len(set(backend.files) - files_before) == 1
    asyncio.run(run())


def test_rebalance_pause_and_no_ping_pong():
    """A single file would only swap imbalance; no movement, and pause is respected."""
    async def run():
        async with cluster(enable_rebalance=True, rebalance_threshold=0.001) as (service, backend):
            assert (await service.rebalancer.once())["status"] == "IDLE"
            service.rebalancer.paused = True
            assert (await service.rebalancer.once())["status"] == "PAUSED"
    asyncio.run(run())


def test_rebalance_conflict_never_deletes_source():
    """Force a metadata race after copy; original replica remains intact."""
    async def run():
        async with cluster(enable_rebalance=True, rebalance_threshold=0.001) as (service, backend):
            await backend.seed("other", size=8192)
            original_files = set(backend.files)
            backend.commit_conflicts = 1
            assert (await service.rebalancer.once())["status"] == "FAILED"
            assert original_files <= set(backend.files)
    asyncio.run(run())


def test_repair_has_priority_over_rebalance():
    """Queue urgent recovery; background balancing must defer."""
    async def run():
        async with cluster(enable_rebalance=True) as (service, backend):
            service.repairs.enqueue("file-123")
            assert (await service.rebalancer.once())["status"] == "DEFERRED"
    asyncio.run(run())
