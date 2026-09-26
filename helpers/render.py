"""Render a video from an EDL.

Implements the HEURISTICS render pipeline in the correct order:

  1. Per-segment extract with color grade + 30ms audio fades baked in
  2. Lossless -c copy concat into base.mp4
  3. If overlays or subtitles: single filter graph that overlays animations
     (with PTS shift so frame 0 lands at the overlay window start)
     and applies `subtitles` filter LAST → final.mp4

Optionally builds a master SRT from the per-source transcripts + EDL
output-timeline offsets, applies the proven force_style (2-word
UPPERCASE chunks, Helvetica 18 Bold, MarginV=35).

Usage:
    python helpers/render.py <edl.json> -o final.mp4
    python helpers/render.py <edl.json> -o preview.mp4 --preview
    python helpers/render.py <edl.json> -o final.mp4 --build-subtitles
    python helpers/render.py <edl.json> -o final.mp4 --no-subtitles
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from pathlib import Path

try:
    from grade import get_preset, auto_grade_for_clip  # same directory
except Exception:
    def get_preset(name: str) -> str:
        return ""

    def auto_grade_for_clip(video, start=0.0, duration=None, verbose=False):  # type: ignore
        return "eq=contrast=1.03:saturation=0.98", {}


# -------- Subtitle style (bold-overlay, proven at 1920×1080 and 1080×1920) --
#
# MarginV is NOT taste — it is a platform safe-zone rule.
# TikTok / IG Reels / Shorts UI (caption, username, music, right-rail actions)
# covers roughly the bottom ~25–30% of a 1080×1920 frame. Captions placed near
# the bottom edge get clipped or obscured by the UI. libass auto-scales the
# render canvas relative to PlayResY=288, so MarginV=90 lands the caption
# baseline roughly 30% up from the bottom on any aspect — clear of the UI on
# every major vertical-video platform. Do not drop this below ~75 without a
# specific reason.
SUB_FORCE_STYLE = (
    "FontName=Helvetica,FontSize=18,Bold=1,"
    "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H00000000,"
    "BorderStyle=1,Outline=2,Shadow=0,"
    "Alignment=2,MarginV=90"
)

LETTERBOX_PLAY_RES_Y = 288


def _letterbox_sub_style(
    bottom_bar: int,
    frame_h: int = 1080,
    max_lines: int = 2,
) -> str:
    """Compute a subtitle style centered within the full-width bottom bar."""
    scale = frame_h / LETTERBOX_PLAY_RES_Y
    padding_px = 20
    max_font_px = (bottom_bar - padding_px) / (1.1 * max_lines)
    font_size = min(22, max(12, int(max_font_px / scale)))
    font_px = font_size * scale
    margin_px = max(0, bottom_bar / 2 - 0.5 * font_px)
    margin_v = round(margin_px / scale)
    return (
        f"FontName=SimHei,FontSize={font_size},Bold=1,"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H00000000,"
        "BorderStyle=1,Outline=2,Shadow=0,WrapStyle=2,"
        f"Alignment=2,MarginL=36,MarginR=36,MarginV={margin_v}"
    )

# -------- Helpers ------------------------------------------------------------


def run(cmd: list[str], quiet: bool = False) -> None:
    if not quiet:
        print(f"  $ {' '.join(str(c) for c in cmd[:6])}{' …' if len(cmd) > 6 else ''}")
    subprocess.run(cmd, check=True)


def resolve_grade_filter(grade_field: str | None) -> str:
    """The EDL's 'grade' field can be a preset name, a raw ffmpeg filter, or 'auto'.

    Returns the filter string to embed into the per-segment -vf chain.
    For 'auto', returns the sentinel "__AUTO__" which is resolved per-segment.
    """
    if not grade_field:
        return ""
    if grade_field == "auto":
        return "__AUTO__"
    # Preset names are short identifiers, filter strings contain '=' or ','.
    if re.fullmatch(r"[a-zA-Z0-9_\-]+", grade_field):
        try:
            return get_preset(grade_field)
        except KeyError:
            print(f"warning: unknown preset '{grade_field}', using as raw filter")
            return grade_field
    return grade_field


def resolve_path(maybe_path: str, base: Path) -> Path:
    """Resolve a path that may be absolute or relative to `base`."""
    p = Path(maybe_path)
    if p.is_absolute():
        return p
    return (base / p).resolve()


def _range_duration(item: dict) -> float:
    """Return a range duration, accepting either end or explicit duration."""
    start = float(item["start"])
    duration = float(item["duration"]) if "duration" in item else float(item["end"]) - start
    if duration <= 0:
        raise ValueError(f"range duration must be positive: {item}")
    return duration


def _render_durations(edl: dict, fps: int = 24) -> list[float]:
    """Quantize range durations to frames while preserving the exact total.

    Encoding many fractional-duration clips independently otherwise rounds each
    clip upward and creates cumulative picture/narration drift after concat.
    """
    raw = [_range_duration(item) for item in edl["ranges"]]
    target_seconds = float(edl.get("total_duration_s", sum(raw)))
    target_frames = round(target_seconds * fps)
    exact_frames = [duration * fps for duration in raw]
    frames = [max(1, math.floor(value)) for value in exact_frames]
    delta = target_frames - sum(frames)
    if delta > 0:
        order = sorted(range(len(frames)), key=lambda i: exact_frames[i] - frames[i], reverse=True)
        for index in range(delta):
            frames[order[index % len(order)]] += 1
    elif delta < 0:
        order = sorted(range(len(frames)), key=lambda i: exact_frames[i] - frames[i])
        for index in range(-delta):
            candidate = order[index % len(order)]
            if frames[candidate] <= 1:
                raise ValueError("cannot quantize ranges to requested total duration")
            frames[candidate] -= 1
    return [frame_count / fps for frame_count in frames]


# -------- HDR → SDR tone mapping (HLG / PQ sources) --------------------------
#
# iPhone defaults to HLG HDR in Rec.2020 (and many mirrorless cameras ship PQ).
# If the source is HDR and we only downconvert bit depth (yuv420p10le → yuv420p)
# without tone-mapping, the output is 8-bit but still carries HLG/PQ transfer
# metadata. Players that honor the metadata (screen recorders, most social
# upload re-encodes) interpret 8-bit values in an HDR container and the result
# looks oversaturated / blown out. QuickTime on macOS can hide this locally —
# screen recording and uploaded renders cannot.
#
# Fix: detect HDR via color_transfer and prepend a zscale+tonemap chain to the
# vf graph so the output is clean Rec.709 SDR.

HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}  # PQ (HDR10) and HLG

TONEMAP_CHAIN = (
    "zscale=t=linear:npl=100,"
    "format=gbrpf32le,"
    "zscale=p=bt709,"
    "tonemap=tonemap=hable:desat=0,"
    "zscale=t=bt709:m=bt709:r=tv,"
    "format=yuv420p"
)


def is_hdr_source(video: Path) -> bool:
    """Return True if the source uses a PQ or HLG transfer function."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=color_transfer",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
            capture_output=True, text=True, check=True,
        )
        return out.stdout.strip() in HDR_TRANSFERS
    except subprocess.CalledProcessError:
        return False


