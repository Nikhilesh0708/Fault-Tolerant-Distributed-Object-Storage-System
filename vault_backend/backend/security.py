"""Scoped, signed tokens.

CONTRACT.md requires retirement deletions to be authorized by an opaque,
coordinator-issued token scoped to one exact replica generation, and
API_CONTRACT.md requires download tickets to be short-lived and scoped to a
single object. Both are implemented here as HMAC-signed, base64url JSON
payloads: nothing to store server-side, easy for a node to verify on its own,
and impossible to forge without the shared secret.

DEV NOTE: COORDINATOR_SECRET defaults to a fixed dev value. Anyone deploying
this beyond localhost must set a real secret via the environment variable of
the same name, or these tokens provide no real security.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any, Optional

_DEFAULT_SECRET = "dev-secret-change-me"
SECRET = os.environ.get("COORDINATOR_SECRET", _DEFAULT_SECRET)
if SECRET == _DEFAULT_SECRET:
    print(
        "[vault-backend] WARNING: COORDINATOR_SECRET not set; using an "
        "insecure default. Set COORDINATOR_SECRET before deploying beyond "
        "localhost."
    )


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64d(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def _sign(payload_bytes: bytes) -> str:
    mac = hmac.new(SECRET.encode("utf-8"), payload_bytes, hashlib.sha256).digest()
    return _b64e(mac)


def make_token(scope: dict[str, Any], ttl_seconds: int) -> str:
    payload = dict(scope)
    payload["exp"] = time.time() + ttl_seconds
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    body = _b64e(payload_bytes)
    sig = _sign(payload_bytes)
    return f"{body}.{sig}"


def read_token(token: str) -> Optional[dict[str, Any]]:
    """Verify signature and expiry; return the scope dict or None."""
    try:
        body, sig = token.split(".", 1)
        payload_bytes = _b64d(body)
        expected_sig = _sign(payload_bytes)
        if not hmac.compare_digest(sig, expected_sig):
            return None
        payload = json.loads(payload_bytes)
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


def make_retirement_token(*, object_id: str, version: str, node_id: str, replica_id: str, ttl_seconds: int = 600) -> str:
    return make_token(
        {
            "kind": "retirement",
            "object_id": object_id,
            "version": version,
            "node_id": node_id,
            "replica_id": replica_id,
        },
        ttl_seconds,
    )


def verify_retirement_token(token: str, *, object_id: str, version: str, node_id: str, replica_id: str) -> bool:
    payload = read_token(token)
    if not payload or payload.get("kind") != "retirement":
        return False
    return (
        payload.get("object_id") == object_id
        and payload.get("version") == version
        and payload.get("node_id") == node_id
        and payload.get("replica_id") == replica_id
    )


def make_download_ticket(*, object_id: str, version: str, ttl_seconds: int = 300) -> str:
    return make_token({"kind": "download", "object_id": object_id, "version": version}, ttl_seconds)


def verify_download_ticket(token: str, *, object_id: str) -> bool:
    payload = read_token(token)
    if not payload or payload.get("kind") != "download":
        return False
    return payload.get("object_id") == object_id
