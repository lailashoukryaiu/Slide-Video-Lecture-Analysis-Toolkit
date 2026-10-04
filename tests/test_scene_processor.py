import sys
import unittest
from types import ModuleType

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

from processors.scene_processor import SceneProcessor


class AdaptiveDetailTests(unittest.TestCase):
    def test_adaptive_detail_changes_candidate_count_for_lecture_transitions(self):
        scores = np.zeros(360, dtype=np.float32)
        transition_strengths = np.linspace(0.04, 0.55, 24, dtype=np.float32)
        for transition, strength in enumerate(transition_strengths):
            start = 5 + transition * 14
            scores[start:start + 4] = strength * np.array(
                [0.2, 0.6, 1.0, 0.35], dtype=np.float32
            )
        samples = [
            (sample_index * 2, float(score), None)
            for sample_index, score in enumerate(scores)
        ]
        scores = np.array([sample[1] for sample in samples[1:]], dtype=np.float32)

        candidate_counts = {
            detail: len(
                SceneProcessor._adaptive_change_candidates(
                    samples,
                    SceneProcessor._adaptive_score_threshold(scores, detail)[1],
                )
            )
            for detail in ("more", "balanced", "fewer")
        }

        self.assertEqual(candidate_counts, {"more": 21, "balanced": 17, "fewer": 10})

    def test_static_frame_scores_do_not_change_the_learned_threshold(self):
        scores = np.zeros(1000, dtype=np.float32)
        scores[::100] = np.linspace(0.01, 0.1, 10, dtype=np.float32)

        thresholds = [
            SceneProcessor._adaptive_score_threshold(scores, detail)[1]
            for detail in ("more", "balanced", "fewer")
        ]

        self.assertEqual(thresholds, sorted(thresholds))
        self.assertTrue(all(threshold > 0.005 for threshold in thresholds))

    def test_no_changed_frame_scores_use_the_minimum_threshold(self):
        scores = np.zeros(1000, dtype=np.float32)

        self.assertEqual(
            SceneProcessor._adaptive_score_threshold(scores, "balanced"),
            (70.0, 0.005),
        )


class SceneProcessorLimitTests(unittest.TestCase):
    def test_short_video_can_return_multiple_slides(self):
        self.assertEqual(SceneProcessor._maximum_slide_count(30, 60), 10)

    def test_hourly_limit_still_scales_for_long_videos(self):
        self.assertEqual(SceneProcessor._maximum_slide_count(1800, 60), 30)

    def test_unlimited_option_does_not_apply_a_cap(self):
        self.assertEqual(SceneProcessor._maximum_slide_count(30, 0), 0)


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
