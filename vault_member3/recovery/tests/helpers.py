import asyncio
from contextlib import asynccontextmanager
from ..config import Config
from ..demo.fake_backend import FakeBackend
from ..service import RecoveryService


@asynccontextmanager
async def cluster(seed=True, **options):
    config = Config(database_path=":memory:", retry_delay=0, **options)
    backend = FakeBackend(config)
    service = RecoveryService(backend, config)
    try:
        if seed:
            await backend.seed(size=8192)
        await service.monitor.once(force=True)
        yield service, backend
    finally:
        await service.close()


async def repair(service, object_id="file-123"):
    await service.repairs.start()
    task, _ = service.repairs.enqueue(object_id)
    await asyncio.wait_for(service.repairs.queue.join(), 5)
    return task
