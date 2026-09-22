from fastapi import FastAPI, Request, Header, HTTPException, BackgroundTasks, UploadFile, File, APIRouter
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import os
import json
import hashlib
from typing import Optional, List, Dict, Any, Union
from pathlib import Path
import asyncio
from concurrent.futures import ThreadPoolExecutor
import glob
import subprocess
import tempfile
import zipfile
import traceback
import yt_dlp
from docx import Document
from docx.shared import Inches
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image as PdfImage, PageBreak, Paragraph, SimpleDocTemplate, Spacer
from youtube_transcript_api import YouTubeTranscriptApi
import re
from PIL import Image as PillowImage, UnidentifiedImageError
from dotenv import load_dotenv

from project_paths import STATIC_DIR, VIDEO_DIR, TRANSCRIPTS_DIR, SCENES_DIR, THUMBNAILS_DIR, FULLSIZE_IMAGES_DIR, SUMMARIES_DIR, EXPORTS_DIR, DETECTIONS_DIR, OCR_RESULTS_DIR, ensure_app_directories

# Import processing modules
from processors.video_processor import VideoProcessor
from processors.scene_processor import SceneProcessor
from processors.ocr_processor import OCRProcessor
from processors.transcript_processor import TranscriptProcessor
from processors.embedding_processor import EmbeddingProcessor
from processors.summary_processor import SummaryProcessor

load_dotenv()

# Initialize FastAPI app
app = FastAPI()

ensure_app_directories()

@app.get("/yolo_status")
async def yolo_status():
    """Return the current availability of the YOLO model."""
    try:
        OCRProcessor.initialize_models()

        loaded = OCRProcessor.yolo_model is not None

        return JSONResponse({
            "loaded": loaded,
            "error": None if loaded else "YOLOv8 model not loaded"
        })

    except Exception as e:
        return JSONResponse({
            "loaded": False,
            "error": str(e)
        })

# Mount static directory
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))

# Initialize processors
video_processor = VideoProcessor()
scene_processor = SceneProcessor()
transcript_processor = TranscriptProcessor()
embedding_processor = EmbeddingProcessor()
summary_processor = SummaryProcessor()

# Create thread pool for background tasks
executor = ThreadPoolExecutor(max_workers=2)

# Dictionary to store SSE clients by video_id
sse_clients = {}

async def send_sse_update(video_id, event_data):
    """Send an SSE update to all clients for a specific video."""
    if video_id in sse_clients:
        data_str = json.dumps(event_data)
        for queue in sse_clients[video_id]:
            try:
                await queue.put(data_str)
            except Exception as e:
                print(f"Error sending SSE update: {str(e)}")

# Initialize OCR processor with SSE update function
ocr_processor = OCRProcessor(send_sse_update=send_sse_update)

# Routes

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse(
    request=request,
    name="index.html"
)

@app.post("/upload_video")
async def upload_video(background_tasks: BackgroundTasks, video: UploadFile = File(...)):
    try:
        # Calculate video hash
        content = await video.read()
        video_hash = hashlib.sha256(content).hexdigest()
        video_path = str(VIDEO_DIR / f"{video_hash}.mp4")
        metadata_path = VIDEO_DIR / "metadata.json"
        metadata = {}
        if metadata_path.exists():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                metadata = {}
        metadata[video_hash] = {"filename": video.filename or f"{video_hash}.mp4"}
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

        # Check if video exists
        
        if os.path.exists(video_path):
            print("Video exists: ", os.path.exists(video_path))
            return await video_processor.handle_existing_video(video_hash, video_path, background_tasks)

        # Save new video and start processing
        with open(video_path, "wb") as buffer:
            buffer.write(content)

        return await video_processor.process_new_video(video_hash, video_path, background_tasks)

    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})

