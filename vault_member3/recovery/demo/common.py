import asyncio
import hashlib
import json
from ..config import Config
from ..service import RecoveryService
from ..integrity_checker import verified_download
from .fake_backend import FakeBackend


async def scenario(kind):
    config = Config(database_path=":memory:", retry_delay=0, enable_rebalance=True)
    backend = FakeBackend(config)
    service = RecoveryService(backend, config)
    try:
        await backend.seed(size=131072)
        await service.start(background=False)
        await service.monitor.once(force=True)
        before = await service.checker.check(await backend.metadata("file-123"))
        assert before.status == "HEALTHY"
        if kind == "failure":
            backend.inject("node-3", "stop")
            for _ in range(config.failure_threshold):
                await service.monitor.once(force=True)
        elif kind == "corruption":
            backend.inject("node-2", "corrupt", "file-123")
        elif kind == "rebalance":
            # Several objects make a single move reduce imbalance rather than swap it.
            for i in range(1, 4):
                await backend.seed(f"extra-{i}", size=131072)
            backend.add_node("node-5")
            backend.inject("node-4", "full")
            result = await service.rebalancer.once()
            assert result["status"] == "COMPLETED", result
            assert result["destination_node"] == "node-5"
            await service.monitor.once(force=True)
            await service.checker.scan()
            print(json.dumps({"demo": kind, "simulation_only": True, "move": result,
                              "summary": service.checker.summary}, indent=2))
            return
        degraded = await service.scrub()
        assert degraded["degraded_objects"] == 1
        await asyncio.wait_for(service.repairs.queue.join(), timeout=10)
        await service.monitor.once(force=True)
        await service.checker.scan()
        meta = await backend.metadata("file-123")
        after = await service.checker.check(meta)
        assert after.status == "HEALTHY" and len(after.healthy) == 3
        digest = hashlib.sha256()
        async with verified_download(backend, config, meta, after.healthy[0]) as stream:
            while part := stream.read(config.chunk_size):
                digest.update(part)
        assert digest.hexdigest() == meta.checksum
        print(json.dumps({"demo": kind, "simulation_only": True, "before": before.status,
                          "fault_detected": degraded, "after": after.model_dump(mode="json"),
                          "download_sha256_verified": True, "max_chunk_bytes": backend.max_chunk_seen,
                          "metrics": service.snapshot()}, indent=2))
    finally:
        await service.close()
