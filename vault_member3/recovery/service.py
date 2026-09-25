import asyncio
import logging
from .config import Config
from .metrics import Metrics
from .health_monitor import HealthMonitor
from .integrity_checker import IntegrityChecker
from .repair_manager import RepairManager
from .rebalancer import Rebalancer
from .models import SystemMetrics, NodeStatus


class RecoveryService:
    def __init__(self, backend, config: Config):
        self.backend, self.config = backend, config
        self.metrics = Metrics(config.database_path, config.history_limit)
        self.monitor = HealthMonitor(backend, config, self.metrics)
        self.checker = IntegrityChecker(backend, config, self.metrics)
        self.repairs = RepairManager(backend, self.monitor, self.checker, config, self.metrics)
        self.rebalancer = Rebalancer(backend, self.monitor, self.checker, self.repairs, config, self.metrics)
        self.background = []
        self.background_errors = {}
        self.closed = False

    async def periodic(self, name, operation, interval):
        while True:
            try:
                await operation()
                self.background_errors.pop(name, None)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.background_errors[name] = "Background check failed; upstream may be unavailable"
                logging.getLogger("vault.recovery").exception("Background %s failed", name)
            await asyncio.sleep(interval)

    async def scrub(self):
        return await self.checker.scan(self.repairs.enqueue)

    async def reconcile(self):
        # Metadata listing is paginated; full scans are deliberately simple for a prototype.
        return await self.scrub()

    async def start(self, background=True):
        await self.repairs.start()
        if background:
            for name, operation, interval in [
                ("health", self.monitor.once, self.config.heartbeat_interval),
                ("reconcile", self.reconcile, self.config.repair_interval),
                ("scrub", self.scrub, self.config.scrub_interval),
                ("rebalance", self.rebalancer.once, self.config.rebalance_interval),
            ]:
                self.background.append(asyncio.create_task(self.periodic(name, operation, interval)))

    async def close(self):
        if self.closed:
            return
        self.closed = True
        for task in self.background:
            task.cancel()
        await asyncio.gather(*self.background, return_exceptions=True)
        await self.repairs.stop()
        await self.backend.close()
        self.metrics.close()

    def snapshot(self):
        summary = self.checker.summary
        counts = self.metrics.counts
        logical = summary.get("logical_bytes", 0)
        used = sum(n.used_bytes for n in self.monitor.nodes.values())
        metrics = SystemMetrics(
            online_nodes=sum(n.status in {NodeStatus.ONLINE, NodeStatus.FULL} for n in self.monitor.nodes.values()),
            offline_nodes=sum(n.status == NodeStatus.OFFLINE for n in self.monitor.nodes.values()),
            suspected_nodes=sum(n.status == NodeStatus.SUSPECTED for n in self.monitor.nodes.values()),
            total_objects=summary.get("objects_checked", 0),
            healthy_objects=summary.get("healthy_objects", 0),
            degraded_objects=summary.get("degraded_objects", 0),
            unavailable_objects=summary.get("unavailable_objects", 0),
            active_repairs=len(self.repairs.pending), completed_repairs=counts["repair_completed"],
            failed_repairs=counts["repair_failed"],
            average_repair_time_seconds=self.metrics.average(self.metrics.repair_times),
            average_detection_time_seconds=self.metrics.average(self.metrics.detection_times),
            storage_overhead=used / logical if logical else None)
        return {**metrics.model_dump(), "counters_since_start": dict(counts),
                "integrity_errors_last_scan": summary.get("corrupted_replicas", 0),
                "rebalance_tasks_recorded": len(self.rebalancer.history),
                "storage_by_node": {k: {"used_bytes": n.used_bytes, "capacity_bytes": n.capacity_bytes}
                                    for k, n in self.monitor.nodes.items()},
                "last_completed_scan": summary.get("completed_at"),
                "background_errors": self.background_errors,
                "note": "Last-scan snapshot, not a linearizable cluster health guarantee. Foreground latency not instrumented."}

    def status(self):
        snapshot = self.snapshot()
        if self.background_errors or self.checker.summary.get("status") != "COMPLETED":
            status = "UNKNOWN"
        elif snapshot["unavailable_objects"]:
            status = "UNAVAILABLE"
        elif snapshot["active_repairs"]:
            status = "REPAIRING"
        elif snapshot["degraded_objects"]:
            status = "DEGRADED"
        else:
            status = "HEALTHY"
        return {"system_status": status, "active_repairs": snapshot["active_repairs"],
                "completed_repairs": snapshot["completed_repairs"], "failed_repairs": snapshot["failed_repairs"],
                "last_completed_scan": snapshot["last_completed_scan"],
                "repairs": [t.model_dump(mode="json") for t in self.repairs.tasks.values()]}