@app.get("/uploaded_videos")
async def uploaded_videos():
    metadata_path = VIDEO_DIR / "metadata.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
    except (OSError, json.JSONDecodeError):
        metadata = {}
    videos = []
    for video_path in sorted(VIDEO_DIR.glob("*"), key=lambda path: path.stat().st_mtime, reverse=True):
        if not video_path.is_file() or video_path.name == "metadata.json":
            continue
        videos.append({
            "video_id": video_path.stem,
            "filename": metadata.get(video_path.stem, {}).get("filename", video_path.name),
            "size_bytes": video_path.stat().st_size,
            "modified_at": video_path.stat().st_mtime
        })
    return {"success": True, "videos": videos}

@app.get("/uploaded_videos/{video_id}")
async def load_uploaded_video(video_id: str, background_tasks: BackgroundTasks):
    video_path = video_processor.get_video_path(video_id)
    if not video_path:
        raise HTTPException(status_code=404, detail="Uploaded video not found")
    return await video_processor.handle_existing_video(video_id, video_path, background_tasks)

@app.get("/video/{video_id}")
async def stream_video(video_id: str, range: Optional[str] = Header(None)):
    try:
        video_path = video_processor.get_video_path(video_id)
        if not video_path:
            raise HTTPException(status_code=404, detail="Video not found")
        return await video_processor.stream_video(video_path, range)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/scenes/{video_id}")
async def get_scenes(video_id: str):
    return await scene_processor.get_scenes(video_id)

@app.post("/detect_scenes/{video_id}")
async def detect_scenes(video_id: str, request: Request, background_tasks: BackgroundTasks):
    video_path = video_processor.get_video_path(video_id)
    if not video_path:
        raise HTTPException(status_code=404, detail="Video not found")
    data = await request.json()
    threshold = data.get("adaptive_threshold", 0.5)
    mode = data.get("mode", "frame_difference")
    try:
        threshold = float(threshold)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid scene detection threshold")
    if not 0.1 <= threshold <= 3:
        raise HTTPException(status_code=400, detail="Scene detection threshold must be between 0.1 and 3")
    if mode not in {"content", "frame_difference"}:
        raise HTTPException(status_code=400, detail="Invalid scene detection mode")
    print(f"Starting scene detection for {video_id}: mode={mode}, threshold={threshold}")
    await scene_processor.start_scene_detection(video_id, video_path, background_tasks, threshold, mode)
    return JSONResponse({"success": True, "message": "Scene detection started"})

@app.get("/scene_detections/{video_id}/{scene_index}")
async def get_scene_detections(video_id: str, scene_index: int):
    return await scene_processor.get_scene_detections(video_id, scene_index)

@app.get("/thumbnails/{video_id}/{filename}")
async def get_thumbnail(video_id: str, filename: str):
    base_dir = THUMBNAILS_DIR.resolve()
    thumbnail_path = (base_dir / video_id / filename).resolve()
    if not thumbnail_path.is_relative_to(base_dir) or not thumbnail_path.is_file():
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    return FileResponse(thumbnail_path)

@app.get("/fullsize_images/{video_id}/{filename}")
async def get_fullsize_image(video_id: str, filename: str):
    base_dir = FULLSIZE_IMAGES_DIR.resolve()
    image_path = (base_dir / video_id / filename).resolve()
    if not image_path.is_relative_to(base_dir) or not image_path.is_file():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(image_path)

