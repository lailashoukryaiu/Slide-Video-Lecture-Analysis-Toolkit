import sys
import unittest
from types import ModuleType


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


class SceneProcessorLimitTests(unittest.TestCase):
    def test_short_video_can_return_multiple_slides(self):
        self.assertEqual(SceneProcessor._maximum_slide_count(30, 60), 10)

    def test_hourly_limit_still_scales_for_long_videos(self):
        self.assertEqual(SceneProcessor._maximum_slide_count(1800, 60), 30)

    def test_unlimited_option_does_not_apply_a_cap(self):
        self.assertEqual(SceneProcessor._maximum_slide_count(30, 0), 0)


if __name__ == "__main__":
    unittest.main()
