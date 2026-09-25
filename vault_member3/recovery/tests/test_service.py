import asyncio
from ..config import Config
from ..metrics import Metrics
from .helpers import cluster


def test_background_loop_repairs_and_shuts_down():
    """Run scheduled workers with short intervals; injected loss is healed without a manual repair call."""
    async def run():
        async with cluster(heartbeat_interval=0.01, repair_interval=0.02, scrub_interval=0.04) as (service, backend):
            backend.inject("node-3", "stop")
            await service.start()
            async with asyncio.timeout(3):
                while service.metrics.counts["repair_completed"] == 0:
                    await asyncio.sleep(0.01)
            assert (await service.checker.check(await backend.metadata("file-123"))).status == "HEALTHY"
            await service.close()
            assert all(t.done() for t in service.background)
    asyncio.run(run())


def test_event_history_survives_restart(tmp_path):
    """Write a bounded SQLite history, reopen it; recent events survive service restart."""
    path = str(tmp_path / "events.sqlite3")
    first = Metrics(path, limit=2)
    for n in range(3):
        first.event("sample", n=n)
    first.close()
    second = Metrics(path, limit=2)
    assert [e["details"]["n"] for e in second.events()] == [2, 1]
    second.close()


def test_environment_configuration(monkeypatch):
    """Set numeric and boolean env values; parse and validate the intended configuration."""
    monkeypatch.setenv("VAULT_HEARTBEAT_INTERVAL", "1.5")
    monkeypatch.setenv("VAULT_ENABLE_REBALANCE", "true")
    assert Config.from_env().heartbeat_interval == 1.5
    assert Config.from_env().enable_rebalance
