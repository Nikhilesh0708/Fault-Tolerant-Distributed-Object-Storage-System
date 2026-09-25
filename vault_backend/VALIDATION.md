# Backend validation

Validated in this execution environment on 26 September 2026, Python 3.12.

## Results

| Check | Result |
|---|---|
| Automated suite | 9 passing tests (`pytest`), run against real HTTP servers (four live storage-node processes plus the combined backend, each a genuine `uvicorn.Server` bound to a real loopback socket — not `TestClient` in-process calls) |
| Python compilation | `python -m compileall -q backend run_nodes.py` succeeded |
| Upload → list → download → delete round trip | Uploaded bytes replicated to 3 of 4 healthy nodes, downloaded bytes matched exactly, delete removed it from listings and made further downloads 404 |
| Empty-file upload | Rejected with 422 |
| Coordinator `/nodes` | Returns the documented `{"nodes":[...]}` wrapper; all four nodes reported online |
| Coordinator `/metadata/{id}` | Authoritative checksum matched the actual uploaded bytes' SHA-256; `revision`, `committed`, `deleted` fields present and correct |
| Conditional replica commit | A simulated repair (writing a 4th replica, then committing it via `POST /metadata/{id}/replica` with `If-Match`) succeeded and bumped `revision`; a replay using the now-stale `expected_revision` was rejected with 409 rather than silently applied |
| Corrupted replica upload | A storage node rejected a body whose bytes didn't match the declared `X-Expected-SHA256`, with 422, and did not persist it |
| Download ticket signing | HMAC-signed tickets verify only for the `object_id` they were scoped to |
| Retirement token scoping | A token issued for one `(object_id, version, node_id, replica_id)` tuple fails verification against any other node or replica id |

Reproduce:

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -v
python -m compileall -q backend run_nodes.py
```

## What was NOT verified

1. **Real multi-process, multi-terminal end-to-end run against the actual
   frontend and recovery packages.** This sandbox's tool environment kills
   background/`nohup`'d processes between separate tool invocations, so a
   persistent standalone `python run_nodes.py` + `uvicorn backend.app:app`
   session spanning multiple commands could not be kept alive here to smoke
   test against real `streamlit run frontend/app.py` and
   `uvicorn recovery.api:create_app` processes. What *was* verified instead:
   the pytest suite starts the identical server code (`backend.app`,
   `backend.node_app`) as real `uvicorn.Server` instances bound to real
   loopback TCP sockets within one Python process, and drives them purely
   over HTTP with `httpx` — the same code path a separate-terminal run would
   exercise, just not literally in separate terminals. Run the three
   commands under "Run it" in `README.md` yourself for that last mile.
2. **Wiring against `recovery/http_adapter.py` itself.** That file's exact
   configuration surface (how it's told the coordinator's base URL, how it
   discovers node URLs) wasn't included in what was handed off for this
   task, so this backend's coordinator contract was validated against
   `CONTRACT.md`'s documented schemas and endpoint behaviors directly, not
   against the adapter's actual request/response handling.
3. **Concurrent uploads/deletes under real contention.** The metadata store
   uses a single `asyncio.Lock`, which is correct for one process but
   untested under concurrent load in this pass.
4. **Disk-full, node-crash-mid-write, and network-partition behavior** for
   this backend specifically (the recovery package's own validation covers
   these for its simulated nodes; this backend's real nodes were not
   separately fault-injected).
5. **Production hardening**: no TLS, no per-user auth/roles (only a single
   shared bearer token, matching the frontend's documented prototype
   scope), no rate limiting, no persistence of metadata across a backend
   restart (in-memory only — a restart loses the object catalog even though
   the bytes remain on the nodes' disks).

## Known design choices worth flagging in review

- **Write quorum vs. required replicas**: an upload succeeds once
  `WRITE_QUORUM` (2) of `REQUIRED_REPLICAS` (3) nodes confirm durability, so
  a freshly uploaded file can be briefly under-replicated. This mirrors how
  the recovery package's own repair loop is meant to be used — it's the
  mechanism that brings such an object back up to 3x, not a gap this backend
  should try to close by itself with its own consensus logic (which
  `CONTRACT.md` explicitly says is out of scope for a single-process
  prototype).
- **Metadata persistence is in-memory only.** This is the same boundary the
  recovery package draws for itself ("SQLite event persistence... not
  optimized for millions of objects" / single-process). A real deployment
  needs a durable metadata store; this prototype does not attempt one.
