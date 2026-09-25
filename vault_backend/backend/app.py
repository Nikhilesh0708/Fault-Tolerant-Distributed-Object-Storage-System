"""Entry point for the combined coordinator + main storage API.

Run this on port 8000 -- the address the Streamlit frontend expects for the
main storage API (API_CONTRACT.md), and the address you should point the
recovery service's HTTP adapter's coordinator base URL at to satisfy
CONTRACT.md's coordinator endpoints. This is one process, one metadata
authority, matching the "single process/worker" boundary the recovery
package's own validation report documents for itself.
"""
from __future__ import annotations

import os

from fastapi import FastAPI

from .coordinator_routes import router as coordinator_router
from .frontend_routes import router as frontend_router
from .store import STORE


def _configured_nodes() -> dict[str, str]:
    raw = os.environ.get(
        "NODE_URLS",
        "node-1=http://127.0.0.1:9001,node-2=http://127.0.0.1:9002,"
        "node-3=http://127.0.0.1:9003,node-4=http://127.0.0.1:9004",
    )
    nodes = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        node_id, _, url = entry.partition("=")
        nodes[node_id.strip()] = url.strip()
    return nodes


def create_app() -> FastAPI:
    app = FastAPI(title="vault-backend", version="1.0.0")
    for node_id, url in _configured_nodes().items():
        STORE.register_node(node_id, url)
    app.include_router(frontend_router)
    app.include_router(coordinator_router)

    @app.get("/")
    def root():
        return {"service": "vault-backend", "nodes": list(_configured_nodes().keys())}

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("BACKEND_HOST", "127.0.0.1")
    port = int(os.environ.get("BACKEND_PORT", 8000))
    uvicorn.run("backend.app:app", host=host, port=port, reload=False)
