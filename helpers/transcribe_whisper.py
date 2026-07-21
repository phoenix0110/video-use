"""Transcribe a video locally with openai-whisper.

Free local alternative to ElevenLabs Scribe. Produces the same JSON shape
the rest of the video-use pipeline expects:

    {
      "words": [
        {"text": "你好", "start": 0.52, "end": 1.18, "type": "word"},
        ...
      ],
      "language": "zh",
      "source": "whisper-small",
      "duration": 12.34
    }

Cached per source: if <edit_dir>/transcripts/<video_stem>.json already
exists, the model is not loaded and the audio is not re-decoded.

Limitations vs Scribe:
  - No speaker diarization (all words lack speaker_id).
  - No audio events (laughs, applause).
  - Word timestamp drift ~50-100ms (absorbed by cut padding per Hard Rule #7).

Usage:
    python helpers/transcribe_whisper.py <video>
    python helpers/transcribe_whisper.py <video> --model small --language zh
    python helpers/transcribe_whisper.py <video> --device cpu
    python helpers/transcribe_whisper.py <video> --edit-dir /custom/edit
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


VALID_MODELS = {
    "tiny", "tiny.en", "base", "base.en", "small", "small.en",
    "medium", "medium.en", "large-v1", "large-v2", "large-v3",
    "large", "large-v3-turbo", "turbo",
}


def find_ffmpeg() -> str:
    """Locate ffmpeg. Checks VIDEO_USE_FFMPEG env var, then PATH."""
    override = os.environ.get("VIDEO_USE_FFMPEG", "").strip()
    if override:
        if not Path(override).exists():
            sys.exit(f"VIDEO_USE_FFMPEG points to non-existent path: {override}")
        return override
    found = shutil.which("ffmpeg")
    if not found:
        sys.exit("ffmpeg not found on PATH. Install it and ensure it's in your PATH.")
    return found


def detect_device() -> str:
    """Return 'cuda' if available, else 'cpu'."""
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def extract_audio(video_path: Path, dest: Path) -> None:
    """Decode audio to mono 16kHz PCM WAV (whisper's preferred input)."""
    cmd = [
        find_ffmpeg(), "-y", "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        str(dest),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def normalize_word_token(tok: str) -> str:
    """Strip leading whitespace and normalize ideographic spaces."""
    if tok is None:
        return ""
    return tok.strip().replace("\u3000", " ")


def convert_segments_to_words(whisper_result: dict) -> tuple[list[dict], float]:
    """Flatten whisper segments into Scribe-compatible words list."""
    out: list[dict] = []
    for seg in whisper_result.get("segments", []):
        for w in seg.get("words") or []:
            tok = normalize_word_token(w.get("word", ""))
            if not tok:
                continue
            ws = w.get("start")
            we = w.get("end")
            if ws is None or we is None:
                continue
            out.append({"text": tok, "start": float(ws), "end": float(we), "type": "word"})

    if not out:
        return out, 0.0
    duration = max(float(whisper_result.get("duration") or 0.0), out[-1]["end"])
    return out, duration


def transcribe_one(
    video: Path,
    edit_dir: Path,
    language: str | None = None,
    model: str = "small",
    device: str | None = None,
    verbose: bool = True,
) -> Path:
    """Transcribe one video with openai-whisper. Returns path to the JSON."""
    transcripts_dir = edit_dir / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    out_path = transcripts_dir / f"{video.stem}.json"

    if out_path.exists():
        if verbose:
            print(f"cached: {out_path.name}")
        return out_path

    if model not in VALID_MODELS:
        raise ValueError(
            f"unknown model '{model}'. Choose from: {', '.join(sorted(VALID_MODELS))}"
        )

    if device is None:
        device = detect_device()

    import whisper  # type: ignore

    # Ensure whisper's internal ffmpeg calls can find the binary
    ffmpeg_dir = str(Path(find_ffmpeg()).parent)
    path_sep = ";" if sys.platform.startswith("win") else ":"
    if ffmpeg_dir not in os.environ.get("PATH", "").split(path_sep):
        os.environ["PATH"] = ffmpeg_dir + path_sep + os.environ.get("PATH", "")

    t0 = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / f"{video.stem}.wav"
        if verbose:
            print(f"  extracting audio from {video.name}", flush=True)
        extract_audio(video, wav)

        if verbose:
            size_mb = wav.stat().st_size / (1024 * 1024)
            print(f"  audio: {wav.name} ({size_mb:.1f} MB)", flush=True)
            print(f"  loading whisper model '{model}' on {device}...", flush=True)

        loaded = whisper.load_model(model, device=device)

        if verbose:
            print(f"  transcribing...", flush=True)

        result = loaded.transcribe(
            str(wav),
            language=language,
            word_timestamps=True,
            fp16=(device == "cuda"),
            verbose=False,
        )

    words, duration = convert_segments_to_words(result)

    payload = {
        "language": result.get("language") or language or "unknown",
        "duration": duration,
        "source": f"whisper-{model}",
        "words": words,
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    dt = time.time() - t0
    if verbose:
        kb = out_path.stat().st_size / 1024
        print(f"  saved: {out_path.name} ({kb:.1f} KB) in {dt:.1f}s")
        print(f"    words: {len(words)}  duration: {duration:.2f}s  lang: {payload['language']}")

    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description="Transcribe a video locally with openai-whisper")
    ap.add_argument("video", type=Path, help="Path to video file")
    ap.add_argument(
        "--edit-dir", type=Path, default=None,
        help="Edit output directory (default: <video_parent>/edit)",
    )
    ap.add_argument(
        "--language", type=str, default=None,
        help="ISO language code (e.g. 'zh', 'en'). Omit to auto-detect.",
    )
    ap.add_argument(
        "--model", type=str, default="small",
        help="Whisper model size (default: small). Options: tiny/base/small/medium/large-v3.",
    )
    ap.add_argument(
        "--device", choices=("auto", "cpu", "cuda"), default="auto",
        help="Inference device (default: auto-detect).",
    )
    args = ap.parse_args()

    video = args.video.resolve()
    if not video.exists():
        sys.exit(f"video not found: {video}")

    edit_dir = (args.edit_dir or (video.parent / "edit")).resolve()
    device = None if args.device == "auto" else args.device

    transcribe_one(
        video=video,
        edit_dir=edit_dir,
        language=args.language,
        model=args.model,
        device=device,
    )


if __name__ == "__main__":
    main()
