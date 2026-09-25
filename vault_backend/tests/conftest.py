from __future__ import annotations

import os
import socket
import threading
import time

import httpx
import pytest
import uvicorn


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def cluster(tmp_path_factory):
    node_ports = [_free_port() for _ in range(4)]
    backend_port = _free_port()
    node_ids = [f"node-{i + 1}" for i in range(4)]
    node_urls = {nid: f"http://127.0.0.1:{p}" for nid, p in zip(node_ids, node_ports)}

    os.environ["NODE_URLS"] = ",".join(f"{k}={v}" for k, v in node_urls.items())
    os.environ["COORDINATOR_SECRET"] = "test-secret"
    os.environ["REQUIRED_REPLICAS"] = "3"
    os.environ["WRITE_QUORUM"] = "2"
    os.environ["STORAGE_BEARER_TOKEN"] = ""

    from backend.app import create_app as create_backend_app
    from backend.node_app import create_app as create_node_app

    servers, threads = [], []

    for node_id, port in zip(node_ids, node_ports):
        data_dir = tmp_path_factory.mktemp(node_id)
        node_app = create_node_app(node_id, str(data_dir), capacity_bytes=10_000_000)
        config = uvicorn.Config(node_app, host="127.0.0.1", port=port, log_level="warning")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        servers.append(server)
        threads.append(thread)

    backend_app = create_backend_app()
    config = uvicorn.Config(backend_app, host="127.0.0.1", port=backend_port, log_level="warning")
    backend_server = uvicorn.Server(config)
    backend_thread = threading.Thread(target=backend_server.run, daemon=True)
    backend_thread.start()
    servers.append(backend_server)
    threads.append(backend_thread)

    deadline = time.time() + 15
    ready = False
    with httpx.Client() as client:
        while time.time() < deadline:
            try:
                node_ok = all(client.get(f"{u}/health", timeout=0.5).status_code == 200 for u in node_urls.values())
                backend_ok = client.get(f"http://127.0.0.1:{backend_port}/", timeout=0.5).status_code == 200
                if node_ok and backend_ok:
                    ready = True
                    break
            except Exception:
                pass
            time.sleep(0.1)
    if not ready:
        raise RuntimeError("cluster did not become ready in time")

    yield {
        "backend_url": f"http://127.0.0.1:{backend_port}",
        "node_urls": node_urls,
    }

    for server in servers:
        server.should_exit = True
    for thread in threads:
        thread.join(timeout=5)
