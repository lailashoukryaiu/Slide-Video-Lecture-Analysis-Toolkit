from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
VIDEO_DIR = STATIC_DIR / "videos"
TRANSCRIPTS_DIR = STATIC_DIR / "transcripts"
SCENES_DIR = STATIC_DIR / "scenes"
THUMBNAILS_DIR = STATIC_DIR / "thumbnails"
FULLSIZE_IMAGES_DIR = STATIC_DIR / "fullsize_images"
SUMMARIES_DIR = STATIC_DIR / "summaries"
DETECTIONS_DIR = STATIC_DIR / "detections"
OCR_RESULTS_DIR = STATIC_DIR / "ocr_results"
EXPORTS_DIR = STATIC_DIR / "exports"

MODEL_CANDIDATES = [
    "slide-model.pt",
    "slidecraft_best.pt",
    "slidevqa_best.pt",
]


def ensure_app_directories():
    for directory in [
        STATIC_DIR,
        VIDEO_DIR,
        TRANSCRIPTS_DIR,
        SCENES_DIR,
        THUMBNAILS_DIR,
        FULLSIZE_IMAGES_DIR,
        SUMMARIES_DIR,
        DETECTIONS_DIR,
        OCR_RESULTS_DIR,
        EXPORTS_DIR,
    ]:
        directory.mkdir(parents=True, exist_ok=True)


def resolve_model_path():
    for model_name in MODEL_CANDIDATES:
        candidate = BASE_DIR / model_name
        if candidate.exists():
            return candidate
    return BASE_DIR / MODEL_CANDIDATES[0]


def static_path(*parts):
    return str(STATIC_DIR.joinpath(*parts))


def videos_path(*parts):
    return str(VIDEO_DIR.joinpath(*parts))


def transcripts_path(*parts):
    return str(TRANSCRIPTS_DIR.joinpath(*parts))


def scenes_path(*parts):
    return str(SCENES_DIR.joinpath(*parts))
