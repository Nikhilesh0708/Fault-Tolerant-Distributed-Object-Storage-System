"""Coordinator endpoints, per CONTRACT.md.

These are called by the recovery service (Member 3), not by the Streamlit
frontend. They let the recovery worker discover nodes, read authoritative
object metadata, push its own health observations, and propose conditional
replica changes (repair a missing replica, retire a stale one, rebalance).
"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query, Request

from . import security
from .models import NodeObservation, ObjectMetadata, ReplicaCommitRequest, ReplicaRef
from .node_client import list_live_nodes
from .store import STORE, ConflictError, NotFoundError

router = APIRouter()


@router.get("/nodes")
async def get_nodes():
    nodes = await list_live_nodes()
    return {"nodes": [n.model_dump() for n in nodes]}


@router.post("/nodes/{node_id}/observations", status_code=204)
async def post_observation(node_id: str, obs: NodeObservation):
    try:
        await STORE.record_observation(node_id, obs)
    except NotFoundError:
        raise HTTPException(status_code=404, detail=f"unknown node {node_id}")
    return None


@router.get("/metadata")
async def list_metadata(limit: int = Query(100, ge=1, le=1000), cursor: str | None = Query(None)):
    page, next_cursor = await STORE.list_metadata(limit=limit, cursor=cursor)
    return {"objects": [m.model_dump() for m in page], "next_cursor": next_cursor}


@router.get("/metadata/{object_id}")
async def get_metadata(object_id: str):
    try:
        meta = await STORE.get_metadata(object_id, include_deleted=False)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="object not found or deleted")
    return meta.model_dump()


@router.post("/metadata/{object_id}/replica")
async def commit_replica(
    object_id: str,
    body: ReplicaCommitRequest,
    if_match: str = Header(..., alias="If-Match"),
):
    try:
        expected_revision = int(if_match)
    except ValueError:
        raise HTTPException(status_code=400, detail="If-Match must be an integer revision")
    if expected_revision != body.expected_revision:
        raise HTTPException(status_code=400, detail="If-Match header and expected_revision body field disagree")

    # Validate the receipt against authoritative metadata before touching state.
    add_ref: ReplicaRef | None = None
    if body.receipt is not None:
        try:
            current = await STORE.get_metadata(object_id, include_deleted=True)
        except NotFoundError:
            raise HTTPException(status_code=404, detail="object not found")
        if not body.receipt.durable:
            raise HTTPException(status_code=422, detail="receipt is not durable")
        if body.receipt.checksum != current.checksum or body.receipt.size != current.size:
            raise HTTPException(status_code=422, detail="receipt does not match authoritative checksum/size")
        add_ref = ReplicaRef(node_id=body.receipt.node_id, replica_id=body.receipt.replica_id, version=body.receipt.version)

    try:
        new_meta = await STORE.commit_replica_change(
            object_id,
            expected_revision=expected_revision,
            version=body.version,
            add=add_ref,
            remove=body.remove,
        )
    except NotFoundError:
        raise HTTPException(status_code=404, detail="object not found")
    except ConflictError as exc:
        # Recovery is expected to reread authoritative state and retry.
        try:
            current = await STORE.get_metadata(object_id, include_deleted=True)
            detail = {"message": str(exc), "metadata": current.model_dump()}
        except NotFoundError:
            detail = {"message": str(exc)}
        raise HTTPException(status_code=409, detail=detail)

    delete_token = None
    if body.remove is not None:
        delete_token = security.make_retirement_token(
            object_id=object_id,
            version=body.remove.version,
            node_id=body.remove.node_id,
            replica_id=body.remove.replica_id,
        )

    return {"metadata": new_meta.model_dump(), "delete_token": delete_token}