def is_portrait_source(video: Path) -> bool:
    """Return True if the video's height > width (portrait / vertical)."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-of", "csv=p=0", str(video)],
            capture_output=True, text=True, check=True,
        )
        w, h = map(int, out.stdout.strip().split(","))
        return h > w
    except Exception:
        return False


# -------- Per-segment extraction (Rule 2 + Rule 3) --------------------------


def extract_segment(
    source: Path,
    seg_start: float,
    duration: float,
    grade_filter: str,
    out_path: Path,
    preview: bool = False,
    draft: bool = False,
    letterbox: dict | None = None,
) -> None:
    """Extract a cut range as its own MP4 with grade + 30ms audio fades baked in.

    `-ss` before `-i` for fast accurate seeking. Scale to 1080p from 4K.
    Portrait sources (height > width) are scaled by height to preserve orientation.

    When ``letterbox`` is provided (e.g. ``{"top": 70, "bottom": 100}``), the
    video content is scaled to fit inside the area between the bars, then padded
    to full frame with black bars at top and bottom. This covers the original
    video's burned-in subtitles and top-of-screen text.

    Quality ladder:
      - final (default): 1080p libx264 fast CRF 20
      - preview:         1080p libx264 medium CRF 22 (evaluable for QC)
      - draft:           720p libx264 ultrafast CRF 28 (cut-point check only)
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)

    portrait = is_portrait_source(source)

    vf_parts: list[str] = []
    if is_hdr_source(source):
        vf_parts.append(TONEMAP_CHAIN)

    if draft:
        scale = "scale=-2:1280" if portrait else "scale=1280:-2"
    else:
        scale = "scale=-2:1920" if portrait else "scale=1920:-2"
    vf_parts.append(scale)

    if letterbox and not portrait:
        top_bar = letterbox.get("top", 0)
        bottom_bar = letterbox.get("bottom", 0)
        if draft:
            ratio = 720 / 1080
            top_bar = round(top_bar * ratio)
            bottom_bar = round(bottom_bar * ratio)
        if top_bar > 0:
            vf_parts.append(f"drawbox=x=0:y=0:w=iw:h={top_bar}:color=black:t=fill")
        if bottom_bar > 0:
            vf_parts.append(f"drawbox=x=0:y=ih-{bottom_bar}:w=iw:h={bottom_bar}:color=black:t=fill")

    if grade_filter:
        vf_parts.append(grade_filter)
    vf = ",".join(vf_parts)

    # 30ms audio fades at both edges (Rule 3) — prevent pops
    fade_out_start = max(0.0, duration - 0.03)
    af = f"afade=t=in:st=0:d=0.03,afade=t=out:st={fade_out_start:.3f}:d=0.03"

    if draft:
        preset, crf = "ultrafast", "28"
    elif preview:
        preset, crf = "medium", "22"
    else:
        preset, crf = "fast", "20"

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{seg_start:.3f}",
        "-i", str(source),
        "-t", f"{duration:.3f}",
        "-vf", vf,
        "-af", af,
        "-c:v", "libx264", "-preset", preset, "-crf", crf,
        "-pix_fmt", "yuv420p", "-r", "24",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def extract_all_segments(
    edl: dict,
    edit_dir: Path,
    preview: bool,
    draft: bool = False,
    letterbox: dict | None = None,
) -> list[Path]:
    """Extract every EDL range into edit_dir/clips_graded/seg_NN.mp4.
    Returns the ordered list of segment paths.

    If the EDL `grade` is "auto", analyze each segment range with
    `auto_grade_for_clip` and apply a per-segment subtle correction.
    Otherwise, apply the same preset/raw filter to every segment.
    """
    resolved = resolve_grade_filter(edl.get("grade"))
    is_auto = resolved == "__AUTO__"
    clips_dir = edit_dir / (
        "clips_draft" if draft else ("clips_preview" if preview else "clips_graded")
    )
    clips_dir.mkdir(parents=True, exist_ok=True)

    ranges = edl["ranges"]
    sources = edl["sources"]

    seg_paths: list[Path] = []
    print(f"extracting {len(ranges)} segment(s) → {clips_dir.name}/")
    if is_auto:
        print("  (auto-grade per segment: analyzing each range)")
    durations = _render_durations(edl)
    for i, (r, duration) in enumerate(zip(ranges, durations)):
        src_name = r["source"]
        src_path = resolve_path(sources[src_name], edit_dir)
        start = float(r["start"])
        end = start + duration
        out_path = clips_dir / f"seg_{i:02d}_{src_name}.mp4"

        if is_auto:
            seg_filter, _stats = auto_grade_for_clip(src_path, start=start, duration=duration, verbose=False)
        else:
            seg_filter = resolved

        note = r.get("beat") or r.get("note") or ""
        print(f"  [{i:02d}] {src_name}  {start:7.2f}-{end:7.2f}  ({duration:5.2f}s)  {note}")
        if is_auto:
            print(f"        grade: {seg_filter or '(none)'}")
        extract_segment(src_path, start, duration, seg_filter, out_path,
                        preview=preview, draft=draft, letterbox=letterbox)
        seg_paths.append(out_path)

    return seg_paths


