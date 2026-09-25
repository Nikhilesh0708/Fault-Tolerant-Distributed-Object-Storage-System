"""Test fixture only: ephemeral single-process data, no distribution or durability."""
from contextlib import contextmanager
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlsplit, unquote
import json
import uuid


def make_server(port=0):
    objects = {"sample": {"filename": "Welcome.txt", "content": b"Vault frontend integration fixture\n"}}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, status, payload=None):
            body = json.dumps(payload).encode() if payload is not None else b""
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/files":
                self.respond(200, {"files": [{"file_id": key, "filename": value["filename"],
                    "size": len(value["content"]), "replicas": 1, "status": "TEST_ONLY", "version": "v1"}
                    for key, value in objects.items()], "next_cursor": None})
            elif path.startswith("/download/"):
                key = unquote(path.removeprefix("/download/"))
                if key not in objects:
                    self.respond(404)
                    return
                value = objects[key]
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", 'attachment; filename="' + value["filename"].replace('"', '_') + '"')
                self.send_header("Content-Length", str(len(value["content"])))
                self.end_headers()
                self.wfile.write(value["content"])
            else:
                self.respond(404)

        def do_POST(self):
            if urlsplit(self.path).path != "/upload":
                self.respond(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length > 34 * 1024**2:
                self.respond(413)
                return
            body = self.rfile.read(length)
            prefix = ("MIME-Version: 1.0\r\nContent-Type: " + self.headers["Content-Type"] + "\r\n\r\n").encode()
            message = BytesParser(policy=policy.default).parsebytes(prefix + body)
            part = next(message.iter_parts(), None)
            if part is None or part.get_param("name", header="content-disposition") != "file":
                self.respond(422)
                return
            key = uuid.uuid4().hex
            objects[key] = {"filename": part.get_filename(), "content": part.get_payload(decode=True)}
            self.respond(201, {"file_id": key, "filename": objects[key]["filename"]})

        def do_DELETE(self):
            path = urlsplit(self.path).path
            key = unquote(path.removeprefix("/files/"))
            if not path.startswith("/files/") or key not in objects:
                self.respond(404)
                return
            del objects[key]
            self.respond(204)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


@contextmanager
def running_server():
    server = make_server()
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


if __name__ == "__main__":
    print("TEST FIXTURE ONLY: http://127.0.0.1:8765 — no replication or persistence", flush=True)
    make_server(8765).serve_forever()
