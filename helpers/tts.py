"""Text-to-speech via Microsoft Edge TTS (free, no API key).

Generates audio (mp3) and optional SRT subtitles from text input.
Supports single-text mode and batch mode that parses narration blocks
from a recap-script markdown file. Batch subtitle mode also writes one
output-timeline master.srt next to the narration directory.

No API key required. Install: pip install edge-tts

Usage:
    python helpers/tts.py "你好世界" -o hello.mp3
    python helpers/tts.py "你好世界" -o hello.mp3 --voice zh-CN-YunxiNeural
    python helpers/tts.py "你好世界" -o hello.mp3 --write-subtitles
    python helpers/tts.py "你好世界" -o hello.mp3 --rate "+20%" --pitch "+5Hz"
    python helpers/tts.py --from-script recap-script.md -o edit/narration/
    python helpers/tts.py --list-voices
    python helpers/tts.py --list-voices --language zh
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    import edge_tts
except ImportError:
    sys.exit("edge-tts not installed. Run: pip install edge-tts")

_REPO_ROOT = Path(__file__).resolve().parent.parent
_VOICES_CONFIG = json.loads((_REPO_ROOT / "config" / "voices.json").read_text(encoding="utf-8"))
DEFAULT_VOICE = _VOICES_CONFIG["default"]
DEFAULT_RATE = _VOICES_CONFIG.get("default_rate", "+0%")
DEFAULT_PITCH = _VOICES_CONFIG.get("default_pitch", "+0Hz")


# -------- Core TTS ------------------------------------------------------------


async def synthesize(
    text: str,
    output: Path,
    voice: str = DEFAULT_VOICE,
    rate: str = DEFAULT_RATE,
    pitch: str = DEFAULT_PITCH,
    write_subtitles: bool = False,
) -> Path:
    """Generate audio from text. Returns the audio output path.

    Optionally writes an SRT file alongside the audio (same stem, .srt).
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)

    if write_subtitles:
        srt_path = output.with_suffix(".srt")
        submaker = edge_tts.SubMaker()
        cue_count = 0
        with open(output, "wb") as f:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                    submaker.feed(chunk)
                    cue_count += 1
        srt_content = submaker.get_srt()
        if cue_count == 0:
            print(f"  WARNING: 0 subtitle cues captured for {output.name} "
                  f"— check edge-tts version / voice compatibility")
        srt_path.write_text(srt_content, encoding="utf-8")
        print(f"  subtitles → {srt_path.name} ({cue_count} cues)")
    else:
        await communicate.save(str(output))

    size_kb = output.stat().st_size / 1024
    print(f"  audio → {output.name} ({size_kb:.1f} KB)")
    return output


def synthesize_sync(
    text: str,
    output: Path,
    voice: str = DEFAULT_VOICE,
    rate: str = DEFAULT_RATE,
    pitch: str = DEFAULT_PITCH,
    write_subtitles: bool = False,
) -> Path:
    """Synchronous wrapper around synthesize()."""
    return asyncio.run(
        synthesize(text, output, voice, rate, pitch, write_subtitles)
    )


# -------- Cache ---------------------------------------------------------------


def _text_hash(text: str, voice: str, rate: str, pitch: str) -> str:
    key = f"{voice}|{rate}|{pitch}|{text}"
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def synthesize_cached(
    text: str,
    output: Path,
    voice: str = DEFAULT_VOICE,
    rate: str = DEFAULT_RATE,
    pitch: str = DEFAULT_PITCH,
    write_subtitles: bool = False,
) -> Path:
    """Like synthesize_sync but skips if output exists and matches the text hash."""
    meta_path = output.with_suffix(".tts_meta.json")
    current_hash = _text_hash(text, voice, rate, pitch)

    if output.exists() and meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
            if meta.get("hash") == current_hash:
                print(f"  cached: {output.name}")
                return output
        except (json.JSONDecodeError, KeyError):
            pass

    result = synthesize_sync(text, output, voice, rate, pitch, write_subtitles)
    meta_path.write_text(json.dumps({"hash": current_hash}))
    return result


# -------- Recap-script parser -------------------------------------------------


