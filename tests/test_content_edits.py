import asyncio
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import content_edits
from processors import transcript_versions


class ContentEditsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in ("TRANSCRIPTS_DIR", "SCENES_DIR", "SUMMARIES_DIR", "VIDEO_DIR"):
            directory = self.root / name
            directory.mkdir()
            setattr(self, name, directory)
            patch = mock.patch.object(content_edits, name, directory)
            patch.start()
            self.addCleanup(patch.stop)
            if hasattr(transcript_versions, name):
                patch = mock.patch.object(transcript_versions, name, directory)
                patch.start()
                self.addCleanup(patch.stop)
        self.transcript = [
            {"start": 0, "duration": 4, "text": "First", "speaker": "A", "extra": 1},
            {"start": 3723, "duration": 4, "text": "Second", "speaker": "B"},
        ]
        self.chapters = [
            {"timestamp": "00:00", "title": "Opening", "summary": "Intro", "extra": 2,
             "sections": [{"timestamp": "00:00", "title": "Part", "point": "Point"}]},
            {"timestamp": "01:02:03", "title": "Next"},
        ]
        self.write(self.TRANSCRIPTS_DIR / "vid_whisper.json", self.transcript)
        self.write(self.SUMMARIES_DIR / "vid.json", self.chapters)
        self.write(self.SCENES_DIR / "vid.json", [{"time_seconds": 0, "image": "keep.jpg"}])
        app = FastAPI()
        self.describe_parts = content_edits.describe_unassigned_parts
        self.describe_patch = mock.patch.object(content_edits, "describe_unassigned_parts",
                                                new=mock.AsyncMock(return_value=[]))
        self.describe_patch.start()
        self.addCleanup(self.describe_patch.stop)
        self.processor = mock.Mock()
        self.processor.summarize_outline_part = mock.AsyncMock(return_value={
            "title": "Connection recovery", "summary": "The connection is restored before the demonstration resumes."})
        app.state.summary_processor = self.processor
        self.app = app
        app.include_router(content_edits.router)
        self.client = TestClient(app)

    @staticmethod
    def write(path, data):
        path.write_text(json.dumps(data), encoding="utf-8")

    def get(self, kind, **context):
        response = self.client.get("/editable_content/vid", params={"kind": kind, **context})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def save(self, kind, edits, status=200, revision=None, **context):
        revision = revision or self.get(kind, **context)["revision"]
        response = self.client.post("/editable_content/vid", json={
            "kind": kind, "revision": revision, "edits": edits, **context,
        })
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def test_chapters_preserve_metadata_and_exact_manual_times_on_reload(self):
        edits = copy.deepcopy(self.chapters)
        edits[0].update(title="My title", summary="My text")
        edits[0]["sections"][0]["timestamp"] = "00:01.500"
        edits[1]["timestamp"] = "01:02:04"
        saved = self.save("chapters", edits)["content"]
        self.assertEqual(saved[0]["extra"], 2)
        self.assertTrue(saved[1]["timestamp_edited"])
        self.assertTrue(saved[0]["sections"][0]["timestamp_edited"])
        self.assertEqual(saved, self.get("chapters")["content"])
        self.assertNotIn("timestamp_edited", saved[0])

    def test_resources_save_reload_remove_and_conflict(self):
        edits = copy.deepcopy(self.chapters)
        link = {"title": "Documentation", "url": "https://example.com/docs"}
        edits[0]["resources"] = [link]
        edits[0]["sections"][0]["resources"] = [{**link, "start": 2.125, "end": 10.5}]
        revision = self.get("chapters")["revision"]
        saved = self.save("chapters", edits)["content"]
        self.assertEqual(saved, self.get("chapters")["content"])
        self.assertEqual(saved[0]["resources"], [link])
        self.assertEqual(saved[0]["sections"][0]["resources"][0]["start"], 2.125)
        self.save("chapters", edits, status=409, revision=revision)
        saved[0]["resources"] = []
        saved[0]["sections"][0]["resources"][0]["title"] = "Updated"
        changed = self.save("chapters", saved)["content"]
        self.assertEqual(changed[0]["resources"], [])
        self.assertEqual(changed[0]["sections"][0]["resources"][0]["title"], "Updated")

    def test_resources_reject_invalid_urls_times_and_shapes(self):
        base = {"title": "Docs", "url": "https://example.com"}
        for value in (None, {}, [None], [base] * 51,
                      [{**base, "title": ""}], [{**base, "url": "javascript:alert(1)"}],
                      [{**base, "url": "https://name:password@example.com"}],
                      [{**base, "url": "https://example.com/a b"}],
                      [{**base, "start": 2, "end": 1}],
                      [{**base, "start": True, "end": 2}],
                      [{**base, "start": 0}],
                      [{**base, "start": 0, "end": 5000}]):
            with self.subTest(value=value):
                edits = copy.deepcopy(self.chapters)
                edits[0]["resources"] = value
                self.save("chapters", edits, status=400)
        self.assertEqual(self.get("chapters")["content"], self.chapters)

    def test_resource_links_survive_outline_merges(self):
        link = {"title": "Docs", "url": "https://example.com"}
        chapters = copy.deepcopy(self.chapters)
        chapters[0]["resources"] = [link]
        chapters[1]["resources"] = [{**link, "title": "Second"}]
        merged = content_edits.outline_operations(chapters, [{"action": "merge_chapter", "chapter": 0}])
        self.assertEqual([item["title"] for item in merged[0]["resources"]], ["Docs", "Second"])

    def edit_row(self, action, chapter, section=None, item=None, status=200, revision=None):
        response = self.client.post("/outline_item/vid", json={
            "action": action, "chapter": chapter, "section": section, "item": item,
            "revision": revision or self.get("chapters")["revision"],
        })
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def test_row_crud_keeps_exact_times_and_metadata_and_rejects_stale_edits(self):
        revision = self.get("chapters")["revision"]
        self.edit_row("add_section", 0, 1, {"timestamp": "00:10.125", "title": "Trouble",
                     "point": "Wait for reconnect", "flagged": True, "flag_reason": "Connection lost",
                     "skipped": False, "unexpected": "discard"})
        saved = self.get("chapters")["content"]
        self.assertTrue(saved[0]["sections"][1]["flagged"])
        self.assertFalse(saved[0]["sections"][1]["skipped"])
        self.assertNotIn("unexpected", saved[0]["sections"][1])
        self.assertEqual(saved[0]["extra"], 2)
        self.edit_row("delete", 0, 1, status=409, revision=revision)
        self.edit_row("add_chapter", 1, item={"timestamp": "00:10.125", "title": "New", "unexpected": True})
        saved = self.get("chapters")["content"]
        self.assertEqual(saved[1]["sections"][0]["title"], "Trouble")
        self.assertNotIn("unexpected", saved[1])
        self.edit_row("update", 1, item={"timestamp": "00:11.250", "title": "New name"})
        saved = self.get("chapters")["content"]
        self.assertEqual(saved[1]["sections"][0]["timestamp"], "00:11.250")
        self.assertTrue(saved[1]["sections"][0]["timestamp_edited"])
        self.edit_row("delete", 1, 0)
        self.edit_row("delete", 1)
        self.assertEqual(len(self.get("chapters")["content"]), 2)
        self.assertEqual(json.loads((self.TRANSCRIPTS_DIR / "vid_whisper.json").read_text()), self.transcript)

    def test_row_validation_never_overwrites_saved_outline(self):
        for item in ({"title": "", "timestamp": "00:10"}, {"title": "X", "timestamp": "NaN"},
                     {"title": "X", "timestamp": "00:10", "skipped": "yes"},
                     {"title": "X", "timestamp": "02:00:00"}):
            self.edit_row("add_section", 0, 1, item, status=400)
        self.assertEqual(self.get("chapters")["content"], self.chapters)
        self.edit_row("add_chapter", True, item={"title": "X", "timestamp": "00:10"}, status=400)

    def test_empty_outline_can_create_a_first_chapter(self):
        self.write(self.SUMMARIES_DIR / "vid.json", [])
        self.edit_row("add_chapter", 0, item={"title": "First", "timestamp": "00:00"})
        self.assertEqual(self.get("chapters")["content"][0]["sections"][0]["timestamp"], "00:00")

    def test_section_end_creates_persistent_grey_gap_and_can_extend_into_it(self):
        saved = self.edit_row("update", 0, 0, item={"end_timestamp": "00:12.345"})["content"]
        gap = saved[0]["sections"][1]
        self.assertEqual(gap["timestamp"], "00:12.345")
        self.assertEqual(gap["title"], "Unassigned")
        self.assertTrue(gap["flagged"])
        self.assertFalse(gap["skipped"])
        self.assertEqual(saved[1]["timestamp"], self.chapters[1]["timestamp"])
        self.assertEqual(saved, self.get("chapters")["content"])
        saved = self.edit_row("update", 0, 0, item={"end_timestamp": "00:20.500"})["content"]
        self.assertEqual(len(saved[0]["sections"]), 2)
        self.assertEqual(saved[0]["sections"][1]["timestamp"], "00:20.500")
        saved = self.edit_row("update", 0, 0, item={"end_timestamp": "01:02:03"})["content"]
        self.assertEqual(len(saved[0]["sections"]), 1)

    def test_new_grey_part_gets_transcript_title_and_summary_without_losing_gap_identity(self):
        self.describe_patch.stop()
        transcript = [{"start": 0, "duration": 10, "text": "Before the cut"},
                      {"start": 10, "duration": 3, "text": "The connection was lost."},
                      {"start": 20, "duration": 10, "text": "It is restored, continue the demo."},
                      {"start": 3723, "duration": 4, "text": "Next chapter"}]
        self.write(self.TRANSCRIPTS_DIR / "vid_whisper.json", transcript)
        saved = self.edit_row("update", 0, item={"end_timestamp": "00:10"})["content"]
        gap = saved[1]
        self.assertEqual(gap["title"], "Connection recovery")
        self.assertIn("restored", gap["summary"])
        self.assertTrue(gap["flagged"])
        self.assertFalse(gap["skipped"])
        self.assertTrue(gap["unassigned"])
        self.assertEqual(self.processor.summarize_outline_part.await_args.args[0], transcript[1:3])
        self.assertEqual(saved, self.get("chapters")["content"])
        self.edit_row("update", 1, item={"title": gap["title"], "end_timestamp": "01:02:03"})
        self.assertTrue(self.get("chapters")["content"][1]["unassigned"])
        self.processor.summarize_outline_part.assert_awaited_once()
        self.edit_row("update", 0, item={"end_timestamp": "00:20"})
        self.assertEqual(self.get("chapters")["content"][1]["timestamp"], "00:20")
        self.assertEqual(self.processor.summarize_outline_part.await_count, 2)

    def test_described_section_uses_point_and_does_not_include_adjacent_speech(self):
        self.describe_patch.stop()
        transcript = [{"start": 0, "duration": 10, "text": "Before"},
                      {"start": 10, "duration": 5, "text": "Within"},
                      {"start": 15, "duration": 3, "text": "After"}]
        content = [{"timestamp": "00:00", "title": "Demo", "sections": [
            {"timestamp": "00:00", "title": "A"},
            content_edits._unassigned_part("00:10"), {"timestamp": "00:15", "title": "B"}]}]
        warnings = asyncio.run(self.describe_parts([], content, transcript, 18, "de", self.processor))
        self.assertEqual(warnings, [])
        self.processor.summarize_outline_part.assert_awaited_once_with([transcript[1]], "de")
        self.assertIn("restored", content[0]["sections"][1]["point"])
        self.assertNotIn("summary", content[0]["sections"][1])

    def test_description_failure_saves_end_with_explicit_warning_and_no_partial_ai_metadata(self):
        self.describe_patch.stop()
        self.processor.summarize_outline_part.side_effect = RuntimeError("Provider unavailable")
        result = self.edit_row("update", 0, item={"end_timestamp": "00:00.500"})
        self.assertEqual(result["content"][1]["title"], "No transcript text")
        self.assertFalse(result["warnings"])
        self.assertFalse(result["content"][1]["skipped"])
        transcript = [{"start": 0, "duration": 1, "text": "Before"},
                      {"start": 2, "duration": 1, "text": "After"}]
        self.write(self.TRANSCRIPTS_DIR / "vid_whisper.json", transcript)
        self.write(self.SUMMARIES_DIR / "vid.json", self.chapters[:1])
        with self.assertLogs(content_edits.logger, level="WARNING"):
            result = self.edit_row("update", 0, item={"end_timestamp": "00:01"})
        self.assertTrue(result["warnings"])
        self.assertEqual(result["content"][1]["title"], "Unassigned")
        self.assertTrue(result["content"][1]["flagged"])

    def test_conflict_during_description_keeps_newer_saved_outline(self):
        self.describe_patch.stop()
        transcript = [{"start": 0, "duration": 1, "text": "Before"}, {"start": 2, "duration": 1, "text": "After"}]
        self.write(self.TRANSCRIPTS_DIR / "vid_whisper.json", transcript)
        self.write(self.SUMMARIES_DIR / "vid.json", self.chapters[:1])
        newer = [{**self.chapters[0], "title": "Newer edit"}]
        async def concurrent_edit(*args):
            self.write(self.SUMMARIES_DIR / "vid.json", newer)
            return {"title": "AI", "summary": "AI summary"}
        self.processor.summarize_outline_part.side_effect = concurrent_edit
        self.edit_row("update", 0, item={"end_timestamp": "00:01"}, status=409)
        self.assertEqual(self.get("chapters")["content"], newer)

    def test_chapter_end_rehomes_later_sections_without_losing_text_and_can_restore_them(self):
        self.chapters[0]["sections"] += [
            {"timestamp": "00:10", "title": "B", "point": "Keep B"},
            {"timestamp": "00:30", "title": "C", "point": "Keep C", "skipped": True},
        ]
        self.write(self.SUMMARIES_DIR / "vid.json", self.chapters)
        saved = self.edit_row("update", 0, item={"end_timestamp": "00:15.125"})["content"]
        self.assertEqual([s["title"] for s in saved[0]["sections"]], ["Part", "B"])
        gap = saved[1]
        self.assertEqual(gap["timestamp"], "00:15.125")
        self.assertTrue(gap["flagged"])
        self.assertFalse(gap["skipped"])
        self.assertEqual([s["title"] for s in gap["sections"]], ["Unassigned", "C"])
        self.assertEqual(gap["sections"][1]["point"], "Keep C")
        self.assertEqual(saved[2]["timestamp"], self.chapters[1]["timestamp"])
        saved = self.edit_row("update", 0, item={"end_timestamp": "00:35"})["content"]
        self.assertEqual([s["title"] for s in saved[0]["sections"]], ["Part", "B", "C"])
        self.assertTrue(saved[0]["sections"][-1]["skipped"])
        self.assertEqual(saved[1]["timestamp"], "00:35")
        saved = self.edit_row("update", 0, item={"end_timestamp": "01:02:03"})["content"]
        self.assertEqual(len(saved), 2)
        self.assertEqual(saved[0]["sections"][-1]["point"], "Keep C")

    def test_new_items_accept_an_end_and_final_tail_is_grey(self):
        saved = self.edit_row("add_section", 0, 1, item={
            "timestamp": "00:10", "end_timestamp": "00:20", "title": "New section"})["content"]
        self.assertEqual(saved[0]["sections"][2]["timestamp"], "00:20")
        saved = self.edit_row("add_chapter", 1, item={
            "timestamp": "00:30", "end_timestamp": "00:40", "title": "New chapter"})["content"]
        self.assertEqual(saved[2]["timestamp"], "00:40")
        self.assertTrue(saved[2]["flagged"])
        last = len(saved) - 1
        saved = self.edit_row("update", last, item={"end_timestamp": "01:02:05"})["content"]
        self.assertEqual(saved[-1]["timestamp"], "01:02:05")
        self.assertTrue(saved[-1]["flagged"])
        self.assertFalse(saved[-1]["skipped"])

    def test_invalid_ends_are_atomic_and_cannot_cross_assigned_boundaries(self):
        for end in ("00:00", "NaN", "00:60", "", None, "01:02:04"):
            self.edit_row("update", 0, item={"end_timestamp": end}, status=400)
            self.assertEqual(self.get("chapters")["content"], self.chapters)
        self.edit_row("update", 1, item={"end_timestamp": "01:02:08"}, status=400)
        self.assertEqual(self.get("chapters")["content"], self.chapters)

    def test_unassigned_can_be_reviewed_and_assigned_without_silent_skipping(self):
        self.edit_row("update", 0, item={"end_timestamp": "00:20"})
        self.edit_row("update", 1, item={"end_timestamp": "01:02:03", "flag_reason": "Check later"})
        grey = self.edit_row("update", 1, item={"end_timestamp": "00:30"})["content"][1]
        self.assertTrue(grey["unassigned"])
        self.assertTrue(grey["flagged"])
        self.assertEqual(grey["flag_reason"], "Check later")
        saved = self.edit_row("update", 1, item={
            "title": "Assigned topic", "flagged": False, "end_timestamp": "00:30"})["content"]
        self.assertNotIn("unassigned", saved[1])
        self.assertFalse(saved[1]["flagged"])
        self.assertTrue(saved[2]["unassigned"])
        self.assertFalse(saved[2]["skipped"])

    def test_grey_section_end_is_editable_without_renaming_or_clearing_review(self):
        self.edit_row("update", 0, 0, item={"end_timestamp": "00:10"})
        grey = self.edit_row("update", 0, 1, item={
            "flag_reason": "Review when convenient", "point": "Keep this note",
        })["content"][0]["sections"][1]
        saved = self.edit_row("update", 0, 1, item={"end_timestamp": "00:20"})["content"]
        self.assertEqual(saved[0]["sections"][1], grey)
        self.assertEqual(saved[0]["sections"][2]["timestamp"], "00:20")
        self.assertTrue(saved[0]["sections"][2]["unassigned"])
        saved = self.edit_row("update", 0, 1, item={"end_timestamp": "00:25"})["content"]
        self.assertEqual(saved[0]["sections"][1], grey)
        self.assertEqual(saved[0]["sections"][2]["timestamp"], "00:25")
        saved = self.edit_row("update", 0, 1, item={"end_timestamp": "01:02:03"})["content"]
        self.assertEqual(saved[0]["sections"][1], grey)
        self.assertEqual(len(saved[0]["sections"]), 2)
        self.assertEqual(self.get("chapters")["content"], saved)

    def test_grey_chapter_end_preserves_children_and_still_rejects_invalid_boundaries(self):
        self.outline()
        saved = self.edit_row("update", 0, item={"end_timestamp": "00:05"})["content"]
        grey = saved[1]
        saved = self.edit_row("update", 1, item={"end_timestamp": "00:15"})["content"]
        self.assertEqual(saved[1]["title"], grey["title"])
        self.assertTrue(saved[1]["flagged"])
        self.assertTrue(saved[1]["unassigned"])
        self.assertEqual([part["title"] for part in saved[1]["sections"]], ["Unassigned", "B"])
        self.assertEqual([part["title"] for part in saved[2]["sections"]], ["Unassigned", "C"])
        self.assertEqual(saved[2]["timestamp"], "00:15")
        self.edit_row("update", 1, item={"end_timestamp": "00:31"}, status=400)
        self.edit_row("update", 1, item={"end_timestamp": "00:05"}, status=400)
        self.assertEqual(self.get("chapters")["content"], saved)

    def test_saving_unchanged_end_preserves_review_and_skip_choices_on_following_gap(self):
        self.edit_row("update", 0, item={"end_timestamp": "00:20"})
        self.edit_row("update", 1, item={"flag_reason": "User note", "skipped": True})
        before = self.get("chapters")["content"][1]
        saved = self.edit_row("update", 0, item={"title": "Renamed", "end_timestamp": "00:20"})["content"]
        self.assertEqual(saved[1], before)

    def test_source_video_duration_covers_silent_tail_and_probe_failures_keep_saved_content(self):
        (self.VIDEO_DIR / "vid.mp4").write_bytes(b"mock media")
        with mock.patch.object(content_edits, "video_duration", new=mock.AsyncMock(return_value=3800)):
            self.assertEqual(self.get("chapters")["duration"], 3800)
            saved = self.edit_row("update", 1, item={"end_timestamp": "01:03:00"})["content"]
            self.assertEqual(saved[-1]["timestamp"], "01:03:00")
        with mock.patch.object(content_edits, "video_duration", new=mock.AsyncMock(side_effect=ValueError("Bad media"))):
            response = self.client.post("/outline_item/vid", json={
                "action": "update", "chapter": 1, "item": {"end_timestamp": "01:02:04"}})
            self.assertEqual(response.status_code, 500)
        self.assertEqual(json.loads((self.SUMMARIES_DIR / "vid.json").read_text()), saved)

    def section_action(self, action, selections, status=200, revision=None):
        response = self.client.post("/outline_selection/vid", json={
            "action": action, "selections": selections,
            "revision": revision or self.get("chapters")["revision"],
        })
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def batch_outline(self):
        chapters = [
            {"timestamp": "00:00", "title": "Demo", "summary": "Keep summary", "sections": [
                {"timestamp": "00:00", "title": "A", "point": "Alpha", "skipped": True},
                {"timestamp": "00:10", "title": "B", "point": "Beta", "flagged": True, "flag_reason": "Review B"},
                {"timestamp": "00:20", "title": "C", "point": "Gamma"},
            ]},
            {"timestamp": "00:30", "title": "Next", "sections": [
                {"timestamp": "00:30", "title": "D", "point": "Delta"}]},
        ]
        self.write(self.SUMMARIES_DIR / "vid.json", chapters)
        return chapters

    def test_batch_merge_adjacent_sections_keeps_text_start_and_flags_without_skipping_retained_text(self):
        original = self.batch_outline()
        self.processor.summarize_outline_part.return_value = {
            "title": "Alpha and Beta", "summary": "Alpha is followed by Beta."}
        saved = self.section_action("merge", [
            {"chapter_index": 0, "section_index": 1}, {"chapter_index": 0, "section_index": 0}])["content"]
        self.assertEqual(len(saved[0]["sections"]), 2)
        merged = saved[0]["sections"][0]
        self.assertEqual(merged["timestamp"], "00:00")
        self.assertEqual(merged["title"], "Alpha and Beta")
        self.assertEqual(merged["point"], "Alpha is followed by Beta.")
        self.processor.summarize_outline_part.assert_awaited_once_with(
            [{"text": "Section 1: A\nAlpha"}, {"text": "Section 2: B\nBeta"}], "", combined=True)
        self.assertTrue(merged["flagged"])
        self.assertFalse(merged["skipped"])
        self.assertEqual(merged["flag_reason"], "Review B")
        self.assertEqual(saved[1]["timestamp"], original[1]["timestamp"])
        self.assertEqual(saved[0]["summary"], "Keep summary")
        self.assertEqual(saved, self.get("chapters")["content"])

    def test_batch_merge_adjacent_chapters_keeps_all_sections_and_generates_chapter_description(self):
        self.batch_outline()
        self.processor.summarize_outline_part.return_value = {
            "title": "Demo and Next", "summary": "Covers the demo and what follows."}
        saved = self.section_action("merge", [
            {"chapter_index": 1, "timestamp": "00:30", "title": "Next"},
            {"chapter_index": 0, "timestamp": "00:00", "title": "Demo"}])["content"]
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["timestamp"], "00:00")
        self.assertEqual(saved[0]["title"], "Demo and Next")
        self.assertEqual(saved[0]["summary"], "Covers the demo and what follows.")
        self.assertEqual([part["title"] for part in saved[0]["sections"]], ["A", "B", "C", "D"])
        self.assertTrue(saved[0]["sections"][0]["skipped"])
        self.processor.summarize_outline_part.assert_awaited_once_with(
            [{"text": "Chapter 1: Demo\nKeep summary"}, {"text": "Chapter 2: Next\n"}], "", combined=True)
        self.assertEqual(saved, self.get("chapters")["content"])

    def test_batch_chapter_merge_rejects_isolated_mixed_and_delete_selections(self):
        original = self.batch_outline()
        original.append({"timestamp": "00:40", "title": "Last", "summary": "", "sections": []})
        self.write(self.SUMMARIES_DIR / "vid.json", original)
        self.section_action("merge", [{"chapter_index": 0}, {"chapter_index": 2}], status=400)
        self.section_action("merge", [{"chapter_index": 0}, {"chapter_index": 1, "section_index": 0}], status=400)
        self.section_action("delete", [{"chapter_index": 0}], status=400)
        self.processor.summarize_outline_part.side_effect = RuntimeError("offline")
        self.section_action("merge", [{"chapter_index": 1}, {"chapter_index": 2}], status=502)
        self.assertEqual(len(self.get("chapters")["content"]), 3)
        self.processor.summarize_outline_part.assert_awaited_once_with(
            [{"text": "Chapter 1: Next\n"}, {"text": "Chapter 2: Last\n"}], "", combined=True)

    def test_batch_delete_nonadjacent_sections_is_label_only_and_preserves_remaining_rows(self):
        self.batch_outline()
        saved = self.section_action("delete", [
            {"chapter_index": 0, "section_index": 0}, {"chapter_index": 1, "section_index": 0},
            {"chapter_index": 0, "section_index": 2}])["content"]
        self.assertEqual([section["title"] for section in saved[0]["sections"]], ["Unassigned", "B"])
        self.assertTrue(saved[0]["sections"][0]["unassigned"])
        self.assertEqual(saved[0]["sections"][0]["timestamp"], saved[0]["timestamp"])
        self.assertEqual(saved[1]["sections"], [])
        self.assertEqual(json.loads((self.TRANSCRIPTS_DIR / "vid_whisper.json").read_text()), self.transcript)

    def test_batch_merge_multiple_groups_preserves_gaps_singletons_and_chapter_boundaries(self):
        self.processor.summarize_outline_part.side_effect = [
            {"title": "G, H and I", "summary": "Text G, Text H and Text I"},
            {"title": "D and E", "summary": "Text D and Text E"},
            {"title": "A and B", "summary": "Text A and Text B"},
        ]
        chapters = [
            {"timestamp": "00:00", "title": "First", "sections": [
                {"timestamp": f"00:{index * 5:02}", "title": title, "point": f"Text {title}",
                 "skipped": index < 2}
                for index, title in enumerate("ABCDEFGH")
            ]},
            {"timestamp": "00:40", "title": "Second", "sections": [
                {"timestamp": f"00:{40 + index * 5:02}", "title": title, "point": f"Text {title}"}
                for index, title in enumerate("IJK")
            ]},
        ]
        self.write(self.SUMMARIES_DIR / "vid.json", chapters)
        original = content_edits._chapters(chapters, chapters)
        selections = [(0, 0), (0, 1), (0, 3), (0, 4), (0, 6), (0, 7), (1, 0), (1, 2)]
        saved = self.section_action("merge", [
            {"chapter_index": chapter, "section_index": section}
            for chapter, section in reversed(selections)
        ])["content"]
        self.assertEqual([part["title"] for part in saved[0]["sections"]], ["A and B", "C", "D and E", "F", "G, H and I"])
        self.assertEqual(saved[0]["sections"][0]["point"], "Text A and Text B")
        self.assertTrue(saved[0]["sections"][0]["skipped"])
        self.assertFalse(saved[0]["sections"][2]["skipped"])
        self.assertEqual(saved[0]["sections"][2]["point"], "Text D and Text E")
        self.assertEqual(saved[0]["sections"][4]["point"], "Text G, Text H and Text I")
        self.assertEqual(self.processor.summarize_outline_part.await_count, 3)
        self.assertEqual(saved[0]["sections"][1], original[0]["sections"][2])
        self.assertEqual(saved[0]["sections"][3], original[0]["sections"][5])
        self.assertEqual(saved[1]["timestamp"], "00:45")
        self.assertEqual(saved[1]["sections"], original[1]["sections"][1:])
        self.assertEqual(saved, self.get("chapters")["content"])

    def test_batch_merge_groups_in_different_chapters_keeps_isolated_selection(self):
        self.processor.summarize_outline_part.side_effect = [
            {"title": "D and E", "summary": "Delta and Epsilon"},
            {"title": "A and B", "summary": "Alpha and Beta"},
        ]
        chapters = self.batch_outline()
        chapters[1]["sections"].extend([
            {"timestamp": "00:40", "title": "E", "point": "Epsilon"},
            {"timestamp": "00:50", "title": "F", "point": "Phi"},
            {"timestamp": "01:00", "title": "G", "point": "Gamma"},
        ])
        self.write(self.SUMMARIES_DIR / "vid.json", chapters)
        original = content_edits._chapters(chapters, chapters)
        saved = self.section_action("merge", [
            {"chapter_index": chapter, "section_index": section}
            for chapter, section in [(1, 3), (0, 1), (1, 1), (0, 0), (1, 0)]
        ])["content"]
        self.assertEqual([part["title"] for part in saved[0]["sections"]], ["A and B", "C"])
        self.assertEqual([part["title"] for part in saved[1]["sections"]], ["D and E", "F", "G"])
        self.assertEqual(saved[1]["sections"][0]["point"], "Delta and Epsilon")
        self.assertEqual(saved[1]["sections"][2], original[1]["sections"][3])
        self.assertEqual(saved, self.get("chapters")["content"])

    def test_batch_merge_description_failures_keep_entire_outline(self):
        original = self.batch_outline()
        selections = [{"chapter_index": 0, "section_index": index} for index in (0, 1)]
        for error in (RuntimeError("Provider unavailable"), ValueError("Invalid response"), asyncio.TimeoutError()):
            self.processor.summarize_outline_part.side_effect = error
            self.section_action("merge", selections, status=502)
            self.assertEqual(self.get("chapters")["content"], original)
        self.app.state.summary_processor = None
        self.section_action("merge", selections, status=503)
        self.assertEqual(self.get("chapters")["content"], original)

    def test_batch_merge_rechecks_revision_after_description_generation(self):
        original = self.batch_outline()
        newer = copy.deepcopy(original)
        newer[0]["title"] = "Newer edit"
        async def describe(*args, **kwargs):
            self.write(self.SUMMARIES_DIR / "vid.json", newer)
            return {"title": "Combined", "summary": "All topics"}
        self.processor.summarize_outline_part.side_effect = describe
        self.section_action("merge", [
            {"chapter_index": 0, "section_index": index} for index in (0, 1)], status=409)
        self.assertEqual(self.get("chapters")["content"], newer)

    def test_batch_merge_later_group_failure_does_not_save_earlier_group(self):
        original = self.batch_outline()
        original[1]["sections"].append({"timestamp": "00:40", "title": "E", "point": "Epsilon"})
        self.write(self.SUMMARIES_DIR / "vid.json", original)
        self.processor.summarize_outline_part.side_effect = [
            {"title": "Combined D and E", "summary": "Delta and Epsilon"},
            RuntimeError("Second request failed"),
        ]
        self.section_action("merge", [
            {"chapter_index": chapter, "section_index": section}
            for chapter, section in [(0, 0), (0, 1), (1, 0), (1, 1)]
        ], status=502)
        self.assertEqual(self.get("chapters")["content"], original)

    def test_batch_actions_reject_gaps_duplicate_and_invalid_indices(self):
        original = self.batch_outline()
        for action, selections in (
            ("merge", [{"chapter_index": 0, "section_index": 0}, {"chapter_index": 0, "section_index": 2}]),
            ("merge", [{"chapter_index": 0, "section_index": 1}, {"chapter_index": 1, "section_index": 0}]),
            ("merge", [{"chapter_index": 0, "section_index": 0}]),
            ("delete", [{"chapter_index": 0, "section_index": 0}] * 2),
            ("delete", [{"chapter_index": 0}]),
            ("delete", [{"chapter_index": True, "section_index": 0}]),
            ("delete", [{"chapter_index": 0, "section_index": -1}]),
            ("delete", []),
        ):
            self.section_action(action, selections, status=400)
            self.assertEqual(self.get("chapters")["content"], original)

    def test_batch_cross_chapter_merge_removes_consumed_chapter_and_describes_every_section(self):
        original = self.batch_outline()
        self.processor.summarize_outline_part.return_value = {
            "title": "Gamma and Delta", "summary": "Gamma followed by Delta"}
        saved = self.section_action("merge", [
            {"chapter_index": 1, "section_index": 0},
            {"chapter_index": 0, "section_index": 2},
        ])["content"]
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["title"], original[0]["title"])
        self.assertEqual(saved[0]["sections"][-1]["timestamp"], "00:20")
        self.assertEqual(saved[0]["sections"][-1]["title"], "Gamma and Delta")
        self.processor.summarize_outline_part.assert_awaited_once_with(
            [{"text": "Section 1: C\nGamma"}, {"text": "Section 2: D\nDelta"}], "", combined=True)
        self.assertEqual(saved, self.get("chapters")["content"])

    def test_batch_merge_can_span_multiple_chapters_without_crossing_an_unselected_section(self):
        chapters = self.batch_outline()
        chapters.append({"timestamp": "00:40", "title": "Third", "sections": [
            {"timestamp": "00:40", "title": "E", "point": "Epsilon"},
            {"timestamp": "00:50", "title": "F", "point": "Phi"}]})
        self.write(self.SUMMARIES_DIR / "vid.json", chapters)
        saved = self.section_action("merge", [
            {"chapter_index": ci, "section_index": si} for ci, si in [(0, 2), (1, 0), (2, 0)]
        ])["content"]
        self.assertEqual(len(saved), 2)
        self.assertEqual(saved[0]["sections"][-1]["timestamp"], "00:20")
        self.assertEqual(saved[1]["timestamp"], "00:50")
        self.assertEqual([part["title"] for part in saved[1]["sections"]], ["F"])
        self.assertEqual(self.processor.summarize_outline_part.await_count, 1)
        self.assertEqual(len(self.processor.summarize_outline_part.await_args.args[0]), 3)

    def test_batch_merge_does_not_jump_over_a_chapter_without_sections(self):
        chapters = self.batch_outline()
        chapters.insert(1, {"timestamp": "00:25", "title": "Interlude", "summary": "Keep this"})
        self.write(self.SUMMARIES_DIR / "vid.json", chapters)
        self.section_action("merge", [
            {"chapter_index": 0, "section_index": 2}, {"chapter_index": 2, "section_index": 0}
        ], status=400)
        self.assertEqual(self.get("chapters")["content"], chapters)

    def test_cross_chapter_merge_description_failure_keeps_original_boundaries(self):
        original = self.batch_outline()
        self.processor.summarize_outline_part.side_effect = RuntimeError("Provider unavailable")
        self.section_action("merge", [
            {"chapter_index": 0, "section_index": 2}, {"chapter_index": 1, "section_index": 0}
        ], status=502)
        self.assertEqual(self.get("chapters")["content"], original)

    def test_inline_cross_chapter_merge_matches_grouping_and_retains_text(self):
        original = self.batch_outline()
        original[1]["sections"].append({"timestamp": "00:40", "title": "E", "point": "Epsilon"})
        result = content_edits.outline_operations(original, [
            {"action": "merge_section", "chapter": 0, "section": 2}])
        self.assertEqual(result[1]["timestamp"], "00:40")
        self.assertEqual(result[0]["sections"][-1]["timestamp"], "00:20")
        self.assertIn("Delta", result[0]["sections"][-1]["point"])
        self.assertEqual(content_edits._chapters(result, result)[1]["timestamp"], "00:40")
        result = content_edits.outline_operations(result, [
            {"action": "merge_section", "chapter": 0, "section": 2}])
        self.assertEqual(len(result), 1)
        self.assertIn("Epsilon", result[0]["sections"][-1]["point"])

    def test_reordering_is_persistent_without_changing_source_boundaries(self):
        from clip_arrangement import section_arrangement
        original = self.batch_outline()
        operation = {"action": "reorder_section", "chapter": 1, "section": 0,
                     "target_chapter": 0, "target_section": 1}
        changed = content_edits.outline_operations(original, [operation])
        self.assertEqual(section_arrangement(changed)[1], {"timestamp": "00:30", "chapter": "00:00"})
        self.assertEqual(changed[1]["sections"], original[1]["sections"])
        self.assertEqual(changed[1]["timestamp"], original[1]["timestamp"])
        saved = self.save("chapters", changed, revision=self.get("chapters")["revision"], operations=[operation])["content"]
        self.assertEqual(saved[0]["clip_arrangement"], changed[0]["clip_arrangement"])
        self.assertEqual(saved, self.get("chapters")["content"])
        reset = content_edits.outline_operations(saved, [{"action": "reset_clip_order"}])
        self.assertNotIn("clip_arrangement", reset[0])
        self.save("chapters", reset, revision=self.get("chapters")["revision"],
                  operations=[{"action": "reset_clip_order"}])
        self.assertNotIn("clip_arrangement", self.get("chapters")["content"][0])

    def test_reordering_unmarked_clips_is_rejected_by_server(self):
        original = self.batch_outline()
        with self.assertRaisesRegex(Exception, "Review or Skip"):
            content_edits.outline_operations(original, [
                {"action": "reorder_section", "chapter": 0, "section": 2,
                 "target_chapter": 1, "target_section": 0}])
        original[1]["sections"][0]["skipped"] = True
        changed = content_edits.outline_operations(original, [
            {"action": "reorder_section", "chapter": 0, "section": 2,
             "target_chapter": 1, "target_section": 0}])
        self.assertEqual(changed[0]["clip_arrangement"][-2]["timestamp"], "00:20")

    def test_batch_stale_revision_and_save_error_do_not_overwrite_saved_outline(self):
        original = self.batch_outline()
        selection = [{"chapter_index": 0, "section_index": 0}]
        self.section_action("delete", selection, status=409, revision="stale")
        with mock.patch.object(content_edits, "persist_chapters", side_effect=OSError("Read only")):
            self.section_action("delete", selection, status=500)
        self.assertEqual(self.get("chapters")["content"], original)

    def test_summary_reload_does_not_normalize_away_user_edits(self):
        from processors import summary_processor
        edits = copy.deepcopy(self.chapters)
        edits[0]["sections"][0]["timestamp"] = "00:01.500"
        saved = self.save("chapters", edits)["content"]
        processor = summary_processor.SummaryProcessor.__new__(summary_processor.SummaryProcessor)
        with mock.patch.object(summary_processor, "SUMMARIES_DIR", self.SUMMARIES_DIR), \
                mock.patch.object(summary_processor, "JSONResponse", side_effect=lambda data: data), \
                mock.patch.object(processor, "_normalize_chapters", side_effect=AssertionError("Must preserve edits")):
            result = asyncio.run(processor.get_summary("vid"))
        self.assertEqual(result["chapters"], saved)

    def test_later_start_can_create_a_new_row_for_the_vacated_time(self):
        chapters = [
            {"timestamp": "00:00", "title": "First", "sections": [
                {"timestamp": "00:00", "title": "A"}, {"timestamp": "00:10", "title": "B"}]},
            {"timestamp": "00:20", "title": "Second", "sections": [
                {"timestamp": "00:20", "title": "C"}, {"timestamp": "00:40", "title": "D"}]},
        ]
        self.write(self.SUMMARIES_DIR / "vid.json", chapters)
        edits = copy.deepcopy(chapters)
        edits[0]["sections"][1].update(timestamp="00:14", split_gap="00:10")
        saved = self.save("chapters", edits)["content"]
        self.assertEqual([(part["timestamp"], part["title"]) for part in saved[0]["sections"]],
                         [("00:00", "A"), ("00:10", "New section"), ("00:14", "B")])
        self.assertNotIn("split_gap", saved[0]["sections"][2])
        self.assertEqual(self.get("chapters")["content"], saved)

        self.write(self.SUMMARIES_DIR / "vid.json", chapters)
        edits = copy.deepcopy(chapters)
        edits[1].update(timestamp="00:30", split_gap="00:20")
        saved = self.save("chapters", edits)["content"]
        self.assertEqual([(item["timestamp"], item["title"]) for item in saved],
                         [("00:00", "First"), ("00:20", "New chapter"), ("00:30", "Second")])
        self.assertEqual(saved[1]["sections"], [])
        self.assertEqual(saved[2]["sections"][0]["timestamp"], "00:30")

        self.write(self.SUMMARIES_DIR / "vid.json", chapters)
        for bad in ({"timestamp": "00:14", "split_gap": "00:11"}, {"timestamp": "00:08", "split_gap": "00:10"},
                    {"timestamp": "00:14", "split_gap": 10}):
            edits = copy.deepcopy(chapters)
            edits[0]["sections"][1].update(bad)
            self.save("chapters", edits, status=400)
        edits = copy.deepcopy(chapters)
        edits[0]["sections"][0].update(timestamp="00:05", split_gap="00:00")
        self.save("chapters", edits, status=400)
        self.assertEqual(self.get("chapters")["content"], chapters)

    def test_chapter_start_updates_first_section_and_previous_range(self):
        chapters = [
            {"timestamp": "00:00", "title": "First", "sections": [
                {"timestamp": "00:00", "title": "A"}, {"timestamp": "00:10", "title": "B"}]},
            {"timestamp": "00:20", "title": "Second", "sections": [
                {"timestamp": "00:20", "title": "C"}, {"timestamp": "00:40", "title": "D"}]},
        ]
        self.write(self.SUMMARIES_DIR / "vid.json", chapters)
        for stamp in ("00:30", "00:15"):
            edits = copy.deepcopy(chapters)
            edits[1]["timestamp"] = stamp
            saved = self.save("chapters", edits)["content"]
            self.assertEqual(saved[1]["sections"][0]["timestamp"], stamp)
            self.assertTrue(saved[1]["sections"][0]["timestamp_edited"])
            previous_start = content_edits._seconds(saved[0]["sections"][-1]["timestamp"])
            self.assertEqual(content_edits._seconds(saved[1]["timestamp"]) - previous_start,
                             content_edits._seconds(stamp) - 10)
            self.assertEqual(self.get("chapters")["content"], saved)
            chapters = saved
        edits = copy.deepcopy(chapters)
        edits[1]["timestamp"] = "00:45"
        self.save("chapters", edits, status=400)
        self.assertEqual(self.get("chapters")["content"], chapters)

    def test_write_failure_is_reported_without_success_response(self):
        with mock.patch.object(transcript_versions, "_write_json", side_effect=PermissionError("read only")):
            response = self.client.post("/editable_content/vid", json={
                "kind": "slides", "revision": self.get("slides")["revision"], "edits": ["Title"],
            })
        self.assertEqual(response.status_code, 500)
        self.assertIn("Could not save", response.json()["detail"])

    def test_transcript_save_preserves_original_timing_speakers_and_metadata(self):
        metadata = {"model": "turbo", "speakers": {"A": "Alice"}}
        self.write(self.TRANSCRIPTS_DIR / "vid_whisper_meta.json", metadata)
        transcript_versions.sync_existing("vid")
        original = transcript_versions.list_versions("vid")[0]
        result = self.save("transcript", ["Corrected", "Second"], version_id=original["id"])
        self.assertTrue(result["version"]["edited"])
        self.assertIn("edited", result["version"]["label"])
        expected = copy.deepcopy(self.transcript)
        expected[0]["text"] = "Corrected"
        self.assertEqual(result["content"], expected)
        self.assertEqual(self.get("transcript")["content"], expected)
        restored = transcript_versions.activate_version("vid", original["id"])
        self.assertEqual(restored["transcript"], self.transcript)
        self.assertEqual(restored["chapters"], self.chapters)
        edited = transcript_versions.activate_version("vid", result["version"]["id"])
        self.assertEqual(edited["metadata"], metadata)

    def test_translation_and_uploaded_edits_keep_provenance(self):
        for source, language in (("uploaded", ""), ("whisper", "de")):
            filename = f"vid_translated_{language}.json" if language else "vid_uploaded.json"
            self.write(self.TRANSCRIPTS_DIR / filename, self.transcript)
            if language:
                self.write(self.SUMMARIES_DIR / "vid_summary_de.json", self.chapters)
            result = self.save("transcript", ["Changed", "Second"], source=source, language=language)
            self.assertEqual(result["version"]["language"], language or None)
            self.assertEqual(self.get("transcript", source=source, language=language)["content"][0]["text"], "Changed")
            active = transcript_versions.get_active_source("vid")
            self.assertEqual(active["kind"], "translation" if language else "transcript")
            if not language:
                self.assertEqual(active["source"], "uploaded")

    def test_slide_title_edit_and_clear_preserve_other_fields(self):
        content = self.save("slides", ["<My title>"])["content"]
        self.assertEqual(content, [{"time_seconds": 0, "image": "keep.jpg", "title": "<My title>", "title_edited": True}])
        self.assertEqual(self.save("slides", [""])["content"][0]["title"], "")

    def test_stale_revision_rejected(self):
        revision = self.get("transcript")["revision"]
        self.save("transcript", ["Changed", "Second"])
        self.save("transcript", ["Lost update", "Second"], revision=revision, status=409)

    def outline(self):
        chapters = [
            {"timestamp": "00:00", "title": "First", "summary": "One", "extra": 7, "sections": [
                {"timestamp": "00:00", "title": "A", "point": "Alpha", "extra": 9},
                {"timestamp": "00:10", "title": "B", "point": "Beta"},
                {"timestamp": "00:20", "title": "C", "point": "Gamma"}]},
            {"timestamp": "00:30", "title": "Second", "summary": "Two", "sections": [
                {"timestamp": "00:30", "title": "D", "point": "Delta"},
                {"timestamp": "00:40", "title": "E", "point": "Epsilon"}]},
        ]
        self.write(self.SUMMARIES_DIR / "vid.json", chapters)
        return chapters

    def test_outline_grouping_saves_atomically_and_retains_text_and_metadata(self):
        for operation in [
            {"action": "merge_section", "chapter": 0, "section": 0},
            {"action": "merge_chapter", "chapter": 0},
            {"action": "promote", "chapter": 0, "section": 1},
            {"action": "move_section", "chapter": 0, "section": 2, "target": 1},
            {"action": "move_section", "chapter": 1, "section": 0, "target": 0},
        ]:
            original = self.outline()
            changed = content_edits.outline_operations(original, [operation])
            changed[0]["title"] = "Inline title"
            saved = self.save("chapters", changed, operations=[operation])["content"]
            self.assertEqual(saved[0]["extra"], 7)
            self.assertEqual(saved[0]["title"], "Inline title")
            for word in ("Alpha", "Beta", "Gamma", "Delta", "Epsilon"):
                self.assertIn(word, json.dumps(saved))
            self.assertEqual(self.get("chapters")["content"], saved)
            if operation["action"] == "merge_section":
                self.assertEqual(saved[0]["sections"][0]["extra"], 9)
                self.assertEqual(saved[0]["sections"][0]["timestamp"], "00:00")
                self.assertEqual(saved[0]["sections"][0]["title"], "A")

    def test_starting_chapters_at_section_boundaries_moves_remaining_sections_and_persists(self):
        original = self.outline()
        original[0]["sections"][1].update(flagged=True, skipped=True, extra="keep")
        original = content_edits._chapters(original, original)
        self.write(self.SUMMARIES_DIR / "vid.json", original)
        saved = self.edit_row("add_chapter", 1, item={
            "timestamp": "00:10", "end_timestamp": "00:30", "title": "New chapter",
        })["content"]
        self.assertEqual([chapter["timestamp"] for chapter in saved], ["00:00", "00:10", "00:30"])
        self.assertEqual(saved[0]["sections"], original[0]["sections"][:1])
        self.assertEqual(saved[1]["sections"], original[0]["sections"][1:])
        self.assertEqual(saved[2], original[1])
        self.assertEqual(saved[0]["extra"], 7)
        self.assertEqual(saved[0]["summary"], "One")
        saved = self.edit_row("add_chapter", 2, item={
            "timestamp": "00:20", "end_timestamp": "00:30", "title": "Another chapter",
        })["content"]
        self.assertEqual([chapter["timestamp"] for chapter in saved], ["00:00", "00:10", "00:20", "00:30"])
        self.assertEqual([len(chapter["sections"]) for chapter in saved], [1, 1, 1, 2])
        self.assertEqual(saved[1]["sections"][0], original[0]["sections"][1])
        self.assertEqual(saved[2]["sections"][0], original[0]["sections"][2])
        self.assertEqual(saved[3], original[1])
        self.assertEqual(self.get("chapters")["content"], saved)

    def test_invalid_structure_never_overwrites_saved_outline(self):
        original = self.outline()
        for op in [
            {"action": "move_section", "chapter": 0, "section": 1, "target": 1},
            {"action": "move_section", "chapter": 0, "section": 2, "target": 2},
            {"action": "promote", "chapter": 0, "section": 0},
            {"action": "merge_section", "chapter": 0, "section": 2},
            {"action": "merge_chapter", "chapter": True},
        ]:
            self.save("chapters", original, status=400, operations=[op])
            self.assertEqual(self.get("chapters")["content"], original)

    def test_slide_inline_timestamps_keep_images_and_translation(self):
        self.write(self.SCENES_DIR / "vid.json", [
            {"timestamp": "00:00", "time_seconds": 0, "image": "first.jpg"},
            {"timestamp": "00:10", "time_seconds": 10, "image": "second.jpg"},
        ])
        result = self.save("slides", [{"timestamp": "00:01.500", "title": "One"},
                                      {"timestamp": "00:12", "title": "Two"}], language="en")["content"]
        self.assertEqual(result[0]["image"], "first.jpg")
        self.assertEqual(result[0]["time_seconds"], 1.5)
        self.assertEqual(result[0]["duration"], 10.5)
        self.assertEqual(result[0]["title_translations"]["en"], "One")
        self.save("slides", [{"timestamp": "00:15", "title": "No"},
                             {"timestamp": "00:12", "title": "Two"}], status=400)

    def test_invalid_edits_do_not_change_files(self):
        edits = copy.deepcopy(self.chapters)
        for timestamp in ("bad", "-01:00", "01:60:00", "00:00"):
            edits[1]["timestamp"] = timestamp
            self.save("chapters", edits, status=400)
        edits = copy.deepcopy(self.chapters)
        edits[0]["sections"][0]["timestamp"] = "02:00:00"
        self.save("chapters", edits, status=400)
        self.save("transcript", ["", "Second"], status=400)
        self.save("slides", ["One", "Two"], status=400)
        self.assertEqual(self.get("chapters")["content"], self.chapters)
        self.assertEqual(self.get("transcript")["content"], self.transcript)

    def test_unchanged_transcript_does_not_relabel_original_as_edited(self):
        self.save("transcript", ["First", "Second"], status=400)
        self.assertFalse(any(version.get("edited") for version in transcript_versions.list_versions("vid")))

    def test_old_version_cannot_overwrite_active_corrections(self):
        transcript_versions.sync_existing("vid")
        original = transcript_versions.list_versions("vid")[0]
        self.save("transcript", ["Changed", "Second"], version_id=original["id"])
        response = self.client.get("/editable_content/vid", params={"kind": "transcript", "version_id": original["id"]})
        self.assertEqual(response.status_code, 409)

    def test_invalid_request_types_and_malformed_json(self):
        for key in ("kind", "source", "language", "version_id"):
            data = {"kind": "slides", key: []}
            self.assertEqual(self.client.post("/editable_content/vid", json=data).status_code, 400)
        self.assertEqual(self.client.post("/editable_content/vid", content="{").status_code, 400)
        self.assertEqual(self.client.get("/editable_content/bad.id", params={"kind": "slides"}).status_code, 400)
        self.write(self.SCENES_DIR / "vid.json", ["bad"])
        self.assertEqual(self.client.get("/editable_content/vid", params={"kind": "slides"}).status_code, 500)

    def test_version_language_and_source_mismatch_rejected(self):
        transcript_versions.sync_existing("vid")
        version = transcript_versions.list_versions("vid")[0]["id"]
        for context in ({"language": "de"}, {"source": "uploaded"}):
            response = self.client.get("/editable_content/vid", params={
                "kind": "transcript", "version_id": version, **context,
            })
            self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()
