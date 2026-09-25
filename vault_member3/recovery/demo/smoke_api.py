"""Start a real loopback HTTP server, exercise the API, and stop only that child."""
import json
import os
import socket
import subprocess
import sys
import time
import httpx


def main():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = os.environ.copy()
    env.update(VAULT_MODE="demo", VAULT_DATABASE_PATH=":memory:", VAULT_ADMIN_TOKEN="",
               VAULT_REPAIR_INTERVAL="0.2", VAULT_HEARTBEAT_INTERVAL="0.2")
    process = subprocess.Popen([sys.executable, "-m", "uvicorn", "recovery.api:create_app", "--factory",
                                "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
                               env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10, trust_env=False) as client:
            deadline = time.monotonic() + 10
            while True:
                try:
                    client.get("/healthz").raise_for_status()
                    break
                except httpx.HTTPError:
                    if time.monotonic() > deadline or process.poll() is not None:
                        raise RuntimeError("Demo API failed to start")
                    time.sleep(0.05)
            checks = {}
            client.post("/health/check").raise_for_status()
            for route in ["/healthz", "/nodes", "/repair-status", "/repair/tasks", "/metrics", "/events", "/integrity-status", "/rebalancing-status", "/openapi.json"]:
                response = client.get(route)
                response.raise_for_status()
                checks["GET " + route] = response.status_code
            response = client.post("/demo/faults", json={"node_id": "node-3", "action": "stop"})
            response.raise_for_status()
            checks["POST /demo/faults"] = response.status_code
            client.post("/repair/start").raise_for_status()
            deadline = time.monotonic() + 10
            while client.get("/repair-status").json()["completed_repairs"] < 1:
                if time.monotonic() > deadline:
                    raise RuntimeError("Automatic repair did not complete")
                time.sleep(0.05)
            for route in ["/integrity/scan", "/repair/file-123", "/rebalancing/pause", "/rebalancing/run", "/rebalancing/resume"]:
                response = client.post(route)
                response.raise_for_status()
                checks["POST " + route] = response.status_code
            response = client.post("/demo/nodes", json={"node_id": "node-5"})
            response.raise_for_status()
            checks["POST /demo/nodes"] = response.status_code
            print(json.dumps({"loopback_server": True, "simulation_only": True, "checks": checks,
                              "completed_repairs": client.get("/repair-status").json()["completed_repairs"]}, indent=2))
    finally:
        process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()


if __name__ == "__main__":
    main()
