import os
import cv2
import asyncio
import math
import numpy as np
from scenedetect import open_video, ContentDetector, SceneManager
from fastapi.responses import JSONResponse
import json
from concurrent.futures import ThreadPoolExecutor

from project_paths import SCENES_DIR, THUMBNAILS_DIR, FULLSIZE_IMAGES_DIR, SUMMARIES_DIR

SIGNATURE_SIZE = (128, 72)
PIXEL_CHANGE_LEVEL = 18
INK_LEVEL = 25
# Share of the frame (outside live regions) that must change to count at all,
# e.g. ignoring cursor movement and compression noise.
NOISE_CHANGE_FRACTION = 0.003
DUPLICATE_CHANGE_FRACTION = 0.005
LIVE_REGION_ACTIVITY = 0.1
QUIET_PAIR_FRACTION = 0.02
MAXIMUM_BUILD_FRACTION = 0.12
BUILD_REMOVED_RATIO = 0.15
ADAPTIVE_PRESETS = {
    "more": {"change_threshold": 0.005, "keep_builds": "all", "large_build": 0.0},
    "balanced": {"change_threshold": 0.005, "keep_builds": "large", "large_build": 0.025},
    "fewer": {"change_threshold": 0.015, "keep_builds": "none", "large_build": 1.0},
}


