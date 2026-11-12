from __future__ import annotations

import io
import asyncio
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import imageio.v3 as iio
import numpy as np
from starlette.testclient import TestClient

from videoforge_mcp.http import MAX_UPLOAD_BYTES, create_app, main


class HttpFlowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.client = TestClient(create_app(self.root))
        video = self.root / "fixture.mp4"
        frames = [np.full((32, 32, 3), index * 35, dtype=np.uint8) for index in range(5)]
        iio.imwrite(video, frames, fps=5)
        self.video = video.read_bytes()

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def upload(self, project="alpha"):
        return self.client.post(f"/api/projects/{project}/uploads", files={"file": ("clip.mp4", self.video, "video/mp4")})

    def test_upload_extract_preview_selection_and_download(self):
        uploaded = self.upload()
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        upload_id = uploaded.json()["upload_id"]
        source = self.root / "http/projects/alpha/uploads" / (upload_id + ".mp4")
        self.assertEqual(source.read_bytes(), self.video)
        result = self.client.post("/api/projects/alpha/extractions", json={"upload_id": upload_id, "target_n": 2})
        self.assertEqual(result.status_code, 201, result.text)
        body = result.json()
        self.assertEqual([x["frame_index"] for x in body["candidates"]], [0, 1, 3, 4])
        self.assertEqual(len(body["candidates"]), 4)
        self.assertNotIn("path", body["source"])
        self.assertNotIn("path", body["candidates"][0])
        base = f"/api/projects/alpha/extractions/{body['extraction_id']}"
        self.assertEqual(self.client.get(base).json()["candidates"], body["candidates"])
        first = body["candidates"][0]
        self.assertEqual(self.client.get(first["preview_url"]).headers["content-type"], "image/png")
        self.assertIn("attachment", self.client.get(first["download_url"]).headers["content-disposition"])
        selected = self.client.post(base + "/selection", json={"selected_candidate_ids": [first["candidate_id"]]})
        self.assertEqual(selected.status_code, 201, selected.text)
        self.assertEqual(self.client.get(base + "/selection").json()["selected_candidate_ids"], [first["candidate_id"]])
        archive = self.client.get(base + "/download.zip?selected=true")
        self.assertEqual(archive.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(archive.content)) as contents:
            self.assertEqual(contents.namelist(), ["candidates.json", "candidates/" + first["candidate_id"] + ".png"])
        self.assertEqual(source.read_bytes(), self.video)

    def test_bad_files_limits_and_project_isolation(self):
        bad = self.client.post("/api/projects/alpha/uploads", files={"file": ("clip.mp4", b"garbage", "video/mp4")})
        self.assertEqual(bad.status_code, 415)
        wrong = self.client.post("/api/projects/alpha/uploads", files={"file": ("clip.txt", self.video, "video/mp4")})
        self.assertEqual(wrong.status_code, 415)
        uploaded = self.upload().json()["upload_id"]
        self.assertEqual(self.client.post("/api/projects/beta/extractions", json={"upload_id": uploaded, "target_n": 2}).status_code, 404)
        self.assertEqual(self.client.post("/api/projects/alpha/extractions", json={"upload_id": "../bad", "target_n": 2}).status_code, 400)
        self.assertEqual(self.client.post("/api/projects/alpha/extractions", json={"upload_id": uploaded, "target_n": 61}).status_code, 400)
        self.assertEqual(self.client.post("/api/projects/alpha/extractions", json={"upload_id": uploaded, "target_n": 2, "source_path": "/etc/passwd"}).status_code, 400)
        self.assertEqual(self.client.post("/api/projects/../uploads", files={"file": ("x.mp4", self.video)}).status_code, 404)

    def test_failed_extraction_does_not_publish_and_retry_uses_new_id(self):
        uploaded = self.upload().json()["upload_id"]
        source = self.root / "http/projects/alpha/uploads" / (uploaded + ".mp4")
        source.write_bytes(b"broken")
        response = self.client.post("/api/projects/alpha/extractions", json={"upload_id": uploaded, "target_n": 2})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(list((self.root / "http/projects/alpha/extractions").glob("*")), [])
        source.write_bytes(self.video)
        good = self.client.post("/api/projects/alpha/extractions", json={"upload_id": uploaded, "target_n": 2})
        self.assertEqual(good.status_code, 201, good.text)
        base = f"/api/projects/alpha/extractions/{good.json()['extraction_id']}"
        self.assertEqual(self.client.get(base + "/candidates/not-here/download").status_code, 404)
        self.assertEqual(self.client.get(base + "/download.zip?selected=true").status_code, 404)
        self.assertEqual(self.client.post(base + "/selection", json={"selected_candidate_ids": ["unknown"]}).status_code, 400)
        candidate = good.json()["candidates"][0]
        frame = self.root / "http/projects/alpha/extractions" / good.json()["extraction_id"] / (candidate["candidate_id"] + ".png")
        frame.write_bytes(b"changed")
        self.assertEqual(self.client.get(base + "/download.zip").status_code, 409)

    def test_project_symlink_conflict(self):
        parent = self.root / "http/projects"
        parent.mkdir(parents=True)
        (parent / "beta").mkdir()
        (parent / "alpha").symlink_to(parent / "beta")
        response = self.upload()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "path_conflict")

    def test_health_and_loopback_entrypoint(self):
        self.assertEqual(self.client.get("/api/health").json(), {"status": "ok", "service": "videoforge-http"})
        with patch("uvicorn.run") as run:
            main()
            self.assertEqual(run.call_args.kwargs["host"], "127.0.0.1")

    def test_job_reference_and_limits(self):
        job_id = "a" * 32
        jobs = self.root / "jobs"
        jobs.mkdir()
        source = self.root / (job_id + ".mp4")
        source.write_bytes(self.video)
        (jobs / (job_id + ".json")).write_text(json.dumps({"job_id": job_id, "status": "completed", "output_path": str(source)}))
        with TestClient(create_app(self.root, jobs)) as client:
            result = client.post("/api/projects/alpha/uploads/references", json={"job_id": job_id})
            self.assertEqual(result.status_code, 201, result.text)
            self.assertEqual(source.read_bytes(), self.video)
            copied = self.root / "http/projects/alpha/uploads" / (result.json()["upload_id"] + ".mp4")
            self.assertEqual(copied.read_bytes(), self.video)
            self.assertEqual(client.post("/api/projects/alpha/uploads/references", json={"job_id": "../a"}).status_code, 400)
            (jobs / (job_id + ".json")).write_text(json.dumps({"job_id": job_id, "status": "completed", "output_path": "/etc/passwd"}))
            self.assertEqual(client.post("/api/projects/alpha/uploads/references", json={"job_id": job_id}).status_code, 409)
            with patch("videoforge_mcp.http.MAX_UPLOAD_BYTES", 8):
                self.assertEqual(client.post("/api/projects/alpha/uploads", files={"file": ("clip.mp4", self.video)}).status_code, 413)
            with patch("videoforge_mcp.http.MAX_FRAMES", 2):
                self.assertEqual(client.post("/api/projects/alpha/uploads", files={"file": ("clip.mp4", self.video)}).status_code, 413)

    def test_upload_exact_byte_limit_and_one_byte_over(self):
        self.assertEqual(MAX_UPLOAD_BYTES, 512 * 1024 * 1024)
        limit = 64
        prefix = b"\0\0\0\0ftyp\0\0\0\0"
        with patch("videoforge_mcp.http.MAX_UPLOAD_BYTES", limit), patch("videoforge_mcp.http._frame_count", return_value=1):
            exact = self.client.post("/api/projects/alpha/uploads", files={"file": ("clip.mp4", prefix + b"x" * (limit - len(prefix)), "video/mp4")})
            self.assertEqual(exact.status_code, 201, exact.text)
            self.assertEqual(exact.json()["size_bytes"], limit)
            over = self.client.post("/api/projects/alpha/uploads", files={"file": ("clip.mp4", prefix + b"x" * (limit + 1 - len(prefix)), "video/mp4")})
            self.assertEqual(over.status_code, 413, over.text)
            self.assertEqual(over.json()["error"]["code"], "upload_too_large")
        uploads = self.root / "http/projects/alpha/uploads"
        self.assertEqual(len(list(uploads.glob("*.mp4"))), 1)
        self.assertEqual(list(uploads.glob("*.part.mp4")), [])

    def test_streaming_multipart_limit_without_content_length(self):
        boundary = "test-boundary"
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"clip.mp4\"\r\n"
                "Content-Type: video/mp4\r\n\r\n").encode() + b"\0\0\0\0ftyp\0\0\0\0" + b"x" * 300 + f"\r\n--{boundary}--\r\n".encode()
        messages = iter([{"type": "http.request", "body": body[:100], "more_body": True},
                         {"type": "http.request", "body": body[100:], "more_body": False}])
        sent = []

        async def receive():
            return next(messages)

        async def send(message):
            sent.append(message)

        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
                 "scheme": "http", "path": "/api/projects/alpha/uploads", "raw_path": b"/api/projects/alpha/uploads",
                 "query_string": b"", "root_path": "", "headers": [(b"content-type", f"multipart/form-data; boundary={boundary}".encode())],
                 "client": ("127.0.0.1", 1234), "server": ("127.0.0.1", 8001)}
        with patch("videoforge_mcp.http.MAX_UPLOAD_BYTES", 64), patch("videoforge_mcp.http.MAX_MULTIPART_OVERHEAD_BYTES", 128):
            asyncio.run(create_app(self.root)(scope, receive, send))
        self.assertEqual(sent[0]["status"], 413)
        self.assertEqual(json.loads(sent[1]["body"])["error"]["code"], "upload_too_large")


if __name__ == "__main__":
    unittest.main()
