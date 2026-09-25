import asyncio
import hashlib
import json
import httpx
import pytest
from ..adapters import BackendError, Missing, Conflict, Unavailable, Blocked, Corrupt
from ..config import Config
from ..http_adapter import HttpBackend
from ..models import ObjectMetadata, ReplicaInfo, ReplicaReceipt, StorageNode


@pytest.mark.parametrize("code,error", [(404, Missing), (409, Conflict), (412, Conflict),
                                       (503, Unavailable), (507, Blocked), (422, Corrupt), (401, BackendError)])
def test_http_error_mapping(code, error):
    """Receive each upstream failure class; expose a safe typed error."""
    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(code)))
        adapter = HttpBackend(Config(), client)
        try:
            with pytest.raises(error):
                await adapter.metadata("file-123")
        finally:
            await adapter.close()
    asyncio.run(run())


def test_invalid_upstream_json_and_registry():
    """Receive malformed schemas; never treat them as healthy metadata or nodes."""
    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"bad": True})))
        adapter = HttpBackend(Config(), client)
        try:
            for operation in (adapter.nodes, lambda: adapter.metadata("file-123"), lambda: adapter.objects(None, 10)):
                with pytest.raises(BackendError):
                    await operation()
        finally:
            await adapter.close()
    asyncio.run(run())


def test_connection_error_mapping():
    """Raise an HTTP transport timeout; adapter surfaces Unavailable without raw request secrets."""
    async def run():
        def handler(request):
            raise httpx.ReadTimeout("secret raw URL", request=request)
        adapter = HttpBackend(Config(), httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        try:
            with pytest.raises(Unavailable, match="timed out"):
                await adapter.metadata("x")
        finally:
            await adapter.close()
    asyncio.run(run())


def test_rest_contract_methods_and_streams():
    """Mock the team's REST contract; check identity, chunking, CAS and authorized deletion."""
    async def run():
        payload = b"vault-test-data" * 100
        checksum = hashlib.sha256(payload).hexdigest()
        replica = ReplicaInfo(node_id="node-1", replica_id="replica-1", version="v1")
        meta = ObjectMetadata(object_id="object", object_key="object.bin", version="v1", revision=2,
                              size=len(payload), checksum=checksum, replicas=[replica])
        receipt = ReplicaReceipt(**replica.model_dump(), size=len(payload), checksum=checksum, durable=True)
        with pytest.raises(ValueError):
            ReplicaReceipt(**replica.model_dump(), size=len(payload), checksum=checksum)
        observed = []

        async def handler(request):
            path = request.url.path
            observed.append((request.method, path))
            if path == "/nodes":
                return httpx.Response(200, json={"nodes": [{"node_id": "node-1", "url": "http://node.test", "capacity_bytes": 10000}]})
            if path == "/metadata":
                assert request.url.params["limit"] == "2"
                return httpx.Response(200, json={"objects": [meta.model_dump()], "next_cursor": None})
            if path == "/metadata/object":
                return httpx.Response(200, json=meta.model_dump())
            if path == "/health":
                return httpx.Response(200, json={"node_id": "node-1", "status": "online", "used_bytes": 10, "capacity_bytes": 10000})
            if path.endswith("/observations"):
                return httpx.Response(204)
            if path.endswith("/checksum"):
                assert request.url.params["recompute"] == "true"
                return httpx.Response(200, json=receipt.model_dump())
            if path.endswith("/replicate"):
                assert await request.aread() == payload
                assert request.headers["x-expected-sha256"] == checksum
                return httpx.Response(200, json=receipt.model_dump())
            if path == "/metadata/object/replica":
                body = json.loads(await request.aread())
                assert request.headers["if-match"] == "2"
                assert body["expected_revision"] == 2 and body["version"] == "v1"
                return httpx.Response(200, json={"metadata": meta.model_dump(), "delete_token": "permit"})
            if request.method == "DELETE":
                assert request.headers["x-retirement-token"] == "permit"
                assert request.headers["if-match"] == replica.replica_id
                return httpx.Response(204)
            if request.method == "GET" and path == "/objects/object":
                return httpx.Response(200, content=payload)
            raise AssertionError((request.method, path))

        adapter = HttpBackend(Config(chunk_size=100), httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        try:
            assert len(await adapter.nodes()) == 1
            assert (await adapter.objects(None, 2))[1] is None
            assert (await adapter.metadata("object")).version == "v1"
            assert (await adapter.health("node-1")).status == "online"
            await adapter.publish_health(StorageNode(node_id="node-1", url="http://node.test"))
            assert (await adapter.inspect(meta, replica)).checksum == checksum
            parts = [chunk async for chunk in adapter.read(meta, replica)]
            assert b"".join(parts) == payload and max(map(len, parts)) <= 100
            async def chunks():
                for offset in range(0, len(payload), 100):
                    yield payload[offset:offset + 100]
            stored = await adapter.write(meta, "node-1", "replica-1", chunks())
            committed = await adapter.commit(meta, stored, replica)
            await adapter.delete(meta, replica, committed.delete_token)
            with pytest.raises(Blocked):
                await adapter.delete(meta, replica, "")
            assert len(observed) == 10
        finally:
            await adapter.close()
    asyncio.run(run())
