"""Exercise export helpers/endpoint without loading transcription or GPU models."""
import ast
import asyncio
from collections import Counter
import html
from io import BytesIO
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import uuid
import zipfile

from docx import Document
from docx.shared import Inches
from docx.image.exceptions import UnrecognizedImageError
from docx.oxml import OxmlElement
from PIL import Image as PillowImage, UnidentifiedImageError
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image as PdfImage, Paragraph, SimpleDocTemplate, Spacer


ROOT = Path(__file__).resolve().parents[1]


class HTTPException(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


def load_export_functions():
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    names = {
        "build_outline_points", "parse_chapter_timestamp", "format_chapter_timestamp",
        "chapter_boundaries_from_topics", "chapter_boundaries_from_scenes", "export_slide_start",
        "export_sentences", "align_export_chapters", "export_key_points", "summarize_export_batch",
        "build_export_subparts", "export_archive_paths",
        "create_combined_chapter_documents", "collapse_word_heading", "export_chapters",
    }
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
             and node.name in names]
    for node in nodes:
        node.decorator_list = []
    namespace = dict(globals(), Request=object, FileResponse=lambda path, **kwargs: SimpleNamespace(path=path, **kwargs))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ROOT / "main.py"), "exec"), namespace)
    return namespace


class ExportDocumentTests(unittest.TestCase):
    def setUp(self):
        self.directory = ROOT / (".export-tests-" + uuid.uuid4().hex)
        self.directory.mkdir()
        self.addCleanup(shutil.rmtree, self.directory)
        self.functions = load_export_functions()
        for name in ("EXPORTS_DIR", "TRANSCRIPTS_DIR", "SUMMARIES_DIR", "SCENES_DIR", "VIDEO_DIR"):
            directory = self.directory / name
            directory.mkdir()
            self.functions[name] = directory
        self.functions["summary_processor"] = SimpleNamespace(
            _refresh_clients=lambda: None, _provider_chain=lambda: [],
        )
        self.functions["video_processor"] = SimpleNamespace(get_video_path=lambda _: self.directory / "lecture.mp4")
        self.transcript = [
            {"start": 0, "duration": 4, "text": "Neural networks learn"},
            {"start": 4, "duration": 4, "text": "from examples. Training adjusts weights."},
            {"start": 8, "duration": 4, "text": "Validation measures generalization."},
            {"start": 12, "duration": 4, "text": "Regularization prevents overfitting."},
        ]
        self.functions["TRANSCRIPTS_DIR"].joinpath("lecture.json").write_text(json.dumps(self.transcript), encoding="utf-8")
        self.functions["SUMMARIES_DIR"].joinpath("lecture.json").write_text(json.dumps([
            {"timestamp": "00:00", "title": "Neural networks"},
            {"timestamp": "00:03", "title": "Training and evaluation"},
        ]), encoding="utf-8")
        self.functions["SCENES_DIR"].joinpath("lecture.json").write_text(json.dumps([
            {"timestamp": "00:00", "title": "Learning"},
            {"timestamp": "00:09", "title": "Validation diagram"},
        ]), encoding="utf-8")

    def run_export(self, **options):
        async def request_json():
            return options

        def run_media(command, **kwargs):
            if command[0] == "ffprobe":
                return SimpleNamespace(stdout="16")
            target = Path(command[-1])
            if target.suffix == ".jpg":
                PillowImage.new("RGB", (32, 18), "white").save(target)
            else:
                target.write_bytes(b"test video")
            return SimpleNamespace(returncode=0)

        with mock.patch.object(subprocess, "run", side_effect=run_media):
            return asyncio.run(self.functions["export_chapters"]("lecture", SimpleNamespace(json=request_json)))

    def test_sentence_alignment_preserves_every_word_once(self):
        sentences = self.functions["export_sentences"](self.transcript)
        self.assertEqual(sentences[0]["text"], "Neural networks learn from examples.")
        chapters = self.functions["align_export_chapters"]([
            {"timestamp": "00:00", "title": "First"},
            {"timestamp": "00:03", "title": "Second"},
        ], sentences)
        self.assertGreater(chapters[1]["start"], 3)
        self.assertEqual(chapters[1]["start"], sentences[1]["start"])
        chunks = [[item["text"] for item in sentences if item["start"] >= part["start"]
                   and (part["end"] is None or item["start"] < part["end"])] for part in chapters]
        self.assertEqual(" ".join(text for chunk in chunks for text in chunk),
                         " ".join(item["text"] for item in self.transcript))

    def test_whisper_unpunctuated_phrases_join_until_pause_or_speaker_turn(self):
        phrases = [
            {"start": 0, "end": 2, "text": "neural networks learn"},
            {"start": 2.1, "end": 4, "text": "from examples", "speaker": "A"},
            {"start": 5.5, "duration": 2, "text": "training adjusts weights", "speaker": "A"},
            {"start": 7.6, "duration": 2, "text": "validation measures generalization", "speaker": "B"},
        ]
        sentences = self.functions["export_sentences"](phrases)
        self.assertEqual([item["text"] for item in sentences], [
            "neural networks learn from examples",
            "training adjusts weights",
            "validation measures generalization",
        ])
        parts = self.functions["align_export_chapters"]([
            {"timestamp": "00:00", "title": "Learning"},
            {"timestamp": "00:03", "title": "Evaluation"},
        ], sentences)
        self.assertEqual(parts[1]["start"], 5.5)
        self.assertEqual(" ".join(item["text"] for item in sentences),
                         " ".join(item["text"] for item in phrases))

    def test_unknown_caption_duration_does_not_invent_pauses(self):
        fragments = [
            {"start": 0, "text": "we learn"},
            {"start": 20, "text": "from examples"},
            {"start": 30, "text": "to improve predictions"},
        ]
        sentences = self.functions["export_sentences"](fragments)
        self.assertEqual(len(sentences), 1)
        self.assertEqual(sentences[0]["text"], "we learn from examples to improve predictions")

    def test_slide_media_links_preserve_fractional_scene_time(self):
        self.functions["SCENES_DIR"].joinpath("lecture.json").write_text(json.dumps([
            {"timestamp": "00:09", "time_seconds": 9.375, "title": "Validation"},
        ]), encoding="utf-8")
        response = self.run_export(include_webpage=True, subpart_mode="slides")
        with zipfile.ZipFile(response.path) as archive:
            webpage = archive.read("index.html").decode()
            self.assertIn("slide_0000009375.jpg", archive.namelist())
        self.assertIn("#t=3.775", webpage)

    def test_html_archive_has_real_hierarchy_and_only_required_assets(self):
        response = self.run_export(include_webpage=True)
        with zipfile.ZipFile(response.path) as archive:
            names = archive.namelist()
            self.assertIn("index.html", names)
            self.assertTrue(all(name == "index.html" or name.endswith((".jpg", ".mp4")) for name in names), names)
            webpage = archive.read("index.html").decode()
            self.assertIn("<h2>Part 1: Neural networks</h2>", webpage)
            self.assertGreaterEqual(webpage.count("<h3>"), 3)
            self.assertIn('<details class="chapter-subparts">', webpage)
            self.assertIn('<details class="subpart">', webpage)
            self.assertIn("No AI-generated summary available.", webpage)
            self.assertNotIn("<summary>Key point", webpage)
            self.assertIn("Extractive key sentences (AI not configured)", webpage)
            self.assertIn("Slide at 00:00:09", webpage)
            self.assertRegex(webpage, r"<h3>[^<]+\(00:00:\d\d\)</h3>")
            for reference in re.findall(r'(?:src|href)="([^"]+)"', webpage):
                self.assertIn(html.unescape(reference).split("#")[0], names)
            transcript_blocks = re.findall(r"<pre>(.*?)</pre>", webpage, re.DOTALL)
            self.assertEqual(" ".join(html.unescape(block).replace("\n", " ") for block in transcript_blocks),
                             " ".join(item["text"] for item in self.transcript))
        print("HTML: timestamped chapter/subpart headings, complete sentences, valid slide links; ZIP =", names)

    def test_explicit_transcripts_and_formats_are_preserved(self):
        response = self.run_export(include_webpage=True, include_transcripts=True,
                                   include_word=True, include_pdf=True, include_outline=True)
        with zipfile.ZipFile(response.path) as archive:
            names = archive.namelist()
            for name in ("chapter_document.docx", "chapter_document.pdf", "video_outline.docx",
                         "transcript_by_chapter.md", "chapters.json"):
                self.assertIn(name, names)
            self.assertTrue(any(name.endswith(".txt") for name in names))
            self.assertFalse(any(name.endswith(".png") for name in names))
        print("Mixed-format ZIP preserves explicitly selected documents and transcript files.")

    def test_no_format_selected_defaults_to_html(self):
        response = self.run_export()
        self.assertEqual(response.path.suffix, ".zip")
        with zipfile.ZipFile(response.path) as archive:
            self.assertIn("index.html", archive.namelist())
            self.assertFalse(any(name.endswith((".docx", ".txt", ".pdf")) for name in archive.namelist()))

    def test_selected_word_has_subpart_headings_and_full_sentences(self):
        response = self.run_export(include_word=True)
        self.assertEqual(response.path.suffix, ".docx")
        document = Document(response.path)
        headings = [paragraph.text for paragraph in document.paragraphs
                    if paragraph.style.name.startswith("Heading")]
        self.assertTrue(any("Part 1: Neural networks" in heading for heading in headings))
        self.assertTrue(any("Training adjusts weights" in heading for heading in headings))
        self.assertTrue(any("(00:00:" in heading for heading in headings))
        self.assertIn("Neural networks learn from examples.", [p.text for p in document.paragraphs])
        print("Word: verified actual document heading hierarchy =", headings)

    def test_pdf_has_actual_subpart_headings(self):
        with mock.patch.dict(self.functions, {"Paragraph": mock.Mock(wraps=Paragraph)}):
            response = self.run_export(include_pdf=True)
            paragraphs = self.functions["Paragraph"].call_args_list
        self.assertEqual(response.path.suffix, ".pdf")
        self.assertGreater(response.path.stat().st_size, 1000)
        self.assertTrue(any(call.args[1].name == "Heading2" for call in paragraphs))
        print("PDF: built", response.path.stat().st_size, "bytes with timestamped Heading2 subparts.")

    def test_ai_points_use_specific_titles_and_errors_are_not_hidden(self):
        processor = self.functions["summary_processor"]
        processor._provider_chain = lambda: [("gemini", "test-model")]
        calls = []

        def complete(*args):
            calls.append(args)
            return json.dumps({
                "summary": "Neural networks learn patterns from examples.",
                "points": [{
                    "sentence_id": 0, "title": "Learning from examples",
                    "text": "Neural networks learn patterns from examples.",
                }],
            })

        processor._complete = complete
        sentences = self.functions["export_sentences"](self.transcript)
        points, method, summary = asyncio.run(self.functions["export_key_points"](sentences))
        self.assertEqual(points[0]["title"], "Learning from examples")
        self.assertEqual(method, "AI-summarized key points")
        self.assertEqual(summary, "Neural networks learn patterns from examples.")
        self.assertEqual(len(calls), 1)
        processor._complete = lambda *args: json.dumps({
            "summary": "A valid short summary.",
            "points": [{"sentence_id": 0, "title": "Key point", "text": "Fake."}],
        })
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(self.functions["export_key_points"](sentences))
        self.assertEqual(raised.exception.status_code, 502)
        self.assertIn("generic", raised.exception.detail)

    def test_long_chapters_keep_bounded_batches_and_short_combined_summary(self):
        processor = self.functions["summary_processor"]
        processor._provider_chain = lambda: [("groq", "test-model")]
        calls = []

        def complete(*args):
            calls.append(args)
            return json.dumps({
                "summary": "The chapter explains learning.",
                "points": [{"sentence_id": 0, "title": "Learning", "text": "Learning uses examples."}],
            })

        processor._complete = complete
        sentences = [{"start": index, "text": "Examples " * 700 + "."} for index in range(3)]
        points, _, summary = asyncio.run(self.functions["export_key_points"](sentences))
        self.assertEqual(len(calls), 4)
        self.assertEqual([point["start"] for point in points], [0, 1, 2])
        self.assertEqual(summary, "The chapter explains learning.")
        self.assertTrue(all(len(call[2]) < 11000 for call in calls))

    def test_one_ai_request_per_chapter_supplies_html_word_and_pdf_summaries(self):
        processor = self.functions["summary_processor"]
        processor._provider_chain = lambda: [("gemini", "test-model")]
        calls = []

        def complete(*args):
            calls.append(args)
            return json.dumps({
                "summary": "This chapter teaches neural network fundamentals.",
                "points": [{
                    "sentence_id": 0,
                    "title": "Neural network fundamentals",
                    "text": "Neural networks learn patterns from examples.",
                }],
            })

        processor._complete = complete
        paragraph = mock.Mock(wraps=Paragraph)
        with mock.patch.dict(self.functions, {"Paragraph": paragraph}):
            response = self.run_export(
                include_webpage=True, include_word=True, include_pdf=True
            )

        with zipfile.ZipFile(response.path) as archive:
            webpage = archive.read("index.html").decode()
            document = Document(BytesIO(archive.read("chapter_document.docx")))
        self.assertEqual(len(calls), 2)
        self.assertEqual(webpage.count("This chapter teaches neural network fundamentals."), 2)
        self.assertEqual(webpage.count('<details class="chapter-subparts">'), 2)
        document_text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        self.assertEqual(
            document_text.count(
                "Summary: This chapter teaches neural network fundamentals."
            ),
            2,
        )
        self.assertTrue(any(
            "<b>Summary:</b> This chapter teaches neural network fundamentals."
            in str(call.args[0])
            for call in paragraph.call_args_list
        ))

    def test_scorm_contains_manifest_without_internal_transcript_files(self):
        response = self.run_export(include_scorm=True)
        with zipfile.ZipFile(response.path) as archive:
            names = archive.namelist()
            self.assertIn("imsmanifest.xml", names)
            self.assertIn("scorm_api.js", names)
            self.assertFalse(any(name.endswith((".txt", ".png", ".json")) for name in names))
            manifest = archive.read("imsmanifest.xml").decode()
            for reference in re.findall(r'<file href="([^"]+)"', manifest):
                self.assertIn(reference, names)

    def test_images_and_clips_work_without_any_transcript(self):
        self.functions["TRANSCRIPTS_DIR"].joinpath("lecture.json").unlink()
        response = self.run_export(include_images=True, include_clips=True)
        with zipfile.ZipFile(response.path) as archive:
            names = archive.namelist()
            self.assertTrue(any(name.endswith(".jpg") for name in names))
            self.assertTrue(any(name.endswith(".mp4") for name in names))
            self.assertTrue(all(name.endswith((".jpg", ".mp4")) for name in names))

    def test_fixed_intervals_and_part_timestamp_option_remain_selectable(self):
        response = self.run_export(include_webpage=True, interval_minutes=0.1,
                                   subpart_mode="slides", timestamp_mode="part")
        with zipfile.ZipFile(response.path) as archive:
            webpage = archive.read("index.html").decode()
        self.assertIn("Interval 2", webpage)
        self.assertIn("Slide at 00:00:09", webpage)
        self.assertNotRegex(webpage, r"<h3>[^<]+\(00:00:")


if __name__ == "__main__":
    unittest.main()
