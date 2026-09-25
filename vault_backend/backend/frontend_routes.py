"""The main storage API, per API_CONTRACT.md. This is what the Streamlit
frontend talks to on port 8000.

Durability policy: each upload is fanned out to REQUIRED_REPLICAS nodes.
We commit metadata (and report success to the caller) once at least
WRITE_QUORUM replicas are confirmed durable; if fewer than that many nodes
accepted the write, we clean up any partial replicas and fail the upload
rather than claim durability we don't have. An object committed below
REQUIRED_REPLICAS is under-replicated but readable; the recovery service's
existing repair loop is what should bring it back up to full replication,
which is exactly the job CONTRACT.md assigns it.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from . import security
from .models import ReplicaRef
from .node_client import healthy_nodes_for_write, hash_file, replicate_file_to_node
from .store import STORE, NotFoundError

router = APIRouter()

REQUIRED_REPLICAS = int(os.environ.get("REQUIRED_REPLICAS", 3))
WRITE_QUORUM = int(os.environ.get("WRITE_QUORUM", 2))
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", 100 * 1024 * 1024))
STORAGE_BEARER_TOKEN = os.environ.get("STORAGE_BEARER_TOKEN", "")
PUBLIC_STORAGE_URL = os.environ.get("PUBLIC_STORAGE_URL", "http://127.0.0.1:8000")
TICKET_TTL_SECONDS = int(os.environ.get("TICKET_TTL_SECONDS", 300))

_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,254}$")


def sanitize_filename(name: str | None) -> str:
    if not name:
        return "upload.bin"
    base = os.path.basename(name).replace("\\", "_").replace("/", "_")
    base = base.strip().lstrip(".") or "upload.bin"
    if not _FILENAME_RE.match(base):
        base = re.sub(r"[^A-Za-z0-9._\-]", "_", base) or "upload.bin"
    return base[:255]


def require_bearer(authorization: str | None = Header(None)):
    if not STORAGE_BEARER_TOKEN:
        return  # auth disabled for this deployment
    expected = f"Bearer {STORAGE_BEARER_TOKEN}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="missing or invalid storage bearer token")


def _status_for(meta) -> str:
    n = len(meta.replicas)
    if n <= 0:
        return "UNKNOWN"
    if n >= meta.required_replicas:
        return "HEALTHY"
    return "DEGRADED"


@router.post("/upload", status_code=201, dependencies=[Depends(require_bearer)])
async def upload(request: Request, file: UploadFile = File(...)):
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="upload exceeds maximum allowed size")

    filename = sanitize_filename(file.filename)

    tmp = tempfile.NamedTemporaryFile(delete=False)
    tmp_path = Path(tmp.name)
    total = 0
    try:
        while True:
            chunk = await file.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                tmp.close()
                tmp_path.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="upload exceeds maximum allowed size")
            tmp.write(chunk)
        tmp.flush()
    finally:
        tmp.close()

    try:
        expected_hash, size = hash_file(tmp_path)
        if size == 0:
            raise HTTPException(status_code=422, detail="empty file uploads are rejected")

        candidates = await healthy_nodes_for_write()
        if not candidates:
            raise HTTPException(status_code=507, detail="no healthy storage nodes available")

        targets = candidates[:REQUIRED_REPLICAS]
        version = "v1"
        object_id = STORE.new_object_id()
        confirmed: list[ReplicaRef] = []
        async with httpx.AsyncClient() as client:
            for node in targets:
                try:
                    receipt = await replicate_file_to_node(
                        client, node, object_id=object_id, version=version, file_path=tmp_path,
                        expected_hash=expected_hash, expected_size=size,
                    )
                    confirmed.append(ReplicaRef(node_id=receipt.node_id, replica_id=receipt.replica_id, version=receipt.version))
                except Exception:
                    continue  # try remaining candidates; final quorum check below decides success

        if len(confirmed) < WRITE_QUORUM:
            # Don't leave orphaned bytes behind for an upload we're about to
            # report as failed -- best-effort immediate cleanup is safe here
            # because these replicas were never referenced by any committed
            # metadata, so there's no ambiguity for a reader to trip over.
            async with httpx.AsyncClient() as cleanup_client:
                for ref in confirmed:
                    node_url = STORE.known_node_urls().get(ref.node_id)
                    if not node_url:
                        continue
                    token = security.make_retirement_token(
                        object_id=object_id, version=ref.version, node_id=ref.node_id, replica_id=ref.replica_id
                    )
                    try:
                        await cleanup_client.delete(
                            f"{node_url}/objects/{object_id}",
                            params={"version": ref.version, "replica_id": ref.replica_id},
                            headers={"X-Retirement-Token": token, "If-Match": ref.replica_id},
                            timeout=5.0,
                        )
                    except Exception:
                        continue
            raise HTTPException(
                status_code=507,
                detail=f"could not durably write to enough storage nodes ({len(confirmed)}/{WRITE_QUORUM} required)",
            )

        meta = await STORE.create_object(
            object_id=object_id,
            object_key=filename,
            version=version,
            size=size,
            checksum=expected_hash,
            required_replicas=REQUIRED_REPLICAS,
            replicas=confirmed,
        )
        return JSONResponse(
            status_code=201,
            content={
                "file_id": meta.object_id,
                "object_id": meta.object_id,
                "filename": meta.object_key,
                "status": _status_for(meta),
            },
        )
    finally:
        tmp_path.unlink(missing_ok=True)


@router.get("/files")
async def list_files(limit: int = Query(100, ge=1, le=1000), cursor: str | None = Query(None)):
    page, next_cursor = await STORE.list_metadata(limit=limit, cursor=cursor)
    files = [
        {
            "file_id": m.object_id,
            "filename": m.object_key,
            "size": m.size,
            "replicas": [r.node_id for r in m.replicas],
            "status": _status_for(m),
            "version": m.version,
        }
        for m in page
        if not m.deleted
    ]
    return {"files": files, "next_cursor": next_cursor}


async def _stream_replica(object_id: str, meta) -> StreamingResponse:
    last_error: Exception | None = None
    # The client must outlive this function -- it's closed inside body_iter's
    # finally block, once the StreamingResponse has fully consumed it, not
    # when this function returns.
    for replica in meta.replicas:
        node_url = STORE.known_node_urls().get(replica.node_id)
        if not node_url:
            continue
        client = httpx.AsyncClient()
        try:
            req = client.build_request(
                "GET",
                f"{node_url}/objects/{object_id}",
                params={"version": replica.version, "replica_id": replica.replica_id},
            )
            resp = await client.send(req, stream=True)
            if resp.status_code != 200:
                await resp.aclose()
                await client.aclose()
                continue

            async def body_iter(resp=resp, client=client):
                try:
                    async for chunk in resp.aiter_bytes(65536):
                        yield chunk
                finally:
                    await resp.aclose()
                    await client.aclose()

            return StreamingResponse(
                body_iter(),
                media_type="application/octet-stream",
                headers={
                    "Content-Disposition": f'attachment; filename="{meta.object_key}"',
                    "Content-Length": str(meta.size),
                },
            )
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            await client.aclose()
            continue
    raise HTTPException(status_code=503, detail=f"no reachable replica for this object ({last_error})")


@router.get("/download/{file_id}")
async def download(file_id: str, ticket: str | None = Query(None), authorization: str | None = Header(None)):
    if STORAGE_BEARER_TOKEN:
        authorized = authorization == f"Bearer {STORAGE_BEARER_TOKEN}"
        if not authorized and ticket:
            authorized = security.verify_download_ticket(ticket, object_id=file_id)
        if not authorized:
            raise HTTPException(status_code=401, detail="missing or invalid credentials")

    try:
        meta = await STORE.get_metadata(file_id)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="file not found")
    if not meta.replicas:
        raise HTTPException(status_code=503, detail="object has no known readable replica")
    return await _stream_replica(file_id, meta)


@router.post("/download/{file_id}/ticket", dependencies=[Depends(require_bearer)])
async def download_ticket(file_id: str):
    try:
        meta = await STORE.get_metadata(file_id)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="file not found")
    token = security.make_download_ticket(object_id=file_id, version=meta.version, ttl_seconds=TICKET_TTL_SECONDS)
    return {"url": f"{PUBLIC_STORAGE_URL}/download/{file_id}?ticket={token}"}


@router.delete("/files/{file_id}", dependencies=[Depends(require_bearer)])
async def delete_file(file_id: str):
    try:
        meta = await STORE.get_metadata(file_id, include_deleted=False)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="file not found")

    tombstoned = await STORE.tombstone(file_id)

    # Best-effort synchronous physical cleanup. If a node is unreachable we
    # simply leave its bytes behind as an orphan for the recovery service's
    # coordinator-controlled garbage collection, per CONTRACT.md point 9 --
    # we never delete an ambiguous copy just because a timeout occurred, and
    # we never fail the client-visible delete because of it either, since the
    # tombstone (not physical erasure) is what makes the object gone.
    async with httpx.AsyncClient() as client:
        for replica in tombstoned.replicas:
            node_url = STORE.known_node_urls().get(replica.node_id)
            if not node_url:
                continue
            token = security.make_retirement_token(
                object_id=file_id, version=replica.version, node_id=replica.node_id, replica_id=replica.replica_id
            )
            try:
                await client.delete(
                    f"{node_url}/objects/{file_id}",
                    params={"version": replica.version, "replica_id": replica.replica_id},
                    headers={"X-Retirement-Token": token, "If-Match": replica.replica_id},
                    timeout=5.0,
                )
            except Exception:
                continue

    return JSONResponse(status_code=200, content={"file_id": file_id, "status": "deleted"})
