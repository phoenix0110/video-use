# Transcription backends — Scribe vs Whisper

video-use supports two transcription backends. Pick whichever fits your
situation. Both produce JSON in the same shape, so the downstream helpers
(`pack_transcripts.py`, `timeline_view.py`, `render.py`) need no changes.

| | ElevenLabs Scribe (default `transcribe.py`) | openai-whisper (`transcribe_whisper.py`) |
|---|---|---|
| **Cost** | Pay per minute | Free (local compute only) |
| **Privacy** | Audio leaves your machine | Audio never leaves your machine |
| **API key required** | Yes (`ELEVENLABS_API_KEY` in `.env`) | No |
| **Word-level timestamps** | ✅ Sub-second accuracy | ✅ Drift ~50-100ms (absorbed by 30-200ms cut padding) |
| **Speaker diarization** | ✅ Built-in | ❌ No (multi-speaker editing needs a separate step) |
| **Audio events** `(laughs)` `(sighs)` | ✅ Built-in | ❌ No (silence-only fallback) |
| **Verbatim fillers** (`umm`, `uh`) | ✅ Preserved | ✅ Preserved |
| **First-run latency** | None | ~1-3 min (downloads ~460MB for `small`) |
| **CPU speed (small, 1 min audio)** | ~10s (network bound) | ~30-60s |
| **GPU speed (small, 1 min audio)** | Same | ~2-5s if `device=cuda` available |

## Choosing

- **Default for most users**: `transcribe_whisper.py`. Free, private, good enough
  for talking heads / tutorials / vlogs / interviews where you can see who's
  speaking.
- **Reach for `transcribe.py`** when: you have an ElevenLabs key already, you
  want audio events (`(laughs)`) or speaker diarization, and budget is not a
  concern.

## Quickstart: whisper (recommended)

The whisper backend has no setup beyond installing the Python package and
having ffmpeg available.

```bash
# One-time install (already done in this environment)
pip install openai-whisper

# Per-video invocation
python helpers/transcribe_whisper.py path/to/video.mp4 --language zh

# Batch (mirror of transcribe_batch.py)
# Just loop over your videos:
for v in path/to/videos/*.mp4; do
  python helpers/transcribe_whisper.py "$v" --language zh
done
```

Tunables:

| flag | default | what it does |
|---|---|---|
| `--model` | `small` | `tiny`/`base`/`small`/`medium`/`large-v3`. Larger = slower + more accurate. |
| `--language` | auto-detect | `zh`, `en`, etc. Speeds up inference and avoids hallucination. |
| `--device` | auto | `auto`/`cpu`/`cuda`. CUDA only if you have a real GPU. |
| `--edit-dir` | `<video_parent>/edit` | Where transcripts/ subdir lives. |

## Switching to Scribe (if you decide to)

```bash
# 1. Get an API key from https://elevenlabs.io/app/settings/api-keys
# 2. Save it (never echoed back, never committed)
printf 'ELEVENLABS_API_KEY=%s\n' "your-key-here" > ~/Developer/video-use/.env
chmod 600 ~/Developer/video-use/.env

# 3. Use the original helper
python helpers/transcribe.py path/to/video.mp4
```

## Key implementation notes

- Both helpers write to `<edit_dir>/transcripts/<video_stem>.json`.
- Both are cached — re-running on the same source skips the API/Whisper call.
- Either backend can be re-run after a manual edit to the JSON, but `_audio_event`
  and `speaker_id` fields will only be present with the Scribe backend.
- If you mix backends across a project (some files Scribe, some Whisper), the
  editor sub-agent's notes per-take still work — both words arrays are
  subscripts of the same overall pipeline.

## Mixing and matching

If you have some ElevenLabs credits and want premium diarization for an
interview but cheap Whisper for the takes:

```bash
# Premium interview
python helpers/transcribe.py interviews/A_001.mp4

# Cheap B-roll takes
python helpers/transcribe_whisper.py takes/B_*.mp4
```

Both produce `<edit>/transcripts/*.json` in the same shape; the editor reads
them uniformly.
