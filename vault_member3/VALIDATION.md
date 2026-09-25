# Validation report — Vault Member 3

Validated in this execution environment on 26 September 2026 (India time), with Python 3.12. Tests and demos are reproducible using the commands below.

## Results

| Check | Result |
|---|---|
| Automated suite | 47 passing tests, including parameterized cases |
| Python compilation | `python -m compileall -q recovery` succeeded |
| Node-failure demo | Recreated third verified copy on node 4; verified downloaded SHA-256 |
| Corruption demo | Detected corrupted bytes, replaced replica, final three committed copies verified |
| Rebalancing demo | Moved one replica to newly joined node 5; four objects retained 12 healthy replicas |
| Live API smoke test | Real Uvicorn loopback process served monitoring/control routes; automatic recovery completed |
| HTTP integration adapter | Mock transport validated routes, streamed chunks, receipt schema, conditional headers, errors and retirement-token use |
| 8 MiB local experiment | Largest received chunk 65,536 bytes; no full-object payload buffer used by the repair path |

The suite emits one upstream Starlette warning that its HTTPX-based TestClient path is deprecated. Tests still pass. This warning concerns test-client integration, not a failed recovery test. HTTPX2 was not added just to suppress it.

Run from the extracted project root:

```sh
python -m pytest -q
python -m compileall -q recovery
python -m recovery.demo.demo_failure_recovery
python -m recovery.demo.demo_corruption_repair
python -m recovery.demo.demo_rebalancing
python -m recovery.demo.smoke_api
python -m recovery.demo.benchmark
```

## Measured local experiment

One benchmark run used an 8,388,608-byte generated object and a 65,536-byte chunk size:

| Measurement | Observed value |
|---|---:|
| Largest transfer chunk | 65,536 bytes |
| Peak Python allocation traced during repair plus downloads | 240,446 bytes |
| Baseline verified-download median (5 samples) | 15.39 ms |
| Repair-window verified-download median (5 samples) | 27.39 ms |
| Repair task duration | 0.119 seconds |
| Entire concurrent measurement window | 0.202 seconds |

These are a **single local simulation run**, not hardware-independent performance estimates or an availability SLA. Tracemalloc measures traced Python allocations, not total process RSS, kernel buffers or filesystem cache. Some download samples can occur after the repair has finished. Forced heartbeat probes exclude configured poll waiting time. Repeat on the actual integrated deployment with representative large files, concurrency, disks and network conditions.

The failure demo can report estimated storage overhead of 4x while the lost node retains an unreferenced copy; corruption repair reports 3x after successful retirement cleanup. This is expected: immediate safe deletion from an unreachable node is impossible. The coordinator's garbage collector must resolve retained/orphaned generations later.

## Safety cases covered

- Heartbeat threshold, SUSPECTED/OFFLINE/RECOVERING transitions and offline probe backoff.
- Actual asyncio health-request deadline, invalid response, simulated network failure and storage-full state.
- Missing, corrupted and outdated replicas; SHA-256 and version enforcement.
- Source bytes changing after a valid inspection and sources disappearing during transfer.
- Verified-download helper refuses to yield a corrupted file.
- Finite retries after interrupted transfer and compare-and-swap conflicts.
- Concurrent deletion cannot resurrect the object; concurrent upload cannot receive an old-version replica.
- Coordinator outage prevents authoritative metadata changes.
- Queue backpressure, deduplication, concurrent-transfer limit and failure isolation.
- Copy/verify/commit/authorized-delete order, paused rebalancing and repair priority.
- Background service startup/shutdown, SQLite event persistence, HTTP status mapping and API authentication.

## What has NOT been verified

1. Integration with your teammates' actual backend/frontend: those sources and running endpoints were not provided.
2. Independent real storage-server crashes, physical machine failures, disk power-loss durability or real network partitions. Fake nodes share a process; the API smoke test uses a real HTTP server but still fake storage nodes.
3. A metadata consensus implementation. HTTP mode relies on the coordinator for authoritative reads, conditional writes, tombstones and quorum behavior.
4. Multiple recovery processes. This version requires one process and one worker; local task locks are not distributed leases.
5. Production workload scale. Full periodic checksum scans, synchronous event journaling and the demo metadata dictionary are not optimized for millions of objects.
6. Production authentication, TLS provisioning, topology/failure-domain-aware placement, persisted repair queues, erasure coding or automatic orphan garbage collection.

Read `CONTRACT.md` before enabling HTTP mode. A passing mock suite is not evidence that an unrelated backend satisfies this contract.
