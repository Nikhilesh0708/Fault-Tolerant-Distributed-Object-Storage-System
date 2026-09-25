# Member 3 implementation guide

## What you own

You own failure observations, replica integrity checks, automatic recovery, bounded repair scheduling, safe rebalancing, monitoring APIs, test-only failure injection, integration tests and the demo. The frontend and authoritative storage/coordinator backend remain your teammates' work.

This package has **demo mode** (runs immediately) and **HTTP mode** (requires the backend contract in `../CONTRACT.md`). It is a working prototype, not a claim of production-grade distributed storage.

## Installation

Use Python 3.11+ (validated on Python 3.12). Extract the archive, enter `vault_member3`, and optionally create a virtual environment:

```sh
python -m venv .venv
```

Windows PowerShell activation:

```powershell
.venv\Scripts\Activate.ps1
```

If activation is restricted, use `.venv\Scripts\python.exe` instead of `python` in the commands below; no policy changes are necessary.

macOS/Linux activation:

```sh
source .venv/bin/activate
```

Install and validate:

```sh
python -m pip install -r recovery/requirements.txt
python -m pytest -q
```

Requirements pin the versions actually tested. SQLite, asyncio, hashlib and tempfile come with Python. Do not commit tokens or runtime SQLite files to your team's repository.

## Start the service

```sh
python -m uvicorn recovery.api:create_app --factory --host 127.0.0.1 --port 8003
```