@app.get("/download_scene_screenshots/{video_id}")
async def download_scene_screenshots(video_id: str):
    scene_path = SCENES_DIR / f"{video_id}.json"
    if not scene_path.is_file():
        raise HTTPException(status_code=404, detail="Run slide detection before downloading screenshots")

    try:
        scenes = json.loads(scene_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=500, detail=f"Could not read detected slides: {error}")
    if not isinstance(scenes, list) or not scenes:
        raise HTTPException(status_code=400, detail="No detected slide screenshots are available")

    image_dir = FULLSIZE_IMAGES_DIR / video_id
    with tempfile.TemporaryDirectory(dir=EXPORTS_DIR) as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        manifest = []
        image_count = 0
        for index, scene in enumerate(scenes):
            source = image_dir / f"{index}.jpg"
            if not source.is_file():
                continue
            filename = f"slide_{index + 1:03d}_{scene.get('timestamp', 'unknown').replace(':', '-')}.jpg"
            (temp_dir / filename).write_bytes(source.read_bytes())
            manifest.append({
                "slide": index + 1,
                "timestamp": scene.get("timestamp"),
                "time_seconds": scene.get("time_seconds"),
                "filename": filename
            })
            image_count += 1

        if not image_count:
            raise HTTPException(status_code=400, detail="Detected slides have no generated screenshots")

        (temp_dir / "screenshots.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        zip_path = EXPORTS_DIR / f"{video_id}_slide_screenshots.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for file_path in temp_dir.iterdir():
                archive.write(file_path, file_path.name)

    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=f"{video_id}_slide_screenshots.zip"
    )

@app.get("/ocr_text/{video_id}")
async def get_ocr_text(video_id: str):
    return await ocr_processor.get_ocr_text(video_id)

@app.post("/stop_ocr/{video_id}")
async def stop_ocr(video_id: str):
    return await ocr_processor.stop_ocr(video_id)

@app.post("/start_ocr/{video_id}")
async def start_ocr(video_id: str):
    return await ocr_processor.start_ocr(video_id)

@app.post("/process_surya_ocr/{video_id}")
async def process_surya_ocr(video_id: str):
    return await ocr_processor.process_surya_ocr(video_id)

@app.post("/set_ocr_preference")
async def set_ocr_preference(request: Request):
    data = await request.json()
    return await ocr_processor.set_preference(data.get("preference", "tesseract"))

@app.get("/get_ocr_preference")
def get_ocr_preference():
    return {"preference": ocr_processor.get_preference()}

@app.get("/get_transcript/{video_id}/{source}")
async def get_transcript(video_id: str, source: str):
    return await transcript_processor.get_transcript(video_id, source)

@app.post("/generate_whisper_transcript/{video_id}")
async def generate_whisper_transcript(video_id: str, background_tasks: BackgroundTasks):
    return await transcript_processor.generate_whisper_transcript(video_id, background_tasks)

@app.get("/whisper_transcript_status/{video_id}")
async def whisper_transcript_status(video_id: str):
    return await transcript_processor.get_whisper_status(video_id)

@app.post("/compute_embeddings/{video_id}")
async def compute_embeddings_endpoint(video_id: str, background_tasks: BackgroundTasks):
    return await embedding_processor.compute_embeddings(video_id, background_tasks)

@app.get("/embeddings_status/{video_id}")
async def embeddings_status(video_id: str):
    return await embedding_processor.get_status(video_id)

@app.get("/get_transcript_ocr_relationships/{video_id}")
async def get_transcript_ocr_relationships(video_id: str):
    return await embedding_processor.get_relationships(video_id)

@app.get("/find_ocr_for_transcript/{video_id}/{transcript_index}")
async def get_ocr_for_transcript(video_id: str, transcript_index: int):
    return await embedding_processor.find_ocr_for_transcript(video_id, transcript_index)

@app.get("/find_transcript_for_ocr/{video_id}/{scene_index}")
async def get_transcript_for_ocr(video_id: str, scene_index: int, ocr_text: str):
    return await embedding_processor.find_transcript_for_ocr(video_id, scene_index, ocr_text)

@app.get("/find_scene_for_transcript/{video_id}/{transcript_index}")
async def get_scene_for_transcript(video_id: str, transcript_index: int):
    return await embedding_processor.find_scene_for_transcript(video_id, transcript_index)

