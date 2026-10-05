import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from processors import transcript_versions


class TranscriptVersionsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.transcripts = root / "transcripts"
        self.summaries = root / "summaries"
        self.transcripts.mkdir()
        self.summaries.mkdir()
        patches = [
            mock.patch.object(transcript_versions, "TRANSCRIPTS_DIR", self.transcripts),
            mock.patch.object(transcript_versions, "SUMMARIES_DIR", self.summaries),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.temp.cleanup)

    def write(self, name, data, directory=None):
        (directory or self.transcripts).joinpath(name).write_text(json.dumps(data), encoding="utf-8")

    def read(self, name, directory=None):
        return json.loads((directory or self.transcripts).joinpath(name).read_text(encoding="utf-8"))

    def test_regenerated_transcripts_are_kept_and_can_be_restored(self):
        first = [{"start": 0, "duration": 2, "text": "First take"}]
        second = [{"start": 0, "duration": 2, "text": "Second take"}]
        self.write("vid_whisper.json", first)
        self.write("vid_whisper_meta.json", {"model": "turbo", "generated_at": "2026-10-05 10:00"})
        transcript_versions.sync_existing("vid")
        self.write("vid_whisper.json", second)
        self.write("vid_whisper_meta.json", {"model": "groq:whisper-large-v3", "generated_at": "2026-10-05 11:00"})

        versions = transcript_versions.list_versions("vid")
        self.assertEqual(len(versions), 2)
        self.assertEqual([version["active_whisper"] for version in versions], [True, False])
        self.assertIn("Groq Whisper Large v3", versions[0]["label"])

        result = transcript_versions.activate_version("vid", versions[1]["id"])
        self.assertEqual(result["transcript"], first)
        self.assertEqual(self.read("vid_whisper.json"), first)
        self.assertEqual(self.read("vid_whisper_meta.json")["model"], "turbo")
        self.assertEqual(len(transcript_versions.list_versions("vid")), 2)

    def test_translations_keep_their_chapters_and_source(self):
        original = [{"start": 0, "text": "Hello"}]
        transcript_versions.save_version("vid", "transcript", original, source="whisper", model="turbo")
        for text, title in (("Hallo", "Einleitung"), ("Guten Tag", "Einführung")):
            transcript_versions.save_version(
                "vid", "translation", [{"start": 0, "text": text}], language="de",
                model="gemini", chapters=[{"timestamp": "00:00", "title": title}],
                based_on=transcript_versions.find_transcript_label("vid", original),
            )
        self.write("vid_translated_de.json", [{"start": 0, "text": "Guten Tag"}])
        translations = [v for v in transcript_versions.list_versions("vid") if v["kind"] == "translation"]
        self.assertEqual(len(translations), 2)
        self.assertTrue(all(v["based_on"] == "Transcript · Whisper turbo" for v in translations))
        first = next(v for v in translations if v["segments"] == 1 and "Hallo" in json.dumps(
            transcript_versions.activate_version("vid", v["id"])["transcript"], ensure_ascii=False))
        self.assertEqual(self.read("vid_translated_de.json"), [{"start": 0, "text": "Hallo"}])
        self.assertEqual(self.read("vid_summary_de.json", self.summaries)[0]["title"], "Einleitung")
        self.assertEqual(first["language"], "de")

    def test_delete_refuses_current_transcript_and_removes_current_translation(self):
        self.write("vid_whisper.json", [{"start": 0, "text": "Hello"}])
        self.write("vid_translated_pl.json", [{"start": 0, "text": "Cześć"}])
        versions = transcript_versions.list_versions("vid")
        current = next(v for v in versions if v["kind"] == "transcript")
        translation = next(v for v in versions if v["kind"] == "translation")
        with self.assertRaises(ValueError):
            transcript_versions.delete_version("vid", current["id"])
        transcript_versions.delete_version("vid", translation["id"])
        self.assertFalse((self.transcripts / "vid_translated_pl.json").exists())
        self.assertEqual([v["kind"] for v in transcript_versions.list_versions("vid")], ["transcript"])

    def test_rejects_unsafe_video_ids(self):
        with self.assertRaises(ValueError):
            transcript_versions.list_versions("../etc")


if __name__ == "__main__":
    unittest.main()
