"""Small reproducible simulation benchmark; not a production performance claim."""
import asyncio
import json
import statistics
import time
import tracemalloc
from ..config import Config
from ..service import RecoveryService
from ..integrity_checker import verified_download
from .fake_backend import FakeBackend


async def main():
    config = Config(database_path=":memory:", retry_delay=0)
    backend = FakeBackend(config)
    for node in backend.registry.values():
        node.capacity_bytes = 64 * 1024**2
    service = RecoveryService(backend, config)
    size = 8 * 1024**2
    try:
        await backend.seed(size=size)
        await service.monitor.once(force=True)
        meta = await backend.metadata("file-123")
        async def downloads():
            samples = []
            for _ in range(5):
                start = time.perf_counter()
                async with verified_download(backend, config, meta, meta.replicas[0]) as stream:
                    while stream.read(config.chunk_size):
                        await asyncio.sleep(0)
                samples.append((time.perf_counter() - start) * 1000)
            return samples
        baseline = await downloads()
        backend.inject("node-3", "stop")
        for _ in range(config.failure_threshold):
            await service.monitor.once(force=True)
        await service.repairs.start()
        tracemalloc.start()
        start = time.perf_counter()
        task, _ = service.repairs.enqueue("file-123")
        loaded = await downloads()
        await service.repairs.queue.join()
        elapsed = time.perf_counter() - start
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert task.status.value == "COMPLETED"
        print(json.dumps({"simulation_only": True, "object_bytes": size,
                          "chunk_bytes": config.chunk_size, "largest_received_chunk": backend.max_chunk_seen,
                          "python_peak_traced_bytes_during_repair_and_download": peak,
                          "baseline_download_median_ms": statistics.median(baseline),
                          "repair_window_download_median_ms": statistics.median(loaded),
                          "repair_time_seconds": service.metrics.average(service.metrics.repair_times),
                          "entire_measurement_window_seconds": elapsed,
                          "note": "Five local verified downloads; repair may finish before all five. Tracemalloc is not process RSS. Forced probes exclude heartbeat waiting."}, indent=2))
    finally:
        await service.close()


if __name__ == "__main__":
    asyncio.run(main())
