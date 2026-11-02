"""Local, reviewable privacy regions and export-only video masking."""
import asyncio
import json
import math
import subprocess
from pathlib import Path

from ffmpeg_tools import media_executable

MAX_REGIONS = 64
EFFECTS = ("blur", "pixelate", "cover")


def privacy_regions(value, duration):
    if not isinstance(value, list) or len(value) > MAX_REGIONS:
        raise ValueError(f"Choose at most {MAX_REGIONS} privacy regions.")
    result = []
    for region in value:
        if not isinstance(region, dict) or region.get("effect") not in EFFECTS:
            raise ValueError("Choose Blur, Pixelate or Solid cover for each privacy region.")
        values = {}
        for key in ("x", "y", "width", "height", "start", "end"):
            number = region.get(key)
            if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
                raise ValueError(f"Invalid privacy region {key}.")
            values[key] = float(number)
        if not (0 <= values["x"] < 1 and 0 <= values["y"] < 1
                and 0 < values["width"] <= 1 - values["x"] + 1e-9
                and 0 < values["height"] <= 1 - values["y"] + 1e-9):
            raise ValueError("Privacy regions must stay inside the video.")
        if not 0 <= values["start"] < values["end"] <= duration + 0.001:
            raise ValueError("Privacy region times must stay inside the video.")
        if "blur_strength" in region:
            strength = region["blur_strength"]
            if type(strength) not in (int, float) or not math.isfinite(strength) or not 1 <= strength <= 48:
                raise ValueError("Blur strength must be a number from 1 to 48.")
            values["blur_strength"] = float(strength)
        result.append({**values, "effect": region["effect"]})
    return result


async def video_dimensions(source):
    probe = await asyncio.to_thread(
        subprocess.run, [media_executable("ffprobe"), "-v", "error", "-select_streams", "v:0",
                         "-show_entries", "stream=width,height:stream_side_data=rotation",
                         "-of", "json", str(source)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if probe.returncode:
        raise ValueError(f"Could not inspect privacy video dimensions: {probe.stderr[-1000:]}")
    stream = json.loads(probe.stdout)["streams"][0]
    width, height = stream["width"], stream["height"]
    rotation = next((item["rotation"] for item in stream.get("side_data_list", []) if "rotation" in item), 0)
    if abs(round(rotation)) % 180 == 90:
        width, height = height, width
    return width, height


def privacy_filter(regions, width, height, start, end):
    filters, previous = ["[0:v:0]setpts=PTS-STARTPTS[p0]"], "p0"
    count = 0
    for region in regions:
        left, right = max(start, region["start"]), min(end, region["end"])
        if right <= left:
            continue
        if min(region["width"] * width, region["height"] * height) < 2:
            raise ValueError("Privacy regions must be at least two source pixels wide and high.")
        x, y = math.floor(region["x"] * width / 2) * 2, math.floor(region["y"] * height / 2) * 2
        w = min(width, math.ceil((region["x"] + region["width"]) * width / 2) * 2) - x
        h = min(height, math.ceil((region["y"] + region["height"]) * height / 2) * 2) - y
        if min(w, h) < 2:
            raise ValueError("Privacy regions must be at least two source pixels wide and high.")
        count += 1
        output = f"p{count}"
        enable = f"gte(t,{left - start:.6f})*lt(t,{right - start:.6f})"
        if region["effect"] == "cover":
            filters.append(f"[{previous}]drawbox=x={x}:y={y}:w={w}:h={h}:color=black:t=fill:"
                           f"enable='{enable}'[{output}]")
        else:
            filters.append(f"[{previous}]split[base{count}][crop{count}]")
            effect = (f"gblur=sigma={region.get('blur_strength', 24):g}:steps=2" if region["effect"] == "blur" else
                      f"scale={max(1, w // 16)}:{max(1, h // 16)},scale={w}:{h}:flags=neighbor")
            filters.append(f"[crop{count}]crop={w}:{h}:{x}:{y}:exact=1,{effect}[mask{count}]")
            filters.append(f"[base{count}][mask{count}]overlay={x}:{y}:"
                           f"enable='{enable}':eof_action=pass[{output}]")
        previous = output
    return ";".join(filters), previous


def expanded_box(box, width, height, padding=0.2):
    x, y, w, h = map(float, box)
    left, top = max(0, x - padding * w), max(0, y - padding * h)
    right, bottom = min(width, x + (1 + padding) * w), min(height, y + (1 + padding) * h)
    return {"x": left / width, "y": top / height,
            "width": (right - left) / width, "height": (bottom - top) / height}


def merge_detection(regions, box, start, end, effect):
    for region in reversed(regions):
        if start > region["end"] + 0.6:
            continue
        overlap_w = max(0, min(box["x"] + box["width"], region["x"] + region["width"])
                        - max(box["x"], region["x"]))
        overlap_h = max(0, min(box["y"] + box["height"], region["y"] + region["height"])
                        - max(box["y"], region["y"]))
        overlap = overlap_w * overlap_h
        if overlap / max(1e-9, min(box["width"] * box["height"], region["width"] * region["height"])) < 0.3:
            continue
        right = max(box["x"] + box["width"], region["x"] + region["width"])
        bottom = max(box["y"] + box["height"], region["y"] + region["height"])
        region["x"], region["y"] = min(box["x"], region["x"]), min(box["y"], region["y"])
        region["width"], region["height"] = right - region["x"], bottom - region["y"]
        region["start"], region["end"] = min(start, region["start"]), max(end, region["end"])
        return
    if len(regions) >= MAX_REGIONS:
        raise ValueError("Too many detected regions. Scan a shorter clip, or cover a fixed webcam area manually.")
    regions.append({**box, "start": start, "end": end, "effect": effect})


async def scan_people(source, start, end, effect, step):
    import cv2

    capture = cv2.VideoCapture(str(source))
    try:
        if not capture.isOpened():
            raise ValueError("Could not open the video for privacy detection.")
        step("Loading the local people detector; first use downloads model weights, not your video")
        people = load_people_detector()
        regions, position, frames = [], start, 0
        step("Scanning people locally every 0.5 seconds; review is required")
        while position < end:
            capture.set(cv2.CAP_PROP_POS_MSEC, position * 1000)
            ok, frame = capture.read()
            if not ok:
                raise ValueError(f"Could not decode the privacy scan frame at {position:.3f}s.")
            height, width = frame.shape[:2]
            scale = min(1, 960 / width)
            frame = cv2.resize(frame, (max(1, round(width * scale)), max(1, round(height * scale))))
            height, width = frame.shape[:2]
            results = people.predict(frame, classes=[0], conf=0.25, imgsz=960, verbose=False)
            for result in results:
                for x1, y1, x2, y2 in result.boxes.xyxy.cpu().tolist():
                    merge_detection(regions, expanded_box((x1, y1, x2 - x1, y2 - y1), width, height),
                                    max(start, position - 0.5), min(end, position + 1), effect)
            frames += 1
            if frames % 10 == 0:
                step(f"Privacy scan {min(99, int((position - start) / (end - start) * 100))}%: "
                     f"{len(regions)} candidate regions")
            position += 0.5
            await asyncio.sleep(0)
        step(f"Scan complete: {len(regions)} candidates. Review misses and false positives before exporting.")
        return regions
    finally:
        capture.release()


def load_people_detector():
    from ultralytics import YOLO

    directory = Path.home() / ".cache" / "lectureseek"
    directory.mkdir(parents=True, exist_ok=True)
    model = YOLO(str(directory / "yolo11n.pt"))
    if model.names.get(0) != "person":
        raise ValueError("The privacy detection model does not contain the expected person class.")
    return model
