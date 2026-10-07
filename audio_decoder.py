import subprocess

import numpy as np
from ffmpeg_tools import media_executable


def decode_audio_samples(media_path):
    """Decode mono 16 kHz samples for Whisper and speaker identification."""
    try:
        result = subprocess.run(
            [
                media_executable("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error",
                "-i", str(media_path), "-vn", "-ac", "1", "-ar", "16000",
                "-f", "f32le", "pipe:1",
            ],
            capture_output=True, check=True,
        )
    except FileNotFoundError as error:
        raise RuntimeError("FFmpeg is required to decode audio. Install FFmpeg and retry.") from error
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"FFmpeg could not decode the audio: {detail or error}") from error
    samples = np.frombuffer(result.stdout, dtype="<f4").astype(np.float32, copy=True)
    if not samples.size:
        raise RuntimeError("The video has no decodable audio track.")
    return samples
