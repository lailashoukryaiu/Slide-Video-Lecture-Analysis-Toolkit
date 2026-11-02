import asyncio
import copy
import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient

import chapter_projects
import video_privacy as privacy
from ffmpeg_tools import media_executable
from export_jobs import ExportJobStore


class VideoPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.region = {"x": .25, "y": .25, "width": .5, "height": .5,
                       "start": 1, "end": 2, "effect": "blur"}

    def test_strict_region_validation(self):
        self.assertEqual(privacy.privacy_regions([self.region], 3), [self.region])
        for key, value in (("x", -1), ("x", float("nan")), ("width", 2), ("height", 0),
                           ("start", True), ("start", 2), ("end", 5), ("effect", "anything")):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                privacy.privacy_regions([{**self.region, key: value}], 3)
        for value in (None, {}, [self.region] * (privacy.MAX_REGIONS + 1)):
            with self.assertRaises(ValueError):
                privacy.privacy_regions(value, 3)

    def test_graph_clips_source_times_and_skips_non_overlapping_masks(self):
        graph, output = privacy.privacy_filter([self.region], 160, 96, .5, 2.5)
        self.assertIn("crop=80:48:40:24:exact=1", graph)
        self.assertIn("gte(t,0.500000)*lt(t,1.500000)", graph)
        self.assertEqual(output, "p1")
        _, output = privacy.privacy_filter([self.region], 160, 96, 2.5, 3)
        self.assertEqual(output, "p0")
        with self.assertRaises(ValueError):
            privacy.privacy_filter([{**self.region, "width": .001}], 160, 96, 0, 3)
        graph, _ = privacy.privacy_filter([{**self.region, "x": .26, "y": .26}], 160, 96, 0, 3)
        self.assertIn("crop=82:50:40:24:exact=1", graph,
                      "Round outwards to chroma-aligned pixels rather than leaking an uncovered edge")
        for effect in privacy.EFFECTS:
            graph, _ = privacy.privacy_filter(
                [{**self.region, "effect": effect}] * privacy.MAX_REGIONS, 3840, 2160, 0, 3)
            self.assertLess(len(subprocess.list2cmdline(["ffmpeg", "-filter_complex", graph])) + 4096, 32767)

    def test_adjustable_blur_strength_validation_and_export(self):
        for strength in (1, 6, 24, 48):
            regions = privacy.privacy_regions([{**self.region, "blur_strength": strength}], 3)
            self.assertEqual(regions[0]["blur_strength"], strength)
            graph, _ = privacy.privacy_filter(regions, 160, 96, 0, 3)
            self.assertIn(f"gblur=sigma={strength}:steps=2", graph)
        for strength in (0, 49, True, None, "6", float("nan"), float("inf")):
            with self.subTest(strength=strength), self.assertRaises(ValueError):
                privacy.privacy_regions([{**self.region, "blur_strength": strength}], 3)
        graph, _ = privacy.privacy_filter([self.region], 160, 96, 0, 3)
        self.assertIn("gblur=sigma=24:steps=2", graph, "Legacy masks retain their old strength")

    def test_merging_is_conservative_and_keeps_disjoint_times_separate(self):
        regions = []
        box = privacy.expanded_box((90, 30, 20, 20), 100, 100)
        self.assertLessEqual(box["x"] + box["width"], 1)
        privacy.merge_detection(regions, box, 0, 1, "cover")
        privacy.merge_detection(regions, box, .5, 1.5, "cover")
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0]["end"], 1.5)
        privacy.merge_detection(regions, box, 4, 5, "cover")
        self.assertEqual(len(regions), 2)

    def make_source(self):
        source = self.root / "source.mp4"
        captions = self.root / "captions.srt"
        captions.write_text("1\n00:00:00,000 --> 00:00:03,000\nNames may remain in captions.\n", encoding="utf-8")
        subprocess.run([
            media_executable("ffmpeg"), "-nostdin", "-y", "-v", "error",
            "-f", "lavfi", "-i", "testsrc2=s=160x96:r=10:d=3",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-i", str(captions),
            "-map", "0:v:0", "-map", "1:a:0", "-map", "2:s:0",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-c:s", "mov_text", str(source),
        ], check=True, capture_output=True)
        return source

    @staticmethod
    def frame(path, time):
        capture = cv2.VideoCapture(str(path))
        try:
            capture.set(cv2.CAP_PROP_POS_MSEC, time * 1000)
            ok, frame = capture.read()
            if not ok:
                raise AssertionError(f"Missing output frame at {time}")
            return frame
        finally:
            capture.release()

    def test_real_effects_change_only_the_region_and_requested_time_and_keep_tracks(self):
        source = self.make_source()
        original_bytes = source.read_bytes()
        for effect in privacy.EFFECTS:
            with self.subTest(effect=effect):
                target = self.root / f"{effect}.mp4"
                region = {**self.region, "effect": effect}
                snapshot = {"video_path": source, "start": .5, "end": 2.5,
                            "privacy_regions": privacy.privacy_regions([region], 3)}
                asyncio.run(chapter_projects._encode_video_download(snapshot, 3, target, lambda _: None))
                before, during, after = [self.frame(target, time) for time in (.2, .8, 1.8)]
                for actual, source_time in ((before, .7), (after, 2.3)):
                    reference = self.frame(source, source_time)
                    self.assertLess(np.abs(actual.astype(float) - reference).mean(), 8)
                reference = self.frame(source, 1.3)
                self.assertLess(np.abs(during[:20].astype(float) - reference[:20]).mean(), 8,
                                "Pixels outside the privacy region should be unchanged apart from encoding")
                masked = during[30:65, 50:110]
                source_masked = reference[30:65, 50:110]
                if effect == "cover":
                    self.assertLess(masked.mean(), 5)
                else:
                    self.assertGreater(np.abs(masked.astype(float) - source_masked).mean(), 15)
                    if effect == "blur":
                        self.assertLess(cv2.Laplacian(masked, cv2.CV_64F).var(),
                                        cv2.Laplacian(source_masked, cv2.CV_64F).var() * .3)
                probe = subprocess.run([media_executable("ffprobe"), "-v", "error", "-show_entries",
                                        "stream=codec_type", "-of", "json", str(target)],
                                       check=True, capture_output=True, text=True)
                self.assertEqual([item["codec_type"] for item in json.loads(probe.stdout)["streams"]],
                                 ["video", "audio", "subtitle"])
        self.assertEqual(source.read_bytes(), original_bytes)

    def test_scan_detects_local_candidates_and_releases_capture_on_cancel(self):
        source = self.make_source()
        people = mock.Mock()
        result = mock.Mock()
        result.boxes.xyxy.cpu.return_value.tolist.return_value = [[40, 20, 70, 50]]
        people.predict.return_value = [result]
        steps = []
        with mock.patch.object(privacy, "load_people_detector", return_value=people):
            result = asyncio.run(privacy.scan_people(source, .5, 2.5, "blur", steps.append))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["start"], .5)
        self.assertEqual(result[0]["end"], 2.5)
        self.assertIn("Review", steps[-1])

        capture = mock.Mock()
        capture.isOpened.return_value = True
        capture.read.return_value = (True, np.zeros((96, 160, 3), np.uint8))
        async def cancel():
            task = asyncio.create_task(privacy.scan_people(source, 0, 3, "cover", lambda _: None))
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        with mock.patch.object(cv2, "VideoCapture", return_value=capture), \
                mock.patch.object(privacy, "load_people_detector", return_value=people):
            asyncio.run(cancel())
        capture.release.assert_called_once()

    def test_routes_validate_before_starting_jobs_and_pin_selected_source(self):
        app = FastAPI()
        app.include_router(chapter_projects.router)
        source = self.root / "selected-copy.mp4"
        region = copy.deepcopy(self.region)
        with TestClient(app) as client, \
                mock.patch.object(chapter_projects, "_media_source", return_value=source), \
                mock.patch.object(chapter_projects, "video_duration", new=mock.AsyncMock(return_value=3)), \
                mock.patch.object(chapter_projects, "_start_video_download", return_value={"job_id": "job"}) as start:
            data = {"start": "00:00.500", "end": "00:02.500", "privacy_regions": [region]}
            self.assertEqual(client.post("/custom_video/video", json=data).status_code, 200)
            snapshot = start.call_args.args[1]
            self.assertEqual(snapshot["video_path"], source)
            self.assertEqual(snapshot["privacy_regions"], [region])
            self.assertEqual(client.post("/custom_video/video", json={**data, "privacy_regions": [{}]}).status_code, 400)
            self.assertEqual(client.post("/custom_video/video", json={**data, "start": "00:02.500",
                "end": "00:03"}).status_code, 400)
            self.assertEqual(start.call_count, 1)
            with mock.patch.object(chapter_projects.privacy_jobs, "start", return_value="scan") as scan:
                response = client.post("/privacy_scans/video", json={**data, "effect": "pixelate"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"job_id": "scan"})
                self.assertEqual(client.post("/privacy_scans/video", json={**data, "effect": "bad"}).status_code, 400)
                self.assertEqual(scan.call_count, 1)

    def test_scan_routes_run_and_cancel_real_isolated_workers(self):
        source = self.make_source()
        app = FastAPI()
        app.include_router(chapter_projects.router)
        store = ExportJobStore(self.root / "scan-jobs")
        people = mock.Mock()
        detection = mock.Mock()
        detection.boxes.xyxy.cpu.return_value.tolist.return_value = [[40, 20, 70, 50]]
        people.predict.return_value = [detection]

        def finish(client, job_id):
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                status = client.get(f"/privacy_scan_jobs/{job_id}")
                self.assertEqual(status.status_code, 200, status.text)
                state = status.json()
                if state["status"] not in {"queued", "running"}:
                    return state
                time.sleep(.02)
            self.fail("Privacy worker did not finish within ten seconds")

        with TestClient(app) as client, \
                mock.patch.object(chapter_projects, "privacy_jobs", store), \
                mock.patch.object(chapter_projects, "_media_source", return_value=source), \
                mock.patch.object(privacy, "load_people_detector", return_value=people):
            response = client.post("/privacy_scans/video", json={
                "start": "00:00.500", "end": "00:02.500", "effect": "cover",
            })
            self.assertEqual(response.status_code, 200, response.text)
            state = finish(client, response.json()["job_id"])
            self.assertEqual(state["status"], "complete", state)
            self.assertEqual(len(state["regions"]), 1)
            self.assertEqual(state["regions"][0]["effect"], "cover")
            self.assertEqual(state["regions"][0]["start"], .5)
            self.assertEqual(state["regions"][0]["end"], 2.5)
            self.assertEqual(client.get("/privacy_scan_jobs/invalid").status_code, 404)

            async def waiting_scan(source, start, end, effect, step):
                step("Waiting for cancellation")
                await asyncio.sleep(30)
                self.fail("Cancelled worker should never finish detection")

            with mock.patch.object(chapter_projects, "scan_people", waiting_scan):
                response = client.post("/privacy_scans/video", json={
                    "start": "00:00", "end": "00:02", "effect": "blur",
                })
                job_id = response.json()["job_id"]
                self.assertEqual(client.post(f"/privacy_scan_jobs/{job_id}/cancel").status_code, 200)
                self.assertEqual(finish(client, job_id)["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
