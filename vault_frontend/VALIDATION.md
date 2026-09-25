# Frontend validation

## Completed checks

- **20 automated tests passed** using Python unittest and Streamlit AppTest.
- All four pages rendered without application exceptions: Overview, Files, Recovery and Connections.
- A real local HTTP upload/list/download/delete round trip passed against an ephemeral test storage fixture. Downloaded bytes matched the upload.
- Multipart encoding was tested for bounded reads, empty files, header sanitization and inconsistent file length.
- Tests verified missing services, invalid JSON, oversized metadata, timeout handling, download size caps, authentication headers and independent service URLs.
- The health check uses the existing recovery service's `/healthz` endpoint.
- Delete without confirmation did not issue a DELETE request. Confirmed deletion did.
- A separate live-service smoke test launched the Member 3 recovery API and Streamlit server. Both returned HTTP 200 from their health endpoints. The dashboard showed one stored object, four of four nodes online, zero active repairs and one healthy object.
- A Chromium browser test uploaded through the real file picker, downloaded the result through the browser, compared bytes, and completed deletion only after checking confirmation.
- Desktop and narrow-screen screenshots were captured and inspected. The included preview screenshot uses explicitly labeled sample data, not live measurements.

## Reproduce

```bat
python -m unittest discover -s tests -v
python -m tests.live_smoke --recovery-root ../vault_member3
```

The optional `--browser` smoke-test flag requires Playwright and Chromium as development tools. They are not frontend runtime dependencies. The ordinary app requires only the two direct dependencies listed in `frontend/requirements.txt`.

Tested runtime: Python 3.12, Streamlit 1.64.0, Requests 2.34.2. The browser check used Playwright 1.51.0 with Chromium 134. Streamlit AppTest emits an expected missing ScriptRunContext warning when launched from unittest; all tests pass.

## Boundaries

The frontend has not been tested against your teammates' unseen production storage backend. Upload/download/delete are implemented and tested against the documented contract, but a compatible backend is still required. Member 3 integration uses its simulated-node demo; it is not a physical-machine resilience test.

The native upload widget buffers files, so uploads are capped at 32 MiB. A 1 GB browser upload is intentionally unsupported. Direct downloads bypass Streamlit; authenticated in-app downloads are capped at 8 MiB. Large authenticated downloads need the optional short-lived ticket endpoint.

The dashboard is a localhost/trusted-user prototype with no login, role model or production deployment configuration. Metadata status is a cached, last-scan observation rather than a continuous availability guarantee. The read-only design preview is never substituted automatically for unavailable live services.