def parse_narration_blocks(script_path: Path) -> list[dict]:
    """Parse a recap-script markdown into narration blocks.

    Expects strict format: ### M:SS–M:SS｜title, narration as > blockquotes.
    """
    content = script_path.read_text(encoding="utf-8")
    section_re = re.compile(
        r"^###\s+(\d+:\d{2})\s*[–\-]\s*(\d+:\d{2})\s*[｜|]\s*(.+)$",
        re.MULTILINE,
    )
    sections = list(section_re.finditer(content))
    blocks: list[dict] = []
    for i, m in enumerate(sections):
        start_str, end_str, title = m.group(1), m.group(2), m.group(3).strip()
        chunk = content[m.end(): sections[i + 1].start() if i + 1 < len(sections) else len(content)]
        lines = [l.lstrip(">").strip() for l in chunk.splitlines() if l.strip().startswith(">")]
        if lines:
            blocks.append({
                "section": title,
                "start": start_str,
                "end": end_str,
                "duration": _mmss_to_seconds(end_str) - _mmss_to_seconds(start_str),
                "text": "\n".join(lines),
            })

    return blocks


def _mmss_to_seconds(mmss: str) -> float:
    parts = mmss.split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    return float(mmss)


async def batch_from_script(
    script_path: Path,
    output_dir: Path,
    voice: str = DEFAULT_VOICE,
    rate: str = "+0%",
    pitch: str = "+0Hz",
    write_subtitles: bool = False,
    master_srt: Path | None = None,
) -> list[dict]:
    """Parse a recap script and generate one audio file per narration block.

    Returns the blocks list with an added 'audio_path' and 'audio_duration' key.
    """
    blocks = parse_narration_blocks(script_path)
    if not blocks:
        print("no narration blocks found in script")
        return []

    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"generating {len(blocks)} narration segment(s) → {output_dir}")

    for i, block in enumerate(blocks):
        slug = re.sub(r"[^\w]+", "_", block["section"])[:30].strip("_")
        filename = f"{i:02d}_{slug}.mp3"
        out_path = output_dir / filename

        meta_path = out_path.with_suffix(".tts_meta.json")
        current_hash = _text_hash(block["text"], voice, rate, pitch)

        srt_path = out_path.with_suffix(".srt")
        subtitles_ready = not write_subtitles or srt_path.exists()
        if out_path.exists() and meta_path.exists() and subtitles_ready:
            try:
                meta = json.loads(meta_path.read_text())
                if meta.get("hash") == current_hash:
                    print(f"  [{i:02d}] {block['section']}: cached")
                    block["audio_path"] = str(out_path)
                    block["audio_duration"] = _probe_duration(out_path)
                    continue
            except (json.JSONDecodeError, KeyError):
                pass

        print(f"  [{i:02d}] {block['section']} ({block['duration']}s target)")
        await synthesize(
            block["text"], out_path, voice, rate, pitch, write_subtitles
        )
        meta_path.write_text(json.dumps({"hash": current_hash}))
        block["audio_path"] = str(out_path)
        block["audio_duration"] = _probe_duration(out_path)

    _print_timing_report(blocks)
    _schedule_output_timeline(blocks)
    full_audio = build_full_narration(blocks, output_dir.parent / "full_narration.m4a")
    _write_narration_manifest(blocks, script_path, output_dir, voice, rate, pitch, full_audio)
    if write_subtitles:
        out_srt = master_srt or (output_dir.parent / "master.srt")
        build_master_srt_from_tts(blocks, out_srt)
    return blocks


# -------- Recap subtitle assembly -------------------------------------------


def _srt_timestamp(seconds: float) -> str:
    total_ms = max(0, round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds_part, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds_part:02d},{milliseconds:03d}"


def _timestamp_seconds(timestamp: str) -> float:
    hours, minutes, rest = timestamp.strip().replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(rest)


def _read_srt(path: Path) -> list[tuple[float, float, str]]:
    """Read the simple SRT emitted by edge-tts without adding a dependency."""
    entries: list[tuple[float, float, str]] = []
    for block in re.split(r"\r?\n\s*\r?\n", path.read_text(encoding="utf-8").strip()):
        lines = [line.strip() for line in block.splitlines()]
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        start_text, end_text = (part.strip() for part in lines[1].split("-->", 1))
        text = "\n".join(lines[2:]).strip()
        if not text:
            continue
        entries.append((_timestamp_seconds(start_text), _timestamp_seconds(end_text), text))
    return entries


