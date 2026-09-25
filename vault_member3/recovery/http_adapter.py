"""Real async REST adapter. See CONTRACT.md before enabling HTTP mode."""
from urllib.parse import quote, urlsplit
import httpx
from pydantic import ValidationError
from .adapters import BackendError, Unavailable, Missing, Conflict, Corrupt, Blocked
from .models import StorageNode, ObjectMetadata, ReplicaReceipt, CommitResult, HealthCheckResult


def segment(value):
    return quote(str(value), safe="")


class HttpBackend:
    def __init__(self, config, client=None):
        self.config = config
        headers = {"Authorization": "Bearer " + config.backend_token} if config.backend_token else {}
        self.client = client or httpx.AsyncClient(timeout=config.request_timeout, headers=headers,
                                                 follow_redirects=False, trust_env=False)
        self.base = config.coordinator_url.rstrip("/")
        self.registry = {}

    @staticmethod
    def check(response):
        if response.status_code == 404:
            raise Missing("Object or replica not found")
        if response.status_code in {409, 412}:
            raise Conflict("Metadata changed; retry with current version")
        if response.status_code == 507:
            raise Blocked("Storage capacity unavailable")
        if response.status_code == 422:
            raise Corrupt("Upstream rejected content or checksum")
        if response.status_code >= 500:
            raise Unavailable("Upstream service unavailable")
        if response.status_code >= 300:
            raise BackendError(f"Upstream request rejected ({response.status_code})")

    async def request(self, method, url, **kwargs):
        try:
            response = await self.client.request(method, url, **kwargs)
            self.check(response)
            return response
        except httpx.HTTPError as exc:
            raise Unavailable("Upstream connection failed or timed out") from exc

    @staticmethod
    def parse(response, model):
        try:
            return model.model_validate(response.json())
        except (ValidationError, ValueError) as exc:
            raise BackendError("Invalid upstream JSON schema") from exc

    async def nodes(self):
        response = await self.request("GET", self.base + "/nodes")
        try:
            nodes = [StorageNode.model_validate(n) for n in response.json()["nodes"]]
            if len({n.node_id for n in nodes}) != len(nodes):
                raise ValueError("Duplicate node IDs")
            for node in nodes:
                url = urlsplit(node.url)
                if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
                    raise ValueError("Invalid node URL")
            self.registry = {n.node_id: n.url.rstrip("/") for n in nodes}
            return nodes
        except (KeyError, TypeError, ValueError) as exc:
            raise BackendError("Invalid node registry response") from exc

    def node_url(self, node_id):
        if node_id not in self.registry:
            raise Unavailable("Node absent from coordinator registry")
        return self.registry[node_id]

    async def objects(self, cursor, limit):
        params = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        response = await self.request("GET", self.base + "/metadata", params=params)
        try:
            body = response.json()
            items = [ObjectMetadata.model_validate(m) for m in body["objects"]]
            next_cursor = body.get("next_cursor")
            if len(items) > limit or (next_cursor is not None and not isinstance(next_cursor, str)):
                raise ValueError("Invalid pagination")
            return items, next_cursor
        except (KeyError, TypeError, ValueError) as exc:
            raise BackendError("Invalid metadata page") from exc

    async def metadata(self, object_id):
        response = await self.request("GET", self.base + "/metadata/" + segment(object_id))
        return self.parse(response, ObjectMetadata)

    async def health(self, node_id):
        response = await self.request("GET", self.node_url(node_id) + "/health")
        return self.parse(response, HealthCheckResult)

    async def publish_health(self, node):
        await self.request("POST", self.base + "/nodes/" + segment(node.node_id) + "/observations",
                           json=node.model_dump(mode="json"))

    async def inspect(self, meta, replica):
        response = await self.request("GET", self.node_url(replica.node_id) + "/objects/" + segment(meta.object_id) + "/checksum",
                                      params={"version": meta.version, "replica_id": replica.replica_id, "recompute": "true"})
        return self.parse(response, ReplicaReceipt)

    async def read(self, meta, replica):
        try:
            async with self.client.stream("GET", self.node_url(replica.node_id) + "/objects/" + segment(meta.object_id),
                                          params={"version": meta.version, "replica_id": replica.replica_id},
                                          headers={"Accept-Encoding": "identity"}) as response:
                self.check(response)
                async for chunk in response.aiter_bytes(self.config.chunk_size):
                    yield chunk
        except httpx.HTTPError as exc:
            raise Unavailable("Replica stream interrupted") from exc

    async def write(self, meta, node_id, replica_id, chunks):
        response = await self.request("POST", self.node_url(node_id) + "/objects/" + segment(meta.object_id) + "/replicate",
            params={"version": meta.version, "replica_id": replica_id}, content=chunks,
            headers={"Content-Type": "application/octet-stream", "X-Expected-SHA256": meta.checksum,
                     "X-Expected-Size": str(meta.size), "Idempotency-Key": replica_id})
        return self.parse(response, ReplicaReceipt)

    async def commit(self, meta, receipt, remove=None):
        response = await self.request("POST", self.base + "/metadata/" + segment(meta.object_id) + "/replica",
            json={"expected_revision": meta.revision, "version": meta.version,
                  "receipt": receipt.model_dump(), "remove": remove.model_dump() if remove else None},
            headers={"If-Match": str(meta.revision)})
        return self.parse(response, CommitResult)

    async def delete(self, meta, replica, token):
        if not token:
            raise Blocked("Safe deletion requires a coordinator-issued retirement token")
        await self.request("DELETE", self.node_url(replica.node_id) + "/objects/" + segment(meta.object_id),
            params={"version": replica.version, "replica_id": replica.replica_id},
            headers={"X-Retirement-Token": token, "If-Match": replica.replica_id})

    async def close(self):
        await self.client.aclose()