# SSE routes and handlers
@app.get("/ocr_progress/{video_id}")
async def ocr_progress(video_id: str):
    async def event_generator():
        if video_id not in sse_clients:
            sse_clients[video_id] = []
        
        queue = asyncio.Queue()
        sse_clients[video_id].append(queue)
        
        try:
            await queue.put(json.dumps({
                "event": "connected",
                "data": {"message": "SSE connection established"}
            }))
            
            while True:
                data = await queue.get()
                yield f"data: {data}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            if video_id in sse_clients and queue in sse_clients[video_id]:
                sse_clients[video_id].remove(queue)
                if not sse_clients[video_id]:
                    del sse_clients[video_id]
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )

def extract_video_id(url):
    """Extract video ID from YouTube URL."""
    pattern = r'(?:v=|\/)([0-9A-Za-z_-]{11}).*'
    match = re.search(pattern, url)
    return match.group(1) if match else None

@app.get("/download/{video_id}")
@app.post("/download/{video_id}")
async def download_video(video_id: str, background_tasks: BackgroundTasks):
    try:
        video_path = str(VIDEO_DIR / f"{video_id}.mp4")
        if not glob.glob(str(VIDEO_DIR / f"{video_id}.*")):
            ydl_opts = {
                'format': 'bestvideo[height<=720][vcodec^=vp9]+bestaudio/bestvideo[height<=720]+bestaudio/best',
                'outtmpl': str(VIDEO_DIR / f'{video_id}.%(ext)s'),
                'merge_output_format': 'mp4',
            }
            cookies_file = os.getenv("YTDLP_COOKIES_FILE")
            if cookies_file:
                cookies_path = Path(cookies_file).expanduser().resolve()
                if not cookies_path.is_file():
                    raise RuntimeError(
                        f"YTDLP_COOKIES_FILE does not exist: {cookies_path}"
                    )
                ydl_opts["cookiefile"] = str(cookies_path)
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    ydl.download([f'https://www.youtube.com/watch?v={video_id}'])
                except yt_dlp.utils.DownloadError as error:
                    error_text = str(error)
                    if "Sign in to confirm you're not a bot" in error_text:
                        raise RuntimeError(
                            "YouTube requires authentication for this download. "
                            "Export YouTube cookies in Netscape format, upload the "
                            "cookie file to Colab, and set YTDLP_COOKIES_FILE to its path."
                        ) from error
                    raise

        downloaded_files = os.listdir(VIDEO_DIR)
        for file in downloaded_files:
            if file.startswith(video_id):
                video_path = str(VIDEO_DIR / file)
                break

        whisper_transcript_path = str(TRANSCRIPTS_DIR / f"{video_id}_whisper.json")
        has_whisper_transcript = os.path.exists(whisper_transcript_path)

        scenes_path = str(SCENES_DIR / f"{video_id}.json")
        has_scenes = False
        if os.path.exists(scenes_path):
            try:
                with open(scenes_path, 'r', encoding='utf-8') as file:
                    has_scenes = bool(json.load(file))
            except (OSError, json.JSONDecodeError):
                has_scenes = False
        
        # Load existing scenes if available
        existing_scenes = []
        if has_scenes:
            try:
                with open(scenes_path, 'r') as f:
                    existing_scenes = json.load(f)
            except Exception as e:
                print(f"Error loading existing scenes: {e}")
        
        # Check if transcript is being generated
        progress_file = str(TRANSCRIPTS_DIR / f"{video_id}_whisper_progress.txt")
        transcript_in_progress = os.path.exists(progress_file)
        
        # Load existing transcript if available
        transcript_to_use = None
        if has_whisper_transcript:
            try:
                with open(whisper_transcript_path, 'r') as f:
                    transcript_to_use = json.load(f)
            except Exception as e:
                print(f"Error loading existing transcript: {e}")
        
        # Try to get YouTube transcript if Whisper transcript is not available
        has_youtube_transcript = False
        if not transcript_to_use and not transcript_in_progress:
            try:
                youtube_transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=['en'])
                if youtube_transcript:
                    transcript_to_use = youtube_transcript
                    has_youtube_transcript = True
                    with open(str(TRANSCRIPTS_DIR / f"{video_id}_youtube.json"), 'w') as f:
                        json.dump(youtube_transcript, f)
            except Exception as e:
                print(f"Error getting YouTube transcript: {e}")
        
        # Start missing processing tasks
        if not has_whisper_transcript and not transcript_in_progress:
            await transcript_processor.start_whisper_generation(video_id, video_path, background_tasks)
            transcript_in_progress = True
        
        return JSONResponse({
            "success": True,
            "video_url": f"/video/{video_id}",
            "video_id": video_id,
            "transcript": transcript_to_use,
            "has_youtube_transcript": has_youtube_transcript,
            "has_whisper_transcript": has_whisper_transcript,
            "transcript_in_progress": transcript_in_progress,
            "scenes": existing_scenes,
            "is_duplicate": os.path.exists(video_path)
        })
            
    except Exception as e:
        return JSONResponse({
            "success": False,
            "error": str(e)
        })

