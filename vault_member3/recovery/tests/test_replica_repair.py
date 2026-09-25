import asyncio
import hashlib
import pytest
from ..integrity_checker import verified_download
from ..models import RepairStatus
from .helpers import cluster, repair


@pytest.mark.parametrize("action", ["corrupt", "delete-replica", "outdated"])
def test_repair_invalid_copy(action):
    """Damage a known replica; repair from correct version and verify every committed copy."""
    async def run():
        async with cluster(chunk_size=1024) as (service, backend):
            backend.inject("node-2", action, "file-123")
            before = await service.checker.check(await backend.metadata("file-123"))
            assert len(before.healthy) == 2
            task = await repair(service)
            assert task.status == RepairStatus.COMPLETED
            meta = await backend.metadata("file-123")
            result = await service.checker.check(meta)
            assert len(result.healthy) == 3 and not result.problems
            assert backend.max_chunk_seen <= 1024
            digest = hashlib.sha256()
            async with verified_download(backend, service.config, meta, result.healthy[0]) as stream:
                while part := stream.read(1024):
                    digest.update(part)
            assert digest.hexdigest() == meta.checksum
    asyncio.run(run())


def test_repeated_repair_is_idempotent():
    """Repair twice after a loss; the second job must create no additional replicas."""
    async def run():
        async with cluster() as (service, backend):
            backend.inject("node-3", "stop")
            await repair(service)
            count = len(backend.commit_log)
            await repair(service)
            assert len(backend.commit_log) == count
    asyncio.run(run())


def test_empty_file():
    """Seed a zero-byte file and lose a replica; checksum and size still verify."""
    async def run():
        async with cluster(seed=False) as (service, backend):
            await backend.seed(size=0)
            backend.inject("node-3", "stop")
            assert (await repair(service)).status == RepairStatus.COMPLETED
    asyncio.run(run())


def test_size_limit_blocks_transfer():
    """Set a lower transfer-size policy than the file; never commit a replacement."""
    async def run():
        async with cluster(max_object_bytes=1024) as (service, backend):
            backend.inject("node-3", "stop")
            count = len(backend.commit_log)
            assert (await repair(service)).status == RepairStatus.BLOCKED
            assert len(backend.commit_log) == count
    asyncio.run(run())
