import asyncio
import json
import shutil
import threading
import time
import uuid
from pathlib import Path

_STATE_LOCK = threading.Lock()


class ExportJobStore:
    def __init__(self, root):
        self.root = Path(root)
        self.instance = uuid.uuid4().hex

    def _directory(self, job_id):
        if len(job_id) != 32 or any(character not in "0123456789abcdef" for character in job_id):
            raise ValueError("Invalid export ID")
        return self.root / job_id

    def _write(self, job_id, state):
        directory = self._directory(job_id)
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / "state.tmp"
        with _STATE_LOCK:
            temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            temporary.replace(directory / "state.json")

    def read(self, job_id):
        with _STATE_LOCK:
            state = json.loads((self._directory(job_id) / "state.json").read_text(encoding="utf-8"))
        if state["status"] in {"queued", "running"} and state["instance"] != self.instance:
            state["status"] = "error"
            state["error"] = "Export interrupted by a server restart. Start a new export."
        state["elapsed"] = round(state.get("finished_at", time.time()) - state["created_at"], 1)
        return state

    def list(self, video_id):
        if not self.root.exists():
            return []
        exports = []
        for path in self.root.glob("*/state.json"):
            state = self.read(path.parent.name)
            if state["video_id"] == video_id:
                exports.append(state)
        return sorted(exports, key=lambda state: state["created_at"], reverse=True)

    def start(self, video_id, options, generate):
        job_id = uuid.uuid4().hex
        state = {
            "id": job_id, "video_id": video_id, "instance": self.instance,
            "status": "queued", "created_at": time.time(), "steps": [],
            "title": options.get("document_title") or "Chapter export",
        }

        def step(message):
            state["steps"].append({
                "elapsed": round(time.time() - state["created_at"], 1),
                "message": str(message),
            })
            self._write(job_id, state)

        def artifacts(directory, paths, title):
            if (directory / "index.html").is_file():
                destination = self._directory(job_id) / "webpage"
                destination.mkdir(parents=True, exist_ok=True)
                for path in paths:
                    shutil.copy2(path, destination / path.name)
                state["title"] = title
                state["open_url"] = f"/export_jobs/{job_id}/webpage/index.html"
                step("HTML webpage saved with its media for future sessions")

        async def work():
            workspace = self._directory(job_id) / "work"
            try:
                workspace.mkdir(parents=True, exist_ok=True)
                state["status"] = "running"
                step("Export worker started")
                response = await generate(video_id, options, step, artifacts, workspace)
                step("Saving the downloadable export")
                source = Path(response.path)
                target = self._directory(job_id) / ("download" + source.suffix)
                shutil.copy2(source, target)
                state["filename"] = source.name
                state["download_file"] = target.name
                state["download_url"] = f"/export_jobs/{job_id}/download"
                shutil.rmtree(workspace)
                state["status"] = "complete"
                state["finished_at"] = time.time()
                step("Export complete")
            except Exception as error:
                state["status"] = "error"
                state["finished_at"] = time.time()
                state["error"] = str(getattr(error, "detail", error))
                if workspace.exists():
                    try:
                        shutil.rmtree(workspace)
                    except OSError as cleanup_error:
                        state["error"] += f" Temporary export cleanup also failed: {cleanup_error}"
                step(f"Export failed: {state['error']}")

        self._write(job_id, state)
        thread = threading.Thread(target=lambda: asyncio.run(work()), name=f"export-{job_id}", daemon=True)
        thread.start()
        return job_id

    def file(self, job_id, filename, webpage=False):
        directory = self._directory(job_id)
        if webpage:
            directory = directory / "webpage"
        path = (directory / filename).resolve()
        if path.parent != directory.resolve() or not path.is_file():
            raise FileNotFoundError("Export file not found")
        return path