@app.post("/process_youtube")
async def process_youtube(request: Request, background_tasks: BackgroundTasks):
    try:
        data = await request.json()
        url = data.get("url")
        if not url:
            return JSONResponse({
                "success": False,
                "error": "No URL provided"
            })
        
        video_id = extract_video_id(url)
        if not video_id:
            return JSONResponse({
                "success": False,
                "error": "Invalid YouTube URL"
            })
        
        return await download_video(video_id, background_tasks)
        
    except Exception as e:
        return JSONResponse({
            "success": False,
            "error": str(e)
        })

@app.post("/generate_summary")
async def generate_summary(request: Request):
    try:
        data = await request.json()
        transcript = data.get("transcript", [])
        video_id = data.get("video_id")
        return await summary_processor.generate_summary(transcript, video_id)
    except Exception as e:
        return JSONResponse({
            "success": False,
            "error": str(e)
        })

@app.get("/summary/{video_id}")
async def get_summary(video_id: str):
    return await summary_processor.get_summary(video_id)

def parse_chapter_timestamp(timestamp: str) -> float:
    parts = [float(part) for part in timestamp.split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    raise ValueError(f"Invalid chapter timestamp: {timestamp}")

def format_chapter_timestamp(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

def create_combined_chapter_documents(
    chapters, chapter_files, pdf_path=None, docx_path=None,
    pdf_text=True, pdf_images=True, word_text=True, word_images=True
):
    """Create title, screenshot, and transcript documents for all chapters."""
    styles = getSampleStyleSheet()
    pdf_story = [Paragraph("Lecture Chapters", styles["Title"])] if pdf_path else None
    word_document = Document() if docx_path else None
    if word_document:
        word_document.add_heading("Lecture Chapters", level=0)

    for index, chapter in enumerate(chapters):
        files = chapter_files[index]
        title = str(chapter["title"])
        timestamp = format_chapter_timestamp(chapter["start"])
        transcript = files["transcript"].read_text(encoding="utf-8").strip()
        heading = f"Chapter {index + 1}: {title}"
        image_path = files["image"]
        try:
            with PillowImage.open(image_path) as image:
                image.verify()
        except (FileNotFoundError, UnidentifiedImageError, OSError):
            image_path = None

        if pdf_story is not None:
            pdf_story.extend([
            Paragraph(heading, styles["Heading1"]),
            Paragraph(f"Starts at {timestamp}", styles["Normal"]),
            Spacer(1, 0.15 * inch),
            ])
        if pdf_story is not None and pdf_images and image_path is not None:
            pdf_story.extend([
                PdfImage(str(image_path), width=6.5 * inch, height=3.65 * inch, kind="proportional"),
                Spacer(1, 0.15 * inch),
            ])
        if pdf_story is not None and pdf_text:
            pdf_story.append(Paragraph(
            (transcript or "No transcript available for this chapter.")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace("\n", "<br/>"),
            styles["BodyText"],
            ))
        if pdf_story is not None and index < len(chapters) - 1:
            pdf_story.append(PageBreak())

        if word_document:
            word_document.add_heading(heading, level=1)
            word_document.add_paragraph(f"Starts at {timestamp}")
        if word_document and word_images and image_path is not None:
            word_document.add_picture(str(image_path), width=Inches(6.5))
        if word_document and word_text:
            word_document.add_heading("Transcript", level=2)
            word_document.add_paragraph(transcript or "No transcript available for this chapter.")
        if word_document and index < len(chapters) - 1:
            word_document.add_page_break()

    footnote = "Transcription model: faster-whisper turbo."
    if pdf_story is not None:
        pdf_story.extend([Spacer(1, 0.3 * inch), Paragraph(footnote, styles["Italic"])])
        SimpleDocTemplate(str(pdf_path), pagesize=letter, rightMargin=0.6 * inch,
                      leftMargin=0.6 * inch, topMargin=0.6 * inch,
                      bottomMargin=0.6 * inch).build(pdf_story)
    if word_document:
        word_document.add_paragraph(footnote, style="Caption")
        word_document.save(str(docx_path))

@app.post("/export_chapters/{video_id}")
async def export_chapters(video_id: str, request: Request):
    """Create a ZIP containing chapter clips, screenshots, and transcripts."""
    options = await request.json()
    interval_minutes = options.get("interval_minutes")
    export_flags = {name: bool(options.get(name, True)) for name in (
        "include_images", "include_transcripts", "include_clips",
        "include_word_text", "include_word_images", "include_pdf_text",
        "include_pdf_images",
    )}
    if not any(export_flags.values()):
        raise HTTPException(status_code=400, detail="Select at least one export option")
    video_path = video_processor.get_video_path(video_id)
    summary_path = SUMMARIES_DIR / f"{video_id}.json"
    if not video_path:
        raise HTTPException(status_code=404, detail="Video not found")
    if interval_minutes is None and not summary_path.is_file():
        raise HTTPException(status_code=400, detail="Generate a chapter summary first")

    try:
        if interval_minutes is not None:
            try:
                interval_seconds = float(interval_minutes) * 60
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="Interval duration must be a number of minutes")
            if interval_seconds <= 0:
                raise HTTPException(status_code=400, detail="Interval duration must be greater than zero")
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
                check=True, capture_output=True, text=True
            )
            duration = float(probe.stdout.strip())
            chapters = [
                {"timestamp": format_chapter_timestamp(start), "title": f"Interval {index + 1}"}
                for index, start in enumerate(
                    range(0, max(1, int(duration)), max(1, int(interval_seconds)))
                )
            ]
        else:
            with summary_path.open("r", encoding="utf-8") as file:
                chapters = json.load(file)
        if not isinstance(chapters, list) or not chapters:
            raise HTTPException(status_code=400, detail="No chapters available")

        transcript = []
        for transcript_path in [
            TRANSCRIPTS_DIR / f"{video_id}_whisper.json",
            TRANSCRIPTS_DIR / f"{video_id}_youtube.json",
            TRANSCRIPTS_DIR / f"{video_id}.json",
        ]:
            if transcript_path.is_file():
                with transcript_path.open("r", encoding="utf-8") as file:
                    transcript = json.load(file)
                break
        if not transcript:
            raise HTTPException(status_code=400, detail="No transcript available")

        chapter_data = []
        for index, chapter in enumerate(chapters):
            if not isinstance(chapter, dict) or "timestamp" not in chapter:
                continue
            start = parse_chapter_timestamp(str(chapter["timestamp"]))
            end = (
                parse_chapter_timestamp(str(chapters[index + 1]["timestamp"]))
                if index + 1 < len(chapters)
                else None
            )
            chapter_data.append({
                "index": len(chapter_data) + 1,
                "title": str(chapter.get("title", f"Chapter {index + 1}")),
                "start": start,
                "end": end,
            })
        if not chapter_data:
            raise HTTPException(status_code=400, detail="Chapter timestamps are invalid")

        export_dir = EXPORTS_DIR / video_id
        export_dir.mkdir(parents=True, exist_ok=True)
        zip_path = EXPORTS_DIR / f"{video_id}_chapters.zip"

        with tempfile.TemporaryDirectory(dir=EXPORTS_DIR) as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            chaptered_lines = [f"# Chapters for {video_id}", ""]
            chapter_files = []
            for chapter in chapter_data:
                number = chapter["index"]
                safe_title = re.sub(r"[^A-Za-z0-9_-]+", "_", chapter["title"]).strip("_") or f"chapter_{number}"
                base_name = f"{number:02d}_{safe_title}"
                clip_path = temp_dir / f"{base_name}.mp4"
                image_path = temp_dir / f"{base_name}.jpg"
                transcript_path = temp_dir / f"{base_name}.txt"

                ffmpeg_clip = [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", str(chapter["start"]),
                    "-i", str(video_path),
                ]
                if chapter["end"] is not None:
                    ffmpeg_clip.extend(["-t", str(chapter["end"] - chapter["start"])])
                ffmpeg_clip.extend(["-c:v", "libx264", "-c:a", "aac", str(clip_path)])
                if export_flags["include_clips"]:
                    subprocess.run(ffmpeg_clip, check=True)
                if export_flags["include_images"] or export_flags["include_word_images"] or export_flags["include_pdf_images"]:
                    subprocess.run([
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", str(chapter["start"]), "-i", str(video_path),
                    "-frames:v", "1", str(image_path)
                    ], check=True)

                chapter_transcript = [
                    item for item in transcript
                    if chapter["start"] <= float(item.get("start", 0))
                    and (chapter["end"] is None or float(item.get("start", 0)) < chapter["end"])
                ]
                transcript_text = "\n".join(
                    f"[{format_chapter_timestamp(float(item.get('start', 0)))}] {item.get('text', '').strip()}"
                    for item in chapter_transcript
                )
                if export_flags["include_transcripts"] or export_flags["include_word_text"] or export_flags["include_pdf_text"]:
                    transcript_path.write_text(transcript_text + "\n", encoding="utf-8")
                chapter_files.append({"image": image_path, "transcript": transcript_path})
                chaptered_lines.extend([
                    f"## Chapter {number}: {chapter['title']}",
                    f"Start: {format_chapter_timestamp(chapter['start'])}",
                    "",
                    transcript_text,
                    "",
                ])

            if export_flags["include_transcripts"]:
                (temp_dir / "transcript_by_chapter.md").write_text("\n".join(chaptered_lines), encoding="utf-8")
            (temp_dir / "chapters.json").write_text(json.dumps(chapter_data, indent=2), encoding="utf-8")
            create_combined_chapter_documents(
                chapter_data, chapter_files,
                temp_dir / "chapter_document.pdf" if export_flags["include_pdf_text"] or export_flags["include_pdf_images"] else None,
                temp_dir / "chapter_document.docx" if export_flags["include_word_text"] or export_flags["include_word_images"] else None,
                pdf_text=export_flags["include_pdf_text"], pdf_images=export_flags["include_pdf_images"],
                word_text=export_flags["include_word_text"], word_images=export_flags["include_word_images"],
            )
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
                for file_path in temp_dir.iterdir():
                    if file_path.suffix == ".mp4" and not export_flags["include_clips"]:
                        continue
                    if file_path.suffix == ".jpg" and not export_flags["include_images"]:
                        continue
                    if file_path.suffix == ".txt" and not export_flags["include_transcripts"]:
                        continue
                    archive.write(file_path, file_path.name)

        return FileResponse(
            zip_path,
            media_type="application/zip",
            filename=f"{video_id}_chapters.zip"
        )
    except HTTPException:
        raise
    except (ValueError, OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=500, detail=f"Chapter export failed: {error}")
    except Exception as error:
        error_type = type(error).__name__
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Chapter export failed unexpectedly ({error_type}): {error!r}"
        )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)