import io
import json
import unittest
from unittest.mock import Mock
from dataclasses import replace
import requests
from frontend.api_client import APIClient, APIError, MultipartBody, normalize_file, safe_filename
from frontend.config import Config, validate_url
from tests.mock_storage import running_server


def response(data=None, status=200, raw=None):
    result = Mock()
    result.status_code = status
    result.iter_content.return_value = [raw if raw is not None else json.dumps(data).encode()]
    return result


class ClientTests(unittest.TestCase):
    def client(self, result=None, **settings):
        session = Mock()
        session.request.return_value = result
        return APIClient(Config(**settings), session), session

    def test_multipart_is_chunked_without_copying_source(self):
        source = io.BytesIO(b"abc" * 10000)
        progress = []
        body = MultipartBody(source, 'test\r\n".bin', 30000, 512, lambda a, b: progress.append(a))
        self.assertIs(body.parts[1], source)
        chunks = []
        while chunk := body.read():
            self.assertLessEqual(len(chunk), 512)
            chunks.append(chunk)
        self.assertEqual(sum(map(len, chunks)), len(body))
        self.assertEqual(progress[-1], 30000)
        self.assertNotIn(b'filename="test\r\n', b"".join(chunks))

    def test_empty_multipart(self):
        body = MultipartBody(io.BytesIO(), "empty.txt", 0, 65536)
        result = b""
        while part := body.read(100):
            result += part
        self.assertEqual(len(result), len(body))

    def test_incorrect_source_length_is_rejected(self):
        body = MultipartBody(io.BytesIO(b"a"), "a", 2, 10)
        with self.assertRaises(APIError):
            while body.read():
                pass

    def test_backend_error_is_sanitized_and_closed(self):
        resp = response({"detail": "secret traceback"}, status=500)
        client, _ = self.client(resp)
        with self.assertRaises(APIError) as error:
            client.get_files()
        self.assertNotIn("secret", str(error.exception))
        resp.close.assert_called_once()

    def test_timeout_has_uncertain_write_message(self):
        client, session = self.client()
        session.request.side_effect = requests.Timeout()
        with self.assertRaisesRegex(APIError, "may have completed"):
            client.delete_file("test")
        self.assertEqual(session.request.call_count, 1)

    def test_unavailable_service_is_reported(self):
        client, session = self.client()
        session.request.side_effect = requests.ConnectionError()
        with self.assertRaisesRegex(APIError, "Recovery service is unavailable"):
            client.get_nodes()

    def test_json_and_metadata_size_validation(self):
        client, _ = self.client(response(raw=b"bad-json"))
        with self.assertRaisesRegex(APIError, "invalid JSON"):
            client.get_files()
        client, _ = self.client(response(raw=b"01234567890"), metadata_limit_bytes=5)
        with self.assertRaisesRegex(APIError, "too large"):
            client.get_files()

    def test_file_normalization(self):
        record = normalize_file({"object_id": "x", "object_key": "notes.pdf", "size_bytes": 100,
                                 "replicas": [{"node_id": "a"}, {"node_id": "b"}]})
        self.assertEqual(record["replicas"], 2)
        self.assertEqual(record["status"], "UNKNOWN")
        with self.assertRaises(APIError):
            normalize_file({"id": "x", "size": -1})

    def test_paginated_and_legacy_file_lists(self):
        record = {"id": "x", "filename": "notes", "size": 0}
        client, session = self.client(response({"files": [record], "next_cursor": "next"}))
        self.assertEqual(client.get_files()["next_cursor"], "next")
        session.request.return_value = response([record])
        self.assertIsNone(client.get_files()["next_cursor"])
        session.request.return_value = response({"files": [], "next_cursor": "same"})
        with self.assertRaises(APIError):
            client.get_files("same")

    def test_healthz_and_tokens_use_correct_services(self):
        client, session = self.client(response({"status": "running"}), storage_token="store", recovery_token="recover")
        client.get_health()
        self.assertTrue(session.request.call_args.args[1].endswith(":8003/healthz"))
        self.assertEqual(session.request.call_args.kwargs["headers"]["Authorization"], "Bearer recover")
        client.delete_file("folder/file")
        self.assertTrue(session.request.call_args.args[1].endswith(":8000/files/folder%2Ffile"))
        self.assertEqual(session.request.call_args.kwargs["headers"]["Authorization"], "Bearer store")

    def test_small_download_enforces_limit_without_content_length(self):
        resp = response(raw=b"x" * (1024**2 + 1))
        client, _ = self.client(resp, small_download_limit_mb=1)
        with self.assertRaisesRegex(APIError, "direct download"):
            client.download_file("x")
        resp.close.assert_called_once()

    def test_upload_limit_before_network(self):
        client, session = self.client()
        with self.assertRaises(APIError):
            client.upload_file(io.BytesIO(), "large", 33 * 1024**2)
        session.request.assert_not_called()

    def test_download_link_origin_and_no_token_in_direct_url(self):
        client, session = self.client(response({"url": "https://other.example/file"}), storage_token="SECRET")
        self.assertNotIn("SECRET", client.download_url("some/file"))
        with self.assertRaises(APIError):
            client.get_download_link("x")
        session.request.return_value = response({"url": "http://127.0.0.1:8000/download/x?ticket=short-lived"})
        self.assertIn("ticket=", client.get_download_link("x"))

    def test_configuration_rejects_credential_urls(self):
        for url in ["file:///etc/passwd", "http://user:secret@localhost", "http://localhost?token=x", "localhost:8000"]:
            with self.assertRaises(ValueError):
                validate_url(url)
        self.assertNotIn("SECRET", repr(Config(storage_token="SECRET")))
        self.assertEqual(safe_filename("../../notes.txt"), "notes.txt")

    def test_real_http_upload_list_download_delete_roundtrip(self):
        with running_server() as url:
            client = APIClient(Config(storage_url=url, public_storage_url=url))
            try:
                payload = b"\x00\x01vault" * 50000
                uploaded = client.upload_file(io.BytesIO(payload), "roundtrip.bin", len(payload))
                key = uploaded["file_id"]
                self.assertIn(key, [f["file_id"] for f in client.get_files()["files"]])
                self.assertEqual(client.download_file(key), payload)
                client.delete_file(key)
                self.assertNotIn(key, [f["file_id"] for f in client.get_files()["files"]])
            finally:
                client.close()


if __name__ == "__main__":
    unittest.main()
