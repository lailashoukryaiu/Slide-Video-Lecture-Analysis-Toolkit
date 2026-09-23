import torch
from faster_whisper import WhisperModel, BatchedInferencePipeline


def get_whisper_device_config():
    """Select a CUDA-capable device when available; otherwise fall back to CPU."""
    if torch.cuda.is_available():
        return "cuda", "float16"
    return "cpu", "int8"


def convert_to_srt_time(time_in_seconds):
    hours, remainder = divmod(time_in_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    milliseconds = (seconds - int(seconds)) * 1000
    return f"{int(hours):02}:{int(minutes):02}:{int(seconds):02},{int(milliseconds):03}"


def timestamps_to_srt(word_timestamps):
    sentences = []
    sentence = ""
    start_time = 0

    for i, word_info in enumerate(word_timestamps):
        if sentence == "":
            start_time = word_info["start"]
        word = word_info["word"]
        sentence += "" + word

        if "." in word or "?" in word or "!" in word or i == len(word_timestamps) - 1:
            sentences.append({"sentence": sentence.strip(), "start": start_time, "end": word_info["end"]})
            sentence = ""

    srt_format_corrected = ""
    for index, sent in enumerate(sentences):
        start_srt = convert_to_srt_time(sent["start"])
        end_srt = convert_to_srt_time(sent["end"])
        srt_format_corrected += f"{index + 1}\n{start_srt} --> {end_srt}\n{sent['sentence']}\n\n"

    return srt_format_corrected


def transcribe_audio(
    file_path, batch_size=16, model_name="turbo", prompt=None,
    precise_timestamps=False
):
    """Transcribe audio file and yield sentences as they are processed."""
    device, compute_type = get_whisper_device_config()
    print(f"Whisper device: {device}")
    print(f"Whisper compute type: {compute_type}")
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    batched_model = BatchedInferencePipeline(model=model)
    options = {
        "batch_size": batch_size,
        "word_timestamps": precise_timestamps,
        "log_progress": True,
    }
    if prompt:
        options["initial_prompt"] = prompt
    segments, info = batched_model.transcribe(file_path, **options)
    total_duration = info.duration
    print(total_duration)

    processed_duration = 0
    for segment in segments:
        if precise_timestamps:
            word_list = [
                {
                    "start": float(word.start),
                    "end": float(word.end),
                    "word": word.word,
                    "probability": float(word.probability),
                }
                for word in (segment.words or [])
            ]
            sentence_data = timestamps_to_srt(word_list)
        else:
            sentence_data = (
                "1\n"
                f"{convert_to_srt_time(segment.start)} --> "
                f"{convert_to_srt_time(segment.end)}\n"
                f"{segment.text.strip()}\n\n"
            )

        processed_duration = max(processed_duration, float(segment.end))
        progress = min(100, (processed_duration / total_duration) * 100) if total_duration > 0 else 0
        yield sentence_data, progress
