# Backend integration contract

This is a **proposed, implemented client contract**, not a claim that your teammates' servers already expose it. No backend source was attached. `recovery/http_adapter.py` contains all production HTTP communication; adapt that file after agreeing on the contract.

## Ownership

| Component | Owner and responsibility |
|---|---|
| Streamlit UI | Frontend teammate (Member 1 in your supplied template); consumes monitoring JSON |
| Metadata authority and storage nodes | Backend teammate; owns registration, object versions, writes, placement, durable bytes, conditional commits and deletion authorization |
| Recovery service | Member 3; observes, verifies, copies, proposes conditional replica updates, tests and reports |

## Non-negotiable semantics

1. `object_id` is an opaque identifier. `object_key` is display metadata, never a filesystem path. `version` identifies immutable content; a new upload has a different version.
2. `revision` changes on every metadata mutation, including replica-set changes and deletions. Coordinator reads and compare-and-swap commits must use authoritative state.
3. A commit must atomically check the expected revision, live committed version, and absence of a deletion tombstone. Return 409/412 on conflict. Do not silently accept stale repairs.
4. Each replica has a unique immutable `replica_id`; at most one counted copy per node. An old retired ID must never be resurrected. A node returning from a partition is not authoritative.
5. A replica upload is provisional until its full size and SHA-256 match. Persist data, atomically publish it on the node, and satisfy the chosen fsync/durability policy before returning `durable: true`.
6. Checksum inspection with `recompute=true` must read actual stored bytes. Echoing a cached checksum would hide corruption.
7. Rebalancing may retire a source only after a verified destination exists. Metadata replacement is atomic. A coordinator-issued token authorizes deletion of one exact retired replica generation, not an entire object key.
8. A coordinator that loses its required metadata quorum must reject authoritative mutations. This recovery client does not implement consensus, quorum selection, or write acknowledgement policy.
9. If metadata commit fails after copying, retain the unreferenced copy until coordinator-controlled garbage collection can establish it is safe to delete. Never delete an ambiguous copy based on a timeout alone.
10. Health observations are from the recovery worker's network perspective. Coordinator placement must apply its own policy to them. This client cannot prove that a timed-out node is permanently dead.

## Schemas

Object metadata:

```json
{
  "object_id": "file-123",
  "object_key": "notes.pdf",
  "version": "v1",
  "revision": 7,
  "size": 8192,
  "checksum": "<64 lowercase SHA-256 hex characters>",
  "required_replicas": 3,
  "committed": true,
  "deleted": false,
  "replicas": [
    {"node_id": "node-1", "replica_id": "generation-a", "version": "v1"},
    {"node_id": "node-2", "replica_id": "generation-b", "version": "v1"}
  ],
  "updated_at": "2026-09-25T20:00:00+00:00"
}
```

The checksum placeholder above is explanatory; actual requests require real 64-character hex values. Timestamps use UTC ISO 8601. Object policy overrides the demo's default replication factor.

Replica receipt:

```json
{
  "node_id": "node-4",
  "replica_id": "generation-c",
  "version": "v1",
  "size": 8192,
  "checksum": "<same SHA-256 as authoritative metadata>",
  "durable": true
}
```

## Coordinator endpoints

| Method and route | Request | Response |
|---|---|---|
| `GET /nodes` | None | `{"nodes": [StorageNode, ...]}` |
| `GET /metadata?limit=100&cursor=...` | Omit cursor initially | `{"objects": [ObjectMetadata, ...], "next_cursor": "opaque-next"}`; final cursor is null |
| `GET /metadata/{object_id}` | None | ObjectMetadata; 404 for absent/deleted object |
| `POST /nodes/{node_id}/observations` | StorageNode with observed status, last_seen, response_ms, failure_count, reason | 200/204 |
| `POST /metadata/{object_id}/replica` | Conditional request below | `{"metadata": ObjectMetadata, "delete_token": "optional-authority-token"}` |

`StorageNode` requires `node_id`, `url`; capacity/usage and monitoring fields are defined in `models.py`. Return unique node IDs. Node URLs come only from the trusted coordinator registry. Production deployments should additionally enforce service-network allowlists at the network boundary.

Replica commit request (header `If-Match: 7`):

