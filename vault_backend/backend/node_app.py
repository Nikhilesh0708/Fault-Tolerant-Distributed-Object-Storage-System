"""A storage node, per CONTRACT.md's storage-node endpoints.

Each node is an independent process owning one data directory. It never
trusts the coordinator's word for what bytes look like: every write is
hashed as it streams in and only published if it matches the caller's
declared SHA-256 and size. Every read streams the exact bytes on disk. All
mutation (write, delete) requires the caller to be the coordinator: writes
are unauthenticated placement decisions the coordinator already made by
choosing this node, deletes require a coordinator-issued retirement token.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from . import security
from .models import ReplicaReceipt

CHUNK_SIZE = 65536


def _paths(data_dir: Path, object_id: str, version: str, replica_id: str) -> tuple[Path, Path]:
    safe = f"{object_id}__{version}__{replica_id}".replace("/", "_")
    return data_dir / f"{safe}.bin", data_dir / f"{safe}.json"


def create_app(node_id: str, data_dir: str, capacity_bytes: int = 1_073_741_824) -> FastAPI:
    app = FastAPI(title=f"vault-storage-node-{node_id}")
    root = Path(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    app.state.node_id = node_id
    app.state.root = root
    app.state.capacity_bytes = capacity_bytes

    def used_bytes() -> int:
        total = 0
        for f in root.glob("*.bin"):
            try:
                total += f.stat().st_size
            except FileNotFoundError:
                continue
        return total

    @app.get("/health")
    def health():
        used = used_bytes()
        status = "full" if capacity_bytes and used / capacity_bytes > 0.98 else "online"
        return {
            "node_id": node_id,
            "status": status,
            "used_bytes": used,
            "capacity_bytes": capacity_bytes,
        }

    @app.get("/objects/{object_id}/checksum")
    def checksum(
        object_id: str,
        version: str = Query(...),
        replica_id: str = Query(...),
        recompute: bool = Query(False),
    ):
        bin_path, meta_path = _paths(root, object_id, version, replica_id)
        if not bin_path.exists():
            raise HTTPException(status_code=404, detail="replica not found")
        if not recompute and meta_path.exists():
            meta = json.loads(meta_path.read_text())
            return ReplicaReceipt(**meta)
        h = hashlib.sha256()
        size = 0
        with bin_path.open("rb") as fh:
            while True:
                chunk = fh.read(CHUNK_SIZE)
                if not chunk:
                    break
                h.update(chunk)
                size += len(chunk)
        return ReplicaReceipt(
            node_id=node_id,
            replica_id=replica_id,
            version=version,
            size=size,
            checksum=h.hexdigest(),
            durable=True,
        )

    @app.get("/objects/{object_id}")
    def get_object(object_id: str, version: str = Query(...), replica_id: str = Query(...)):
        bin_path, _ = _paths(root, object_id, version, replica_id)
        if not bin_path.exists():
            raise HTTPException(status_code=404, detail="replica not found")
        return FileResponse(
            path=str(bin_path),
            media_type="application/octet-stream",
            filename=f"{object_id}.bin",
        )

    @app.post("/objects/{object_id}/replicate")
    async def replicate(
        object_id: str,
        request: Request,
        version: str = Query(...),
        replica_id: str = Query(...),
        x_expected_sha256: str = Header(..., alias="X-Expected-SHA256"),
        x_expected_size: int = Header(..., alias="X-Expected-Size"),
        idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    ):
        expected_hash = x_expected_sha256.lower().strip()
        bin_path, meta_path = _paths(root, object_id, version, replica_id)

        # Idempotent replay / conflicting-bytes-under-same-id guard.
        if bin_path.exists() and meta_path.exists():
            existing = json.loads(meta_path.read_text())
            if existing.get("checksum") == expected_hash and existing.get("size") == x_expected_size:
                return ReplicaReceipt(**existing)
            raise HTTPException(
                status_code=409,
                detail="an immutable replica already exists under this id with different bytes",
            )

        if capacity_bytes and used_bytes() + x_expected_size > capacity_bytes:
            raise HTTPException(status_code=507, detail="insufficient storage capacity")

        h = hashlib.sha256()
        size = 0
        tmp = tempfile.NamedTemporaryFile(dir=root, delete=False, suffix=".part")
        try:
            async for chunk in request.stream():
                if not chunk:
                    continue
                h.update(chunk)
                size += len(chunk)
                tmp.write(chunk)
            tmp.flush()
            os.fsync(tmp.fileno())
        except Exception:
            tmp.close()
            try:
                os.unlink(tmp.name)
            except FileNotFoundError:
                pass
            raise HTTPException(status_code=422, detail="incomplete or cancelled upload body")
        finally:
            tmp.close()

        digest = h.hexdigest()
        if size != x_expected_size or digest != expected_hash:
            try:
                os.unlink(tmp.name)
            except FileNotFoundError:
                pass
            raise HTTPException(
                status_code=422,
                detail=f"checksum/size mismatch: expected {x_expected_size}b/{expected_hash}, got {size}b/{digest}",
            )

        os.replace(tmp.name, bin_path)  # atomic publish

        receipt = ReplicaReceipt(
            node_id=node_id,
            replica_id=replica_id,
            version=version,
            size=size,
            checksum=digest,
            durable=True,
        )
        meta_path.write_text(receipt.model_dump_json())
        return receipt

    @app.delete("/objects/{object_id}")
    def delete_object(
        object_id: str,
        version: str = Query(...),
        replica_id: str = Query(...),
        x_retirement_token: str = Header(..., alias="X-Retirement-Token"),
        if_match: str = Header(..., alias="If-Match"),
    ):
        if if_match != replica_id:
            raise HTTPException(status_code=412, detail="If-Match does not match replica_id")
        if not security.verify_retirement_token(
            x_retirement_token,
            object_id=object_id,
            version=version,
            node_id=node_id,
            replica_id=replica_id,
        ):
            raise HTTPException(status_code=403, detail="invalid or out-of-scope retirement token")
        bin_path, meta_path = _paths(root, object_id, version, replica_id)
        if not bin_path.exists():
            raise HTTPException(status_code=404, detail="replica not found")
        bin_path.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)
        return JSONResponse(status_code=200, content={"deleted": True})

    return app


if __name__ == "__main__":
    import uvicorn

    node_id = os.environ["NODE_ID"]
    data_dir = os.environ.get("NODE_DATA_DIR", f"./data/{node_id}")
    capacity = int(os.environ.get("NODE_CAPACITY_BYTES", 1_073_741_824))
    port = int(os.environ.get("NODE_PORT", 9001))
    host = os.environ.get("NODE_HOST", "127.0.0.1")
    uvicorn.run(create_app(node_id, data_dir, capacity), host=host, port=port)
