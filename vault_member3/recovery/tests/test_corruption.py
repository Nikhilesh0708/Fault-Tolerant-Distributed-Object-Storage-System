import asyncio
import pytest
from ..adapters import Corrupt
from ..integrity_checker import verified_download
from ..models import RepairStatus
from .helpers import cluster, repair


def test_corruption_between_inspection_and_transfer():
    """Corrupt source bytes after a good inspection; streamed SHA-256 must prevent commit."""
    async def run():
        async with cluster() as (service, backend):
            backend.inject("node-3", "stop")
            read = backend.read
            async def bad_read(meta, replica):
                async for chunk in read(meta, replica):
                    yield b"x" * len(chunk)
            backend.read = bad_read
            count = len(backend.commit_log)
            task = await repair(service)
            assert task.status == RepairStatus.FAILED
            assert len(backend.commit_log) == count
            assert not list(backend.root.glob("*.partial"))
    asyncio.run(run())


def test_verified_download_never_yields_bad_file():
    """Corrupt the selected copy; download helper raises before yielding its file handle."""
    async def run():
        async with cluster() as (service, backend):
            meta = await backend.metadata("file-123")
            backend.inject("node-1", "corrupt", "file-123")
            yielded = False
            with pytest.raises(Corrupt):
                async with verified_download(backend, service.config, meta, meta.replicas[0]):
                    yielded = True
            assert not yielded
    asyncio.run(run())


def test_paginated_scrub():
    """Use one object per page; scan each object and categorize its damage exactly once."""
    async def run():
        async with cluster(page_size=1) as (service, backend):
            await backend.seed("second", size=128)
            backend.inject("node-1", "corrupt", "file-123")
            backend.inject("node-2", "outdated", "second")
            summary = await service.checker.scan()
            assert summary["objects_checked"] == 2
            assert summary["corrupted_replicas"] == summary["outdated_replicas"] == 1
    asyncio.run(run())
