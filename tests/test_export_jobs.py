import ast
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from export_jobs import ExportJobStore
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.testclient import TestClient


class ExportJobTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = ExportJobStore(Path(self.directory.name))

    def wait_for_job(self, job_id):
        for _ in range(100):
            state = self.store.read(job_id)
            if state["status"] in {"complete", "error"}:
                return state
            time.sleep(0.02)
        self.fail("Export job did not finish")

    def test_steps_and_html_survive_restart_with_isolated_download(self):
        async def generate(video_id, options, step, artifacts, workspace):
            step("Encoding a clip")
            html = workspace / "index.html"
            html.write_text("<h1>Saved lecture</h1>", encoding="utf-8")
            asset = workspace / "clip.mp4"
            asset.write_bytes(b"clip")
            artifacts(workspace, [html, asset], "Lecture")
            download = workspace / "lecture.zip"
            download.write_bytes(b"archive")
            return SimpleNamespace(path=download)
        job_id = self.store.start("video", {}, generate)
        state = self.wait_for_job(job_id)
        self.assertEqual(state["status"], "complete")
        with patch("export_jobs.time.time", return_value=state["finished_at"] + 60):
            self.assertEqual(self.store.read(job_id)["elapsed"], state["elapsed"])
        self.assertIn("Encoding a clip", [step["message"] for step in state["steps"]])
        restarted = ExportJobStore(Path(self.directory.name))
        self.assertEqual(restarted.list("video")[0]["open_url"], state["open_url"])
        self.assertEqual(restarted.file(job_id, "index.html", webpage=True).read_text(), "<h1>Saved lecture</h1>")
        self.assertEqual(restarted.file(job_id, state["download_file"]).read_bytes(), b"archive")
        with self.assertRaises(FileNotFoundError):
            restarted.file(job_id, "../state.json", webpage=True)
        with self.assertRaises(ValueError):
            restarted.read("../other")

    def test_worker_errors_are_visible(self):
        async def generate(*args):
            raise RuntimeError("FFmpeg failed")
        state = self.wait_for_job(self.store.start("video", {}, generate))
        self.assertEqual(state["status"], "error")
        self.assertIn("FFmpeg failed", state["error"])

    def test_same_filename_exports_are_retained_separately(self):
        async def generate(video_id, options, step, artifacts, workspace):
            path = workspace / "lecture.zip"
            path.write_bytes(options["content"])
            return SimpleNamespace(path=path)
        first = self.store.start("video", {"content": b"first"}, generate)
        second = self.store.start("video", {"content": b"second"}, generate)
        for job_id, expected in ((first, b"first"), (second, b"second")):
            state = self.wait_for_job(job_id)
            self.assertEqual(state["status"], "complete")
            self.assertEqual(self.store.file(job_id, state["download_file"]).read_bytes(), expected)
        self.assertEqual(len(self.store.list("video")), 2)

    def test_running_work_does_not_block_status_and_restart_is_reported(self):
        release = threading.Event()
        self.addCleanup(release.set)
        async def generate(video_id, options, step, artifacts, workspace):
            step("Waiting for encoder")
            release.wait(timeout=3)
            path = workspace / "document.pdf"
            path.write_bytes(b"pdf")
            return SimpleNamespace(path=path)
        job_id = self.store.start("video", {}, generate)
        state = self.store.read(job_id)
        self.assertIn(state["status"], {"queued", "running"})
        restarted = ExportJobStore(Path(self.directory.name))
        self.assertEqual(restarted.read(job_id)["status"], "error")
        self.assertIn("restart", restarted.read(job_id)["error"])
        release.set()
        self.wait_for_job(job_id)

    def test_http_start_poll_preview_and_download(self):
        app = FastAPI()

        async def generate(video_id, request, step, artifacts, workspace):
            options = await request.json()
            self.assertEqual(options["document_title"], "Test lecture")
            step("Building HTML webpage")
            html = workspace / "index.html"
            html.write_text("<h1>Lecture</h1>", encoding="utf-8")
            artifacts(workspace, [html], options["document_title"])
            archive = workspace / "lecture.zip"
            archive.write_bytes(b"zip")
            return FileResponse(archive)

        source = Path(__file__).resolve().parents[1] / "main.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        names = {
            "start_export_job", "read_export_job", "export_job_status",
            "saved_exports", "download_export_job", "open_export_webpage",
        }
        routes = ast.Module(
            body=[node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                  and node.name in names], type_ignores=[],
        )
        namespace = {
            "app": app, "Request": Request, "HTTPException": HTTPException,
            "FileResponse": FileResponse, "export_jobs": self.store,
            "export_chapters": generate,
            "video_processor": SimpleNamespace(get_video_path=lambda video_id: "video.mp4"),
        }
        exec(compile(routes, str(source), "exec"), namespace)
        with TestClient(app) as client:
            response = client.post("/export_jobs/video", json={"document_title": "Test lecture"})
            self.assertEqual(response.status_code, 200)
            data = response.json()
            state = self.wait_for_job(data["job_id"])
            self.assertEqual(client.get(data["status_url"]).json()["status"], "complete")
            preview = client.get(state["open_url"])
            self.assertIn("text/html", preview.headers["content-type"])
            self.assertEqual(preview.text, "<h1>Lecture</h1>")
            self.assertEqual(client.get(state["download_url"]).content, b"zip")
            self.assertEqual(len(client.get("/saved_exports/video").json()["exports"]), 1)
            self.assertEqual(client.get("/export_jobs/bad/status").status_code, 404)
            self.assertEqual(client.get(state["open_url"].replace("index.html", "missing.mp4")).status_code, 404)
            self.store.file(data["job_id"], state["download_file"]).unlink()
            self.assertEqual(client.get(state["download_url"]).status_code, 404)
