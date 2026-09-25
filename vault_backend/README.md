# Vault backend — Member 2

This is the storage backend the frontend and recovery packages were built
against: it implements the **main storage API** the Streamlit frontend calls
(`API_CONTRACT.md`) and the **coordinator + storage-node contract** the
recovery service's HTTP adapter calls (`CONTRACT.md`).

It is one process (the "backend", combining the metadata authority /
coordinator with the frontend-facing routes) plus N independent storage-node
processes. Object bytes live only on nodes; the backend never stores file
content itself, only metadata.

## What it does

- **Upload**: fans a file out to `REQUIRED_REPLICAS` (default 3) storage
  nodes, verifying SHA-256 + size on every node before trusting it as
  durable. Commits metadata only once `WRITE_QUORUM` (default 2) nodes have
  confirmed a durable write; otherwise it cleans up any partial replicas and
  fails the upload rather than claim durability it doesn't have.
- **List / download**: reads authoritative metadata, streams bytes straight
  from a healthy replica node to the caller.
- **Delete**: writes a tombstone (immediately authoritative — deleted files
  disappear from listings and downloads at once), then best-effort
  physically deletes each replica using a scoped, signed retirement token.
  An unreachable node's copy is left as an orphan for later
  coordinator-controlled cleanup, per `CONTRACT.md` — never silently assumed
  safe to skip, never used to block the logical delete either.
- **Coordinator endpoints** (`/nodes`, `/metadata`, `/metadata/{id}`,
  `/nodes/{id}/observations`, `/metadata/{id}/replica`): what the recovery
  service's HTTP adapter talks to when it repairs a missing replica or
  rebalances. Replica changes are compare-and-swap on `revision`; a stale
  caller gets `409` back with current authoritative state to reread, per
  `CONTRACT.md`'s non-negotiable semantics.

## Run it

From this folder, Python 3.11+ (tested on 3.12):

```sh
python -m pip install -r requirements.txt

# terminal 1: four storage nodes on 9001-9004
python run_nodes.py

# terminal 2: the backend (coordinator + main storage API) on 8000
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Windows: `start_nodes.bat` and `start_backend.bat` do the same two steps.

Then, alongside your existing two packages:

```sh
# terminal 3 — Member 3's recovery service, unchanged
python -m uvicorn recovery.api:create_app --factory --host 127.0.0.1 --port 8003

# terminal 4 — the frontend, unchanged
python -m streamlit run frontend/app.py
```

The frontend's default `http://localhost:8000` main-storage-API base URL and
`http://localhost:8003` recovery base URL both work out of the box with no
frontend changes.

## Wiring the recovery service to this backend

`recovery/http_adapter.py` is the recovery package's client of the
coordinator and storage nodes described in `CONTRACT.md`. This backend
implements that contract at `http://127.0.0.1:8000` (coordinator routes) and
`http://127.0.0.1:9001-9004` (storage nodes). Point the recovery service's
HTTP-mode configuration at those addresses to move it off its in-memory demo
nodes and onto this real backend. That wiring is the one piece this project
can't do for you sight-unseen, since `recovery/http_adapter.py`'s exact
config surface wasn't in the files handed off with this task — but the
addresses and endpoint shapes it needs to hit are exactly what's implemented
here, matching `CONTRACT.md` field-for-field.

## Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `NODE_URLS` | 4 local nodes on 9001-9004 | `node_id=url` pairs, comma-separated |
| `REQUIRED_REPLICAS` | `3` | Target replica count per object |
| `WRITE_QUORUM` | `2` | Minimum durable replicas to accept an upload |
| `MAX_UPLOAD_BYTES` | `104857600` (100 MiB) | Upload size cap |
| `STORAGE_BEARER_TOKEN` | unset (auth disabled) | If set, required for upload/delete/ticket-issue and for `/download` unless a valid ticket is supplied |
| `PUBLIC_STORAGE_URL` | `http://127.0.0.1:8000` | Origin embedded in download tickets |
| `TICKET_TTL_SECONDS` | `300` | Download ticket lifetime |
| `COORDINATOR_SECRET` | insecure dev default (a startup warning is printed) | HMAC secret signing retirement tokens and download tickets — **set a real value before running this anywhere but localhost** |

Per-node: `NODE_ID`, `NODE_PORT`, `NODE_DATA_DIR`, `NODE_CAPACITY_BYTES`
(default 1 GiB), read by `backend/node_app.py` when launched directly.

## Tests

```sh
python -m pytest -v
```

See `VALIDATION.md` for what was actually run and what wasn't.
