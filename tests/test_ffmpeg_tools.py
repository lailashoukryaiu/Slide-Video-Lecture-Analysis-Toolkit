import os
import unittest
from unittest import mock

import ffmpeg_tools


class MediaExecutableTests(unittest.TestCase):
    def test_current_path_takes_precedence(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(ffmpeg_tools.shutil, "which", return_value="installed-ffmpeg") as which:
            self.assertEqual(ffmpeg_tools.media_executable("ffmpeg"), "installed-ffmpeg")
            which.assert_called_once_with("ffmpeg")

    def test_windows_installed_path_is_used_when_process_path_is_stale(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(ffmpeg_tools, "_windows_install_path", return_value="updated-install-path"), \
                mock.patch.object(ffmpeg_tools.shutil, "which", side_effect=[None, "resolved-ffmpeg"]) as which:
            self.assertEqual(ffmpeg_tools.media_executable("ffmpeg"), "resolved-ffmpeg")
            self.assertIn("updated-install-path", which.call_args.kwargs["path"])

    def test_explicit_executable_is_used(self):
        with mock.patch.dict(os.environ, {"FFMPEG_BINARY": "custom-ffmpeg"}, clear=True), \
                mock.patch.object(ffmpeg_tools.shutil, "which", return_value="custom-path") as which:
            self.assertEqual(ffmpeg_tools.media_executable("ffmpeg"), "custom-path")
            which.assert_called_once_with("custom-ffmpeg")

    def test_invalid_override_does_not_silently_fall_back(self):
        with mock.patch.dict(os.environ, {"FFMPEG_BINARY": "missing"}, clear=True), \
                mock.patch.object(ffmpeg_tools.shutil, "which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "FFMPEG_BINARY does not point"):
                ffmpeg_tools.media_executable("ffmpeg")

    def test_missing_tool_has_actionable_error(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(ffmpeg_tools, "_windows_install_path", return_value=""), \
                mock.patch.object(ffmpeg_tools.shutil, "which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "Install FFmpeg.*FFPROBE_BINARY"):
                ffmpeg_tools.media_executable("ffprobe")

    def test_unsupported_tool_is_rejected(self):
        with self.assertRaises(ValueError):
            ffmpeg_tools.media_executable("other")

    def test_non_windows_launch_does_not_access_windows_registry(self):
        with mock.patch.object(ffmpeg_tools.sys, "platform", "linux"):
            self.assertEqual(ffmpeg_tools._windows_install_path(), "")