def _schedule_output_timeline(blocks: list[dict]) -> None:
    """Attach output offsets while preserving recap section targets.

    A narration shorter than its section leaves visual breathing room, so the
    following section must *not* move earlier. A narration that overruns its
    target pushes following sections later. This is the timing model described
    by the recap workflow and is intentionally not a simple MP3-duration sum.
    """
    cursor = 0.0
    for block in blocks:
        declared_start = _mmss_to_seconds(block["start"])
        target_duration = float(block["duration"])
        audio_duration = float(block.get("audio_duration") or target_duration)
        output_start = max(cursor, declared_start)
        output_duration = max(target_duration, audio_duration)
        block["output_start"] = round(output_start, 3)
        block["output_duration"] = round(output_duration, 3)
        cursor = output_start + output_duration


def build_master_srt_from_tts(blocks: list[dict], output: Path) -> Path:
    """Shift the per-section Edge TTS SRTs onto the recap output timeline."""
    entries: list[tuple[float, float, str]] = []
    for block in blocks:
        audio_path = Path(block["audio_path"])
        srt_path = audio_path.with_suffix(".srt")
        if not srt_path.exists():
            raise FileNotFoundError(
                f"missing TTS subtitle file for '{block['section']}': {srt_path}"
            )
        offset = float(block["output_start"])
        for start, end, text in _read_srt(srt_path):
            if end <= start:
                end = start + 0.1
            entries.append((offset + start, offset + end, text))

    entries.sort(key=lambda entry: entry[0])
    lines: list[str] = []
    for index, (start, end, text) in enumerate(entries, start=1):
        lines.extend((str(index), f"{_srt_timestamp(start)} --> {_srt_timestamp(end)}", text, ""))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")
    print(f"master subtitles → {output} ({len(entries)} cues)")
    return output


def build_full_narration(blocks: list[dict], output: Path) -> Path:
    """Join narration clips while preserving each section's output-time slot.

    Plain concat would make later narration start too early whenever a section's
    speech is shorter than its planned visual duration. This filter graph adds
    silence for both intentional section gaps and unused visual breathing room.
    """
    if not blocks:
        raise ValueError("cannot assemble narration without blocks")

    inputs: list[str] = []
    filters: list[str] = []
    concat_labels: list[str] = []
    cursor = 0.0
    for index, block in enumerate(blocks):
        audio_path = Path(block["audio_path"])
        inputs.extend(("-i", str(audio_path)))
        output_start = float(block["output_start"])
        leading_silence = output_start - cursor
        if leading_silence > 0.005:
            label = f"gap{index}"
            filters.append(
                f"anullsrc=r=48000:cl=stereo,atrim=duration={leading_silence:.3f}[{label}]"
            )
            concat_labels.append(f"[{label}]")

        audio_label = f"audio{index}"
        filters.append(f"[{index}:a]aresample=48000,asetpts=PTS-STARTPTS[{audio_label}]")
        concat_labels.append(f"[{audio_label}]")

        audio_duration = float(block.get("audio_duration") or 0.0)
        slot_duration = float(block["output_duration"])
        trailing_silence = slot_duration - audio_duration
        if trailing_silence > 0.005:
            label = f"tail{index}"
            filters.append(
                f"anullsrc=r=48000:cl=stereo,atrim=duration={trailing_silence:.3f}[{label}]"
            )
            concat_labels.append(f"[{label}]")
        cursor = output_start + slot_duration

    filters.append(f"{''.join(concat_labels)}concat=n={len(concat_labels)}:v=0:a=1[aout]")
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", ";".join(filters),
        "-map", "[aout]",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart", str(output),
    ]
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    print(f"full narration → {output}")
    return output


def _write_narration_manifest(
    blocks: list[dict],
    script_path: Path,
    output_dir: Path,
    voice: str,
    rate: str,
    pitch: str,
    full_audio: Path,
) -> Path:
    """Persist the timing contract for EDL construction and later rerenders."""
    public_keys = (
        "section", "start", "end", "duration", "audio_path", "audio_duration",
        "output_start", "output_duration",
    )
    manifest: dict[str, Any] = {
        "version": 1,
        "script": str(script_path.resolve()),
        "voice": voice,
        "rate": rate,
        "pitch": pitch,
        "full_audio": str(full_audio.resolve()),
        "blocks": [{key: block.get(key) for key in public_keys} for block in blocks],
    }
    out_path = output_dir / "narration_manifest.json"
    out_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"narration manifest → {out_path.name}")
    return out_path


