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
            scenes = await asyncio.to_thread(self.detect_scenes, video_path, mode, options)

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
            video.close()
        timestamps = [0.0, *[scene[0].get_seconds() for scene in detected_scenes]]
        duration_seconds = self._video_duration(video_path)
        timestamps = self._merge_boundaries(
            timestamps, chapter_timestamps, options["minimum_slide_duration"]
        )
        before_filter = len(timestamps)
        timestamps = self._apply_hourly_cap(
            timestamps,
            duration_seconds,
            options["maximum_slides_per_hour"],
            preserved=chapter_timestamps,
        )
        if options["merge_similar_slides"]:
            timestamps = self._remove_similar_timestamps(video_path, timestamps, 0.055)
            timestamps = self._merge_boundaries(
                timestamps, chapter_timestamps, options["minimum_slide_duration"]
            )
        timestamps = self._apply_hourly_cap(
            timestamps,
            duration_seconds,
            options["maximum_slides_per_hour"],
            preserved=chapter_timestamps,
        )
        return timestamps, {
            "mode": "content",
            "content_threshold": options["content_threshold"],
            "minimum_slide_duration_seconds": options["minimum_slide_duration"],
            "raw_boundaries": before_filter,
            "chapter_boundaries_added": len(chapter_timestamps),
            "detected_changes": len(timestamps),
        }

    def detect_adaptive_scenes(self, video_path: str, options: dict, chapter_timestamps: list) -> tuple:
        """Detect video-specific visual-change peaks in one low-resolution pass."""
        cap = cv2.VideoCapture(video_path)
        try:
            fps = cap.get(cv2.CAP_PROP_FPS)
            frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
            duration_seconds = frame_count / fps if fps > 0 else 0
            if fps <= 0 or frame_count <= 0:
                raise ValueError("Could not read video frame rate or frame count")

            sample_interval_seconds = max(1.0, min(2.0, duration_seconds / 3600))
            sample_step = max(1, int(round(fps * sample_interval_seconds)))
            previous_frame = None
            samples = []
            frame_number = 0
            while True:
                if frame_number % sample_step == 0:
                    ret, frame = cap.read()
                else:
                    ret = cap.grab()
                    frame = None
                if not ret:
                    break
                if frame is not None:
                    sample = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (320, 180))
                    score = 0.0
                    if previous_frame is not None:
                        difference = cv2.absdiff(previous_frame, sample)
                        score = cv2.countNonZero(
                            cv2.compare(difference, 25, cv2.CMP_GT)
                        ) / difference.size
                    signature = cv2.resize(sample, (64, 36), interpolation=cv2.INTER_AREA)
                    samples.append((frame_number / fps, score, signature))
                    previous_frame = sample
                frame_number += 1

            scores = np.array([item[1] for item in samples[1:]], dtype=np.float32)
            if not scores.size:
                return [0.0], {
                    "mode": "adaptive",
                    "duration_seconds": duration_seconds,
                    "sampled_frames": len(samples),
                    "detected_changes": 1,
                }
            percentiles = {"fewer": 98.0, "balanced": 96.5, "more": 94.0}
            percentile = percentiles[options["adaptive_detail"]]
            adaptive_threshold = max(0.005, float(np.percentile(scores, percentile)))
            candidates = []
            for index in range(1, len(samples) - 1):
                timestamp, score, signature = samples[index]
                if (
                    score >= adaptive_threshold
                    and score >= samples[index - 1][1]
                    and score >= samples[index + 1][1]
                ):
                    candidates.append((timestamp, score, signature))

            candidates.sort(key=lambda item: item[1], reverse=True)
            selected = [(0.0, 1.0, samples[0][2])]
            minimum_duration = options["minimum_slide_duration"]
            max_count = self._maximum_slide_count(
                duration_seconds, options["maximum_slides_per_hour"]
            )
            duplicate_thresholds = {"fewer": 0.075, "balanced": 0.055, "more": 0.035}
            duplicate_threshold = duplicate_thresholds[options["adaptive_detail"]]
            duplicates_removed = 0
            for candidate in candidates:
                if max_count and len(selected) >= max_count:
                    break
                if any(abs(candidate[0] - item[0]) < minimum_duration for item in selected):
                    continue
                if options["merge_similar_slides"] and any(
                    self._signature_difference(candidate[2], item[2]) < duplicate_threshold
                    for item in selected
                ):
                    duplicates_removed += 1
                    continue
                selected.append(candidate)

            timestamps = sorted(item[0] for item in selected)
            timestamps = self._merge_boundaries(
                timestamps, chapter_timestamps, minimum_duration
            )
            timestamps = self._apply_hourly_cap(
                timestamps, duration_seconds, options["maximum_slides_per_hour"],
                preserved=chapter_timestamps,
            )
            return timestamps, {
                "mode": "adaptive",
                "strategy": "adaptive_peaks",
                "fps": fps,
                "frame_count": frame_count,
                "duration_seconds": duration_seconds,
                "sampled_frames": len(samples),
                "sample_interval_seconds": sample_step / fps,
                "minimum_slide_duration_seconds": minimum_duration,
                "detail_level": options["adaptive_detail"],
                "adaptive_percentile": percentile,
                "adaptive_threshold_percent": adaptive_threshold * 100,
                "raw_candidates": len(candidates),
                "near_duplicates_removed": duplicates_removed,
                "chapter_boundaries_added": len(chapter_timestamps),
                "maximum_slides_per_hour": options["maximum_slides_per_hour"],
                "detected_changes": len(timestamps)
            }
        finally:
            cap.release()

    @staticmethod
    def _signature_difference(first, second):
        difference = cv2.absdiff(first, second)
        changed = cv2.compare(difference, 18, cv2.CMP_GT)
        return cv2.countNonZero(changed) / difference.size

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
        return max(1, math.ceil(duration_seconds / 3600 * maximum_slides_per_hour))

    def _apply_hourly_cap(
        self, timestamps, duration_seconds, maximum_slides_per_hour, preserved=None
    ):
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
        cap = cv2.VideoCapture(video_path)
        try:
            accepted = []
            previous_signature = None
            fps = cap.get(cv2.CAP_PROP_FPS)
            for timestamp in sorted(timestamps):
                cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(timestamp * fps)))
                success, frame = cap.read()
                if not success:
                    continue
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                signature = cv2.resize(gray, (64, 36), interpolation=cv2.INTER_AREA)
                if (
                    previous_signature is None
                    or self._signature_difference(signature, previous_signature) >= threshold
                ):
                    accepted.append(timestamp)
                    previous_signature = signature
            return accepted
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