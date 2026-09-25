# Vault frontend API contract

The frontend uses two independently configurable API bases. It does not assume the recovery service can accept uploads.

## Main storage API — default port 8000

| Method | Endpoint | Contract |
|---|---|---|
| POST | `/upload` | Multipart body with one field named `file`; return JSON containing file_id, object_id or id |
| GET | `/files?limit=100&cursor=...` | Paginated wrapper below, or a legacy bare file-record array |
| GET | `/download/{file_id}` | Exact file bytes; return attachment filename and stream the response |
| DELETE | `/files/{file_id}` | 200/204 after completing deletion; 202 means pending |
| POST | `/download/{file_id}/ticket` | Optional for protected large downloads; return a short-lived URL |

File IDs are opaque strings and percent-encoded into URLs. Endpoint identifiers should not require routers to reinterpret encoded slashes as route separators; using UUIDs is simplest.

Example listing:

```json
{
  "files": [
    {"file_id": "file-123", "filename": "notes.pdf", "size": 8192, "replicas": 3, "status": "HEALTHY", "version": "v1"}
  ],
  "next_cursor": null
}
```

Recognized aliases: `object_id`/`id` for file_id, `object_key`/`name` for filename, `size_bytes` for size, `replica_count` for replicas. A replicas array is displayed using its length; the frontend does not independently verify its health. Status omitted by the backend is UNKNOWN, never assumed healthy.

The backend should return one current committed version per file ID in a listing. If you expose multiple versions, give each selectable record an unambiguous ID and adapt the client contract consistently.

Upload uses Content-Length, Content-Type multipart/form-data and an Idempotency-Key header. The backend must read the body incrementally, enforce its own size checks, validate filenames safely, and own durability/replication decisions. The frontend has no write quorum logic. Example completed response:

```json
{"file_id": "file-123", "filename": "notes.pdf", "status": "HEALTHY"}
```

Return 200 or 201 when upload processing is complete according to your documented acknowledgement policy. A 202 response is displayed as accepted/pending, not durable completion. A response without an object ID is not treated as a confirmed upload; the user is told to inspect the file list before retrying.

HTTP 401/403 = credential problem, 404 = missing object/route, 409/412 = object changed, 413 = upload too large, 422 = rejected request format, 429 = busy, 507 = storage full, 5xx = service error. Raw server tracebacks are not shown in the UI. Writes are not automatically retried.

### Downloads

Without a configured storage bearer token, the Download button is a direct browser link. The public storage URL must be reachable from the browser. Same-origin cookies may work if your backend uses them, but this app does not implement cookie login.

When a storage token is configured, Requests sends it only to the storage API. It is not embedded in browser links. For large protected downloads, implement:

```text
POST /download/file-123/ticket
Authorization: Bearer <storage token>
```

```json
{"url": "https://storage.example/download/file-123?ticket=short-lived-scoped-ticket"}
```

The URL must have the same origin as the configured public storage URL. The backend must scope and expire the ticket. The frontend cannot create secure tickets itself. If your backend returns signed object-store URLs on another origin, extend the client with an explicit allowlist before enabling that workflow.

The alternative authenticated in-app download is capped at 8 MiB. Never send a gigabyte payload to Streamlit's native download widget.

## Recovery API — default port 8003

These routes match the Member 3 package already built:

| Method | Endpoint | Use |
|---|---|---|
| GET | `/healthz` | Liveness and demo/HTTP mode; note the final z |
| GET | `/nodes` | Wrapper `{"nodes":[...]}` |
| GET | `/repair-status` | System status, counts and repair task records |
| GET | `/metrics` | Object counts, storage usage and measurements |
| GET | `/events?limit=20` | Wrapper `{"events":[...]}` |
| POST | `/health/check` | Probe registered nodes |
| POST | `/integrity/scan` | Integrity scan and repair enqueue |
| POST | `/rebalancing/pause` | Pause future balancing work |
| POST | `/rebalancing/resume` | Resume balancing |
| POST | `/rebalancing/run` | Attempt one move |

Example node response:

```json
{"nodes":[{"node_id":"node-1","status":"ONLINE","used_bytes":262144,"capacity_bytes":1048576,"response_ms":1.2}]}
```

Example repair response:

```json
{
  "system_status":"REPAIRING",
  "active_repairs":1,
  "completed_repairs":0,
  "failed_repairs":0,
  "repairs":[{"task_id":"repair-1","object_id":"file-123","status":"REPAIRING","source_node":"node-1","destination_node":"node-4","progress":50,"message":"Copying and verifying immutable replica"}]
}
```

Recovery controls use the recovery admin token, not the storage token. Live monitoring remains available even when the separate file-storage API is disconnected. A healthy recovery snapshot applies only to objects tracked by that recovery service, not automatically to a separate test storage fixture.

## Deployment notes

Streamlit makes its API requests from its Python server, so those calls do not require browser CORS configuration. Direct downloads are browser navigations. If services run on different computers, replace localhost with appropriate reachable addresses. An address reachable from the Streamlit server may not be reachable from the user's browser, which is why the public storage URL is separate.

No changes were made to the Member 3 recovery package in this task. Backend changes needed to match this contract remain the backend teammate's responsibility.
