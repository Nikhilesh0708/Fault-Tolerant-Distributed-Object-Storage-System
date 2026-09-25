from __future__ import annotations

import hashlib

import httpx
import pytest

from backend import security


def test_all_nodes_healthy(cluster):
    with httpx.Client() as client:
        for url in cluster["node_urls"].values():
            resp = client.get(f"{url}/health")
            assert resp.status_code == 200
            assert resp.json()["status"] == "online"


def test_coordinator_nodes_wrapper(cluster):
    with httpx.Client() as client:
        resp = client.get(f"{cluster['backend_url']}/nodes")
        assert resp.status_code == 200
        body = resp.json()
        assert "nodes" in body
        assert len(body["nodes"]) == 4
        assert all(n["status"] == "online" for n in body["nodes"])


def test_upload_list_download_delete_roundtrip(cluster):
    backend = cluster["backend_url"]
    payload = b"hello vault, this is a durability test payload" * 100
    with httpx.Client(timeout=10.0) as client:
        resp = client.post(f"{backend}/upload", files={"file": ("notes.pdf", payload, "application/pdf")})
        assert resp.status_code == 201, resp.text
        body = resp.json()
        file_id = body["file_id"]
        assert body["filename"] == "notes.pdf"
        assert body["status"] == "HEALTHY"  # all 4 nodes healthy -> full replication achieved

        listing = client.get(f"{backend}/files").json()
        matches = [f for f in listing["files"] if f["file_id"] == file_id]
        assert len(matches) == 1
        assert matches[0]["size"] == len(payload)
        assert len(matches[0]["replicas"]) == 3

        download = client.get(f"{backend}/download/{file_id}")
        assert download.status_code == 200
        assert download.content == payload

        delete = client.delete(f"{backend}/files/{file_id}")
        assert delete.status_code == 200

        listing_after = client.get(f"{backend}/files").json()
        assert all(f["file_id"] != file_id for f in listing_after["files"])

        download_after = client.get(f"{backend}/download/{file_id}")
        assert download_after.status_code == 404


def test_upload_rejects_empty_file(cluster):
    backend = cluster["backend_url"]
    with httpx.Client() as client:
        resp = client.post(f"{backend}/upload", files={"file": ("empty.txt", b"", "text/plain")})
        assert resp.status_code == 422


def test_metadata_get_matches_authoritative_checksum(cluster):
    backend = cluster["backend_url"]
    payload = b"metadata round trip content"
    with httpx.Client() as client:
        upload_resp = client.post(f"{backend}/upload", files={"file": ("a.bin", payload)})
        file_id = upload_resp.json()["file_id"]

        meta = client.get(f"{backend}/metadata/{file_id}").json()
        assert meta["object_id"] == file_id
        assert meta["checksum"] == hashlib.sha256(payload).hexdigest()
        assert meta["revision"] == 1
        assert meta["committed"] is True
        assert meta["deleted"] is False


def test_replica_commit_conditional_success_and_conflict(cluster):
    backend = cluster["backend_url"]
    payload = b"replica commit semantics test"
    with httpx.Client() as client:
        upload_resp = client.post(f"{backend}/upload", files={"file": ("b.bin", payload)})
        file_id = upload_resp.json()["file_id"]
        meta = client.get(f"{backend}/metadata/{file_id}").json()
        assert meta["revision"] == 1
        used_nodes = {r["node_id"] for r in meta["replicas"]}
        free_node_id = next(n for n in cluster["node_urls"] if n not in used_nodes)
        free_node_url = cluster["node_urls"][free_node_id]

        checksum = meta["checksum"]
        size = meta["size"]
        replica_id = "gen-repair-1"

        # Simulate the recovery worker copying the object onto the 4th node.
        put = client.post(
            f"{free_node_url}/objects/{file_id}/replicate",
            params={"version": "v1", "replica_id": replica_id},
            headers={
                "Content-Type": "application/octet-stream",
                "X-Expected-SHA256": checksum,
                "X-Expected-Size": str(size),
                "Idempotency-Key": replica_id,
            },
            content=payload,
        )
        assert put.status_code == 200

        commit = client.post(
            f"{backend}/metadata/{file_id}/replica",
            headers={"If-Match": str(meta["revision"])},
            json={
                "expected_revision": meta["revision"],
                "version": "v1",
                "receipt": {
                    "node_id": free_node_id,
                    "replica_id": replica_id,
                    "version": "v1",
                    "size": size,
                    "checksum": checksum,
                    "durable": True,
                },
            },
        )
        assert commit.status_code == 200, commit.text
        new_meta = commit.json()["metadata"]
        assert new_meta["revision"] == meta["revision"] + 1
        assert len(new_meta["replicas"]) == 4

        # Stale revision must be rejected, not silently applied.
        stale = client.post(
            f"{backend}/metadata/{file_id}/replica",
            headers={"If-Match": str(meta["revision"])},  # old revision again
            json={
                "expected_revision": meta["revision"],
                "version": "v1",
                "receipt": {
                    "node_id": free_node_id,
                    "replica_id": "gen-repair-2",
                    "version": "v1",
                    "size": size,
                    "checksum": checksum,
                    "durable": True,
                },
            },
        )
        assert stale.status_code == 409


def test_node_rejects_corrupted_replica(cluster):
    node_url = next(iter(cluster["node_urls"].values()))
    with httpx.Client() as client:
        resp = client.post(
            f"{node_url}/objects/file-corrupt-test/replicate",
            params={"version": "v1", "replica_id": "gen-x"},
            headers={
                "Content-Type": "application/octet-stream",
                "X-Expected-SHA256": "0" * 64,
                "X-Expected-Size": "5",
                "Idempotency-Key": "gen-x",
            },
            content=b"hello",
        )
        assert resp.status_code == 422


def test_download_ticket_round_trip():
    token = security.make_download_ticket(object_id="file-abc", version="v1", ttl_seconds=60)
    assert security.verify_download_ticket(token, object_id="file-abc")
    assert not security.verify_download_ticket(token, object_id="file-other")


def test_retirement_token_scope_is_enforced():
    token = security.make_retirement_token(object_id="file-1", version="v1", node_id="node-1", replica_id="gen-a")
    assert security.verify_retirement_token(token, object_id="file-1", version="v1", node_id="node-1", replica_id="gen-a")
    assert not security.verify_retirement_token(token, object_id="file-1", version="v1", node_id="node-2", replica_id="gen-a")
    assert not security.verify_retirement_token(token, object_id="file-1", version="v1", node_id="node-1", replica_id="gen-b")
