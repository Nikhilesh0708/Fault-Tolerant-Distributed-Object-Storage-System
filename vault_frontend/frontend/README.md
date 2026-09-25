# Vault frontend — Python + Streamlit

A separate frontend for your three-member Vault project. It includes an overview dashboard, file actions, node health, repair progress, event history and service configuration. It uses **Streamlit and Requests only as direct runtime dependencies**. No React project, Node.js server, or separate JavaScript frontend is required.

## Start on Windows

1. Extract `Vault_Frontend.zip`.
2. Open the extracted `vault_frontend` folder. It contains `frontend`, `tests`, and `start_frontend.bat`.
3. Click File Explorer's address bar, type `cmd`, and press Enter.
4. Run these commands, one at a time:

```bat
python -m pip install -r frontend/requirements.txt
python -m streamlit run frontend/app.py
```

5. Open [http://localhost:8501](http://localhost:8501).
6. Leave the terminal open while using the dashboard. Press Ctrl+C to stop it.

If `python` is not recognized, use `py` instead. Python 3.11+ is recommended; this package was tested with Python 3.12. After installing dependencies, double-clicking `start_frontend.bat` also launches the app when the `python` command is available.

Run from the `vault_frontend` root, not from inside its `frontend` subfolder, so Streamlit also loads `.streamlit/config.toml`.

## Keep your recovery server running

Your previous Member 3 package remains separate and unchanged. In another terminal:

```bat
cd /d C:\NIKHILESH\vault_member3
python -m uvicorn recovery.api:create_app --factory --port 8003
```

Keep both windows open. The frontend automatically reads the recovery service's `/healthz`, `/nodes`, `/repair-status`, `/metrics`, and `/events` routes. It does not request `/health` on the recovery port.

| Component | Default address | What it provides |
|---|---|---|
| Frontend | http://127.0.0.1:8501 | Dashboard in your browser |
| Member 3 recovery | http://127.0.0.1:8003 | Nodes, status, repair tasks, events |
| Main storage backend | http://127.0.0.1:8000 | Upload, list, download, delete |

**The Member 3 recovery server does not provide file storage endpoints.** With only that service running, node and repair monitoring work, while the Files screen explains that the main storage backend is not connected. Upload is disabled until the file-list endpoint responds successfully. No frontend simulation is silently substituted for a missing backend.

If the backend teammate runs their service on another port, change **Connections → Storage API address** and **Browser download address**. The latter must be reachable from the browser, not only from the Streamlit server.

## Screens

### Overview

- Last verified system status, stored/healthy object counts and active repairs.
- Node cards with status text, storage usage and most recent response time.
- First few stored files and recent recovery events.
- Refresh button and optional five-second automatic monitoring refresh.

Counts come from the recovery service's last scan; they can lag the main file list. A healthy replica set can coexist with an offline node. A disconnected service shows UNKNOWN rather than invented healthy values.

### Files

- Upload one file with its filename, size, transfer progress and backend response.
- Search the current metadata page by filename or object ID.
- First/next-page navigation when the backend returns a cursor.
- Choose an object, then download it or confirm deletion.
- Missing backend, invalid JSON, timeouts, missing files, authentication errors, storage-full and server errors are shown as readable messages.

Search applies only to the loaded page. Older backends may return a bare array instead of a paginated wrapper. Those lists are supported up to the metadata response limit, with at most 2,000 records displayed; larger deployments should implement pagination.

### Recovery

- Active/completed/failed task counts and up to eight recent task cards.
- Byte-based progress received from Member 3's API.
- Optional controls to probe nodes, run an integrity scan, pause/resume rebalancing and attempt one move.

Controls call the existing backend; they do not implement repair or placement logic in the frontend. In HTTP mode, enter the Member 3 admin token to use protected controls. A scan can take time; timing out in the frontend does not prove the backend stopped running it.

### Connections

- Separate storage and recovery addresses.
- Browser-facing download address.
- Separate optional bearer tokens, entered as password fields.
- Test both connections independently.

Values entered here last for this browser session. Tokens are not saved to disk, put into permanent download URLs, or included in user-facing errors. The app is a local/trusted-user prototype, not a public multi-tenant service. Do not expose an arbitrary connection form and administrative controls publicly without authentication and network access controls.

## Design preview

Choose **Design preview** in the sidebar to inspect the layout without a backend. Preview values are fixed samples, visibly labeled, and file/recovery actions are disabled. Preview data is never used automatically if a live service fails. Switch back to **Live services** for actual API responses.

When the Member 3 API reports `mode: demo`, the frontend labels it as a recovery demo. These nodes are simulated by that backend, even though the frontend's HTTP connection to it is real.

## Upload/download memory limits — read this before large-file demos

Streamlit's native file picker represents uploaded files in memory. Therefore, this frontend **cannot promise O(chunk_size) total memory for a 1 GB browser upload**.

- The selected upload is capped at **32 MiB** by both Streamlit configuration and per-widget validation. An environment setting may lower this cap, but not raise it above 32 MiB.
- `api_client.py` streams a multipart body from the selected file in 64 KiB pieces, avoiding Requests' normal additional full-file multipart copy. It never calls `uploaded_file.getvalue()` or caches uploaded content.
- Progress reaches at most 95% while sending; it reaches 100% only after receiving the backend response. A 202 response is described as accepted/pending, not as proof of a completed durable write.
- Normal downloads use a direct browser link to the storage API, so file bytes bypass the Streamlit Python process. The backend should return `Content-Disposition: attachment` and stream the data.
- With a storage bearer token, browser links cannot inherit that token. The UI supports an optional short-lived download-ticket endpoint, or an explicitly capped **8 MiB** in-app download. Larger protected files require the ticket endpoint.
- The in-app download is read in chunks with a hard byte limit, including when Content-Length is absent. The final small download is held in memory by Streamlit; it is not constant-memory streaming.

For actual gigabyte browser uploads, the team needs a direct/resumable browser-to-storage upload flow. That is a future backend/browser integration, not something this app pretends to provide.

## Configuration

Defaults are in `frontend/config.py`. Set environment variables before launching, or use the Connections page:

| Environment variable | Default |
|---|---|
| `VAULT_STORAGE_URL` | http://127.0.0.1:8000 |
| `VAULT_RECOVERY_URL` | http://127.0.0.1:8003 |
| `VAULT_PUBLIC_STORAGE_URL` | Same as storage URL |
| `VAULT_STORAGE_TOKEN` | Empty |
| `VAULT_RECOVERY_TOKEN` | Empty |
| `VAULT_UPLOAD_LIMIT_MB` | 32, permitted range 1–32 |

Example in Windows Command Prompt:

```bat
set VAULT_STORAGE_URL=http://127.0.0.1:8000
set VAULT_RECOVERY_URL=http://127.0.0.1:8003
python -m streamlit run frontend/app.py
```

The recovery token is the value configured as `VAULT_ADMIN_TOKEN` when starting Member 3. It has a different environment name here to distinguish upstream credentials from frontend settings.

Metadata is cached for five seconds **inside each Streamlit session**, including connection errors, to avoid repeated requests on every widget change. Uploaded/downloaded bytes are not put in that cache. Changing connections, refreshing, or successful mutations clears it. Upload and delete requests are never automatically retried, because a timeout may occur after the backend commits them.

## Folder contents

| Path | Purpose |
|---|---|
| `frontend/app.py` | Dashboard pages, widgets, session metadata cache and UI actions |
| `frontend/api_client.py` | All REST communication, streaming multipart upload, schema checks, download/delete |
| `frontend/config.py` | Environment/default connection and resource limits |
| `frontend/styles.py` | Static CSS for the Streamlit theme; no JavaScript |
| `frontend/preview.py` | Explicitly labeled, read-only sample data |
| `frontend/requirements.txt` | Two pinned direct runtime dependencies |
| `frontend/README.md` | This full guide |
| `.streamlit/config.toml` | Theme, localhost binding and upload cap |
| `start_frontend.bat` | Windows launcher |
| `API_CONTRACT.md` | Required storage endpoints and monitoring contracts |
| `VALIDATION.md` | Verification results and remaining limitations |
| `tests/test_client.py` | API, error handling and transfer tests |
| `tests/test_app.py` | Streamlit application-level tests |
| `tests/mock_storage.py` | Ephemeral HTTP fixture used only in tests |
| `tests/live_smoke.py` | Optional real-service integration and browser smoke check |
| `artifacts/Vault_Dashboard_Preview.png` | Actual desktop screenshot, with labeled sample data |
| `artifacts/Vault_Mobile_Preview.png` | Narrow viewport screenshot |

## Testing

From the archive root:

```bat
python -m unittest discover -s tests -v
```

No pytest dependency is required. Tests use Python unittest, Streamlit AppTest and a local test HTTP server. Test setup warnings mentioning missing ScriptRunContext are expected from Streamlit's testing harness and do not mean the application failed.

Optional integration check, with the Member 3 folder alongside this package and its dependencies already installed:

```bat
python -m tests.live_smoke --recovery-root ../vault_member3
```

Add `--browser` only if Playwright and its Chromium browser are installed in your development environment. Browser tooling is not needed to run the frontend or the 20 default tests. The optional check starts and stops its own local test servers.

For a manual end-to-end file-actions demonstration before the team's real backend is ready, run:

```bat
python -m tests.mock_storage
```

Set both storage addresses in Connections to `http://127.0.0.1:8765`. This is **only an ephemeral single-process test fixture**, not Member 2's backend. Its files are marked `TEST_ONLY`, it has no replication, and files disappear when that fixture exits. Restore the real storage API addresses after testing. Do not interpret recovery counts from port 8003 as counts for this separate fixture.

Manual checklist:

1. Start recovery and frontend; confirm actual node statuses appear.
2. Stop/restart a demo node using your existing recovery CLI; watch the dashboard refresh.
3. Connect a storage backend/fixture, upload a small file, find it in Files, download and compare its bytes.
4. Press Delete without the confirmation box: no DELETE request should occur.
5. Confirm deletion, then verify the file disappears from the backend list.
6. Stop the storage backend: file actions should report unavailable while recovery monitoring remains usable.

## Complexity

For n files on the current page and m nodes:

- File normalization, rendering and search: O(n).
- Node cards: O(m).
- Selection lookup: O(n) to build the ID map, then O(1) lookup.
- Transfers: O(file_size) time. Extra multipart encoder memory is O(chunk_size), but native upload-widget memory is O(file_size), capped at 32 MiB per active session.
- Direct download: file payload bypasses Streamlit. Small authenticated fallback uses O(file_size), capped at 8 MiB plus temporary copies/framework overhead.
- Metadata: bounded response size (2 MiB each), bounded session cache (up to roughly 21 entries), default 100 records requested per page. These are per-session bounds, not a guarantee for unlimited users.
- Monitoring refresh is independent of file forms using a Streamlit fragment. It does not interrupt file selection every five seconds.

## Official implementation references

- [Streamlit file uploader](https://docs.streamlit.io/develop/api-reference/widgets/st.file_uploader): UploadedFile is a BytesIO subclass; the app caps its size.
- [Streamlit fragments](https://docs.streamlit.io/develop/api-reference/execution-flow/st.fragment): only monitoring views refresh periodically.

This frontend does not implement storage nodes, chunk placement, replication, metadata consensus, checksums on storage nodes or repair scheduling. It delegates those operations to the relevant backend.
