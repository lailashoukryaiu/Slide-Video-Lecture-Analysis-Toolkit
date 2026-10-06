import os
import sys
import json
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from fastapi.responses import JSONResponse
from PIL import Image
import pytesseract
from queue import Queue
import threading
from ultralytics import YOLO
import asyncio

from project_paths import resolve_model_path, FULLSIZE_IMAGES_DIR, SCENES_DIR

TESSERACT_TIMEOUT_SECONDS = 30
TESSERACT_MISSING_MESSAGE = (
    "Tesseract OCR was not found. Install it (conda install -c conda-forge tesseract, "
    "or apt-get install tesseract-ocr on Colab/Linux) or set TESSERACT_CMD to tesseract's full path."
)


def configure_tesseract():
    """Point pytesseract at a Tesseract binary even when the environment is not activated."""
    env_prefix = os.path.abspath(sys.prefix)
    candidates = [
        os.environ.get("TESSERACT_CMD"),
        shutil.which("tesseract"),
        os.path.join(env_prefix, "Library", "bin", "tesseract.exe"),
        os.path.join(env_prefix, "bin", "tesseract"),
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Tesseract-OCR", "tesseract.exe"),
    ]
    for candidate in candidates:
        if not candidate or not os.path.isfile(candidate):
            continue
        command = os.path.abspath(candidate)
        pytesseract.pytesseract.tesseract_cmd = command
        # Conda's Tesseract only finds its language data when the env is activated.
        env_tessdata = os.path.join(env_prefix, "share", "tessdata")
        if (
            not os.environ.get("TESSDATA_PREFIX")
            and command.lower().startswith(env_prefix.lower())
            and os.path.isdir(env_tessdata)
        ):
            os.environ["TESSDATA_PREFIX"] = env_tessdata
        return command
    return None


TESSERACT_CMD = configure_tesseract()


def tesseract_available():
    return TESSERACT_CMD is not None

# Import Surya for OCR
try:
    from surya.recognition import RecognitionPredictor
    from surya.detection import DetectionPredictor
    SURYA_AVAILABLE = True
except ImportError:
    SURYA_AVAILABLE = False
    print("Surya not available. Will use Tesseract for OCR.")