def _probe_duration(audio_path: Path) -> float | None:
    """Get audio duration via ffprobe. Returns None if ffprobe unavailable."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
            capture_output=True, text=True, check=True,
        )
        return round(float(out.stdout.strip()), 2)
    except Exception:
        return None


def _print_timing_report(blocks: list[dict]) -> None:
    """Print a summary comparing script target durations vs TTS audio durations."""
    print("\ntiming report:")
    total_target = 0.0
    total_audio = 0.0
    overflows: list[str] = []

    for b in blocks:
        target = b["duration"]
        actual = b.get("audio_duration")
        total_target += target
        if actual is not None:
            total_audio += actual
            delta = actual - target
            marker = ""
            if delta > 0.5:
                marker = f"  ⚠ +{delta:.1f}s overflow"
                overflows.append(f"{b['section']} (+{delta:.1f}s)")
            print(f"  {b['section']:20s}  target {target:5.1f}s  audio {actual:5.1f}s{marker}")
        else:
            print(f"  {b['section']:20s}  target {target:5.1f}s  audio ???")

    print(f"\n  total target: {total_target:.1f}s  total audio: {total_audio:.1f}s")
    if overflows:
        print(f"  overflow sections: {', '.join(overflows)}")
        print("  (these sections need extended source footage or faster --rate)")


# -------- List voices ---------------------------------------------------------


async def list_voices(language: str | None = None) -> None:
    print("preconfigured voices (config/voices.json):\n")
    default_id = _VOICES_CONFIG["default"]
    for v in _VOICES_CONFIG["voices"]:
        if language and not v["locale"].lower().startswith(language.lower()):
            continue
        marker = " *" if v["id"] == default_id else ""
        print(f"  {v['id']:40s}  {v['gender']:8s}  {v.get('description', '')}{marker}")
    print(f"\n  (* = default)\n")
    print("all available Edge TTS voices:\n")
    voices = await edge_tts.list_voices()
    if language:
        voices = [v for v in voices if v["Locale"].lower().startswith(language.lower())]
    for v in sorted(voices, key=lambda x: x["ShortName"]):
        gender = v.get("Gender", "?")
        locale = v.get("Locale", "?")
        print(f"  {v['ShortName']:40s}  {gender:6s}  {locale}")
    print(f"\n  {len(voices)} voice(s)")


# -------- CLI -----------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Text-to-speech via Edge TTS (free, no API key)"
    )
    ap.add_argument("text", nargs="?", help="Text to synthesize")
    ap.add_argument("-o", "--output", type=Path, help="Output audio path (mp3)")
    ap.add_argument(
        "--from-script", type=Path, metavar="SCRIPT",
        help="Parse recap-script markdown and batch-generate narration audio",
    )
    ap.add_argument("--voice", default=DEFAULT_VOICE, help=f"Voice name (default: {DEFAULT_VOICE})")
    ap.add_argument("--rate", default=DEFAULT_RATE, help=f"Speech rate (default: {DEFAULT_RATE})")
    ap.add_argument("--pitch", default=DEFAULT_PITCH, help=f"Pitch adjustment (default: {DEFAULT_PITCH})")
    ap.add_argument("--write-subtitles", action="store_true", help="Also write SRT alongside audio")
    ap.add_argument(
        "--master-srt", type=Path,
        help="Output master SRT in --from-script mode (default: parent of narration directory/master.srt)",
    )
    ap.add_argument("--list-voices", action="store_true", help="List available voices and exit")
    ap.add_argument("--language", type=str, help="Filter voices by language prefix (e.g. 'zh', 'en')")
    args = ap.parse_args()

    if args.list_voices:
        asyncio.run(list_voices(args.language))
        return

    if args.from_script:
        if not args.from_script.exists():
            sys.exit(f"script not found: {args.from_script}")
        if not args.output:
            sys.exit("--output / -o required (output directory for batch mode)")
        asyncio.run(batch_from_script(
            args.from_script, args.output,
            voice=args.voice, rate=args.rate, pitch=args.pitch,
            write_subtitles=args.write_subtitles, master_srt=args.master_srt,
        ))
        return

    if not args.text:
        sys.exit("provide text as positional arg, or use --from-script")
    if not args.output:
        sys.exit("--output / -o required")

    synthesize_cached(
        args.text, args.output,
        voice=args.voice, rate=args.rate, pitch=args.pitch,
        write_subtitles=args.write_subtitles,
    )


if __name__ == "__main__":
    main()
