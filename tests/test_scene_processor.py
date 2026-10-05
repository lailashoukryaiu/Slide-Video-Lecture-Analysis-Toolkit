import math
import os
import sys
import tempfile
import unittest
from types import ModuleType

import cv2
import numpy as np


scenedetect_module = sys.modules.setdefault("scenedetect", ModuleType("scenedetect"))
scenedetect_module.open_video = object
scenedetect_module.SceneManager = object
scenedetect_module.ContentDetector = object
detectors_module = sys.modules.setdefault(
    "scenedetect.detectors", ModuleType("scenedetect.detectors")
)
detectors_module.ContentDetector = object

fastapi_module = sys.modules.setdefault("fastapi", ModuleType("fastapi"))
fastapi_responses_module = sys.modules.setdefault(
    "fastapi.responses", ModuleType("fastapi.responses")
)
fastapi_responses_module.JSONResponse = object
fastapi_module.responses = fastapi_responses_module

from processors.scene_processor import SceneProcessor, resolve_minimum_slide_duration


FRAME_WIDTH, FRAME_HEIGHT, FRAME_RATE = 640, 360, 1
WORDS = ["gradient", "loss", "model", "layer", "data", "train", "error", "weights"]


def render_slide(title, bullets, shown, diagram=False, boxed=False):
    image = np.full((FRAME_HEIGHT, FRAME_WIDTH, 3), 250, np.uint8)
    cv2.rectangle(image, (0, 0), (FRAME_WIDTH, 50), (120, 60, 20), -1)
    cv2.putText(image, title, (15, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
    for row, bullet in enumerate(bullets[:shown]):
        cv2.putText(
            image, "- " + bullet, (30, 95 + row * 40),
            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (30, 30, 30), 1,
        )
    if boxed:
        cv2.rectangle(image, (420, 200), (600, 320), (40, 120, 200), -1)
    if diagram:
        cv2.rectangle(image, (380, 120), (620, 330), (30, 160, 60), -1)
        cv2.circle(image, (500, 225), 60, (240, 240, 240), -1)
    return image


def lecture_plan():
    """Ten-minute same-template lecture: 15 slides, two 3-step builds, one quick slide."""
    rng = np.random.default_rng(0)
    plan = []
    slide_seconds = 40
    for slide in range(15):
        bullets = [" ".join(rng.choice(WORDS, 4)) for _ in range(4)]
        start = slide * slide_seconds
        if slide in (2, 9):
            for step in range(3):
                plan.append((start + step * 13, dict(
                    title=f"Build {slide}", bullets=bullets, shown=2 + step,
                    diagram=slide == 9 and step == 2,
                )))
        elif slide == 12:
            plan.append((start, dict(title="Recap A", bullets=bullets, shown=4)))
            plan.append((start + 12, dict(title="Recap B", bullets=bullets[::-1], shown=4)))
        else:
            plan.append((start, dict(
                title=f"Slide {slide} topic", bullets=bullets, shown=4,
                boxed=slide % 3 == 1,
            )))
    return plan


def write_video(path, plan, duration, cursor=False, webcam=False):
    writer = cv2.VideoWriter(
        path, cv2.VideoWriter_fourcc(*"MJPG"), FRAME_RATE, (FRAME_WIDTH, FRAME_HEIGHT)
    )
    state = 0
    for frame_number in range(int(duration * FRAME_RATE)):
        seconds = frame_number / FRAME_RATE
        while state + 1 < len(plan) and plan[state + 1][0] <= seconds:
            state += 1
        image = render_slide(**plan[state][1])
        if cursor and (frame_number // 10) % 3 == 0:
            x = int(200 + 150 * math.sin(frame_number / 4))
            cv2.circle(image, (x, 180), 5, (0, 0, 255), -1)
        if webcam:
            inset = np.full((90, 120, 3), 60, np.uint8)
            center = (int(60 + 12 * math.sin(frame_number / 3)), int(50 + 6 * math.cos(frame_number / 4)))
            cv2.ellipse(inset, center, (25, 32), 0, 0, 360, (150, 170, 210), -1)
            image[FRAME_HEIGHT - 95:FRAME_HEIGHT - 5, FRAME_WIDTH - 125:FRAME_WIDTH - 5] = inset
        writer.write(image)
    writer.release()


def detection_options(detail, minimum_duration, maximum_per_hour, merge=True):
    return {
        "adaptive_detail": detail,
        "minimum_slide_duration": minimum_duration,
        "maximum_slides_per_hour": maximum_per_hour,
        "merge_similar_slides": merge,
    }


PRESET_LIMITS = {"more": (5, 0), "balanced": (10, 0), "fewer": (20, 60)}
# Mirrors the "Match detail level" defaults resolved in static/js/api-module.js.
PRESET_PERCENT_LIMITS = {"more": (0.25, 0), "balanced": (0.5, 0), "fewer": (1, 60)}


class AdaptiveFinalCountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.processor = SceneProcessor.__new__(SceneProcessor)
        cls.plan = lecture_plan()
        cls.lecture_path = os.path.join(cls.temp_dir.name, "lecture.avi")
        write_video(cls.lecture_path, cls.plan, 600, cursor=True)
        cls.webcam_path = os.path.join(cls.temp_dir.name, "webcam.avi")
        write_video(cls.webcam_path, cls.plan, 600, cursor=True, webcam=True)
        cls.slide_starts = [
            start for start, slide in cls.plan
            if not slide["title"].startswith("Build") or slide["shown"] == 2
        ]
        cls.build_steps = [
            start for start, slide in cls.plan
            if slide["title"].startswith("Build") and slide["shown"] > 2
        ]

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()

    def detect(self, path, detail, minimum_duration, maximum_per_hour, merge=True):
        return self.processor.detect_adaptive_scenes(
            path, detection_options(detail, minimum_duration, maximum_per_hour, merge), []
        )

    def assertFindsTimes(self, timestamps, expected):
        for expected_time in expected:
            self.assertTrue(
                any(0 <= timestamp - expected_time <= 3 for timestamp in timestamps),
                f"missing slide at {expected_time}s in {timestamps}",
            )

    def test_old_default_limit_made_every_detail_level_identical(self):
        counts = {}
        for detail in ("more", "balanced", "fewer"):
            timestamps, diagnostics = self.detect(self.lecture_path, detail, 10, 60)
            counts[detail] = len(timestamps)
            self.assertIn("slide_limit", diagnostics["limited_by"])
            self.assertEqual(diagnostics["slide_limit"], 10)
        self.assertEqual(counts, {"more": 10, "balanced": 10, "fewer": 10})

    def test_detail_levels_differ_in_final_count_with_same_limits(self):
        results = {
            detail: self.detect(self.lecture_path, detail, 5, 0)
            for detail in ("more", "balanced", "fewer")
        }
        counts = {detail: len(result[0]) for detail, result in results.items()}

        self.assertEqual(counts["more"], len(self.plan))
        self.assertGreater(counts["more"], counts["balanced"])
        self.assertGreaterEqual(counts["balanced"], counts["fewer"])
        for timestamps, diagnostics in results.values():
            self.assertFindsTimes(timestamps, self.slide_starts)
            self.assertEqual(diagnostics["preset_counts"], counts)
            self.assertEqual(diagnostics["removed_by_slide_limit"], 0)
        self.assertFindsTimes(results["more"][0], self.build_steps)
        self.assertEqual(results["balanced"][1]["builds_merged"], 3)

    def test_preset_default_limits_give_more_balanced_fewer_order(self):
        counts = {}
        for detail, (minimum_duration, maximum_per_hour) in PRESET_LIMITS.items():
            timestamps, diagnostics = self.detect(
                self.lecture_path, detail, minimum_duration, maximum_per_hour
            )
            counts[detail] = len(timestamps)
            self.assertEqual(diagnostics["detected_changes"], len(timestamps))
        self.assertGreater(counts["more"], counts["balanced"])
        self.assertGreater(counts["balanced"], counts["fewer"])

    def test_percentage_presets_scale_with_video_length(self):
        duration = self.processor._video_duration(self.lecture_path)
        counts = {}
        for detail, (percent, maximum_per_hour) in PRESET_PERCENT_LIMITS.items():
            options = resolve_minimum_slide_duration(
                {**detection_options(detail, 10, maximum_per_hour),
                 "minimum_slide_duration_percent": percent},
                duration,
            )
            self.assertEqual(
                options["minimum_slide_duration"], round(max(2.0, duration * percent / 100), 1)
            )
            timestamps, _ = self.processor.detect_adaptive_scenes(self.lecture_path, options, [])
            counts[detail] = len(timestamps)
        self.assertGreater(counts["more"], counts["balanced"])
        self.assertGreater(counts["balanced"], counts["fewer"])

    def test_webcam_inset_does_not_add_or_hide_slides(self):
        for detail in ("more", "balanced", "fewer"):
            lecture, _ = self.detect(self.lecture_path, detail, 5, 0)
            with_webcam, diagnostics = self.detect(self.webcam_path, detail, 5, 0)
            self.assertEqual(len(with_webcam), len(lecture), detail)
            self.assertFindsTimes(with_webcam, self.slide_starts)
            self.assertLess(diagnostics["static_area_percent"], 100)

    def test_turning_off_merging_keeps_every_build_step(self):
        timestamps, diagnostics = self.detect(self.lecture_path, "fewer", 5, 0, merge=False)
        self.assertEqual(len(timestamps), len(self.plan))
        self.assertEqual(diagnostics["builds_merged"], 0)


class EqualStrengthTransitionTests(unittest.TestCase):
    """Full slide replacements of equal strength are genuine for every preset."""

    @staticmethod
    def samples(slide_count=12, seconds_per_slide=30):
        samples = []
        signatures = []
        for slide in range(slide_count):
            signature = np.full((72, 128), 245, np.uint8)
            cv2.putText(
                signature, f"S{slide}", (10 + slide * 4, 50),
                cv2.FONT_HERSHEY_SIMPLEX, 1.4, 20, 3,
            )
            signatures.append(signature)
        for second in range(slide_count * seconds_per_slide):
            samples.append((float(second), signatures[second // seconds_per_slide]))
        return samples

    def select(self, samples, detail, minimum_duration=10, maximum_per_hour=0):
        return SceneProcessor.__new__(SceneProcessor)._select_adaptive_timestamps(
            samples, len(samples), detection_options(detail, minimum_duration, maximum_per_hour), [],
        )

    def test_every_detail_level_keeps_all_equal_strength_slides(self):
        samples = self.samples()
        for detail in ("more", "balanced", "fewer"):
            timestamps, diagnostics = self.select(samples, detail)
            self.assertEqual(timestamps, [float(second) for second in range(0, 360, 30)])
            self.assertEqual(diagnostics["limited_by"], [])

    def test_limit_reports_when_it_hides_genuine_slides(self):
        timestamps, diagnostics = self.select(self.samples(), "more", maximum_per_hour=40)
        self.assertEqual(len(timestamps), 10)
        self.assertEqual(diagnostics["removed_by_slide_limit"], 2)
        self.assertEqual(diagnostics["limited_by"], ["slide_limit"])

    def test_minimum_duration_merges_quick_flips_and_reports_it(self):
        samples = self.samples(slide_count=6, seconds_per_slide=8)
        timestamps, diagnostics = self.select(samples, "balanced", minimum_duration=20)
        self.assertLess(len(timestamps), 6)
        self.assertEqual(diagnostics["limited_by"], ["minimum_slide_duration"])

    def test_static_frames_with_cursor_noise_stay_one_slide(self):
        base = np.full((72, 128), 245, np.uint8)
        samples = []
        for second in range(120):
            signature = base.copy()
            cv2.circle(signature, (20 + second % 60, 30), 1, 0, -1)
            samples.append((float(second), signature))
        timestamps, diagnostics = self.select(samples, "more", minimum_duration=5)
        self.assertEqual(timestamps, [0.0])
        self.assertEqual(diagnostics["visual_changes"], 0)


class SlideLimitPriorityTests(unittest.TestCase):
    def test_cap_keeps_start_chapters_and_strongest_changes(self):
        processor = SceneProcessor.__new__(SceneProcessor)
        timestamps = [0.0] + [float(value) for value in range(30, 600, 30)]
        priorities = {timestamp: 0.01 for timestamp in timestamps}
        priorities.update({90.0: 0.4, 300.0: 0.3})
        capped = processor._apply_hourly_cap(
            timestamps, 600, 40, preserved=[450.0], priorities=priorities
        )
        self.assertEqual(len(capped), 10)
        self.assertTrue({0.0, 90.0, 300.0, 450.0}.issubset(capped))


class SceneProcessorLimitTests(unittest.TestCase):
    def test_short_video_can_return_multiple_slides(self):
        self.assertEqual(SceneProcessor._maximum_slide_count(30, 60), 10)

    def test_hourly_limit_still_scales_for_long_videos(self):
        self.assertEqual(SceneProcessor._maximum_slide_count(1800, 60), 30)

    def test_unlimited_option_does_not_apply_a_cap(self):
        self.assertEqual(SceneProcessor._maximum_slide_count(30, 0), 0)


class MinimumDurationPercentTests(unittest.TestCase):
    def test_percent_converts_to_seconds_with_floor(self):
        hour = resolve_minimum_slide_duration({"minimum_slide_duration_percent": 0.5}, 3600)
        self.assertEqual(hour["minimum_slide_duration"], 18.0)
        short = resolve_minimum_slide_duration({"minimum_slide_duration_percent": 0.25}, 120)
        self.assertEqual(short["minimum_slide_duration"], 2.0)

    def test_seconds_option_is_kept_without_percent(self):
        options = {"minimum_slide_duration": 10}
        self.assertIs(resolve_minimum_slide_duration(options, 3600), options)


class VideoStreamReleaseTests(unittest.TestCase):
    def test_opencv_stream_without_close_releases_capture(self):
        class FakeCapture:
            released = False

            def release(self):
                self.released = True

        class FakeVideoStreamCv2:
            def __init__(self):
                self._cap = FakeCapture()

        video = FakeVideoStreamCv2()
        SceneProcessor._release_video_stream(video)
        self.assertTrue(video._cap.released)

    def test_stream_with_close_is_closed(self):
        class FakeStream:
            closed = False

            def close(self):
                self.closed = True

        video = FakeStream()
        SceneProcessor._release_video_stream(video)
        self.assertTrue(video.closed)


if __name__ == "__main__":
    unittest.main()
