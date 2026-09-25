"""All network communication lives here. No automatic retries of writes/deletes."""
import io
import json
import math
import re
import uuid
from urllib.parse import quote, urlsplit
from typing import BinaryIO, Callable
import requests
from .config import Config


class APIError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def safe_filename(name: str) -> str:
    name = re.split(r"[/\\]", name)[-1]
    name = re.sub(r'[\x00-\x1f\x7f"\\]', "_", name).strip(" .")
    return name[:180] or "download.bin"


def integer(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise APIError(f"Backend returned an invalid {label}.")
    return value


def normalize_file(item: dict) -> dict:
    if not isinstance(item, dict):
        raise APIError("Backend returned an invalid file record.")
    file_id = item.get("file_id", item.get("object_id", item.get("id")))
    if not isinstance(file_id, str) or not file_id:
        raise APIError("A file record is missing its identifier.")
    name = item.get("filename", item.get("object_key", item.get("name", file_id)))
    if not isinstance(name, str):
        raise APIError("Backend returned an invalid filename.")
    replicas = item.get("replicas", item.get("replica_count"))
    if isinstance(replicas, list):
        replicas = len(replicas)
    if replicas is not None:
        replicas = integer(replicas, "replica count")
    return {"file_id": file_id, "filename": name[:512],
            "size": integer(item.get("size", item.get("size_bytes", 0)), "file size"),
            "replicas": replicas, "status": str(item.get("status", "UNKNOWN")).upper()[:40],
            "version": str(item.get("version", ""))[:100]}


class MultipartBody:
    """File-like multipart encoder: requests must not construct a full multipart copy."""
    def __init__(self, source: BinaryIO, filename: str, size: int, chunk_size: int,
                 progress: Callable[[int, int], None] | None = None):
        self.boundary = "vault-" + uuid.uuid4().hex
        ascii_name = safe_filename(filename)
        prefix = (f"--{self.boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
                  f"filename=\"{ascii_name}\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode()
        suffix = f"\r\n--{self.boundary}--\r\n".encode()
        self.parts = [io.BytesIO(prefix), source, io.BytesIO(suffix)]
        self.size, self.chunk_size, self.progress = size, chunk_size, progress
        self.length = len(prefix) + size + len(suffix)
        self.index, self.sent, self.position = 0, 0, 0
        source.seek(0)

    def __len__(self):
        return self.length

    def tell(self):
        return self.position

    def read(self, size=-1):
        if size == 0:
            return b""
        limit = min(size, self.chunk_size) if size and size > 0 else self.chunk_size
        while self.index < 3:
            # Bounded partial reads are valid for this file-like upload body.
            chunk = self.parts[self.index].read(limit)
            if chunk:
                if self.index == 1:
                    self.sent += len(chunk)
                    if self.sent > self.size:
                        raise APIError("File changed while uploading. Select it again.")
                    if self.progress:
                        self.progress(self.sent, self.size)
                self.position += len(chunk)
                return chunk
            if self.index == 1 and self.sent != self.size:
                raise APIError("File ended before its declared size. Select it again.")
            self.index += 1
        return b""


class APIClient:
    def __init__(self, config: Config, session=None):
        self.config = config
        self.session = session or requests.Session()
        self.session.trust_env = False

    def close(self):
        self.session.close()

    def _request(self, method, path, *, recovery=False, timeout=None, **kwargs):
        base = self.config.recovery_url if recovery else self.config.storage_url
        token = self.config.recovery_token if recovery else self.config.storage_token
        headers = kwargs.pop("headers", {})
        if token:
            headers["Authorization"] = "Bearer " + token
        try:
            response = self.session.request(method, base.rstrip("/") + path,
                headers=headers, timeout=(self.config.connect_timeout, timeout or self.config.read_timeout),
                allow_redirects=False, stream=True, **kwargs)
        except requests.Timeout as exc:
            message = ("The request timed out. Check the file list before retrying; the backend may have completed it."
                       if method != "GET" else "The service took too long to respond. Try refreshing.")
            raise APIError(message) from exc
        except requests.RequestException as exc:
            service = "Recovery" if recovery else "Storage"
            raise APIError(f"{service} service is unavailable. Check its address and start the server.") from exc
        if response.status_code >= 300:
            code = response.status_code
            response.close()
            messages = {401: "Authentication required. Check the service token in Connections.",
                        403: "This token does not have permission for that action.",
                        404: "The requested file or API route was not found.",
                        409: "The object changed. Refresh the file list before trying again.",
                        412: "The object version changed. Refresh before trying again.",
                        413: "The backend rejected the file size. Choose a smaller file.",
                        422: "The backend rejected the request format. Check its API contract.",
                        429: "The service is busy. Wait a moment before retrying.",
                        507: "The storage service has no space available."}
            raise APIError(messages.get(code, "The service could not complete this request."), code)
        return response

    def _json(self, method, path, *, recovery=False, return_status=False, **kwargs):
        response = self._request(method, path, recovery=recovery, **kwargs)
        try:
            if response.status_code == 204:
                return ({}, 204) if return_status else {}
            body = bytearray()
            for chunk in response.iter_content(self.config.chunk_size):
                if len(body) + len(chunk) > self.config.metadata_limit_bytes:
                    raise APIError("Metadata response is too large. Ask the backend to support pagination.")
                body.extend(chunk)
            data = json.loads(body)
            return (data, response.status_code) if return_status else data
        except (ValueError, UnicodeError) as exc:
            raise APIError("The service returned invalid JSON.") from exc
        except requests.RequestException as exc:
            raise APIError("The connection was interrupted while reading the response.") from exc
        finally:
            response.close()

    def get_files(self, cursor=None):
        params = {"limit": self.config.page_size}
        if cursor is not None:
            params["cursor"] = cursor
        data = self._json("GET", "/files", params=params)
        if isinstance(data, list):
            rows, next_cursor = data, None
        elif isinstance(data, dict) and isinstance(data.get("files"), list):
            rows, next_cursor = data["files"], data.get("next_cursor")
        else:
            raise APIError("The file list must contain a 'files' array.")
        if next_cursor is not None and not isinstance(next_cursor, str):
            raise APIError("Invalid pagination cursor.")
        if next_cursor is not None and next_cursor == cursor:
            raise APIError("The backend pagination cursor did not advance.")
        return {"files": [normalize_file(row) for row in rows[:2000]], "next_cursor": next_cursor,
                "truncated": len(rows) > 2000}

    def upload_file(self, source, filename, size, progress=None):
        if not 0 <= size <= self.config.upload_limit_mb * 1024**2:
            raise APIError(f"Choose a file no larger than {self.config.upload_limit_mb} MiB.")
        body = MultipartBody(source, filename, size, self.config.chunk_size, progress)
        data, status = self._json("POST", "/upload", data=body, timeout=self.config.upload_timeout, return_status=True,
                         headers={"Content-Type": "multipart/form-data; boundary=" + body.boundary,
                                  "Content-Length": str(len(body)), "Idempotency-Key": uuid.uuid4().hex})
        if not isinstance(data, dict) or not any(data.get(k) for k in ("file_id", "object_id", "id")):
            raise APIError("Upload response has no file ID. Check the file list before retrying.")
        data["accepted_only"] = status == 202
        return data

    def delete_file(self, file_id):
        response = self._request("DELETE", "/files/" + quote(file_id, safe=""))
        status = response.status_code
        response.close()
        return status

    def download_file(self, file_id):
        """Small authenticated download only; capped even if the server omits Content-Length."""
        response = self._request("GET", "/download/" + quote(file_id, safe=""))
        maximum = self.config.small_download_limit_mb * 1024**2
        try:
            body = bytearray()
            for chunk in response.iter_content(self.config.chunk_size):
                if len(body) + len(chunk) > maximum:
                    raise APIError(f"This file needs a direct download link; it exceeds the {self.config.small_download_limit_mb} MiB in-app limit.")
                body.extend(chunk)
            return bytes(body)
        except requests.RequestException as exc:
            raise APIError("Download was interrupted. Try again.") from exc
        finally:
            response.close()

    def download_url(self, file_id):
        return self.config.public_storage_url.rstrip("/") + "/download/" + quote(file_id, safe="")

    def get_download_link(self, file_id):
        data = self._json("POST", "/download/" + quote(file_id, safe="") + "/ticket")
        link = data.get("url") if isinstance(data, dict) else None
        if not isinstance(link, str):
            raise APIError("The backend did not return a download link.")
        parsed, expected = urlsplit(link), urlsplit(self.config.public_storage_url)
        if (parsed.scheme, parsed.netloc) != (expected.scheme, expected.netloc) or parsed.username or parsed.password:
            raise APIError("Download links must use the configured public storage origin.")
        return link

    def _dict(self, path, recovery=True):
        data = self._json("GET", path, recovery=recovery)
        if not isinstance(data, dict):
            raise APIError("The service returned an unexpected response format.")
        return data

    def get_nodes(self):
        data = self._dict("/nodes")
        if not isinstance(data.get("nodes"), list):
            raise APIError("The node response must contain a 'nodes' array.")
        for node in data["nodes"]:
            if not isinstance(node, dict) or not isinstance(node.get("node_id"), str):
                raise APIError("The service returned an invalid node record.")
            integer(node.get("used_bytes", 0), "storage usage")
            integer(node.get("capacity_bytes", 0), "storage capacity")
            if not isinstance(node.get("status", "UNKNOWN"), str):
                raise APIError("Invalid node status.")
        return data["nodes"]

    def get_repair_status(self):
        data = self._dict("/repair-status")
        if not isinstance(data.get("system_status"), str) or not isinstance(data.get("repairs", []), list):
            raise APIError("Invalid repair status response.")
        if any(not isinstance(t, dict) for t in data.get("repairs", [])):
            raise APIError("Invalid repair task record.")
        for field in ("active_repairs", "completed_repairs", "failed_repairs"):
            if field in data:
                integer(data[field], field)
        for task in data.get("repairs", []):
            progress = task.get("progress", 0)
            if not isinstance(progress, (int, float)) or not math.isfinite(progress):
                raise APIError("Invalid repair progress.")
        return data

    def get_health(self):
        data = self._dict("/healthz")
        if data.get("status") != "running":
            raise APIError("The recovery service did not report a running state.")
        return data

    def get_metrics(self):
        data = self._dict("/metrics")
        for field in ("total_objects", "healthy_objects", "degraded_objects"):
            if field in data:
                integer(data[field], field)
        return data

    def get_events(self):
        data = self._dict("/events?limit=20")
        events = data.get("events", [])
        if not isinstance(events, list) or any(not isinstance(e, dict) for e in events):
            raise APIError("Invalid event list.")
        return events

    def recovery_action(self, action):
        paths = {"check": "/health/check", "scan": "/integrity/scan", "pause": "/rebalancing/pause",
                 "resume": "/rebalancing/resume", "rebalance": "/rebalancing/run"}
        if action not in paths:
            raise APIError("Unknown recovery action.")
        return self._json("POST", paths[action], recovery=True, timeout=120)