```json
{
  "expected_revision": 7,
  "version": "v1",
  "receipt": {"node_id": "node-4", "replica_id": "generation-c", "version": "v1", "size": 8192, "checksum": "<expected hash>", "durable": true},
  "remove": {"node_id": "node-3", "replica_id": "generation-old", "version": "v1"}
}
```

`remove` is null for an addition. When present, remove exactly that generation and add the receipt atomically; reject if the source reference changed. The coordinator must validate the receipt, enforce its placement policy, and return a scoped retirement token if physical deletion is authorized. Replaying an already-committed request may return success or 409; the worker rereads state. It must not introduce duplicate node entries.

Safe pagination should use stable cursors/snapshots. This simple worker assumes acyclic, advancing cursors and a bounded page size. Objects changed during a scan are reconciled again on the next scan; a scan is not a cluster-wide consistent snapshot.

## Storage-node endpoints

| Method and route | Required parameters/behavior |
|---|---|
| `GET /health` | Return `{"node_id":"node-1","status":"online","used_bytes":1000,"capacity_bytes":10000}`; status may be `full` |
| `GET /objects/{object_id}/checksum` | Query: version, replica_id, recompute=true. Return actual ReplicaReceipt |
| `GET /objects/{object_id}` | Query: version, replica_id. Stream exact immutable bytes; no content transformation |
| `POST /objects/{object_id}/replicate` | Query: version, replica_id. Raw chunked binary request, not multipart/JSON. Return durable ReplicaReceipt |
| `DELETE /objects/{object_id}` | Query: version, replica_id. Require retirement token and If-Match; delete only that generation |

Replication headers:

```text
Content-Type: application/octet-stream
X-Expected-SHA256: <expected hash>
X-Expected-Size: <decimal bytes>
Idempotency-Key: <replica_id>
```

The node must handle cancelled/incomplete bodies and clean provisional files. For an existing identical immutable replica, return its verified receipt idempotently; never overwrite different bytes under the same ID. The client streams and hashes the source and subsequently recomputes the destination checksum before the metadata commit.

Deletion headers:

```text
X-Retirement-Token: <opaque coordinator-issued authorization>
If-Match: <replica_id>
```

The node must validate this token, including its object/version/node/generation scope and retirement status. The fake adapter uses in-memory tokens only; implement real authenticated authorization in the backend. If deletion cannot be confirmed, keep extra bytes and use coordinator-controlled cleanup. Safety takes priority over immediately reaching exactly 3x storage.

## Error mapping

| Upstream condition | HTTP status | Recovery behavior |
|---|---:|---|
| Missing replica/object | 404 | Missing replica triggers repair; missing object cancels its task |
| Metadata/replica conflict | 409 or 412 | Reread authoritative metadata, bounded retry |
| Rejected checksum/body | 422 | Corrupt/error classification; no commit |
| Full destination | 507 | Blocked; rediscover on later reconciliation |
| Authority unavailable | 503 | Bounded retries; no offline metadata writes |
| Connection or read failure | timeout/transport error | Unavailable, never proof of permanent loss |

## Frontend handoff

Member 1 can call this module directly at `http://127.0.0.1:8003`, or Member 2 can proxy the monitoring routes through the main API. `GET /nodes` returns an object with a `nodes` list, not a bare list. `GET /repair-status` returns the documented status wrapper. Only this recovery service should own these routes at that address; avoid overlapping coordinator routes on the same port.

The UI should treat `UNAVAILABLE` as “no currently verified readable copy”, `UNKNOWN` as “not checked or monitoring has failed”, and `BLOCKED` as “repair requires a healthy source/capacity/authority”. None of these alone proves permanent data loss. Progress is measured bytes for the current copy, capped at 95% until commit; it is not a prediction of remaining time.

## Verified-download integration

The main download route belongs to the backend, not Member 3. `verified_download()` in `integrity_checker.py` provides a reusable async context manager that streams into a temporary file, verifies it, then yields a readable file handle. Keep the context open until the HTTP response has consumed the handle. On failure, choose another authoritative replica and retry. No bad bytes are exposed before verification, but time-to-first-byte and temporary disk usage increase with object size. A chunk-authenticated download design would need per-chunk authenticated metadata, which is outside this prototype.