Open [API docs](http://127.0.0.1:8003/docs). A FastAPI lifespan starts the health monitor, repair workers, periodic reconciliation, scrubbing and the rebalancing scheduler. Rebalancing is paused by default. There is no need to launch each Python file independently.

Use **one process, one worker**. Do not use `--workers 2` or multiple recovery-service instances. Deduplication, byte reservations and locks are process-local. An HA recovery deployment needs coordinator-backed leases/fencing and a durable shared queue, which are not supplied here.

Demo mode seeds `file-123`, a 256 KiB object on nodes 1–3, and leaves node 4 available. All fake nodes are adapters within this process, with temporary disk files. Stopping the service discards demo data. Monitoring events persist in `recovery-events.sqlite3` by default; task history and counters are process-local and restart from zero.

## Commands for your presentation

Run these from the project root in another terminal while the server is running:

```sh
python -m recovery.failure_injector --node node-3 --action stop
python -m recovery.failure_injector --node node-3 --action restart
python -m recovery.failure_injector --node node-2 --action corrupt --object file-123
python -m recovery.failure_injector --node node-2 --action delete-replica --object file-123
python -m recovery.failure_injector --node node-1 --action delay --seconds 10
python -m recovery.failure_injector --node node-1 --action timeout
python -m recovery.failure_injector --node node-1 --action reject
python -m recovery.failure_injector --node node-1 --action outdated --object file-123
python -m recovery.failure_injector --node node-4 --action full
```

These are separate scenarios, not a sequence to apply all at once. Start from a fresh demo if you need the initial replica placement. Repair may move a replica away from a node, so check metadata/scenario output before corrupting it again. `restart` clears simulated liveness/full/delay flags but does not undo corrupted bytes or recreate a deleted replica.

The CLI only contacts loopback demo servers. It does not kill OS processes, configure firewalls, fill disks or modify arbitrary paths. Demo fault endpoints are absent in HTTP mode.

### Deterministic standalone demos

These create isolated temporary clusters, assert their expected results and clean up their own files:

```sh
python -m recovery.demo.demo_failure_recovery
python -m recovery.demo.demo_corruption_repair
python -m recovery.demo.demo_rebalancing
python -m recovery.demo.smoke_api
python -m recovery.demo.benchmark
```

- Failure demo: three verified replicas → simulated node loss → automatic repair on the spare → three verified replicas and a matching download checksum.
- Corruption demo: change one copy's bytes → detect checksum mismatch → replace and retire bad copy → matching hashes.
- Rebalancing demo: seed several objects, make the existing spare ineligible, join node 5, safely move one replica to node 5, verify all four objects still have three copies.
- Smoke test: starts a real loopback Uvicorn server, exercises the HTTP routes, then terminates only the child it started.
- Benchmark: 8 MiB generated object, 64 KiB chunks, local repair and verified-download measurements. Results are simulations, not durability/availability promises.

## Monitoring and controls

Use the `/docs` page to try endpoints without writing code. Read endpoints do not trigger file transfers. Mutating endpoints require `Authorization: Bearer <VAULT_ADMIN_TOKEN>` when a token is configured.

| Method | Route | Purpose |
|---|---|---|
| GET | `/healthz` | Service liveness, mode and single-worker warning |
| GET | `/nodes` | Last observed node health and storage usage |
| GET | `/repair-status` | Last-scan cluster status, counts and bounded task history |
| GET | `/repair/tasks?limit=100` | Recent repair jobs, including blocked/failed jobs |
| GET | `/metrics` | Counts, durations, estimated storage overhead, errors |
| GET | `/events?limit=100` | Recent persisted events |
| GET | `/integrity-status` | Scan summary and bounded problem samples |
| GET | `/rebalancing-status` | Pause flag and move history |
| POST | `/health/check` | Force a health probe of all registered nodes |
| POST | `/repair/start` | Scan objects and enqueue unhealthy ones |
| POST | `/repair/{object_id}` | Queue one object; 202 accepted, 429 when full |
| POST | `/integrity/scan` | Full scrub; creates repair jobs |
| POST | `/rebalancing/resume` | Allow scheduled rebalancing |
| POST | `/rebalancing/pause` | Prevent new moves (does not cancel an in-flight copy) |
| POST | `/rebalancing/run` | Attempt at most one beneficial move if unpaused |
| POST | `/demo/faults` | Demo-only fault injection |
| POST | `/demo/nodes` | Demo-only registration, e.g. `{"node_id":"node-5"}` |

Manual scan/probe/rebalance endpoints wait for their operation to finish. Each backend request and repair has deadlines, but a full scan of many objects can take a long time. Use scheduled scans and poll status for larger datasets. A future production API should expose job IDs for these long-running controls.

Example node response:

```json
{"nodes":[{"node_id":"node-3","url":"http://demo.invalid/node-3","used_bytes":262144,"capacity_bytes":1048576,"status":"OFFLINE","failure_count":3,"last_seen":"2026-09-25T20:00:00+00:00","response_ms":0.1,"reason":"Demo node unreachable"}]}
```

Example repair response (illustrative values):

```json
{
  "system_status":"REPAIRING",
  "active_repairs":1,
  "completed_repairs":0,
  "failed_repairs":0,
  "last_completed_scan":"2026-09-25T20:00:00+00:00",
  "repairs":[{
    "task_id":"example-id",
    "object_id":"file-123",
    "version":"v1",
    "status":"REPAIRING",
    "source_node":"node-1",
    "destination_node":"node-4",
    "failed_node":"node-3",
    "progress":47.5,
    "attempts":1,
    "message":"Copying and verifying immutable replica"
  }]
}
```

Real task responses also contain creation/update timestamps. Progress can reset when a transfer retries. Node-full is a capacity state, not proof that stored files are unreadable. Corruption is tracked per replica rather than treating all content on that node as corrupted.

## Configuration

Every field in `config.py` maps to an environment variable named `VAULT_` plus its uppercase field name. Values are validated on startup.

| Variable | Default | Meaning |
|---|---|---|
| `VAULT_MODE` | demo | demo or http |
| `VAULT_COORDINATOR_URL` | http://127.0.0.1:8000 | Metadata authority |
| `VAULT_BACKEND_TOKEN` | empty | Optional bearer credential for upstream services |
| `VAULT_ADMIN_TOKEN` | empty | Required in HTTP mode; protects recovery controls |
| `VAULT_DATABASE_PATH` | recovery-events.sqlite3 | Bounded event journal; `:memory:` for isolated tests |
| `VAULT_HEARTBEAT_INTERVAL` | 5 | Seconds between probe rounds |
| `VAULT_HEARTBEAT_TIMEOUT` | 2 | Deadline for one health request |
| `VAULT_FAILURE_THRESHOLD` | 3 | Consecutive failures before OFFLINE |
| `VAULT_OFFLINE_PROBE_INTERVAL` | 30 | Reduced-rate probing of offline nodes |
| `VAULT_REQUEST_TIMEOUT` | 30 | HTTP transport timeout |
| `VAULT_OPERATION_TIMEOUT` | 600 | Deadline per repair attempt/inspection/move |
| `VAULT_REPAIR_INTERVAL` | 10 | Reconciliation interval after the preceding round ends |
| `VAULT_RETRY_LIMIT` | 3 | Attempts per repair job |
| `VAULT_RETRY_DELAY` | 0.5 | Initial exponential retry delay, with jitter |
| `VAULT_SCRUB_INTERVAL` | 60 | Seconds between scrub rounds |
| `VAULT_REBALANCE_INTERVAL` | 120 | Seconds between balancing attempts |
| `VAULT_MAX_CONCURRENT_REPAIRS` | 3 | Shared transfer concurrency bound |
| `VAULT_MAX_HEALTH_CHECKS` | 8 | Health-probe batch bound |
| `VAULT_QUEUE_SIZE` | 100 | Pending-job bound; later scans rediscover overflow |
| `VAULT_PAGE_SIZE` | 100 | Metadata page bound |
| `VAULT_HISTORY_LIMIT` | 500 | Bound for recent task/event/problem histories |
| `VAULT_REPLICATION_FACTOR` | 3 | Demo seed policy; real objects use coordinator metadata |
| `VAULT_CHUNK_SIZE` | 65536 | Transfer chunk bytes |
| `VAULT_MAX_OBJECT_BYTES` | 10737418240 | Maximum permitted copy size, 10 GiB |
| `VAULT_TRANSFER_BYTES_PER_SECOND` | 0 | Per-transfer pacing; 0 means unpaced |
| `VAULT_REBALANCE_THRESHOLD` | 0.20 | Minimum utilization gap; avoid overshoot/ping-pong |
| `VAULT_ENABLE_REBALANCE` | false | Start paused unless explicitly enabled |

For HTTP mode in Windows PowerShell, after the backend contract is implemented:

```powershell
$env:VAULT_MODE = "http"
$env:VAULT_COORDINATOR_URL = "http://127.0.0.1:8000"
$env:VAULT_ADMIN_TOKEN = "replace-with-your-own-random-token"
python -m uvicorn recovery.api:create_app --factory --host 127.0.0.1 --port 8003
```

On macOS/Linux:

```sh
export VAULT_MODE=http
export VAULT_COORDINATOR_URL=http://127.0.0.1:8000
export VAULT_ADMIN_TOKEN='replace-with-your-own-random-token'
python -m uvicorn recovery.api:create_app --factory --host 127.0.0.1 --port 8003
```

Use TLS and appropriate authentication when crossing trusted-host boundaries. Keep demo mode on loopback. Read-only monitoring endpoints are unauthenticated here; put them behind an authenticated gateway before wider exposure. The code does not provision certificates or infrastructure.

## Failure policy and recovery safety

- One bad heartbeat produces SUSPECTED. The configured threshold produces OFFLINE. This means unreachable from this observer, not certainly dead.
- An offline node's first successful probe produces RECOVERING, then ONLINE on the next healthy probe. Replica content must still pass integrity verification before being counted.
- Repairs can start from direct replica failures before a node reaches the heartbeat threshold. A transient timeout may create extra copies; that is preferable to deleting data based on an uncertain failure.
- Only an authoritative committed version and exact expected checksum can be repair sources. A checksum does not reconstruct lost data.
- Each object has one pending job in this process. Fixed striped locks serialize repair/rebalance operations on the same object. The coordinator's conditional update rejects races with uploads/deletes.
- Transfers use new immutable replica generations, with stable per-job retry IDs. Temp files are not counted; successful copying alone does not publish metadata.
- No valid source or insufficient distinct nodes/capacity produces BLOCKED. Later reconciliation can retry. Backend errors use finite retries with backoff; one failed job does not kill the worker.
- A full queue rejects additional jobs instead of growing memory indefinitely; periodic scans provide eventual rediscovery.
- Rebalancing gives existing repair jobs priority, uses the same concurrency limiter, checks remaining copies, commits a conditional replacement, and requests deletion using a scoped retirement token.
- Unknown/missing tokens or interrupted deletion leave extra bytes. The coordinator must implement orphan and retired-replica garbage collection; this service deliberately does not guess whether deletion is safe.
- Interrupted jobs are rediscovered after service restart from authoritative metadata. Only bounded events persist locally; this is reconciliation-based restart recovery, not a persisted task queue.

Object availability is reported independently from repair-task success. An unsuccessful repair does not imply that the object is lost. `UNAVAILABLE` is used instead of declaring permanent `FAILED` when every copy might simply be partitioned. `HEALTHY` means the last completed scan found the required replicas; it does not promise future availability. During a running/failed scan, cluster status is UNKNOWN.

## Complexity and performance

Let N be object count, R replicas per object, M registered nodes, B bytes scrubbed, S transferred object bytes, C concurrent transfers, K chunk bytes, P page size, Q queue size and H history bound.

| Operation | Cost |
|---|---|
| Health round | O(M) requests; at most configured batch size in flight |
| Full integrity scan | O(NR) replica checks plus O(B) checksum work on nodes |
| Repair transfer | O(S) read/hash/write work, plus destination verification and fresh replica checks |
| Destination selection | O(M log M) sorting, not an invented O(1) bound |
| Repair memory | Approximately O(CK), plus HTTP buffers and bounded metadata |
| Metadata working memory | O(M + PR + QR + HR) conservatively; no whole-object byte buffers |
| Verified download | O(S) temporary disk and O(K) payload RAM; complete verification before yielding |
| Rebalance scan | May inspect O(NR) replicas and sort O(M log M) candidates per object until a useful move is found |

Nested iteration over objects and their replicas is intentional, O(NR), not automatically O(N²). Demo metadata itself lives in memory O(NR), outside the worker's paginated scan; that test double is not a large-scale metadata database. SQLite event writes and demo filesystem operations are synchronous and small; heavy production journaling should be moved to a dedicated writer.

This prototype uses full checksum verification for reconciliation as well as scrubbing. That is simple and thorough but expensive for large datasets. For production, add backend change feeds, incremental inventory, checksum age policies, independently throttled scrubbing, topology-aware placement, per-node transfer budgets and durable work queues. The transfer pacing option does not throttle server-side checksum reads.

The API reports foreground latency as null because it does not own uploads/downloads. The separate benchmark measures local verified-download latency during a repair window. Estimated physical/logical storage overhead includes last-reported bytes on offline nodes and may exceed replication factor because retained copies and stale observations exist.

Default heartbeat detection has a rough upper bound of one polling wait plus three failed probes' scheduling/deadline time under an otherwise healthy event loop. Do not promise a precise recovery SLA: repairs also depend on scan duration, healthy sources, spare capacity, network throughput, queueing and metadata availability. Forced probes in tests do not measure real polling delay.

## File map

All paths below are relative to the archive root.

| File | Purpose |
|---|---|
| `README.md` | Quick start |
| `CONTRACT.md` | Backend and frontend integration agreement |
| `VALIDATION.md` | Actual test/demo results and limitations |
| `pytest.ini` | Test discovery |
| `recovery/__init__.py` | Package declaration |
| `recovery/config.py` | Typed environment configuration |
| `recovery/models.py` | Validated node/object/receipt/task models |
| `recovery/adapters.py` | Backend protocol and typed errors |
| `recovery/http_adapter.py` | Async REST integration and streamed transfers |
| `recovery/failure_detector.py` | Node state transitions and observation timing |
| `recovery/health_monitor.py` | Bounded health rounds and registry refresh |
| `recovery/integrity_checker.py` | Verification, paginated scans, verified-download helper |
| `recovery/repair_manager.py` | Bounded queue, deduplication, retries, conditional repair |
| `recovery/rebalancer.py` | Safe, incremental replica movement |
| `recovery/metrics.py` | Bounded SQLite journal and runtime counters |
| `recovery/service.py` | Lifecycle, scheduling and status composition |
| `recovery/api.py` | FastAPI monitoring/control routes |
| `recovery/failure_injector.py` | Loopback demo-only CLI |
| `recovery/requirements.txt` | Tested dependency pins |
| `recovery/README.md` | This full guide |
| `recovery/demo/__init__.py` | Demo package |
| `recovery/demo/fake_backend.py` | Disk-backed storage/coordinator test double |
| `recovery/demo/common.py` | Shared scenario assertions |
| `recovery/demo/demo_failure_recovery.py` | Node-loss demo |
| `recovery/demo/demo_corruption_repair.py` | Corruption demo |
| `recovery/demo/demo_rebalancing.py` | Join-and-rebalance demo |
| `recovery/demo/benchmark.py` | Local memory/latency experiment |
| `recovery/demo/smoke_api.py` | Real loopback API process test |
| `recovery/tests/__init__.py` | Test package |
| `recovery/tests/helpers.py` | Isolated cluster fixtures/helpers |
| `recovery/tests/test_health_monitor.py` | Heartbeats, thresholds, timeout, full/join/leave/backoff |
| `recovery/tests/test_node_failure.py` | Failure recovery, insufficient nodes, total unavailability |
| `recovery/tests/test_replica_repair.py` | Missing/corrupt/old replicas, idempotency, chunks, size policy |
| `recovery/tests/test_corruption.py` | Mid-transfer corruption, protected download, pagination |
| `recovery/tests/test_retries.py` | Interrupted transfer, CAS race, deletion, upload, authority outage |
| `recovery/tests/test_concurrent_repairs.py` | Queue bounds, deduplication, concurrency, isolation |
| `recovery/tests/test_rebalancing.py` | Safe copy/commit/delete, pause, race, repair priority |
| `recovery/tests/test_api.py` | Route coverage, token checks and validation errors |
| `recovery/tests/test_http_adapter.py` | REST request/response contract and error mapping |
| `recovery/tests/test_service.py` | Background lifecycle, persistent events, environment config |

## Before team submission

1. Agree on `CONTRACT.md` with the backend teammate. Implement paginated object listing, health observations, versioned replica endpoints, conditional metadata updates and scoped deletion tokens.
2. Connect the frontend to this module's monitoring JSON. Keep upload/download/delete user actions routed to the main backend.
3. Switch to HTTP mode and verify real nodes; the mock tests alone cannot prove that another implementation satisfies the contract.
4. Repeat node process crashes, network partitions, disk-full, actual restarts, checksum corruption and concurrent upload/delete tests in your integrated environment.
5. Present only the guarantees you measured. Metadata consensus, physical failure domains, independent process failures, production authentication and durable garbage collection remain backend/deployment responsibilities.

## Implementation references

- [FastAPI lifespan events](https://fastapi.tiangolo.com/advanced/events/): startup/shutdown lifecycle used in `api.py`.
- [HTTPX async support](https://www.python-httpx.org/async/): reusable async client, streamed responses and async request-body generators used in `http_adapter.py`.

No claim is made that this package implements Raft, erasure coding, geo-replication, Byzantine fault tolerance or a production availability SLA.
