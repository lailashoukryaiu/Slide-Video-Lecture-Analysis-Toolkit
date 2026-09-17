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
                    "complete": True
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

    async def start_scene_detection(self, video_id: str, video_path: str, background_tasks, adaptive_threshold: float = 0.5):
        """Start scene detection in the background."""
        scene_path = SCENES_DIR / f"{video_id}.json"
        error_path = SCENES_DIR / f"{video_id}_error.txt"
        if scene_path.exists():
            scene_path.unlink()
        if error_path.exists():
            error_path.unlink()
        background_tasks.add_task(self.run_scene_detection, video_id, video_path, adaptive_threshold)

    async def run_scene_detection(self, video_id: str, video_path: str, adaptive_threshold: float):
        """Run scene detection and save results."""
        try:
            scenes = await self.detect_scenes(video_path, adaptive_threshold)

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

    async def detect_scenes(self, video_path: str, adaptive_threshold: float = 0.5) -> list:
        """Detect scene changes in the video and return timestamps."""
        try:
            # Detect scenes using content detection
            video = open_video(video_path)

            scene_manager = SceneManager()
            scene_manager.add_detector(
                ContentDetector(
                    threshold=max(1.0, adaptive_threshold * 20),
                    min_scene_len=10,
                )
            )
            scene_manager.detect_scenes(video=video, show_progress=True, frame_skip=2)
            scenes = scene_manager.get_scene_list()
            video.close()

            print(f"Detected {len(scenes)} scenes")
            
            scene_changes = []
            cap = cv2.VideoCapture(video_path)
            fps = cap.get(cv2.CAP_PROP_FPS)
            frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
            duration_seconds = frame_count / fps if fps > 0 else 0
            video_id = os.path.splitext(os.path.basename(video_path))[0]

            video_thumbnails_dir = str(THUMBNAILS_DIR / video_id)
            video_fullsize_dir = str(FULLSIZE_IMAGES_DIR / video_id)
            os.makedirs(video_thumbnails_dir, exist_ok=True)
            os.makedirs(video_fullsize_dir, exist_ok=True)
            
            timestamps = [
                scene[0].get_seconds()
                if hasattr(scene[0], "get_seconds")
                else float(scene[0])
                for scene in scenes
            ]

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
            
        except Exception as e:
            print(f"Error detecting scenes: {str(e)}")
            return []

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