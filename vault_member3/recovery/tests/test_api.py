from fastapi.testclient import TestClient
from ..api import create_app
from ..config import Config


def test_all_monitoring_and_control_endpoints():
    """Start actual FastAPI lifespan; exercise every demo/control route and its JSON."""
    app = create_app(Config(database_path=":memory:"), background=False)
    with TestClient(app) as client:
        for path in ["/healthz", "/nodes", "/repair-status", "/repair/tasks", "/metrics", "/events", "/integrity-status", "/rebalancing-status", "/openapi.json"]:
            response = client.get(path)
            assert response.status_code == 200, (path, response.text)
        assert client.post("/health/check").status_code == 200
        assert len(client.get("/nodes").json()["nodes"]) == 4
        assert client.post("/repair/start").status_code == 200
        assert client.post("/integrity/scan").status_code == 200
        assert client.post("/repair/file-123").status_code == 202
        assert client.post("/repair/missing").status_code == 404
        assert client.post("/rebalancing/pause").json()["paused"]
        assert client.post("/rebalancing/run").json()["status"] == "PAUSED"
        assert not client.post("/rebalancing/resume").json()["paused"]
        assert client.post("/rebalancing/run").status_code == 200
        assert client.post("/demo/nodes", json={"node_id": "node-5"}).status_code == 200
        assert client.post("/demo/faults", json={"node_id": "node-3", "action": "stop"}).status_code == 200
        assert client.post("/demo/faults", json={"node_id": "node-3", "action": "restart"}).status_code == 200
        assert client.post("/demo/faults", json={"node_id": "node-3", "action": "bad"}).status_code == 422


def test_admin_authentication_and_no_production_fault_routes():
    """Configure HTTP mode; writes need the token and destructive demo routes do not exist."""
    from ..demo.fake_backend import FakeBackend
    config = Config(mode="http", admin_token="test-secret", database_path=":memory:")
    app = create_app(config, FakeBackend(config), background=False)
    with TestClient(app) as client:
        assert client.post("/health/check").status_code == 401
        assert client.post("/health/check", headers={"Authorization": "Bearer test-secret"}).status_code == 200
        assert client.post("/demo/faults", json={"node_id": "node-3", "action": "stop"}).status_code == 404


def test_invalid_query_and_unknown_node_are_handled():
    """Send malformed input and unknown IDs; return structured errors without tracebacks."""
    with TestClient(create_app(Config(database_path=":memory:"), background=False)) as client:
        assert client.get("/repair/tasks?limit=-1").status_code == 422
        response = client.post("/demo/faults", json={"node_id": "unknown", "action": "stop"})
        assert response.status_code == 404
        assert "Traceback" not in response.text