# -------- Lossless concat ----------------------------------------------------


def concat_segments(segment_paths: list[Path], out_path: Path, edit_dir: Path) -> None:
    """Lossless concat via the concat demuxer. No re-encode."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    concat_list = edit_dir / "_concat.txt"
    concat_list.write_text(
        "".join(f"file '{p.resolve()}'\n" for p in segment_paths),
        encoding="utf-8",
    )

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",
        "-movflags", "+faststart",
        str(out_path),
    ]
    print(f"concat → {out_path.name}")
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        concat_list.unlink(missing_ok=True)
    except OSError:
        # WorkBuddy shim may block unlink on Windows; concat still succeeded.
        pass


# -------- Master SRT (Rule 5) ------------------------------------------------


PUNCT_BREAK = set(".,!?;:")


def _srt_timestamp(seconds: float) -> str:
    total_ms = int(round(seconds * 1000))
    h, rem = divmod(total_ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _parse_srt_timestamp(value: str) -> float:
    match = re.fullmatch(r"(\d+):(\d{2}):(\d{2})[,.](\d{3})", value.strip())
    if not match:
        raise ValueError(f"invalid SRT timestamp: {value}")
    hours, minutes, seconds, millis = map(int, match.groups())
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


def _text_units(text: str) -> int:
    """Approximate display width: CJK/full-width glyphs count twice."""
    return sum(1 if ord(char) < 128 else 2 for char in text)


def _split_overwide_chunk(text: str, max_units: int) -> list[str]:
    """Split a punctuation-free overwide phrase into balanced captions."""
    total_units = _text_units(text)
    if total_units <= max_units:
        return [text]

    part_count = math.ceil(total_units / max_units)
    parts: list[str] = []
    remaining = text
    for parts_left in range(part_count, 1, -1):
        target = _text_units(remaining) / parts_left
        running = 0
        best_index = 1
        best_distance = float("inf")
        for index, char in enumerate(remaining[:-1], start=1):
            running += 1 if ord(char) < 128 else 2
            distance = abs(running - target)
            if distance < best_distance:
                best_index = index
                best_distance = distance
        parts.append(remaining[:best_index].strip())
        remaining = remaining[best_index:].strip()
    if remaining:
        parts.append(remaining)
    return [part for part in parts if part]


def _split_single_line_text(text: str, max_units: int) -> list[str]:
    """Split a caption into indivisible punctuation-delimited clauses.

    A phrase ending in a comma is never split across captions. Full stops and
    semicolons act as hard timing boundaries but are omitted from the rendered
    text; this keeps Chinese narration captions conversational rather than
    typeset like prose.
    """
    text = re.sub(r"\s+", " ", text.replace("\\N", " ")).strip()
    if not text:
        return []

    raw_clauses = re.findall(r"[^，,。；;！？!?：:]+[，,。；;！？!?：:]?", text)
    chunks: list[str] = []
    current = ""
    for raw_clause in raw_clauses:
        raw_clause = raw_clause.strip()
        if not raw_clause:
            continue
        terminal = raw_clause[-1] if raw_clause[-1] in "，,。.;；!?！？：:" else None
        clause = raw_clause
        hard_break = terminal is not None and terminal in "。.;；"
        if terminal is not None and terminal in "。.;；":
            clause = raw_clause[:-1].rstrip()

        candidate = f"{current}{clause}"
        if current and _text_units(candidate) > max_units:
            chunks.append(current)
            current = clause
        else:
            current = candidate

        if hard_break and current:
            chunks.append(current)
            current = ""

    if current:
        chunks.append(current)
    # Commas are useful inside a caption, but a caption should not end on a
    # comma/full stop/semicolon. Strip only after layout decisions so an
    # internal comma is preserved when adjacent clauses share one caption.
    cleaned = [chunk.rstrip("，,。.;；").rstrip() for chunk in chunks]
    fitted: list[str] = []
    for chunk in cleaned:
        if chunk:
            fitted.extend(_split_overwide_chunk(chunk, max_units))
    return fitted


def prepare_single_line_srt(
    source: Path,
    output: Path,
    max_units: int = 48,
) -> Path:
    """Write a render-only SRT with one non-overlapping caption at a time.

    Long cues are split at punctuation (or a safe width fallback), and their
    original time window is divided proportionally by visual text width.
    """
    raw_blocks = re.split(r"\r?\n\s*\r?\n", source.read_text(encoding="utf-8-sig").strip())
    entries: list[list[float | str]] = []
    for block in raw_blocks:
        lines = block.splitlines()
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        start_text, end_text = (part.strip() for part in lines[1].split("-->", 1))
        start = _parse_srt_timestamp(start_text)
        end = _parse_srt_timestamp(end_text)
        text = " ".join(line.strip() for line in lines[2:] if line.strip())
        chunks = _split_single_line_text(text, max_units)
        if not chunks or end <= start:
            continue
        weights = [max(1, _text_units(chunk)) for chunk in chunks]
        total_weight = sum(weights)
        cursor = start
        for index, (chunk, weight) in enumerate(zip(chunks, weights)):
            chunk_end = end if index == len(chunks) - 1 else cursor + (end - start) * weight / total_weight
            entries.append([cursor, chunk_end, chunk])
            cursor = chunk_end

    entries.sort(key=lambda entry: float(entry[0]))
    for index in range(len(entries) - 1):
        next_start = float(entries[index + 1][0])
        if float(entries[index][1]) > next_start:
            entries[index][1] = next_start

    lines: list[str] = []
    for index, (start, end, text) in enumerate(entries, start=1):
        if float(end) <= float(start):
            continue
        lines.extend([
            str(index),
            f"{_srt_timestamp(float(start))} --> {_srt_timestamp(float(end))}",
            str(text),
            "",
        ])
    output.write_text("\n".join(lines), encoding="utf-8")
    print(f"single-line subtitles → {output.name} ({len(entries)} cues)")
    return output


def _words_in_range(transcript: dict, t_start: float, t_end: float) -> list[dict]:
    out: list[dict] = []
    for w in transcript.get("words", []):
        if w.get("type") != "word":
            continue
        ws = w.get("start")
        we = w.get("end")
        if ws is None or we is None:
            continue
        if we <= t_start or ws >= t_end:
            continue
        out.append(w)
    return out


def build_master_srt(edl: dict, edit_dir: Path, out_path: Path) -> None:
    """Build an output-timeline SRT from per-source transcripts.

    - 2-word chunks (break on any punctuation in between)
    - UPPERCASE text
    - Output times computed as word.start - segment_start + segment_offset
    """
    transcripts_dir = edit_dir / "transcripts"
    sources = edl["sources"]

    entries: list[tuple[float, float, str]] = []
    seg_offset = 0.0

    durations = _render_durations(edl)
    for r, seg_duration in zip(edl["ranges"], durations):
        src_name = r["source"]
        seg_start = float(r["start"])
        seg_end = seg_start + seg_duration

        tr_path = transcripts_dir / f"{src_name}.json"
        if not tr_path.exists():
            print(f"  no transcript for {src_name}, skipping captions for this segment")
            seg_offset += seg_duration
            continue

        transcript = json.loads(tr_path.read_text(encoding="utf-8-sig"))
        words_in_seg = _words_in_range(transcript, seg_start, seg_end)

        # Group into 2-word chunks, break on punctuation
        chunks: list[list[dict]] = []
        current: list[dict] = []
        for w in words_in_seg:
            text = (w.get("text") or "").strip()
            if not text:
                continue
            current.append(w)
            # Break if the current text ends in punctuation or we hit 2 words
            ends_in_punct = bool(text) and text[-1] in PUNCT_BREAK
            if len(current) >= 2 or ends_in_punct:
                chunks.append(current)
                current = []
        if current:
            chunks.append(current)

        for chunk in chunks:
            local_start = max(seg_start, chunk[0].get("start", seg_start))
            local_end = min(seg_end, chunk[-1].get("end", seg_end))
            out_start = max(0.0, local_start - seg_start) + seg_offset
            out_end = max(0.0, local_end - seg_start) + seg_offset
            if out_end <= out_start:
                out_end = out_start + 0.4
            text = " ".join((w.get("text") or "").strip() for w in chunk)
            text = re.sub(r"\s+", " ", text).strip()
            # Strip trailing punctuation for cleaner uppercase look
            text = text.rstrip(",;:")
            text = text.upper()
            entries.append((out_start, out_end, text))

        seg_offset += seg_duration

    # Sort and write as SRT
    entries.sort(key=lambda e: e[0])
    lines: list[str] = []
    for i, (a, b, t) in enumerate(entries, start=1):
        lines.append(str(i))
        lines.append(f"{_srt_timestamp(a)} --> {_srt_timestamp(b)}")
        lines.append(t)
        lines.append("")
    out_path.write_text("\n".join(lines))
    print(f"master SRT → {out_path.name} ({len(entries)} cues)")


# -------- Loudness normalization (social-ready audio) -----------------------


# Social-media standard: -14 LUFS integrated, -1 dBTP peak, LRA 11 LU.
# Matches YouTube / Instagram / TikTok / X / LinkedIn normalization targets.
LOUDNORM_I = -14.0
LOUDNORM_TP = -1.0
LOUDNORM_LRA = 11.0


def measure_loudness(video_path: Path) -> dict[str, str] | None:
    """Run ffmpeg loudnorm first pass and parse the JSON measurement.

    Returns a dict with measured_i, measured_tp, measured_lra, measured_thresh,
    target_offset, or None if measurement failed.
    """
    filter_str = (
        f"loudnorm=I={LOUDNORM_I}:TP={LOUDNORM_TP}:LRA={LOUDNORM_LRA}:print_format=json"
    )
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-nostats",
        "-i", str(video_path),
        "-af", filter_str,
        "-vn", "-f", "null", "-",
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    # loudnorm prints the JSON to stderr at the end of the run
    stderr = proc.stderr

    # Find the JSON block — loudnorm output contains a `{ ... }` block
    start = stderr.rfind("{")
    end = stderr.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        data = json.loads(stderr[start : end + 1])
    except json.JSONDecodeError:
        return None
    needed = {"input_i", "input_tp", "input_lra", "input_thresh", "target_offset"}
    if not needed.issubset(data.keys()):
        return None
    return data


def apply_loudnorm_two_pass(
    input_path: Path,
    output_path: Path,
    preview: bool = False,
) -> bool:
    """Run two-pass loudnorm on input_path, write normalized copy to output_path.

    Returns True on success, False if measurement failed (caller should fall
    back to copying the input unchanged).

    In preview mode, skips the measurement pass and uses a one-pass approximation
    for speed. Final mode always does the proper two-pass.
    """
    if preview:
        # One-pass approximation — faster, slightly less accurate.
        filter_str = f"loudnorm=I={LOUDNORM_I}:TP={LOUDNORM_TP}:LRA={LOUDNORM_LRA}"
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-nostats",
            "-i", str(input_path),
            "-c:v", "copy",
            "-af", filter_str,
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart",
            str(output_path),
        ]
        print(f"  loudnorm (1-pass preview) → {output_path.name}")
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return True

    # Full two-pass
    print(f"  loudnorm pass 1: measuring {input_path.name}")
    measurement = measure_loudness(input_path)
    if measurement is None:
        print("  loudnorm measurement failed — falling back to 1-pass")
        return apply_loudnorm_two_pass(input_path, output_path, preview=True)

    print(f"    measured: I={measurement['input_i']} LUFS  "
          f"TP={measurement['input_tp']}  LRA={measurement['input_lra']}")

    filter_str = (
        f"loudnorm=I={LOUDNORM_I}:TP={LOUDNORM_TP}:LRA={LOUDNORM_LRA}"
        f":measured_I={measurement['input_i']}"
        f":measured_TP={measurement['input_tp']}"
        f":measured_LRA={measurement['input_lra']}"
        f":measured_thresh={measurement['input_thresh']}"
        f":offset={measurement['target_offset']}"
        f":linear=true"
    )
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-nostats",
        "-i", str(input_path),
        "-c:v", "copy",
        "-af", filter_str,
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart",
        str(output_path),
    ]
    print(f"  loudnorm pass 2: normalizing → {output_path.name}")
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return True


# -------- Narration + source audio mixing ------------------------------------


DEFAULT_AUDIO_MIX = {
    "source_mode": "throughout",
    "source_under_narration": 0.15,
    "source_in_gaps": 1.0,
    "narration_volume": 1.0,
}


def _audio_mix_config(edl_value: dict | None) -> dict:
    config = dict(DEFAULT_AUDIO_MIX)
    if edl_value:
        config.update(edl_value)
    if config["source_mode"] not in {"throughout", "narration_only", "muted"}:
        raise ValueError("audio_mix.source_mode must be throughout, narration_only, or muted")
    for key in ("source_under_narration", "source_in_gaps", "narration_volume"):
        value = float(config[key])
        if not 0 <= value <= 2:
            raise ValueError(f"audio_mix.{key} must be between 0 and 2")
        config[key] = value
    return config


def _load_narration_manifest(narration_path: Path) -> list[dict] | None:
    """Find and load narration_manifest.json next to the narration audio.

    Returns the blocks list if found, None otherwise.
    """
    candidates = [
        narration_path.parent / "narration" / "narration_manifest.json",
        narration_path.parent / "narration_manifest.json",
    ]
    for p in candidates:
        if p.exists():
            try:
                manifest = json.loads(p.read_text(encoding="utf-8"))
                blocks = manifest.get("blocks", [])
                if blocks:
                    return blocks
            except (json.JSONDecodeError, KeyError):
                continue
    return None


def _build_duck_filter(
    base_input: str,
    narration_input: str,
    blocks: list[dict],
    audio_mix: dict,
) -> tuple[str, str]:
    """Build an ffmpeg audio filter that mixes narration over ducked source.

    Source and narration levels come from the EDL ``audio_mix`` object.

    Returns (filter_string, output_label).
    """
    duck_expr_parts: list[str] = []
    for b in blocks:
        audio_dur = float(b.get("audio_duration") or 0)
        if audio_dur <= 0:
            continue
        t0 = float(b["output_start"])
        t1 = t0 + audio_dur
        duck_expr_parts.append(f"between(t,{t0:.3f},{t1:.3f})")

    narration_volume = audio_mix["narration_volume"]
    if not duck_expr_parts:
        return f"{narration_input}volume={narration_volume}[outa]", "[outa]"

    duck_expr = "+".join(duck_expr_parts)
    mode = audio_mix["source_mode"]
    under = 0.0 if mode == "muted" else audio_mix["source_under_narration"]
    gaps = audio_mix["source_in_gaps"] if mode == "throughout" else 0.0
    duck_formula = f"if({duck_expr},{under},{gaps})"

    parts: list[str] = [
        f"{base_input}volume=eval=frame:volume='{duck_formula}'[srcducked]",
        f"{narration_input}aresample=48000,volume={narration_volume}[narr48]",
        f"[srcducked][narr48]amix=inputs=2:duration=longest:dropout_transition=0:weights=1 1[outa]",
    ]
    return ";".join(parts), "[outa]"


# -------- Final compositing (Rule 1 + Rule 4) -------------------------------


def build_final_composite(
    base_path: Path,
    overlays: list[dict],
    subtitles_path: Path | None,
    out_path: Path,
    edit_dir: Path,
    narration_path: Path | None = None,
    letterbox: dict | None = None,
    subtitle_layout: dict | None = None,
    audio_mix: dict | None = None,
    target_duration: float | None = None,
) -> None:
    """Final pass: base → overlays (PTS-shifted) → subtitles LAST → out.

    If narration_path is provided, its audio is mixed with the source track:
    source is ducked during narration windows and plays at full volume in gaps.
    If there are no overlays, no subtitles, and no narration, just copy base.
    """
    has_overlays = bool(overlays)
    has_subs = subtitles_path is not None and subtitles_path.exists()
    has_narration = narration_path is not None

    if not has_overlays and not has_subs and not has_narration:
        run(["ffmpeg", "-y", "-i", str(base_path), "-c", "copy", str(out_path)], quiet=True)
        return

    narration_blocks: list[dict] | None = None
    if has_narration:
        narration_blocks = _load_narration_manifest(narration_path)

    use_audio_mix = has_narration and narration_blocks is not None

    if not has_overlays and not has_subs and has_narration and not use_audio_mix:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(base_path), "-i", str(narration_path),
            "-map", "0:v", "-map", "1:a",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart",
            str(out_path),
        ]
        print(f"muxing narration → {out_path.name}")
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return

    inputs: list[str] = ["-i", str(base_path)]
    for ov in overlays:
        ov_path = resolve_path(ov["file"], edit_dir)
        inputs += ["-i", str(ov_path)]

    narration_input_idx: int | None = None
    if has_narration:
        narration_input_idx = 1 + len(overlays)
        inputs += ["-i", str(narration_path)]

    filter_parts: list[str] = []
    for idx, ov in enumerate(overlays, start=1):
        t = float(ov["start_in_output"])
        filter_parts.append(f"[{idx}:v]setpts=PTS-STARTPTS+{t}/TB[a{idx}]")

    current = "[0:v]"
    for idx, ov in enumerate(overlays, start=1):
        t = float(ov["start_in_output"])
        dur = float(ov["duration"])
        end = t + dur
        next_label = f"[v{idx}]"
        filter_parts.append(
            f"{current}[a{idx}]overlay=enable='between(t,{t:.3f},{end:.3f})'{next_label}"
        )
        current = next_label

    # Subtitles LAST — Rule 1
    if has_subs:
        subs_abs = str(subtitles_path.resolve()).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
        if letterbox:
            max_lines = int((subtitle_layout or {}).get("max_lines", 2))
            sub_style = _letterbox_sub_style(letterbox.get("bottom", 100), max_lines=max_lines)
        else:
            sub_style = SUB_FORCE_STYLE
        filter_parts.append(
            f"{current}subtitles='{subs_abs}':force_style='{sub_style}'[outv]"
        )
        out_label = "[outv]"
    else:
        if has_overlays:
            filter_parts.append(f"{current}null[outv]")
            out_label = "[outv]"
        else:
            out_label = "[0:v]"

    if use_audio_mix:
        audio_filter, audio_out = _build_duck_filter(
            f"[0:a]", f"[{narration_input_idx}:a]", narration_blocks,
            audio_mix or DEFAULT_AUDIO_MIX,
        )
        filter_parts.append(audio_filter)
        audio_map = audio_out
        audio_codec = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
        mix = audio_mix or DEFAULT_AUDIO_MIX
        print(
            "  audio: mixing narration + source "
            f"({len(narration_blocks)} sections; under={mix['source_under_narration']}, "
            f"gaps={mix['source_in_gaps']})"
        )
    elif narration_input_idx is not None:
        audio_map = f"{narration_input_idx}:a"
        audio_codec = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    else:
        audio_map = "0:a"
        audio_codec = ["-c:a", "copy"]

    needs_filter_complex = bool(filter_parts)

    if needs_filter_complex:
        # When audio comes from the filter graph, video must too
        if out_label == "[0:v]" and use_audio_mix:
            filter_parts.insert(0, "[0:v]null[outv]")
            out_label = "[outv]"

        filter_complex = ";".join(filter_parts)
        cmd = [
            "ffmpeg", "-y",
            *inputs,
            "-filter_complex", filter_complex,
            "-map", out_label,
            "-map", audio_map,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-pix_fmt", "yuv420p",
            *audio_codec,
            "-movflags", "+faststart",
            *(["-t", f"{target_duration:.3f}"] if target_duration else []),
            str(out_path),
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            *inputs,
            "-map", "0:v",
            "-map", audio_map,
            "-c:v", "copy",
            *audio_codec,
            "-movflags", "+faststart",
            *(["-t", f"{target_duration:.3f}"] if target_duration else []),
            str(out_path),
        ]

    print(f"compositing → {out_path.name}")
    print(f"  overlays: {len(overlays)}, subtitles: {'yes' if has_subs else 'no'}, "
          f"narration: {'mix' if use_audio_mix else ('replace' if narration_input_idx else 'no')}")
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


# -------- Main ---------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description="Render a video from an EDL")
    ap.add_argument("edl", type=Path, help="Path to edl.json")
    ap.add_argument("-o", "--output", type=Path, required=True, help="Output video path")
    ap.add_argument(
        "--preview",
        action="store_true",
        help="Preview mode: 1080p, medium, CRF 22 — evaluable for QC, faster than final.",
    )
    ap.add_argument(
        "--draft",
        action="store_true",
        help="Draft mode: 720p, ultrafast, CRF 28 — cut-point verification only.",
    )
    ap.add_argument(
        "--build-subtitles",
        action="store_true",
        help="Build master.srt from transcripts + EDL offsets before compositing",
    )
    ap.add_argument(
        "--no-subtitles",
        action="store_true",
        help="Skip subtitles even if the EDL references one",
    )
    ap.add_argument(
        "--no-loudnorm",
        action="store_true",
        help="Skip audio loudness normalization. Default is on (-14 LUFS, -1 dBTP, LRA 11).",
    )
    ap.add_argument(
        "--narration", type=Path, default=None,
        help="Narration audio file to replace source audio (overrides EDL narration field)",
    )
    args = ap.parse_args()

    edl_path = args.edl.resolve()
    if not edl_path.exists():
        sys.exit(f"edl not found: {edl_path}")

    edl = json.loads(edl_path.read_text(encoding="utf-8-sig"))
    edit_dir = edl_path.parent
    out_path = args.output.resolve()

    # 0. Layout + audio policy. A full-width subtitle bar is part of the base
    # render so the subtitle text can remain borderless and be composited last.
    subtitle_layout = edl.get("subtitle_layout") or {}
    letterbox = dict(edl.get("letterbox") or {})
    if subtitle_layout.get("bar_full_width"):
        if subtitle_layout.get("bar_position", "bottom") != "bottom":
            sys.exit("subtitle_layout.bar_position currently supports only 'bottom'")
        letterbox["bottom"] = int(subtitle_layout.get("bar_height", 84))
    if not letterbox:
        letterbox = None
    if letterbox:
        print(f"letterbox: top={letterbox.get('top', 0)}px  bottom={letterbox.get('bottom', 0)}px")
    try:
        audio_mix = _audio_mix_config(edl.get("audio_mix"))
    except (TypeError, ValueError) as exc:
        sys.exit(str(exc))

    # 1. Extract per-segment (auto-grade per range if EDL grade is "auto")
    segment_paths = extract_all_segments(
        edl, edit_dir, preview=args.preview, draft=args.draft, letterbox=letterbox
    )

    # 2. Concat → base
    if args.draft:
        base_name = "base_draft.mp4"
    elif args.preview:
        base_name = "base_preview.mp4"
    else:
        base_name = "base.mp4"
    base_path = edit_dir / base_name
    concat_segments(segment_paths, base_path, edit_dir)

    # 3. Subtitles: build if requested, resolve final path
    subs_path: Path | None = None
    if not args.no_subtitles:
        if args.build_subtitles:
            subs_path = edit_dir / "master.srt"
            build_master_srt(edl, edit_dir, subs_path)
        elif edl.get("subtitles"):
            subs_path = resolve_path(edl["subtitles"], edit_dir)
            if not subs_path.exists():
                sys.exit(f"subtitles path in EDL does not exist: {subs_path}")
        if subs_path and int(subtitle_layout.get("max_lines", 2)) == 1:
            max_units = int(subtitle_layout.get("max_units", 48))
            subs_path = prepare_single_line_srt(
                subs_path,
                edit_dir / f"{subs_path.stem}.single-line.srt",
                max_units=max_units,
            )

    # 4. Resolve narration audio (CLI override > EDL field)
    narration_path: Path | None = None
    if args.narration:
        narration_path = args.narration.resolve()
    elif edl.get("narration"):
        narration_path = resolve_path(edl["narration"], edit_dir)
    if narration_path and not narration_path.exists():
        sys.exit(f"narration audio not found: {narration_path}")

    # 5. Composite (overlays + subtitles LAST) → intermediate (pre-loudnorm) path
    overlays = edl.get("overlays") or []
    for index, overlay in enumerate(overlays, start=1):
        if not isinstance(overlay, dict) or "file" not in overlay:
            sys.exit(f"overlay #{index} must be an object with a 'file' path")
        overlay_path = resolve_path(str(overlay["file"]), edit_dir)
        if not overlay_path.exists():
            sys.exit(f"overlay #{index} file does not exist: {overlay_path}")
        if "start_in_output" not in overlay or "duration" not in overlay:
            sys.exit(f"overlay #{index} needs start_in_output and duration")
    target_duration = float(edl["total_duration_s"]) if edl.get("total_duration_s") else None
    if args.no_loudnorm:
        build_final_composite(
            base_path, overlays, subs_path, out_path, edit_dir, narration_path,
            letterbox, subtitle_layout, audio_mix, target_duration,
        )
    else:
        tmp_composite = out_path.with_suffix(".prenorm.mp4")
        build_final_composite(
            base_path, overlays, subs_path, tmp_composite, edit_dir, narration_path,
            letterbox, subtitle_layout, audio_mix, target_duration,
        )
        print("loudness normalization → social-ready (-14 LUFS / -1 dBTP / LRA 11)")
        apply_loudnorm_two_pass(tmp_composite, out_path, preview=args.draft)
        try:
            tmp_composite.unlink(missing_ok=True)
        except OSError:
            pass

    size_mb = out_path.stat().st_size / (1024 * 1024)
    print(f"\ndone: {out_path} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
