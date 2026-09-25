import asyncio
from ..models import RepairStatus
from .helpers import cluster


def test_duplicate_and_queue_backpressure():
    """Fill a one-slot queue; duplicates reuse a job and excess work is rejected for later reconciliation."""
    async def run():
        async with cluster(queue_size=1) as (service, backend):
            first, created = service.repairs.enqueue("file-123")
            second, again = service.repairs.enqueue("file-123")
            third, overflow = service.repairs.enqueue("other")
            assert created and not again and first is second
            assert third is None and not overflow
    asyncio.run(run())


def test_concurrent_repairs_respect_limit_and_isolate_failure():
    """Repair three objects with two slots, one without sources; valid jobs still finish."""
    async def run():
        async with cluster(max_concurrent_repairs=2) as (service, backend):
            await backend.seed("other", size=1024)
            await backend.seed("lost", size=1024)
            for n in ("node-1", "node-2", "node-3"):
                backend.inject(n, "delete-replica", "lost")
            backend.inject("node-3", "stop")
            active, peak = 0, 0
            original = backend.write
            async def measured(*args):
                nonlocal active, peak
                active += 1
                peak = max(peak, active)
                try:
                    await asyncio.sleep(0.01)
                    return await original(*args)
                finally:
                    active -= 1
            backend.write = measured
            tasks = [service.repairs.enqueue(k)[0] for k in ("file-123", "other", "lost")]
            await service.repairs.start()
            await asyncio.wait_for(service.repairs.queue.join(), 5)
            assert peak <= 2
            assert [t.status for t in tasks] == [RepairStatus.COMPLETED, RepairStatus.COMPLETED, RepairStatus.BLOCKED]
            assert not service.repairs.pending
    asyncio.run(run())