class OCRProcessor:
    # Class-level variables for shared predictors
    surya_recognition_predictor = None
    surya_detection_predictor = None
    yolo_model = None
    
    @classmethod
    def initialize_models(cls):
        global SURYA_AVAILABLE
        """Initialize shared models if not already initialized."""
        model_path = resolve_model_path()
        if cls.yolo_model is None:
            if not model_path.exists():
                print(f"Warning: YOLO model not found at {model_path}. OCR detection will be unavailable until the model is added.")
            else:
                try:
                    cls.yolo_model = YOLO(str(model_path))
                except Exception as e:
                    print(f"Warning: Failed to load YOLOv8 model: {str(e)}")

        if SURYA_AVAILABLE and cls.surya_recognition_predictor is None:
            try:
                cls.surya_recognition_predictor = RecognitionPredictor()
                cls.surya_detection_predictor = DetectionPredictor()
                print("Surya OCR initialized successfully")
            except Exception as e:
                print(f"Failed to initialize Surya OCR: {str(e)}")
                
                SURYA_AVAILABLE = False

    def __init__(self, send_sse_update=None):
        self.ocr_preference = "tesseract"  # Can be "tesseract", "surya", or "both"
        self.surya_confidence_threshold = 0.6
        self.yolo_queue = Queue()
        self.ocr_queue = Queue()
        self.video_ocr_tasks = {}
        self.video_surya_tasks = {}
        self.send_sse_update = send_sse_update
        try:
            self.loop = asyncio.get_event_loop()
        except RuntimeError:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
        
        # Task tracking
        self.yolo_tasks_total = 0
        self.yolo_tasks_completed = 0
        self.ocr_tasks_total = 0
        self.ocr_tasks_completed = 0
        self.cancelled_videos = set()
        self.ocr_status = {}
        self.active_ocr_batches = {}
        self.task_lock = threading.Lock()
        
        # Initialize shared models
        self.__class__.initialize_models()
        
        # Start worker threads
        self.start_workers()

    def start_workers(self):
        """Start the YOLO and OCR worker threads."""
        self.yolo_thread = threading.Thread(target=self.yolo_worker, daemon=True)
        self.yolo_thread.start()
        
        self.ocr_thread = threading.Thread(target=self.ocr_worker, daemon=True)
        self.ocr_thread.start()

    def run_async(self, coro):
        """Run coroutine in the event loop."""
        if self.loop.is_running():
            # Create a new event loop for this thread if the main loop is running
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(coro)
            finally:
                loop.close()
        else:
            return self.loop.run_until_complete(coro)

    async def queue_yolo_task(self, video_id: str, scene_index: int, image_path: str, scene_path: str):
        """Queue an image for YOLO processing."""
        with self.task_lock:
            self.yolo_tasks_total += 1
        self.yolo_queue.put((video_id, scene_index, image_path, scene_path))

    def yolo_worker(self):
        """Background thread to process images with YOLO."""
        while True:
            try:
                item = self.yolo_queue.get()
                if item is None:
                    break
                
                video_id, scene_index, image_path, scene_path = item
                result = self.process_image_with_yolo(image_path)
                
                if result["success"]:
                    self.update_scene_with_yolo_results(scene_path, scene_index, result)
                
            except Exception as e:
                print(f"Error in YOLO worker thread: {str(e)}")
            finally:
                self.yolo_queue.task_done()
                with self.task_lock:
                    self.yolo_tasks_completed += 1

    def process_image_with_yolo(self, image_path: str):
        """Process an image with YOLOv8 and return the results."""
        if self.__class__.yolo_model is None:
            return {"success": False, "error": "YOLOv8 model not loaded"}
        
        try:
            results = self.__class__.yolo_model(image_path)
            detections = []
            
            for result in results:
                boxes = result.boxes
                for box in boxes:
                    x1, y1, x2, y2 = box.xyxy[0].tolist()
                    confidence = box.conf[0].item()
                    class_id = int(box.cls[0].item())
                    class_name = result.names[class_id]
                    
                    detection = {
                        "class": class_name,
                        "confidence": round(confidence, 3),
                        "bbox": [round(x, 2) for x in [x1, y1, x2, y2]]
                    }
                    
                    if class_name.lower() in ["title", "page-text", "other-text", "caption"]:
                        detection["needs_ocr"] = True
                        detection["ocr_class"] = class_name.lower()
                    
                    detections.append(detection)
            
            # Merge overlapping text detections
            detections = self.merge_overlapping_detections(detections)
            
            return {
                "success": True,
                "detections": detections
            }
        except Exception as e:
            print(f"Error processing image with YOLO: {str(e)}")
            return {
                "success": False,
                "error": str(e)
            }

    def merge_overlapping_detections(self, detections):
        """Merge overlapping text detections."""
        i = 0
        while i < len(detections):
            if not detections[i].get("needs_ocr", False):
                i += 1
                continue
            
            j = i + 1
            merged = False
            while j < len(detections):
                if not detections[j].get("needs_ocr", False):
                    j += 1
                    continue
                
                overlap_score, merged_box = self.calculate_iou_for_merge(
                    detections[i]["bbox"],
                    detections[j]["bbox"]
                )
                
                if merged_box is not None:
                    detections[i]["bbox"] = merged_box
                    if detections[j]["confidence"] > detections[i]["confidence"]:
                        detections[i]["confidence"] = detections[j]["confidence"]
                        detections[i]["class"] = detections[j]["class"]
                        detections[i]["ocr_class"] = detections[j]["ocr_class"]
                    detections.pop(j)
                    merged = True
                else:
                    j += 1
            
            if not merged:
                i += 1
        
        return detections

    def calculate_iou_for_merge(self, box1, box2):
        """Calculate IoU and determine if boxes should be merged."""
        x1_1, y1_1, x2_1, y2_1 = box1
        x1_2, y1_2, x2_2, y2_2 = box2
        
        area1 = (x2_1 - x1_1) * (y2_1 - y1_1)
        area2 = (x2_2 - x1_2) * (y2_2 - y1_2)
        
        x1_i = max(x1_1, x1_2)
        y1_i = max(y1_1, y1_2)
        x2_i = min(x2_1, x2_2)
        y2_i = min(y2_1, y2_2)
        
        if x2_i < x1_i or y2_i < y1_i:
            return 0.0, None
        
        area_i = (x2_i - x1_i) * (y2_i - y1_i)
        iou = area_i / (area1 + area2 - area_i)
        
        containment1 = area_i / area1 if area1 > 0 else 0
        containment2 = area_i / area2 if area2 > 0 else 0
        
        should_merge = iou > 0.7 or containment1 > 0.7 or containment2 > 0.7
        
        if should_merge:
            merged_box = [
                min(x1_1, x1_2),
                min(y1_1, y1_2),
                max(x2_1, x2_2),
                max(y2_1, y2_2)
            ]
            return max(iou, containment1, containment2), merged_box
        
        return iou, None

    def update_scene_with_yolo_results(self, scene_path: str, scene_index: int, result: dict):
        """Update scene data with YOLO detection results."""
        try:
            with open(scene_path, 'r') as f:
                scenes = json.load(f)
            
            if scene_index < len(scenes):
                scenes[scene_index]["yolo_detections"] = result
                
                with open(scene_path, 'w') as f:
                    json.dump(scenes, f)
                
        except Exception as e:
            print(f"Error updating scene with YOLO results: {str(e)}")

    def collect_scene_ocr_tasks(self, video_id, scene, scene_index):
        """Return Tesseract tasks for unread text detections and whether the scene has text."""
        image_path = str(FULLSIZE_IMAGES_DIR / video_id / f"{scene_index}.jpg")
        tasks = []
        has_text_detections = False
        detections = scene.get("yolo_detections", {}).get("detections", [])
        for detection_index, detection in enumerate(detections):
            if not detection.get("needs_ocr"):
                continue
            has_text_detections = True
            if "ocr_text" not in detection:
                tasks.append((detection_index, detection["bbox"], image_path))
        return tasks, has_text_detections, image_path

    def is_ocr_running(self, video_id):
        with self.task_lock:
            return self.active_ocr_batches.get(video_id, 0) > 0

    def get_ocr_status(self, video_id):
        with self.task_lock:
            status = self.ocr_status.get(video_id)
            if not status:
                return None
            status = dict(status)
        if status.get("started_at"):
            end = status.get("finished_at") or time.time()
            status["elapsed_seconds"] = round(end - status["started_at"], 1)
        if status.get("updated_at"):
            status["seconds_since_update"] = round(time.time() - status["updated_at"], 1)
        return status

    def publish_ocr_event(self, video_id, event, data, status=None):
        """Record the latest OCR state and push it to connected browsers."""
        now = time.time()
        with self.task_lock:
            current = dict(self.ocr_status.get(video_id) or {})
            if status in ("queued", "running") and current.get("status") not in ("queued", "running", "stopping"):
                current = {"started_at": now}
            current.update({k: v for k, v in data.items() if k not in ("partial_results", "final_results")})
            if status:
                current["status"] = status
            current.setdefault("started_at", now)
            current["updated_at"] = now
            if status in ("complete", "stopped", "error"):
                current["finished_at"] = now
            else:
                current.pop("finished_at", None)
            self.ocr_status[video_id] = current
        payload = dict(data)
        payload["status"] = current.get("status")
        payload["elapsed_seconds"] = round(now - current["started_at"], 1)
        coro = self.send_progress_update(video_id, {"event": event, "data": payload})
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop:
            running_loop.create_task(coro)
        else:
            asyncio.run(coro)

    def ocr_worker(self):
        """Background thread to process OCR tasks."""
        while True:
            item = self.ocr_queue.get()
            if item is None:
                self.ocr_queue.task_done()
                break
            video_id = item[0]
            try:
                if len(item) == 3:  # Tesseract OCR task
                    video_id, scenes_tasks, scene_path = item
                    task_count = sum(len(tasks) for tasks in scenes_tasks.values())
                    if video_id in self.cancelled_videos:
                        self.publish_ocr_event(video_id, "ocr_cancelled", {
                            "type": "tesseract",
                            "message": "OCR stopped before it started."
                        }, status="stopped")
                    else:
                        self.process_tesseract_tasks(video_id, scenes_tasks, scene_path)
                    with self.task_lock:
                        self.ocr_tasks_completed += task_count
                elif len(item) == 4 and item[3] == "surya_batch":  # Surya OCR task
                    video_id, surya_tasks, scene_path = item[:3]
                    task_count = len(surya_tasks)
                    if video_id not in self.cancelled_videos:
                        self.process_surya_tasks(video_id, surya_tasks, scene_path)
                    with self.task_lock:
                        self.ocr_tasks_completed += task_count
            except Exception as e:
                print(f"Error in OCR worker thread: {str(e)}")
            finally:
                with self.task_lock:
                    remaining = self.active_ocr_batches.get(video_id, 0) - 1
                    if remaining > 0:
                        self.active_ocr_batches[video_id] = remaining
                    else:
                        self.active_ocr_batches.pop(video_id, None)
                self.ocr_queue.task_done()

    async def send_progress_update(self, video_id, data):
        """Send progress update via SSE if the function is available."""
        if self.send_sse_update:
            await self.send_sse_update(video_id, data)

    @staticmethod
    def tesseract_worker_count(total_tasks):
        return max(1, min(4, os.cpu_count() or 1, total_tasks))

    @staticmethod
    def write_scenes(scene_path, scenes):
        with open(scene_path, "w") as f:
            json.dump(scenes, f)

    def process_tesseract_tasks(self, video_id, scenes_tasks, scene_path):
        """Read every queued text element with Tesseract, a few at a time."""
        try:
            with open(scene_path, "r") as f:
                scenes = json.load(f)

            jobs = []
            for scene_index, tasks in sorted(scenes_tasks.items(), key=lambda item: int(item[0])):
                scene_index = int(scene_index)
                if scene_index >= len(scenes):
                    continue
                for detection_index, bbox, image_path in tasks:
                    jobs.append((scene_index, detection_index, bbox, image_path))

            total_tasks = len(jobs)
            slide_total = len({job[0] for job in jobs})
            worker_count = self.tesseract_worker_count(total_tasks)
            completed_tasks = 0
            failed_tasks = 0
            first_error = None
            stopped = False

            self.publish_ocr_event(video_id, "ocr_progress", {
                "type": "tesseract",
                "total": total_tasks,
                "completed": 0,
                "failed": 0,
                "percent": 0,
                "slide_total": slide_total,
                "active_slides": sorted({job[0] + 1 for job in jobs})[:worker_count],
                "message": f"Reading {total_tasks} text elements on {slide_total} slides ({worker_count} at a time)"
            }, status="running")

            last_save = time.monotonic()
            unsaved = False
            executor = ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="tesseract")
            futures = {
                executor.submit(self.perform_tesseract_ocr, image_path, bbox): (scene_index, detection_index)
                for scene_index, detection_index, bbox, image_path in jobs
            }
            try:
                for future in as_completed(futures):
                    scene_index, detection_index = futures[future]
                    try:
                        result = future.result()
                    except Exception as error:
                        result = {"success": False, "error": str(error)}

                    detections = scenes[scene_index].get("yolo_detections", {}).get("detections", [])
                    text = ""
                    if detection_index < len(detections):
                        detection = detections[detection_index]
                        if result.get("success"):
                            text = result.get("text", "")
                            detection["ocr_text"] = text
                            detection["ocr_source"] = "tesseract"
                            detection.pop("ocr_error", None)
                        else:
                            detection["ocr_error"] = result.get("error", "Unknown Tesseract error")
                        unsaved = True
                    if not result.get("success"):
                        failed_tasks += 1
                        first_error = first_error or result.get("error")

                    completed_tasks += 1
                    if unsaved and (time.monotonic() - last_save > 2 or completed_tasks == total_tasks):
                        self.write_scenes(scene_path, scenes)
                        last_save = time.monotonic()
                        unsaved = False

                    active_slides = sorted({futures[f][0] + 1 for f in futures if not f.done()})[:worker_count]
                    percent_complete = int((completed_tasks / total_tasks) * 100) if total_tasks else 100
                    if active_slides:
                        step = f"Now reading slide {', '.join(str(n) for n in active_slides)}"
                    else:
                        step = "Finishing up"
                    snippet = f' Last: "{text[:60]}"' if text else ""
                    self.publish_ocr_event(video_id, "ocr_progress", {
                        "type": "tesseract",
                        "total": total_tasks,
                        "completed": completed_tasks,
                        "failed": failed_tasks,
                        "percent": percent_complete,
                        "scene_index": scene_index,
                        "slide_number": scene_index + 1,
                        "slide_total": slide_total,
                        "active_slides": active_slides,
                        "last_error": first_error,
                        "message": f"{step} \u2014 {completed_tasks} of {total_tasks} text elements read.{snippet}",
                        "partial_results": self.extract_ocr_results_for_scene(scenes, scene_index)
                    }, status="running")

                    if video_id in self.cancelled_videos:
                        stopped = True
                        for pending in futures:
                            pending.cancel()
                        break
            finally:
                executor.shutdown(wait=True, cancel_futures=True)

            if unsaved:
                self.write_scenes(scene_path, scenes)

            if stopped:
                self.publish_ocr_event(video_id, "ocr_cancelled", {
                    "type": "tesseract",
                    "total": total_tasks,
                    "completed": completed_tasks,
                    "failed": failed_tasks,
                    "percent": int((completed_tasks / total_tasks) * 100) if total_tasks else 0,
                    "message": f"OCR stopped after {completed_tasks} of {total_tasks} text elements.",
                    "final_results": self.extract_ocr_results(scenes)
                }, status="stopped")
                return

            if total_tasks and failed_tasks == total_tasks:
                self.publish_ocr_event(video_id, "ocr_error", {
                    "type": "tesseract",
                    "total": total_tasks,
                    "completed": completed_tasks,
                    "failed": failed_tasks,
                    "error": f"Tesseract could not read any text element: {first_error}",
                    "message": f"Tesseract could not read any text element: {first_error}"
                }, status="error")
                return

            failed_note = f" ({failed_tasks} could not be read)" if failed_tasks else ""
            self.publish_ocr_event(video_id, "ocr_complete", {
                "type": "tesseract",
                "total": total_tasks,
                "completed": completed_tasks,
                "failed": failed_tasks,
                "percent": 100,
                "last_error": first_error,
                "message": f"Read {total_tasks} text elements on {slide_total} slides{failed_note}.",
                "final_results": self.extract_ocr_results(scenes)
            }, status="complete")

        except Exception as e:
            print(f"Error processing Tesseract OCR tasks: {str(e)}")
            self.publish_ocr_event(video_id, "ocr_error", {
                "type": "tesseract",
                "error": str(e),
                "message": f"OCR failed: {e}"
            }, status="error")

    def perform_tesseract_ocr(self, image_path, bbox):
        """Perform OCR on a specific region of an image."""
        try:
            with Image.open(image_path) as image:
                x1, y1, x2, y2 = bbox
                cropped = image.crop((x1, y1, x2, y2))
                cropped.load()
            text = pytesseract.image_to_string(cropped, timeout=TESSERACT_TIMEOUT_SECONDS)
            text = ' '.join(text.split())

            return {
                "success": True,
                "text": text
            }
        except RuntimeError as e:
            message = str(e)
            if "timeout" in message.lower():
                message = f"Tesseract took longer than {TESSERACT_TIMEOUT_SECONDS}s on this element"
            return {"success": False, "error": message}
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    def process_surya_tasks(self, video_id, surya_tasks, scene_path):
        """Process Surya OCR tasks for a video."""
        if not SURYA_AVAILABLE:
            return

        try:
            with open(scene_path, 'r') as f:
                scenes = json.load(f)

            total_tasks = len(surya_tasks)
            completed_tasks = 0

            self.publish_ocr_event(video_id, "ocr_progress", {
                "type": "surya",
                "total": total_tasks,
                "completed": 0,
                "failed": 0,
                "percent": 0,
                "slide_total": total_tasks,
                "message": f"Starting Surya OCR on {total_tasks} slides"
            }, status="running")

            for scene_index, image_path in surya_tasks:
                if video_id in self.cancelled_videos:
                    self.publish_ocr_event(video_id, "ocr_cancelled", {
                        "type": "surya",
                        "message": f"Surya OCR stopped after {completed_tasks} of {total_tasks} slides.",
                        "final_results": self.extract_ocr_results(scenes)
                    }, status="stopped")
                    return
                self.publish_ocr_event(video_id, "ocr_progress", {
                    "type": "surya",
                    "active_slides": [scene_index + 1],
                    "message": f"Now reading slide {scene_index + 1} with Surya \u2014 {completed_tasks} of {total_tasks} slides done."
                }, status="running")
                result = self.process_image_with_surya(image_path)
                if result["success"] and "results" in result:
                    self.update_scene_with_surya_results(scenes, scene_index, result["results"])
                    self.write_scenes(scene_path, scenes)

                completed_tasks += 1
                percent_complete = int((completed_tasks / total_tasks) * 100)
                self.publish_ocr_event(video_id, "ocr_progress", {
                    "type": "surya",
                    "total": total_tasks,
                    "completed": completed_tasks,
                    "percent": percent_complete,
                    "scene_index": scene_index,
                    "slide_number": scene_index + 1,
                    "message": f"Read slide {scene_index + 1} with Surya \u2014 {completed_tasks} of {total_tasks} slides done.",
                    "partial_results": self.extract_ocr_results_for_scene(scenes, scene_index)
                }, status="running")

            self.publish_ocr_event(video_id, "ocr_complete", {
                "type": "surya",
                "total": total_tasks,
                "completed": completed_tasks,
                "percent": 100,
                "message": f"Surya read {total_tasks} slides.",
                "final_results": self.extract_ocr_results(scenes)
            }, status="complete")

        except Exception as e:
            print(f"Error processing Surya OCR tasks: {str(e)}")
            self.publish_ocr_event(video_id, "ocr_error", {
                "type": "surya",
                "error": str(e),
                "message": f"Surya OCR failed: {e}"
            }, status="error")

    async def stop_ocr(self, video_id: str):
        """Cancel queued and active OCR work for a video."""
        with self.task_lock:
            self.cancelled_videos.add(video_id)
            self.video_ocr_tasks.pop(video_id, None)
            self.video_surya_tasks.pop(video_id, None)
            active = self.active_ocr_batches.get(video_id, 0) > 0
        if active:
            self.publish_ocr_event(video_id, "ocr_stopping", {
                "message": "Stopping OCR after the text elements already being read..."
            }, status="stopping")
        else:
            self.publish_ocr_event(video_id, "ocr_cancelled", {
                "message": "OCR is stopped."
            }, status="stopped")
        return JSONResponse({"success": True, "active": active, "message": "OCR processing stopping" if active else "OCR processing stopped"})

    async def start_ocr(self, video_id: str):
        """Queue OCR work for completed scene detections."""
        scene_path = str(SCENES_DIR / f"{video_id}.json")
        if not os.path.exists(scene_path):
            return JSONResponse({
                "success": False,
                "error": "No detected slides yet. Slide detection is still running or has not been run; "
                         "start OCR when the slides appear.",
            })
        use_tesseract = self.ocr_preference in ("tesseract", "both")
        use_surya = self.ocr_preference in ("surya", "both") and SURYA_AVAILABLE
        if use_tesseract and not tesseract_available():
            self.publish_ocr_event(video_id, "ocr_error", {
                "type": "tesseract", "error": TESSERACT_MISSING_MESSAGE, "message": TESSERACT_MISSING_MESSAGE
            }, status="error")
            return JSONResponse({"success": False, "error": TESSERACT_MISSING_MESSAGE})
        if self.is_ocr_running(video_id):
            return JSONResponse({
                "success": True,
                "already_running": True,
                "message": "OCR is already running",
                "ocr_status": self.get_ocr_status(video_id)
            })
        with open(scene_path, "r", encoding="utf-8") as file:
            scenes = json.load(file)

        tesseract_tasks = {}
        surya_tasks = []
        for scene_index, scene in enumerate(scenes):
            if not scene.get("yolo_detections", {}).get("success"):
                continue
            tasks, has_text, image_path = self.collect_scene_ocr_tasks(video_id, scene, scene_index)
            if use_tesseract and tasks:
                tesseract_tasks[scene_index] = tasks
            if use_surya and has_text and "surya_ocr" not in scene:
                surya_tasks.append((scene_index, image_path))

        total = sum(len(tasks) for tasks in tesseract_tasks.values())
        if not total and not surya_tasks:
            self.publish_ocr_event(video_id, "ocr_complete", {
                "type": "tesseract", "total": 0, "completed": 0, "failed": 0, "percent": 100,
                "message": "All detected text elements have already been read."
            }, status="complete")
            return JSONResponse({"success": True, "queued": 0, "message": "Nothing left to read"})

        with self.task_lock:
            self.cancelled_videos.discard(video_id)
            batches = (1 if total else 0) + (1 if surya_tasks else 0)
            self.active_ocr_batches[video_id] = self.active_ocr_batches.get(video_id, 0) + batches
            self.ocr_tasks_total += total + len(surya_tasks)
        slide_total = len(tesseract_tasks) or len(surya_tasks)
        self.publish_ocr_event(video_id, "ocr_progress", {
            "type": "tesseract" if total else "surya",
            "total": total or len(surya_tasks),
            "completed": 0,
            "failed": 0,
            "percent": 0,
            "slide_total": slide_total,
            "message": f"Queued {total or len(surya_tasks)} {'text elements' if total else 'slides'} on {slide_total} slides..."
        }, status="queued")
        if total:
            self.ocr_queue.put((video_id, tesseract_tasks, scene_path))
        if surya_tasks:
            self.ocr_queue.put((video_id, surya_tasks, scene_path, "surya_batch"))
        return JSONResponse({
            "success": True,
            "queued": total,
            "surya_queued": len(surya_tasks),
            "message": "OCR processing started",
            "ocr_status": self.get_ocr_status(video_id)
        })

    def process_image_with_surya(self, image_path):
        """Process an image with Surya OCR."""
        if not SURYA_AVAILABLE:
            return {"success": False, "error": "Surya OCR not available"}
        
        try:
            image = Image.open(image_path)
            predictions = self.__class__.surya_recognition_predictor([image], [None], self.__class__.surya_detection_predictor)
            
            if not predictions:
                return {"success": True, "results": []}
            
            results = []
            ocr_result = predictions[0]
            
            if hasattr(ocr_result, 'text_lines'):
                for text_line in ocr_result.text_lines:
                    if text_line.confidence < self.surya_confidence_threshold:
                        continue
                    
                    if hasattr(text_line, 'polygon'):
                        x_coords = [point[0] for point in text_line.polygon]
                        y_coords = [point[1] for point in text_line.polygon]
                        bbox = [min(x_coords), min(y_coords), max(x_coords), max(y_coords)]
                    elif hasattr(text_line, 'bbox'):
                        bbox = text_line.bbox
                    else:
                        continue
                    
                    results.append({
                        "text": text_line.text,
                        "confidence": text_line.confidence,
                        "bbox": [float(coord) for coord in bbox],
                        "matched": False
                    })
            
            return {"success": True, "results": results}
            
        except Exception as e:
            return {"success": False, "error": str(e)}

    def update_scene_with_surya_results(self, scenes, scene_index, surya_results):
        """Update scene data with Surya OCR results."""
        if scene_index >= len(scenes):
            return
        
        scene = scenes[scene_index]
        if "yolo_detections" in scene and scene["yolo_detections"].get("success", False):
            detections = scene["yolo_detections"].get("detections", [])
            
            for surya_result in surya_results:
                surya_bbox = surya_result["bbox"]
                matches = []
                
                for i, detection in enumerate(detections):
                    if detection.get("needs_ocr", False):
                        yolo_bbox = detection.get("bbox", [0, 0, 0, 0])
                        iou = self.calculate_iou(surya_bbox, yolo_bbox)
                        
                        if iou > 0.3:
                            matches.append({
                                "index": i,
                                "iou": iou,
                                "class": detection.get("class", ""),
                                "ocr_class": detection.get("ocr_class", "text")
                            })
                
                if matches:
                    matches.sort(key=lambda x: x["iou"], reverse=True)
                    surya_result["matched"] = True
                    surya_result["matches"] = matches
                    
                    for match in matches:
                        detection_index = match["index"]
                        detections[detection_index]["ocr_text"] = surya_result["text"]
                        detections[detection_index]["ocr_source"] = "surya"
                        detections[detection_index]["match_iou"] = match["iou"]
        
        scene["surya_ocr"] = {
            "success": True,
            "results": surya_results
        }

    def calculate_iou(self, box1, box2):
        """Calculate Intersection over Union between two boxes."""
        x1_1, y1_1, x2_1, y2_1 = box1
        x1_2, y1_2, x2_2, y2_2 = box2
        
        x1_i = max(x1_1, x1_2)
        y1_i = max(y1_1, y1_2)
        x2_i = min(x2_1, x2_2)
        y2_i = min(y2_1, y2_2)
        
        if x2_i < x1_i or y2_i < y1_i:
            return 0.0
        
        intersection = (x2_i - x1_i) * (y2_i - y1_i)
        box1_area = (x2_1 - x1_1) * (y2_1 - y1_1)
        box2_area = (x2_2 - x1_2) * (y2_2 - y1_2)
        
        return intersection / (box1_area + box2_area - intersection)

    async def get_ocr_text(self, video_id: str):
        """Get all OCR text from a video's scenes."""
        scene_path = str(SCENES_DIR / f"{video_id}.json")
        if not os.path.exists(scene_path):
            return JSONResponse({
                "success": False,
                "error": "Scene data not found"
            })
        
        try:
            with open(scene_path, 'r') as f:
                scenes = json.load(f)
            
            ocr_results = []
            pending_ocr_count = 0
            failed_ocr_count = 0
            text_element_count = 0
            pending_slides = set()
            detections_complete = bool(scenes) and all(
                "yolo_detections" in scene for scene in scenes
            )
            
            for scene_index, scene in enumerate(scenes):
                added_from_yolo = set()
                
                if "yolo_detections" in scene and scene["yolo_detections"].get("success", False):
                    detections = scene["yolo_detections"].get("detections", [])
                    
                    for detection_index, detection in enumerate(detections):
                        if detection.get("needs_ocr", False):
                            text_element_count += 1
                            if "ocr_text" not in detection:
                                pending_slides.add(scene_index)
                                if detection.get("ocr_error"):
                                    failed_ocr_count += 1
                                else:
                                    pending_ocr_count += 1
                        
                        if "ocr_text" in detection and detection["ocr_text"].strip():
                            if detection.get("ocr_source", "") == "tesseract" or "match_iou" not in detection:
                                ocr_results.append(self.format_ocr_result(scene, scene_index, detection))
                                added_from_yolo.add(detection_index)
                
                if "surya_ocr" in scene and scene["surya_ocr"].get("success", True):
                    surya_results = scene["surya_ocr"].get("results", [])
                    
                    for result in surya_results:
                        if not result.get("matched", False) and result.get("text", "").strip():
                            ocr_results.append(self.format_surya_result(scene, scene_index, result))
            
            return JSONResponse({
                "success": True,
                "ocr_count": len(ocr_results),
                "ocr_results": ocr_results,
                "pending_ocr_count": pending_ocr_count,
                "failed_ocr_count": failed_ocr_count,
                "text_element_count": text_element_count,
                "pending_slide_count": len(pending_slides),
                "ocr_running": self.is_ocr_running(video_id),
                "ocr_status": self.get_ocr_status(video_id),
                "tesseract_available": tesseract_available(),
                "detections_complete": detections_complete,
                "processing_complete": detections_complete
            })
            
        except Exception as e:
            return JSONResponse({
                "success": False,
                "error": str(e)
            })

    def format_ocr_result(self, scene, scene_index, detection):
        """Format OCR result from YOLO detection."""
        return {
            "scene_index": scene_index,
            "timestamp": scene.get("timestamp", "00:00"),
            "time_seconds": scene.get("time_seconds", 0),
            "text": detection["ocr_text"],
            "confidence": detection.get("confidence", 0),
            "bbox": detection.get("bbox", []),
            "ocr_class": detection.get("ocr_class", "text"),
            "ocr_source": detection.get("ocr_source", "tesseract"),
            "matched": True,
            "match_iou": detection.get("match_iou", 1.0)
        }

    def format_surya_result(self, scene, scene_index, result):
        """Format Surya OCR result."""
        return {
            "scene_index": scene_index,
            "timestamp": scene.get("timestamp", "00:00"),
            "time_seconds": scene.get("time_seconds", 0),
            "text": result["text"],
            "confidence": result.get("confidence", 0),
            "bbox": result.get("bbox", []),
            "ocr_class": "unmatched",
            "ocr_source": "surya",
            "matched": False
        }

    async def process_surya_ocr(self, video_id: str):
        """Trigger Surya OCR processing for a video."""
        if not SURYA_AVAILABLE:
            return JSONResponse({
                "success": False,
                "error": "Surya OCR is not available"
            })

        scene_path = str(SCENES_DIR / f"{video_id}.json")
        if not os.path.exists(scene_path):
            return JSONResponse({
                "success": False,
                "error": "Scene data not found"
            })

        try:
            with open(scene_path, 'r') as f:
                scenes = json.load(f)

            surya_tasks = []
            image_dir = FULLSIZE_IMAGES_DIR / video_id
            for scene_index in range(len(scenes)):
                image_path = image_dir / f"{scene_index}.jpg"
                if image_path.is_file():
                    surya_tasks.append((scene_index, str(image_path)))

            if not surya_tasks:
                return JSONResponse({
                    "success": False,
                    "error": (
                        "No scene images are available for Surya OCR. "
                        "Wait for scene processing to finish and try again."
                    ),
                    "queued": 0,
                    "scene_count": len(scenes),
                    "image_directory": str(image_dir)
                })

            with self.task_lock:
                self.cancelled_videos.discard(video_id)
                self.active_ocr_batches[video_id] = self.active_ocr_batches.get(video_id, 0) + 1
                self.ocr_tasks_total += len(surya_tasks)
            self.ocr_queue.put((video_id, surya_tasks, scene_path, "surya_batch"))
            
            return JSONResponse({
                "success": True,
                "message": f"Queued {len(surya_tasks)} images for Surya OCR processing",
                "queued": len(surya_tasks)
            })
            
        except Exception as e:
            return JSONResponse({
                "success": False,
                "error": str(e)
            })

    async def set_preference(self, preference: str):
        """Set the OCR preference."""
        if preference not in ["tesseract", "surya", "both"]:
            return JSONResponse({
                "success": False,
                "error": "Invalid preference"
            })
        
        self.ocr_preference = preference
        return JSONResponse({
            "success": True,
            "message": f"OCR preference set to {preference}"
        })

    def get_preference(self):
        """Get the current OCR preference."""
        return self.ocr_preference

    def extract_ocr_results_for_scene(self, scenes, scene_index):
        """Extract OCR results for a specific scene."""
        if scene_index >= len(scenes):
            return []
        
        scene = scenes[scene_index]
        ocr_results = []
        
        # Get OCR results from YOLO detections
        if "yolo_detections" in scene and scene["yolo_detections"].get("success", False):
            detections = scene["yolo_detections"].get("detections", [])
            
            for detection in detections:
                if "ocr_text" in detection and detection["ocr_text"].strip():
                    ocr_results.append(self.format_ocr_result(scene, scene_index, detection))
        
        # Get Surya OCR results
        if "surya_ocr" in scene and scene["surya_ocr"].get("success", True):
            surya_results = scene["surya_ocr"].get("results", [])
            
            for result in surya_results:
                if not result.get("matched", False) and result.get("text", "").strip():
                    ocr_results.append(self.format_surya_result(scene, scene_index, result))
        
        return ocr_results

    def extract_ocr_results(self, scenes):
        """Extract all OCR results from scenes."""
        ocr_results = []
        
        for scene_index, scene in enumerate(scenes):
            scene_results = self.extract_ocr_results_for_scene(scenes, scene_index)
            ocr_results.extend(scene_results)
        
        return ocr_results

    async def wait_for_completion(self):
        """Wait for all queued YOLO and OCR tasks to complete."""
        while True:
            with self.task_lock:
                yolo_done = self.yolo_tasks_completed >= self.yolo_tasks_total
                ocr_done = self.ocr_tasks_completed >= self.ocr_tasks_total
                
                if yolo_done and ocr_done:
                    print(f"All tasks completed: YOLO {self.yolo_tasks_completed}/{self.yolo_tasks_total}, OCR {self.ocr_tasks_completed}/{self.ocr_tasks_total}")
                    return
            
            await asyncio.sleep(1)
            
    async def get_task_status(self):
        """Get the current status of YOLO and OCR tasks."""
        with self.task_lock:
            return {
                "yolo_total": self.yolo_tasks_total,
                "yolo_completed": self.yolo_tasks_completed,
                "ocr_total": self.ocr_tasks_total,
                "ocr_completed": self.ocr_tasks_completed,
                "all_complete": (self.yolo_tasks_completed >= self.yolo_tasks_total and 
                                self.ocr_tasks_completed >= self.ocr_tasks_total)
            } 