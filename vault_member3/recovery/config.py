"""Environment-based configuration; no secrets are required for demo mode."""
import os
from dataclasses import dataclass, fields


@dataclass(frozen=True)
class Config:
    mode: str = "demo"
    coordinator_url: str = "http://127.0.0.1:8000"
    backend_token: str = ""
    admin_token: str = ""
    database_path: str = "recovery-events.sqlite3"
    heartbeat_interval: float = 5.0
    heartbeat_timeout: float = 2.0
    failure_threshold: int = 3
    offline_probe_interval: float = 30.0
    request_timeout: float = 30.0
    operation_timeout: float = 600.0
    repair_interval: float = 10.0
    retry_limit: int = 3
    retry_delay: float = 0.5
    scrub_interval: float = 60.0
    rebalance_interval: float = 120.0
    max_concurrent_repairs: int = 3
    max_health_checks: int = 8
    queue_size: int = 100
    page_size: int = 100
    history_limit: int = 500
    replication_factor: int = 3
    chunk_size: int = 65536
    max_object_bytes: int = 10 * 1024**3
    transfer_bytes_per_second: int = 0
    rebalance_threshold: float = 0.20
    enable_rebalance: bool = False

    def __post_init__(self):
        if self.mode not in {"demo", "http"}:
            raise ValueError("VAULT_MODE must be demo or http")
        for name in ("heartbeat_interval", "heartbeat_timeout", "failure_threshold",
                     "offline_probe_interval", "request_timeout", "operation_timeout",
                     "repair_interval", "retry_limit", "scrub_interval", "rebalance_interval",
                     "max_concurrent_repairs", "max_health_checks", "queue_size", "page_size",
                     "history_limit", "replication_factor", "chunk_size", "max_object_bytes"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.retry_delay < 0 or self.transfer_bytes_per_second < 0:
            raise ValueError("Retry delay and bandwidth limit cannot be negative")
        if not 0 < self.rebalance_threshold <= 1:
            raise ValueError("rebalance_threshold must be in (0, 1]")
        if self.mode == "http" and not self.admin_token:
            raise ValueError("HTTP mode requires VAULT_ADMIN_TOKEN")

    @classmethod
    def from_env(cls):
        values = {}
        for field in fields(cls):
            raw = os.getenv("VAULT_" + field.name.upper())
            if raw is not None:
                if field.type is bool:
                    if raw.lower() not in {"true", "false", "1", "0"}:
                        raise ValueError(f"Invalid boolean for {field.name}")
                    values[field.name] = raw.lower() in {"true", "1"}
                else:
                    values[field.name] = field.type(raw)
        return cls(**values)
