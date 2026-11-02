import unittest

from resource_links import retime_resources, validate_resources
from chapter_projects import clip_speed_chapters


class ResourceLinksTests(unittest.TestCase):
    def test_non_finite_times_and_unsafe_urls(self):
        link = {"title": "Docs", "url": "https://example.com"}
        for value in (float("nan"), float("inf"), -1):
            with self.assertRaises(ValueError):
                validate_resources([{**link, "start": value, "end": 20}])
        for url in ("file:///tmp/test", "//example.com", "https://[broken", "https://example.com:bad"):
            with self.assertRaises(ValueError):
                validate_resources([{**link, "url": url}])

    def test_clip_speed_and_offset(self):
        inherited = {"title": "All", "url": "https://example.com"}
        timed = {**inherited, "title": "Timed", "start": 4, "end": 14}
        outside = {**timed, "start": 20, "end": 22}
        self.assertEqual(retime_resources([inherited, timed, outside], 6, 12, 2, 3),
                         [inherited, {**timed, "start": 3, "end": 6}])
        chapters = [{"timestamp": "00:00", "title": "Topic", "resources": [timed],
                     "sections": [{"timestamp": "00:00", "title": "Part", "resources": [inherited, timed]}]}]
        clipped = clip_speed_chapters(chapters, 6, 12, 2)
        self.assertEqual(clipped[0]["resources"][0]["start"], 0)
        self.assertEqual(clipped[0]["sections"][0]["resources"][1]["end"], 3)
        self.assertEqual(chapters[0]["resources"][0]["start"], 4, "Source metadata must be unchanged")
