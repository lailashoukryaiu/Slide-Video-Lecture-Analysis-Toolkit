import asyncio
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from queue import Queue
from unittest import mock

from processors import ocr_processor
from processors.ocr_processor import OCRProcessor


def make_processor(events):
    processor = OCRProcessor.__new__(OCRProcessor)
    processor.ocr_preference = "tesseract"
    processor.ocr_queue = Queue()
    processor.video_ocr_tasks = {}
    processor.video_surya_tasks = {}
    processor.ocr_tasks_total = 0
    processor.ocr_tasks_completed = 0
    processor.cancelled_videos = set()
    processor.ocr_status = {}
    processor.active_ocr_batches = {}
    processor.task_lock = threading.Lock()

    async def record(video_id, data):
        events.append(data)

    processor.send_sse_update = record
    return processor


def write_scene_file(directory, detection_count):
    scene = {
        "yolo_detections": {
            "success": True,
            "detections": [
                {"needs_ocr": True, "bbox": [0, 0, 10, 10], "ocr_class": "text"}
                for _ in range(detection_count)
            ],
        }
    }
    path = os.path.join(directory, "video.json")
    with open(path, "w") as file:
        json.dump([scene, json.loads(json.dumps(scene))], file)
    return path


class ConfigureTesseractTests(unittest.TestCase):
    def test_finds_conda_binary_when_env_is_not_activated(self):
        prefix = os.path.abspath(os.path.join("C:\\", "envs", "lecture"))
        conda_binary = os.path.join(prefix, "Library", "bin", "tesseract.exe")
        tessdata = os.path.join(prefix, "share", "tessdata")
        with mock.patch.object(ocr_processor.sys, "prefix", prefix), \
                mock.patch.object(ocr_processor.shutil, "which", return_value=None), \
                mock.patch.dict(os.environ, {"TESSERACT_CMD": "", "TESSDATA_PREFIX": ""}), \
                mock.patch.object(ocr_processor.os.path, "isfile", side_effect=lambda p: p == conda_binary), \
                mock.patch.object(ocr_processor.os.path, "isdir", side_effect=lambda p: p == tessdata), \
                mock.patch.object(ocr_processor.pytesseract.pytesseract, "tesseract_cmd", "tesseract"):
            self.assertEqual(ocr_processor.configure_tesseract(), conda_binary)
            self.assertEqual(ocr_processor.pytesseract.pytesseract.tesseract_cmd, conda_binary)
            self.assertEqual(os.environ["TESSDATA_PREFIX"], tessdata)

    def test_returns_none_when_tesseract_is_missing(self):
        with mock.patch.object(ocr_processor.shutil, "which", return_value=None), \
                mock.patch.dict(os.environ, {"TESSERACT_CMD": ""}), \
                mock.patch.object(ocr_processor.os.path, "isfile", return_value=False):
            self.assertIsNone(ocr_processor.configure_tesseract())


class TesseractProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.events = []
        self.processor = make_processor(self.events)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def tasks_for(self, scene_path):
        with open(scene_path) as file:
            scenes = json.load(file)
        return {
            index: self.processor.collect_scene_ocr_tasks("video", scene, index)[0]
            for index, scene in enumerate(scenes)
        }

    def test_reads_all_elements_and_reports_each_step(self):
        scene_path = write_scene_file(self.tmp, 3)
        results = iter([{"success": True, "text": f"text {i}"} for i in range(5)] +
                       [{"success": False, "error": "bad crop"}])
        self.processor.perform_tesseract_ocr = lambda image_path, bbox: next(results)

        self.processor.process_tesseract_tasks("video", self.tasks_for(scene_path), scene_path)

        progress = [e for e in self.events if e["event"] == "ocr_progress"]
        self.assertEqual(len(progress), 7)
        self.assertEqual(progress[-1]["data"]["completed"], 6)
        self.assertIn("6 of 6 text elements read", progress[-1]["data"]["message"])
        final = self.events[-1]
        self.assertEqual(final["event"], "ocr_complete")
        self.assertEqual(final["data"]["failed"], 1)
        self.assertIn("1 could not be read", final["data"]["message"])
        status = self.processor.get_ocr_status("video")
        self.assertEqual(status["status"], "complete")
        self.assertNotIn("final_results", status)

        with open(scene_path) as file:
            detections = [d for s in json.load(file) for d in s["yolo_detections"]["detections"]]
        self.assertEqual(sum("ocr_text" in d for d in detections), 5)
        self.assertEqual(sum("ocr_error" in d for d in detections), 1)

    def test_all_failures_report_an_error(self):
        scene_path = write_scene_file(self.tmp, 1)
        self.processor.perform_tesseract_ocr = lambda image_path, bbox: {"success": False, "error": "missing"}

        self.processor.process_tesseract_tasks("video", self.tasks_for(scene_path), scene_path)

        self.assertEqual(self.events[-1]["event"], "ocr_error")
        self.assertIn("missing", self.events[-1]["data"]["message"])

    def test_stop_ends_the_batch(self):
        scene_path = write_scene_file(self.tmp, 4)

        def read_and_stop(image_path, bbox):
            self.processor.cancelled_videos.add("video")
            return {"success": True, "text": "x"}

        self.processor.perform_tesseract_ocr = read_and_stop
        with mock.patch.object(OCRProcessor, "tesseract_worker_count", return_value=1):
            self.processor.process_tesseract_tasks("video", self.tasks_for(scene_path), scene_path)

        self.assertEqual(self.events[-1]["event"], "ocr_cancelled")
        self.assertLess(self.events[-1]["data"]["completed"], 8)
        self.assertEqual(self.processor.get_ocr_status("video")["status"], "stopped")

    def test_start_reports_missing_tesseract(self):
        scene_path = write_scene_file(self.tmp, 1)
        with mock.patch.object(ocr_processor, "SCENES_DIR", Path(self.tmp)), \
                mock.patch.object(ocr_processor, "tesseract_available", return_value=False):
            response = asyncio.run(self.processor.start_ocr("video"))

        body = json.loads(response.body)
        self.assertFalse(body["success"])
        self.assertIn("Tesseract", body["error"])
        self.assertEqual(self.events[-1]["event"], "ocr_error")
        self.assertTrue(os.path.exists(scene_path))


if __name__ == "__main__":
    unittest.main()