class SceneProcessor:
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=2)

    async def get_scenes(self, video_id: str):
        """Get scenes for a video."""
        scene_path = str(SCENES_DIR / f"{video_id}.json")
        diagnostics_path = SCENES_DIR / f"{video_id}_diagnostics.json"
        error_path = SCENES_DIR / f"{video_id}_error.txt"
        if error_path.exists():
            try:
                error = error_path.read_text(encoding="utf-8")
            except OSError as read_error:
                error = f"Could not read scene detection error: {read_error}"
            return JSONResponse({
                "success": False,
                "error": error
            })
        if os.path.exists(scene_path):
            try:
                with open(scene_path, 'r') as f:
                    scenes = json.load(f)
                return JSONResponse({
                    "success": True,
                    "scenes": scenes,
                    "complete": True,
                    "diagnostics": self._read_diagnostics(diagnostics_path)
                })
            except Exception as e:
                return JSONResponse({
                    "success": False,
                    "error": str(e)
                })
        else:
            return JSONResponse({
                "success": True,
                "scenes": [],
                "complete": False
            })

    async def get_scene_detections(self, video_id: str, scene_index: int):
        """Get detection data for a specific scene."""
        scene_path = str(SCENES_DIR / f"{video_id}.json")
        if not os.path.exists(scene_path):
            return JSONResponse({
                "success": False,
                "error": "Scene data not found"
            })
        
        try:
            with open(scene_path, 'r') as f:
                scenes = json.load(f)
            
            if scene_index < len(scenes):
                scene = scenes[scene_index]
                return JSONResponse({
                    "success": True,
                    "scene": scene,
                    "has_detections": "yolo_detections" in scene,
                    "detection_count": len(scene.get("yolo_detections", {}).get("detections", [])) if "yolo_detections" in scene else 0
                })
            else:
                return JSONResponse({
                    "success": False,
                    "error": f"Scene index {scene_index} out of range"
                })
        except Exception as e:
            return JSONResponse({
                "success": False,
                "error": str(e)
            })

    async def start_scene_detection(self, video_id: str, video_path: str, background_tasks, mode: str, options: dict):
        """Start scene detection in the background."""
        scene_path = SCENES_DIR / f"{video_id}.json"
        diagnostics_path = SCENES_DIR / f"{video_id}_diagnostics.json"
        error_path = SCENES_DIR / f"{video_id}_error.txt"
        if scene_path.exists():
            scene_path.unlink()
        if diagnostics_path.exists():
            diagnostics_path.unlink()
        if error_path.exists():
            error_path.unlink()
        for image_dir in (
            THUMBNAILS_DIR / video_id,
            FULLSIZE_IMAGES_DIR / video_id,
        ):
            if image_dir.exists():
                for image_path in image_dir.glob("*.jpg"):
                    image_path.unlink()
        background_tasks.add_task(self.run_scene_detection, video_id, video_path, mode, options)

    async def run_scene_detection(self, video_id: str, video_path: str, mode: str, options: dict):
        """Run scene detection and save results."""
        try:
            # Detection is CPU-bound (OpenCV/PySceneDetect) and blocking. Run it
            # in a worker thread so the event loop stays free to serve other
            # requests, such as streaming the video the browser needs for
            # its preview, while this background task is running.
            scenes = await asyncio.to_thread(
                self.detect_scenes, video_path, mode, options
            )

            scene_path = str(SCENES_DIR / f"{video_id}.json")
            with open(scene_path, 'w') as f:
                json.dump(scenes, f)

            print(f"Saved {len(scenes)} scenes for video {video_id}")

            # Generate scene images and object detections, but leave OCR opt-in.
            await self.process_scene_images(video_id)
                
        except Exception as e:
            print(f"Error in background scene detection: {str(e)}")
            error_path = SCENES_DIR / f"{video_id}_error.txt"
            error_path.write_text(str(e), encoding="utf-8")

    @staticmethod
    def _read_diagnostics(path):
        try:
            return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        except (OSError, json.JSONDecodeError):
            return None

    def detect_scenes(self, video_path: str, mode: str, options: dict) -> list:
        """Detect slide changes using the selected configured detector.

        This runs synchronously (called via asyncio.to_thread) because it is
        CPU-bound OpenCV/PySceneDetect work with no I/O to await.
        """
        video_id = os.path.splitext(os.path.basename(video_path))[0]
        duration_seconds = self._video_duration(video_path)
        chapter_timestamps = (
            self._load_chapter_timestamps(video_id)
            if options["include_chapter_boundaries"] or mode == "chapters"
            else []
        )
        chapter_timestamps = [
            timestamp for timestamp in chapter_timestamps
            if timestamp <= duration_seconds
        ]
        if mode == "chapters":
            if not chapter_timestamps:
                raise ValueError(
                    "No transcript chapters are available. Generate chapters first or choose a visual detection method."
                )
            timestamps = self._merge_boundaries(
                [], chapter_timestamps, options["minimum_slide_duration"]
            )
            diagnostics = {
                "mode": "chapters",
                "chapter_boundaries_added": len(chapter_timestamps),
                "detected_changes": len(timestamps),
            }
        elif mode == "content":
            timestamps, diagnostics = self.detect_content_scenes(
                video_path, options, chapter_timestamps
            )
        else:
            timestamps, diagnostics = self.detect_adaptive_scenes(
                video_path, options, chapter_timestamps
            )

        diagnostics_path = SCENES_DIR / f"{video_id}_diagnostics.json"
        diagnostics_path.write_text(json.dumps(diagnostics), encoding="utf-8")
        return self.build_scene_changes(
            video_path, timestamps, options["screenshot_height"]
        )

    @staticmethod
    def _release_video_stream(video) -> None:
        """Release a PySceneDetect stream; VideoStreamCv2 has no close() method."""
        close = getattr(video, "close", None)
        if callable(close):
            close()
            return
        capture = getattr(video, "capture", None) or getattr(video, "_cap", None)
        release = getattr(capture, "release", None)
        if callable(release):
            release()

    def detect_content_scenes(self, video_path: str, options: dict, chapter_timestamps: list) -> tuple:
        """Detect hard cuts using PySceneDetect's content detector."""
        video = open_video(video_path)
        scene_manager = SceneManager()
        fps = float(video.frame_rate)
        scene_manager.add_detector(ContentDetector(
            threshold=options["content_threshold"],
            min_scene_len=max(1, round(fps * options["minimum_slide_duration"]))
        ))
        try:
            scene_manager.detect_scenes(video=video, show_progress=True, frame_skip=2)
            detected_scenes = scene_manager.get_scene_list()
        finally:
            self._release_video_stream(video)
        timestamps = [0.0, *[scene[0].get_seconds() for scene in detected_scenes]]
        duration_seconds = self._video_duration(video_path)
        raw_boundaries = len(self._merge_boundaries(
            timestamps, chapter_timestamps, options["minimum_slide_duration"]
        ))
        priorities = None
        duplicates_removed = 0
        if options["merge_similar_slides"]:
            before_merge = len(set(timestamps))
            timestamps, priorities = self._remove_similar_timestamps(
                video_path, timestamps, DUPLICATE_CHANGE_FRACTION
            )
            duplicates_removed = max(0, before_merge - len(timestamps))
        timestamps = self._merge_boundaries(
            timestamps, chapter_timestamps, options["minimum_slide_duration"]
        )
        before_limit = len(timestamps)
        timestamps = self._apply_hourly_cap(
            timestamps,
            duration_seconds,
            options["maximum_slides_per_hour"],
            preserved=chapter_timestamps,
            priorities=priorities,
        )
        removed_by_limit = before_limit - len(timestamps)
        return timestamps, {
            "mode": "content",
            "content_threshold": options["content_threshold"],
            "duration_seconds": duration_seconds,
            "minimum_slide_duration_seconds": options["minimum_slide_duration"],
            "maximum_slides_per_hour": options["maximum_slides_per_hour"],
            "slide_limit": self._maximum_slide_count(
                duration_seconds, options["maximum_slides_per_hour"]
            ),
            "merge_similar_slides": options["merge_similar_slides"],
            "raw_boundaries": raw_boundaries,
            "near_duplicates_removed": duplicates_removed,
            "removed_by_slide_limit": removed_by_limit,
            "limited_by": ["slide_limit"] if removed_by_limit else [],
            "chapter_boundaries_added": len(chapter_timestamps),
            "detected_changes": len(timestamps),
        }

    def detect_adaptive_scenes(self, video_path: str, options: dict, chapter_timestamps: list) -> tuple:
        """Detect settled slide states in one low-resolution pass."""
        fps, frame_count, duration_seconds, sample_interval, samples = (
            self._sample_video_signatures(video_path)
        )
        if len(samples) < 2:
            return [0.0], {
                "mode": "adaptive",
                "duration_seconds": duration_seconds,
                "sampled_frames": len(samples),
                "detected_changes": 1,
            }
        mask = self._static_area_mask([signature for _, signature in samples])
        timestamps, diagnostics = self._select_adaptive_timestamps(
            samples, duration_seconds, options, chapter_timestamps, mask
        )
        diagnostics["preset_counts"] = {
            detail: len(self._select_adaptive_timestamps(
                samples, duration_seconds, {**options, "adaptive_detail": detail},
                chapter_timestamps, mask,
            )[0])
            for detail in ADAPTIVE_PRESETS
        }
        diagnostics.update({
            "fps": fps,
            "frame_count": frame_count,
            "sampled_frames": len(samples),
            "sample_interval_seconds": sample_interval,
            "static_area_percent": (
                100.0 if mask is None else float(mask.mean() * 100)
            ),
        })
        return timestamps, diagnostics

    @staticmethod
    def _signature(frame):
        gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return cv2.resize(gray, SIGNATURE_SIZE, interpolation=cv2.INTER_AREA)

    def _sample_video_signatures(self, video_path):
        cap = cv2.VideoCapture(video_path)
        try:
            fps = cap.get(cv2.CAP_PROP_FPS)
            frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
            duration_seconds = frame_count / fps if fps > 0 else 0
            if fps <= 0 or frame_count <= 0:
                raise ValueError("Could not read video frame rate or frame count")

            sample_interval_seconds = max(1.0, min(2.0, duration_seconds / 3600))
            sample_step = max(1, int(round(fps * sample_interval_seconds)))
            samples = []
            frame_number = 0
            while True:
                if frame_number % sample_step == 0:
                    ret, frame = cap.read()
                    if ret:
                        samples.append((frame_number / fps, self._signature(frame)))
                else:
                    ret = cap.grab()
                if not ret:
                    break
                frame_number += 1
            return fps, frame_count, duration_seconds, sample_step / fps, samples
        finally:
            cap.release()

    @staticmethod
    def _static_area_mask(signatures):
        """Ignore regions that change almost constantly, such as webcam insets."""
        if len(signatures) < 20:
            return None
        change_counts = np.zeros(signatures[0].shape, dtype=np.float32)
        quiet_pairs = 0
        for previous, current in zip(signatures, signatures[1:]):
            changed = cv2.absdiff(previous, current) > PIXEL_CHANGE_LEVEL
            # Slide transitions change many pixels at once; only count the
            # small, local changes that happen between them.
            if changed.mean() < QUIET_PAIR_FRACTION:
                change_counts += changed
                quiet_pairs += 1
        if quiet_pairs < 10:
            return None
        live = (change_counts / quiet_pairs) > LIVE_REGION_ACTIVITY
        if not live.any():
            return None
        live = cv2.dilate(live.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
        static = ~live
        return static if static.mean() >= 0.3 else None

    @staticmethod
    def _change_fraction(first, second, mask=None):
        changed = cv2.absdiff(first, second) > PIXEL_CHANGE_LEVEL
        if mask is None:
            return float(changed.mean())
        return float(changed[mask].mean())

    @staticmethod
    def _is_additive_change(before, after, mask=None):
        """True when new content appears without removing existing content (a build)."""
        before = before.astype(np.int16)
        after = after.astype(np.int16)
        region = np.ones(before.shape, dtype=bool) if mask is None else mask
        background = float(np.median(before[region]))
        ink_before = np.abs(before - background) > INK_LEVEL
        ink_after = np.abs(after - background) > INK_LEVEL
        changed = (np.abs(after - before) > PIXEL_CHANGE_LEVEL) & region
        added = int(np.count_nonzero(changed & ink_after & ~ink_before))
        removed = int(np.count_nonzero(changed & ink_before & ~ink_after))
        return (
            added > 0
            and removed <= BUILD_REMOVED_RATIO * added
            and removed < NOISE_CHANGE_FRACTION * np.count_nonzero(region)
        )

    def _select_adaptive_timestamps(
        self, samples, duration_seconds, options, chapter_timestamps, mask=None
    ):
        """Turn sampled signatures into final slide timestamps for one detail level.

        Each settled visual state is compared with the current slide. Detail
        levels decide which differences start a new slide: every build step
        (More), only large builds (Balanced) or only slide replacements
        (Fewer). Full slide replacements are kept by every level.
        """
        detail = options["adaptive_detail"]
        preset = ADAPTIVE_PRESETS[detail]
        merge_similar = options["merge_similar_slides"]
        minimum_duration = options["minimum_slide_duration"]
        change_threshold = (
            preset["change_threshold"] if merge_similar else DUPLICATE_CHANGE_FRACTION
        )
        keep_builds = preset["keep_builds"] if merge_similar else "all"

        selected = [{"time": 0.0, "strength": 1.0}]
        reference = samples[0][1]
        state = samples[0][1]
        visual_changes = 0
        small_changes_merged = 0
        builds_merged = 0
        merged_by_minimum_duration = 0
        for index in range(1, len(samples)):
            timestamp, signature = samples[index]
            if self._change_fraction(state, signature, mask) < NOISE_CHANGE_FRACTION:
                continue
            if index + 1 < len(samples):
                still_changing = self._change_fraction(
                    signature, samples[index + 1][1], mask
                )
                if still_changing >= NOISE_CHANGE_FRACTION:
                    continue
            state = signature
            visual_changes += 1
            novelty = self._change_fraction(reference, signature, mask)
            if novelty < change_threshold:
                small_changes_merged += 1
                reference = signature
                continue
            if keep_builds != "all" and novelty < MAXIMUM_BUILD_FRACTION and (
                self._is_additive_change(reference, signature, mask)
            ) and (keep_builds == "none" or novelty < preset["large_build"]):
                builds_merged += 1
                reference = signature
                continue
            reference = signature
            previous = selected[-1]
            if timestamp - previous["time"] < minimum_duration:
                merged_by_minimum_duration += 1
                if previous["time"] > 0:
                    previous["time"] = timestamp
                    previous["strength"] = max(previous["strength"], novelty)
                continue
            selected.append({"time": timestamp, "strength": novelty})

        visual_timestamps = [round(item["time"], 3) for item in selected]
        priorities = {
            round(item["time"], 3): item["strength"] for item in selected
        }
        timestamps = self._merge_boundaries(
            visual_timestamps, chapter_timestamps, minimum_duration
        )
        before_limit = len(timestamps)
        timestamps = self._apply_hourly_cap(
            timestamps, duration_seconds, options["maximum_slides_per_hour"],
            preserved=chapter_timestamps, priorities=priorities,
        )
        removed_by_limit = before_limit - len(timestamps)
        limited_by = []
        if merged_by_minimum_duration:
            limited_by.append("minimum_slide_duration")
        if removed_by_limit:
            limited_by.append("slide_limit")
        return timestamps, {
            "mode": "adaptive",
            "strategy": "settled_slide_states",
            "duration_seconds": duration_seconds,
            "detail_level": detail,
            "minimum_slide_duration_seconds": minimum_duration,
            "maximum_slides_per_hour": options["maximum_slides_per_hour"],
            "slide_limit": self._maximum_slide_count(
                duration_seconds, options["maximum_slides_per_hour"]
            ),
            "merge_similar_slides": merge_similar,
            "change_threshold_percent": change_threshold * 100,
            "keep_builds": keep_builds,
            "visual_changes": visual_changes,
            "small_changes_merged": small_changes_merged,
            "builds_merged": builds_merged,
            "merged_by_minimum_duration": merged_by_minimum_duration,
            "removed_by_slide_limit": removed_by_limit,
            "limited_by": limited_by,
            "chapter_boundaries_added": len(chapter_timestamps),
            "detected_changes": len(timestamps),
        }

    @staticmethod
    def _video_duration(video_path):
        capture = cv2.VideoCapture(video_path)
        try:
            fps = capture.get(cv2.CAP_PROP_FPS)
            frame_count = capture.get(cv2.CAP_PROP_FRAME_COUNT)
            return frame_count / fps if fps > 0 else 0
        finally:
            capture.release()

    @staticmethod
    def _maximum_slide_count(duration_seconds, maximum_slides_per_hour):
        if not maximum_slides_per_hour:
            return 0
        proportional_limit = math.ceil(
            duration_seconds / 3600 * maximum_slides_per_hour
        )
        short_video_allowance = min(10, maximum_slides_per_hour)
        return max(short_video_allowance, proportional_limit)

    def _apply_hourly_cap(
        self, timestamps, duration_seconds, maximum_slides_per_hour,
        preserved=None, priorities=None,
    ):
        """Limit the slide count, keeping the start, chapters and strongest changes."""
        maximum_count = self._maximum_slide_count(
            duration_seconds, maximum_slides_per_hour
        )
        timestamps = sorted(set(round(float(timestamp), 3) for timestamp in timestamps))
        if not maximum_count or len(timestamps) <= maximum_count:
            return timestamps

        preserved = preserved or []
        required = {
            timestamp
            for timestamp in timestamps
            if timestamp == 0 or any(abs(timestamp - item) < 0.5 for item in preserved)
        }
        remaining_slots = max(0, maximum_count - len(required))
        optional = [timestamp for timestamp in timestamps if timestamp not in required]
        if remaining_slots and optional:
            if priorities:
                ranked = sorted(
                    optional, key=lambda item: priorities.get(item, 0.0), reverse=True
                )
                required.update(ranked[:remaining_slots])
            else:
                indices = np.linspace(
                    0, len(optional) - 1, min(remaining_slots, len(optional)), dtype=int
                )
                required.update(optional[index] for index in indices)
        return sorted(required)

    @staticmethod
    def _merge_boundaries(visual_timestamps, chapter_timestamps, minimum_duration):
        merged = sorted(set([0.0, *visual_timestamps]))
        merge_window = max(1.0, minimum_duration / 2)
        for timestamp in chapter_timestamps:
            if not any(abs(timestamp - existing) <= merge_window for existing in merged):
                merged.append(timestamp)
        return sorted(merged)

    def _remove_similar_timestamps(self, video_path, timestamps, threshold):
        """Drop cuts whose frame matches the previous kept slide; return change strengths."""
        cap = cv2.VideoCapture(video_path)
        try:
            accepted = []
            strengths = {}
            previous_signature = None
            fps = cap.get(cv2.CAP_PROP_FPS)
            for timestamp in sorted(set(timestamps)):
                cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(timestamp * fps)))
                success, frame = cap.read()
                if not success:
                    continue
                signature = self._signature(frame)
                strength = (
                    1.0 if previous_signature is None
                    else self._change_fraction(signature, previous_signature)
                )
                if strength >= threshold:
                    accepted.append(timestamp)
                    strengths[round(float(timestamp), 3)] = strength
                    previous_signature = signature
            return accepted, strengths
        finally:
            cap.release()

    @staticmethod
    def _load_chapter_timestamps(video_id):
        summary_path = SUMMARIES_DIR / f"{video_id}.json"
        if not summary_path.exists():
            return []
        try:
            chapters = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        timestamps = []
        for chapter in chapters if isinstance(chapters, list) else []:
            value = chapter.get("timestamp") if isinstance(chapter, dict) else None
            if isinstance(value, (int, float)):
                timestamps.append(max(0.0, float(value)))
                continue
            parts = str(value or "").split(":")
            try:
                numbers = [float(part) for part in parts]
            except ValueError:
                continue
            if len(numbers) == 2:
                timestamps.append(max(0.0, numbers[0] * 60 + numbers[1]))
            elif len(numbers) == 3:
                timestamps.append(
                    max(0.0, numbers[0] * 3600 + numbers[1] * 60 + numbers[2])
                )
        return sorted(set(timestamps))

    def build_scene_changes(self, video_path: str, timestamps: list, screenshot_height: int = 720) -> list:
        """Create scene records and preview images for detected timestamps."""
        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        duration_seconds = frame_count / fps if fps > 0 else 0
        video_id = os.path.splitext(os.path.basename(video_path))[0]

        video_thumbnails_dir = str(THUMBNAILS_DIR / video_id)
        video_fullsize_dir = str(FULLSIZE_IMAGES_DIR / video_id)
        os.makedirs(video_thumbnails_dir, exist_ok=True)
        os.makedirs(video_fullsize_dir, exist_ok=True)
            
        scene_changes = []
        for i, timestamp in enumerate(timestamps):
                minutes = int(timestamp // 60)
                seconds = int(timestamp % 60)
                
                # Generate thumbnail and full-size image
                frame_number = int(timestamp * fps)
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
                ret, frame = cap.read()
                
                if ret:
                    # Save full-size image
                    fullsize_path = f"{video_fullsize_dir}/{i}.jpg"
                    height, width = frame.shape[:2]
                    max_fullsize_height = screenshot_height
                    if height > max_fullsize_height:
                        scale_ratio = max_fullsize_height / height
                        new_dimensions = (int(width * scale_ratio), max_fullsize_height)
                        fullsize_frame = cv2.resize(frame, new_dimensions, interpolation=cv2.INTER_AREA)
                    else:
                        fullsize_frame = frame
                    cv2.imwrite(fullsize_path, fullsize_frame)
                    
                    # Save thumbnail
                    thumbnail_path = f"{video_thumbnails_dir}/{i}.jpg"
                    thumbnail_width = min(240, fullsize_frame.shape[1])
                    thumbnail_height = max(
                        1,
                        round(fullsize_frame.shape[0] * thumbnail_width / fullsize_frame.shape[1])
                    )
                    thumbnail_frame = cv2.resize(
                        fullsize_frame,
                        (thumbnail_width, thumbnail_height),
                        interpolation=cv2.INTER_AREA
                    )
                    cv2.imwrite(thumbnail_path, thumbnail_frame)
                    
                    print(f"Generated thumbnail and fullsize image for scene {i}")
                    
                scene_changes.append({
                    "timestamp": f"{minutes:02d}:{seconds:02d}",
                    "time_seconds": timestamp,
                    "duration": (
                        timestamps[i + 1] - timestamp
                        if i + 1 < len(timestamps)
                        else max(0, duration_seconds - timestamp)
                    ),
                    "thumbnail": f"/thumbnails/{video_id}/{i}.jpg",
                    "fullsize": f"/fullsize_images/{video_id}/{i}.jpg"
                })
            
        cap.release()
        return scene_changes

    async def process_scene_images(self, video_id: str):
        """Queue scene images for YOLO processing and wait for completion."""
        from .ocr_processor import OCRProcessor

        scene_path = str(SCENES_DIR / f"{video_id}.json")
        if not os.path.exists(scene_path):
            return

        try:
            with open(scene_path, 'r') as f:
                scenes = json.load(f)

            ocr_processor = OCRProcessor()

            for i, scene in enumerate(scenes):
                if "fullsize" in scene:
                    image_path = str(FULLSIZE_IMAGES_DIR / video_id / f"{i}.jpg")
                    if os.path.exists(image_path):
                        await ocr_processor.queue_yolo_task(video_id, i, image_path, scene_path)
                        print(f"Queued image {image_path} for YOLO processing")

            await ocr_processor.wait_for_completion()
            print(f"Completed YOLO and OCR processing for video {video_id}")

        except Exception as e:
            print(f"Error processing scene images: {str(e)}")