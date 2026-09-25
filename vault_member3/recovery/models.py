from datetime import datetime, timezone
from enum import Enum
from pydantic import BaseModel, Field, model_validator


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class NodeStatus(str, Enum):
    ONLINE = "ONLINE"
    SUSPECTED = "SUSPECTED"
    OFFLINE = "OFFLINE"
    RECOVERING = "RECOVERING"
    FULL = "FULL"
    UNKNOWN = "UNKNOWN"


class RepairStatus(str, Enum):
    QUEUED = "QUEUED"
    REPAIRING = "REPAIRING"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StorageNode(BaseModel):
    node_id: str
    url: str
    used_bytes: int = Field(default=0, ge=0)
    capacity_bytes: int = Field(default=1, gt=0)
    status: NodeStatus = NodeStatus.UNKNOWN
    failure_count: int = 0
    last_seen: str | None = None
    response_ms: float | None = None
    reason: str = ""


class ReplicaInfo(BaseModel):
    node_id: str
    replica_id: str
    version: str


class ObjectMetadata(BaseModel):
    object_id: str
    object_key: str
    version: str
    revision: int = Field(ge=0)
    size: int = Field(ge=0)
    checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    required_replicas: int = Field(default=3, gt=0)
    replicas: list[ReplicaInfo] = Field(default_factory=list)
    committed: bool = True
    deleted: bool = False
    updated_at: str = Field(default_factory=now)

    @model_validator(mode="after")
    def unique_nodes(self):
        if len({r.node_id for r in self.replicas}) != len(self.replicas):
            raise ValueError("At most one counted replica per node")
        return self


class HealthCheckResult(BaseModel):
    node_id: str
    status: str
    used_bytes: int = Field(ge=0)
    capacity_bytes: int = Field(gt=0)


class ReplicaReceipt(BaseModel):
    node_id: str
    replica_id: str
    version: str
    size: int = Field(ge=0)
    checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    durable: bool


class CommitResult(BaseModel):
    metadata: ObjectMetadata
    delete_token: str | None = None


class IntegrityCheckResult(BaseModel):
    object_id: str
    version: str
    checked_at: str = Field(default_factory=now)
    required_replicas: int
    healthy: list[ReplicaInfo] = Field(default_factory=list)
    problems: dict[str, str] = Field(default_factory=dict)
    status: str = "UNKNOWN"


class RepairTask(BaseModel):
    task_id: str
    object_id: str
    version: str | None = None
    status: RepairStatus = RepairStatus.QUEUED
    source_node: str | None = None
    destination_node: str | None = None
    failed_node: str | None = None
    progress: float = 0
    attempts: int = 0
    message: str = "Queued"
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)


class RebalanceTask(BaseModel):
    object_id: str
    source_node: str
    destination_node: str
    status: str
    message: str = ""
    created_at: str = Field(default_factory=now)


class SystemMetrics(BaseModel):
    online_nodes: int = 0
    offline_nodes: int = 0
    suspected_nodes: int = 0
    total_objects: int = 0
    healthy_objects: int = 0
    degraded_objects: int = 0
    unavailable_objects: int = 0
    active_repairs: int = 0
    completed_repairs: int = 0
    failed_repairs: int = 0
    average_repair_time_seconds: float | None = None
    average_detection_time_seconds: float | None = None
    storage_overhead: float | None = None
    foreground_latency_ms: float | None = None
