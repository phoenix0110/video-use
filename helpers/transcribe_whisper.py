"""Transcribe a video locally with openai-whisper.

Fallback / cost-free alternative to ElevenLabs Scribe. Produces the same
JSON shape the rest of the video-use pipeline expects:

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

Differences vs Scribe (intentional, see SKILL.md Hard Rule #8 tradeoffs):
  - No speaker diarization (whisper can't do it). All words have no
    speaker_id. Multi-speaker editing needs a different upstream.
  - No audio events (laughs, applause). The editor still gets phrase
    boundaries from silences and punctuation, just not event tags.
  - Word timestamps come from whisper's internal alignment; timing
    drift ~50-100ms in either direction (Scribe is similar). Cut padding
    in the EDL absorbs the drift per SKILL.md Hard Rule #7.

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
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


# -------- ffmpeg discovery ---------------------------------------------------
#
# The original transcribe.py assumes `ffmpeg` is on $PATH. That's brittle on
# Windows when the user just installed ffmpeg via winget (PATH update requires
# a fresh shell) or when the spawner (Python subprocess) sees a translated PATH
# that doesn't include the binary's real location. We probe a short list of
# known locations before falling back to shutil.which.


_FFMPEG_CANDIDATES_WIN = [
    r"C:\Users\yinsh\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.2-full_build\bin\ffmpeg.exe",
    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
    r"C:\ffmpeg\bin\ffmpeg.exe",
    os.path.expanduser(r"~\scoop\apps\ffmpeg\current\bin\ffmpeg.exe"),
    r"C:\ProgramData\chocolatey\bin\ffmpeg.exe",
]


def find_ffmpeg() -> str:
    """Locate ffmpeg.exe. Returns the full path.

    Search order:
      1. $VIDEO_USE_FFMPEG env var (lets `env.sh` override the default)
      2. shutil.which on PATH
      3. Known Windows install locations
      4. PATH re-probe excluding shim translation issues
    """
    env_override = os.environ.get("VIDEO_USE_FFMPEG", "").strip()
    if env_override and Path(env_override).exists():
        return env_override

    found = shutil.which("ffmpeg")
    if found:
        return found

    if sys.platform.startswith("win"):
        for cand in _FFMPEG_CANDIDATES_WIN:
            if Path(cand).exists():
                return cand

    sys.exit(
        "ffmpeg not found. Install it (e.g. `winget install Gyan.FFmpeg`) "
        "and either restart your shell, or set VIDEO_USE_FFMPEG=/full/path/to/ffmpeg.exe"
    )


# -------- Model registry (mirror whisper.available_models()) -----------------

VALID_MODELS = {
    "tiny", "tiny.en", "base", "base.en", "small", "small.en",
    "medium", "medium.en", "large-v1", "large-v2", "large-v3",
    "large", "large-v3-turbo", "turbo",
}


def device_flag() -> str:
    """Pick the best available device without importing torch at import time.

    Returns "cuda" if a CUDA-capable GPU is visible, else "cpu".
    """
    try:
        import torch  # local import keeps whisper-free startup for help()
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def extract_audio(video_path: Path, dest: Path, ffmpeg_path: str | None = None) -> None:
    """Decode the video's audio track to mono 16kHz PCM WAV (whisper's preferred input).

    `ffmpeg_path` lets the caller pin the full path; otherwise we resolve via
    find_ffmpeg() right before spawning (cheap — just a handful of stat() calls).
    """
    if ffmpeg_path is None:
        ffmpeg_path = find_ffmpeg()
    cmd = [
        ffmpeg_path, "-y", "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        str(dest),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# -------- Whisper → Scribe-compatible JSON ----------------------------------


def normalize_word_token(tok: str) -> str:
    """whisper emits UTF-8 punctuation-prefixed tokens (e.g. " 你"). Strip leading space.
    Also normalize full-width and Chinese punctuation to a consistent form."""
    if tok is None:
        return ""
    s = tok.strip()
    s = s.replace("\u3000", " ")  # ideographic space
    return s


def convert_segments_to_words(whisper_result: dict) -> tuple[list[dict], float]:
    """Flatten whisper's `segments[*].words` into the Scribe `words` list.

    Whisper word dict shape: {"word": " 你", "start": 0.52, "end": 1.18,
                              "probability": 0.96}
    Scribe word dict shape:  {"text": "你", "start": 0.52, "end": 1.18,
                              "type": "word"}
    """
    out: list[dict] = []
    for seg in whisper_result.get("segments", []):
        for w in seg.get("words") or []:
            tok = normalize_word_token(w.get("word", ""))
            if not tok:
                continue
            ws = w.get("start")
            we = w.get("end")
            if ws is None or we is None:
                # Whisper sometimes holds a word across punctuation with
                # start only and end defaults to the next word's start.
                # Skip those — pack_transcripts/editor rely on real ranges.
                continue
            out.append({"text": tok, "start": float(ws), "end": float(we), "type": "word"})
    duration = float(whisper_result.get("duration") or 0.0)
    if not out:
        return out, 0.0
    # Recompute duration as the last word's end (whisper's top-level duration
    # is occasionally a few hundred ms short when trailing silence is present).
    duration = max(duration, out[-1]["end"])
    return out, duration


def transcribe_one(
    video: Path,
    edit_dir: Path,
    api_key_ignore: str | None = None,  # kept for signature parity with transcribe.py
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

    if verbose:
        print(f"  extracting audio from {video.name}", flush=True)

    if device is None:
        device = device_flag()
    if verbose:
        print(f"  loading whisper model '{model}' on {device} (first run downloads weights)", flush=True)

    # Import only now — keeps the help screen instant and respects device warm-up.
    import whisper  # type: ignore

    # Whisper's own audio loader spawns `ffmpeg` directly without consulting
    # our `find_ffmpeg()` shim — it relies on PATH. Add the ffmpeg dir to
    # os.environ *for this process* so whisper can find it. This is benign
    # outside our helper.
    ffmpeg_full = find_ffmpeg()
    ffmpeg_dir = str(Path(ffmpeg_full).parent)
    if sys.platform.startswith("win"):
        path_sep = ";"
        current = os.environ.get("PATH", "")
        if ffmpeg_dir not in current.split(path_sep):
            os.environ["PATH"] = ffmpeg_dir + path_sep + current
    else:
        path_sep = ":"
        current = os.environ.get("PATH", "")
        if ffmpeg_dir not in current.split(path_sep):
            os.environ["PATH"] = ffmpeg_dir + path_sep + current

    t0 = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / f"{video.stem}.wav"
        extract_audio(video, wav, ffmpeg_path=ffmpeg_full)
        size_mb = wav.stat().st_size / (1024 * 1024)
        if verbose:
            print(f"  audio: {wav.name} ({size_mb:.1f} MB)", flush=True)

        # Load the model (first run downloads weights; subsequent runs hit cache).
        if verbose:
            print(f"  loading whisper weights ({model}) ...", flush=True)
        loaded = whisper.load_model(model, device=device)

        if verbose:
            print(f"  transcribing...", flush=True)

        # fp16 only works on cuda; whisper raises a warning if asked for fp16 on cpu.
        use_fp16 = device == "cuda"
        result = loaded.transcribe(
            str(wav),
            language=language,
            word_timestamps=True,
            fp16=use_fp16,
            verbose=False,  # we provide our own progress lines
        )

    words, duration = convert_segments_to_words(result)

    payload = {
        "language": result.get("language") or language or "unknown",
        "duration": duration,
        "source": f"whisper-{model}",
        "device": device,
        "transcribed_at": int(time.time()),
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
        "--edit-dir",
        type=Path,
        default=None,
        help="Edit output directory (default: <video_parent>/edit)",
    )
    ap.add_argument(
        "--language",
        type=str,
        default=None,
        help="ISO language code (e.g. 'zh', 'en'). Omit to auto-detect.",
    )
    ap.add_argument(
        "--model",
        type=str,
        default="small",
        help="Which whisper model to use (default: small). See VALID_MODELS.",
    )
    ap.add_argument(
        "--device",
        choices=("auto", "cpu", "cuda"),
        default="auto",
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
