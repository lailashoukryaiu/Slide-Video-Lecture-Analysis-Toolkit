import os
import cv2
from scenedetect import open_video, ContentDetector, SceneManager
from fastapi.responses import JSONResponse
import json
from concurrent.futures import ThreadPoolExecutor

from project_paths import SCENES_DIR, THUMBNAILS_DIR, FULLSIZE_IMAGES_DIR

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

    async def start_scene_detection(self, video_id: str, video_path: str, background_tasks, adaptive_threshold: float = 0.5, mode: str = "frame_difference"):
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
        background_tasks.add_task(self.run_scene_detection, video_id, video_path, adaptive_threshold, mode)

    async def run_scene_detection(self, video_id: str, video_path: str, adaptive_threshold: float, mode: str):
        """Run scene detection and save results."""
        try:
            scenes = await self.detect_scenes(video_path, adaptive_threshold, mode)

            scene_path = str(SCENES_DIR / f"{video_id}.json")
            with open(scene_path, 'w') as f:
                json.dump(scenes, f)
                
            print(f"Saved {len(scenes)} scenes for video {video_id}")
            
            # Queue and wait for scene images processing
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

    async def detect_scenes(self, video_path: str, adaptive_threshold: float = 0.5, mode: str = "frame_difference") -> list:
        """Detect slide changes using the selected configured detector."""
        if mode == "content":
            return await self.detect_content_scenes(video_path, adaptive_threshold)
        return await self.detect_frame_difference_scenes(video_path, adaptive_threshold)

    async def detect_content_scenes(self, video_path: str, adaptive_threshold: float) -> list:
        """Detect hard cuts using PySceneDetect's content detector."""
        video = open_video(video_path)
        scene_manager = SceneManager()
        scene_manager.add_detector(ContentDetector(
            threshold=max(1.0, adaptive_threshold * 20),
            min_scene_len=10
        ))
        scene_manager.detect_scenes(video=video, show_progress=True, frame_skip=2)
        detected_scenes = scene_manager.get_scene_list()
        timestamps = [scene[0].get_seconds() for scene in detected_scenes]
        diagnostics_path = SCENES_DIR / f"{os.path.splitext(os.path.basename(video_path))[0]}_diagnostics.json"
        diagnostics_path.write_text(json.dumps({
            "mode": "content",
            "threshold": max(1.0, adaptive_threshold * 20),
            "detected_changes": len(timestamps)
        }), encoding="utf-8")
        return await self.build_scene_changes(video_path, timestamps)

    async def detect_frame_difference_scenes(self, video_path: str, adaptive_threshold: float) -> list:
        """Detect slide changes using sampled frame differences."""
        cap = cv2.VideoCapture(video_path)
        try:
            fps = cap.get(cv2.CAP_PROP_FPS)
            frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
            duration_seconds = frame_count / fps if fps > 0 else 0
            video_id = os.path.splitext(os.path.basename(video_path))[0]
            if fps <= 0 or frame_count <= 0:
                raise ValueError("Could not read video frame rate or frame count")

            # The slider represents the minimum percentage of pixels that must
            # change between samples. Lower values detect smaller slide changes.
            change_threshold = adaptive_threshold / 100
            sample_step = max(1, int(round(fps / 2)))
            min_scene_gap = max(sample_step, int(round(fps * 1.0)))
            previous_frame = None
            stable_frame = None
            timestamps = []
            last_change_frame = -min_scene_gap
            frame_number = 0
            maximum_changed_ratio = 0.0
            sampled_frames = 0
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                if frame_number % sample_step == 0:
                    sample = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (320, 180))
                    sampled_frames += 1
                    if previous_frame is not None:
                        if stable_frame is None:
                            stable_frame = previous_frame
                        previous_difference = cv2.absdiff(previous_frame, sample)
                        stable_difference = cv2.absdiff(stable_frame, sample)
                        previous_mask = cv2.compare(previous_difference, 25, cv2.CMP_GT)
                        stable_mask = cv2.compare(stable_difference, 25, cv2.CMP_GT)
                        previous_ratio = cv2.countNonZero(previous_mask) / previous_difference.size
                        stable_ratio = cv2.countNonZero(stable_mask) / stable_difference.size
                        changed_ratio = max(previous_ratio, stable_ratio)
                        maximum_changed_ratio = max(maximum_changed_ratio, changed_ratio)
                        if (
                            changed_ratio >= change_threshold
                            and frame_number - last_change_frame >= min_scene_gap
                        ):
                            timestamps.append(frame_number / fps)
                            last_change_frame = frame_number
                            stable_frame = sample
                    previous_frame = sample
                frame_number += 1

            print(
                f"Detected {len(timestamps)} slide changes "
                f"(threshold={change_threshold:.4f}, max_changed_ratio={maximum_changed_ratio:.4f})"
            )
            diagnostics_path = SCENES_DIR / f"{video_id}_diagnostics.json"
            diagnostics_path.write_text(json.dumps({
                "mode": "frame_difference",
                "fps": fps,
                "frame_count": frame_count,
                "duration_seconds": duration_seconds,
                "sampled_frames": sampled_frames,
                "sample_interval_seconds": sample_step / fps,
                "threshold_percent": adaptive_threshold,
                "maximum_changed_percent": maximum_changed_ratio * 100,
                "detected_changes": len(timestamps)
            }), encoding="utf-8")
            return await self.build_scene_changes(video_path, timestamps)
        finally:
            cap.release()

    async def build_scene_changes(self, video_path: str, timestamps: list) -> list:
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
                    max_fullsize_height = 1080
                    if height > max_fullsize_height:
                        scale_ratio = max_fullsize_height / height
                        new_dimensions = (int(width * scale_ratio), max_fullsize_height)
                        fullsize_frame = cv2.resize(frame, new_dimensions, interpolation=cv2.INTER_AREA)
                    else:
                        fullsize_frame = frame
                    cv2.imwrite(fullsize_path, fullsize_frame)
                    
                    # Save thumbnail
                    thumbnail_path = f"{video_thumbnails_dir}/{i}.jpg"
                    thumbnail_scale_ratio = 0.25
                    thumbnail_dimensions = (int(width * thumbnail_scale_ratio), int(height * thumbnail_scale_ratio))
                    thumbnail_frame = cv2.resize(frame, thumbnail_dimensions, interpolation=cv2.INTER_AREA)
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