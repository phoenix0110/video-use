---
name: video-use
description: Edit any video by conversation. Transcribe, cut, color grade, generate overlay animations, burn subtitles — for talking heads, montages, tutorials, travel, interviews. No presets, no menus. Ask questions, confirm the plan, execute, iterate, persist. Production-correctness rules are hard; everything else is artistic freedom.
---

# Video Use

## Principle

1. **LLM reasons from raw transcript + on-demand visuals.** The only derived artifact that earns its keep is a packed phrase-level transcript (`takes_packed.md`). Everything else — filler tagging, retake detection, shot classification, emphasis scoring — you derive at decision time.
2. **Audio is primary, visuals follow.** Cut candidates come from speech boundaries and silence gaps. Drill into visuals only at decision points.
3. **Ask → confirm → execute → iterate → persist.** Never touch the cut until the user has confirmed the strategy in plain English.
4. **Generalize.** Do not assume what kind of video this is. Look at the material, ask the user, then edit.
5. **Artistic freedom is the default.** Every specific value, preset, font, color, duration, pitch structure, and technique in this document is a *worked example* from one proven video — not a mandate. Read them to understand what's possible and why each worked. Then make your own taste calls based on what the material actually is and what the user actually wants. **The only things you MUST do are in the Hard Rules section below.** Everything else is yours.
6. **Invent freely.** If the material calls for a technique not described here — split-screen, picture-in-picture, lower-third identity cards, reaction cuts, speed ramps, freeze frames, crossfades, match cuts, L-cuts, J-cuts, speed ramps over breath, whatever — build it. The helpers are ffmpeg and PIL. They can do anything the format supports. Do not wait for permission.
7. **Verify your own output before showing it to the user.** If you wouldn't ship it, don't present it.

## Hard Rules (production correctness — non-negotiable)

These are the things where deviation produces silent failures or broken output. They are not taste, they are correctness. Memorize them.

1. **`ffmpeg` and `ffprobe` must be on PATH.** Before running any helper, verify with `ffmpeg -version`. If missing, install it (`winget install ffmpeg` / `brew install ffmpeg` / `apt install ffmpeg`) and confirm it's accessible. Do not proceed without them.
2. **Subtitles are applied LAST in the filter chain**, after every overlay. Otherwise overlays hide captions. Silent failure.
3. **Per-segment extract → lossless `-c copy` concat**, not single-pass filtergraph. Otherwise you double-encode every segment when overlays are added.
4. **30ms audio fades at every segment boundary** (`afade=t=in:st=0:d=0.03,afade=t=out:st={dur-0.03}:d=0.03`). Otherwise audible pops at every cut.
5. **Overlays use `setpts=PTS-STARTPTS+T/TB`** to shift the overlay's frame 0 to its window start. Otherwise you see the middle of the animation during the overlay window.
6. **Master SRT uses output-timeline offsets**: `output_time = word.start - segment_start + segment_offset`. Otherwise captions misalign after segment concat.
7. **Never cut inside a word.** Snap every cut edge to a word boundary from the Scribe transcript.
8. **Pad every cut edge.** Working window: 30–200ms. Scribe timestamps drift 50–100ms — padding absorbs the drift. Tighter for fast-paced, looser for cinematic.
9. **Word-level verbatim ASR only.** Never SRT/phrase mode (loses sub-second gap data). Never normalized fillers (loses editorial signal).
10. **Cache transcripts per source.** Never re-transcribe unless the source file itself changed.
11. **Parallel sub-agents for multiple animations.** Never sequential. Spawn N at once via the `Agent` tool; total wall time ≈ slowest one.
12. **Strategy confirmation before execution.** Never touch the cut until the user has approved the plain-English plan.
12. **All session outputs in `<videos_dir>/edit/`.** Never write inside the `video-use/` project directory.
13. **Quantize multi-segment timelines to delivery frames.** Independent fractional-duration extracts round per clip; without frame allocation, dozens of cuts accumulate picture/narration drift. `render.py` distributes the EDL total across 24fps frame boundaries and caps the composite at `total_duration_s`.

Everything else in this document is a worked example. Deviate whenever the material calls for it.

## Directory layout

The skill lives in `video-use/`. User footage lives wherever they put it. All session outputs go into `<videos_dir>/edit/`.

```
<videos_dir>/
├── <source files, untouched>
└── edit/
    ├── project.md               ← memory; appended every session
    ├── takes_packed.md          ← phrase-level transcripts, the LLM's primary reading view
    ├── edl.json                 ← cut decisions
    ├── transcripts/<name>.json  ← cached raw Scribe JSON
    ├── animations/slot_<id>/    ← per-animation source + render + reasoning
    ├── clips_graded/            ← per-segment extracts with grade + fades
    ├── master.srt               ← output-timeline subtitles
    ├── downloads/               ← yt-dlp outputs
    ├── verify/                  ← debug frames / timeline PNGs
    ├── preview.mp4
    └── final.mp4
```

## Setup

First-time install lives in `install.md` (clone, deps, ffmpeg, skill registration, API key). Don't re-run it every session; on cold start just verify:

- `ELEVENLABS_API_KEY` resolves — either in the environment or in `.env` at the video-use repo root. If missing, ask the user to paste one and write it to `.env` (never to the user's `<videos_dir>`).
- `ffmpeg` + `ffprobe` on PATH.
- Python deps installed (`uv sync` or `pip install -e .` inside the repo).
- Node.js + npm available if the session needs HyperFrames or Remotion slots. HyperFrames currently requires Node.js 22+.
- `edge-tts` installed (`pip install edge-tts`) — needed for `tts.py` narration generation. No API key required.
- `yt-dlp`, HyperFrames, Remotion, Manim installed only on first use.
- First-use animation setup happens inside the slot directory, never at the video-use repo root. HyperFrames can be invoked with `npx --yes hyperframes ...`; Remotion can be scaffolded with `npx create-video@latest` or installed as a project-local dependency before using its `remotion render` command.
- This skill vendors `skills/manim-video/`. Read its SKILL.md when building a Manim slot.

Helpers (`helpers/transcribe.py`, `helpers/render.py`, etc.) live alongside this SKILL.md. Resolve their paths relative to the directory containing this file — the skill is typically symlinked at `~/.claude/skills/video-use/` or `~/.codex/skills/video-use/`.

## Helpers

- **`transcribe.py <video>`** — single-file Scribe call. `--num-speakers N` optional. Cached.
- **`transcribe_whisper.py <video>`** — local openai-whisper alternative (free, no API key, audio stays local). Same JSON shape as `transcribe.py`. No speaker diarization or audio events.
- **`transcribe_batch.py <videos_dir>`** — 4-worker parallel transcription. Use for multi-take.
- **`pack_transcripts.py --edit-dir <dir>`** — `transcripts/*.json` → `takes_packed.md` (phrase-level, break on silence ≥ 0.5s).
- **`timeline_view.py <video> <start> <end>`** — filmstrip + waveform PNG. On-demand visual drill-down. **Not a scan tool** — use it at decision points, not constantly.
- **`render.py <edl.json> -o <out>`** — per-segment extract → concat → overlays (PTS-shifted) → subtitles LAST. `--preview` for 720p fast. `--build-subtitles` to generate master.srt inline.
- **`grade.py <in> -o <out>`** — ffmpeg filter chain grade. Presets + `--filter '<raw>'` for custom.
- **`tts.py <text> -o <out>`** — Edge TTS (free, no API key). Single text or `--from-script recap-script.md -o dir/` for batch narration. In recap mode, `--write-subtitles` writes per-section SRTs **and** an output-timeline `master.srt`; it also assembles a slot-aligned `full_narration.m4a`. `--list-voices` to browse. Default voice: `zh-CN-YunjianNeural`.
- **`text_overlay.py --manifest <json>`** — renders a manifest of stylized on-screen text to full-frame, transparent PNG assets with Pillow. `render.py` composites those assets as overlays.

For animations, create `<edit>/animations/slot_<id>/` with `Bash` and spawn a sub-agent via the `Agent` tool.

## The process

1. **Inventory.** `ffprobe` every source. `transcribe_batch.py` on the directory. `pack_transcripts.py` to produce `takes_packed.md`. Sample one or two `timeline_view`s for a visual first impression.
2. **Pre-scan for problems.** One pass over `takes_packed.md` to note verbal slips, obvious mis-speaks, or phrasings to avoid. Plain list, feed into the editor brief.
3. **Converse.** Describe what you see in plain English. Ask questions *shaped by the material*. Collect: content type, target length/aspect, aesthetic/brand direction, pacing feel, must-preserve moments, must-cut moments, animation and grade preferences, subtitle needs. Do not use a fixed checklist — the right questions are different every time.
4. **Propose strategy.** 4–8 sentences: shape, take choices, cut direction, animation plan, grade direction, subtitle style, length estimate. **Wait for confirmation.**
5. **Execute.** Produce `edl.json` via the editor sub-agent brief. Drill into `timeline_view` at ambiguous moments. Build animations in parallel sub-agents. Apply grade per-segment. Compose via `render.py`.
6. **Preview.** `render.py --preview`.
7. **Self-eval (before showing the user).** Run `timeline_view` on the **rendered output** (not the sources) at every cut boundary (±1.5s window). Check each image for:
   - Visual discontinuity / flash / jump at the cut
   - Waveform spike at the boundary (audio pop that slipped past the 30ms fade)
   - Subtitle hidden behind an overlay (Rule 1 violation)
   - Overlay misaligned or showing wrong frames (Rule 4 violation)

   Also sample: first 2s, last 2s, and 2–3 mid-points — check grade consistency, subtitle readability, overall coherence. Run `ffprobe` on the output to verify duration matches the EDL expectation.

   If anything fails: fix → re-render → re-eval. **Cap at 3 self-eval passes** — if issues remain after 3, flag them to the user rather than looping forever. Only present the preview once the self-eval passes.
8. **Iterate + persist.** Natural-language feedback, re-plan, re-render. Never re-transcribe. Final render on confirmation. Append to `project.md`.

## Cut craft (techniques)

- **Audio-first.** Candidate cuts from word boundaries and silence gaps.
- **Preserve peaks.** Laughs, punchlines, emphasis beats. Extend past punchlines to include reactions — the laugh IS the beat.
- **Speaker handoffs** benefit from air between utterances. Common values: 400–600ms. Less for fast-paced, more for cinematic. Taste call.
- **Audio events as signals.** `(laughs)`, `(sighs)`, `(applause)` mark beats. Extend past them.
- **Silence gaps are cut candidates.** Silences ≥400ms are usually the cleanest. 150–400ms phrase boundaries are usable with a visual check. <150ms is unsafe (mid-phrase).
- **Example cut padding** (the launch video shipped with this): 50ms before the first kept word, 80ms after the last. Tighter for montage energy, looser for documentary. Stay in the 30–200ms working window (Hard Rule 7).
- **Never reason audio and video independently.** Every cut must work on both tracks.

## The packed transcript (primary reading view)

`pack_transcripts.py` reads all `transcripts/*.json` and produces one markdown file where each take is a list of phrase-level lines, each prefixed with its `[start-end]` time range. Phrases break on any silence ≥ 0.5s OR speaker change. This is the artifact the editor sub-agent reads to pick cuts — it gives word-boundary precision from text alone at 1/10 the tokens of raw JSON.

Example line:
```
## C0103  (duration: 43.0s, 8 phrases)
  [002.52-005.36] S0 Ninety percent of what a web agent does is completely wasted.
  [006.08-006.74] S0 We fixed this.
```

## Editor sub-agent brief (for multi-take selection)

When the task is "pick the best take of each beat across many clips," spawn a dedicated sub-agent with a brief shaped like this. The structure is load-bearing; the pitch-shape example is not.

```
You are editing a <type> video. Pick the best take of each beat and 
assemble them chronologically by beat, not by source clip order.

INPUTS:
  - takes_packed.md (time-annotated phrase-level transcripts of all takes)
  - Product/narrative context: <2 sentences from the user>
  - Speaker(s): <name, role, delivery style note>
  - Expected structure: <pick an archetype or invent one>
  - Verbal slips to avoid: <list from the pre-scan pass>
  - Target runtime: <seconds>

Common structural archetypes (pick, adapt, or invent):
  - Tech launch / demo:   HOOK → PROBLEM → SOLUTION → BENEFIT → EXAMPLE → CTA
  - Tutorial:             INTRO → SETUP → STEPS → GOTCHAS → RECAP
  - Interview:            (QUESTION → ANSWER → FOLLOWUP) repeat
  - Travel / event:       ARRIVAL → HIGHLIGHTS → QUIET MOMENTS → DEPARTURE
  - Documentary:          THESIS → EVIDENCE → COUNTERPOINT → CONCLUSION
  - Music / performance:  INTRO → VERSE → CHORUS → BRIDGE → OUTRO
  - Or invent your own.

RULES:
  - Start/end times must fall on word boundaries from the transcript.
  - Pad cut boundaries (working window 30–200ms).
  - Prefer silences ≥ 400ms as cut targets.
  - Unavoidable slips are kept if no better take exists. Note them in "reason".
  - If over budget, revise: drop a beat or trim tails. Report total and self-correct.

OUTPUT (JSON array, no prose):
  [{"source": "C0103", "start": 2.42, "end": 6.85, "beat": "HOOK",
    "quote": "...", "reason": "..."}, ...]

Return the final EDL and a one-line total runtime check.
```

## Color grade (when requested)

Your job is to **reason about the image**, not apply a preset. Look at a frame (via `timeline_view`), decide what's wrong, adjust one thing, look again.

Mental model is ASC CDL. Per channel: `out = (in * slope + offset) ** power`, then global saturation. `slope` → highlights, `offset` → shadows, `power` → midtones.

**Example filter chains** (`grade.py` has `--list-presets`; use them as starting points or mix your own):

- **`warm_cinematic`** — retro/technical, subtle teal/orange split, desaturated. Shipped in a real launch video. Safe for talking heads.
- **`neutral_punch`** — minimal corrective: contrast bump + gentle S-curve. No hue shifts.
- **`none`** — straight copy. Default when the user hasn't asked.

For anything else — portraiture, nature, product, music video, documentary — invent your own chain. `grade.py --filter '<raw ffmpeg>'` accepts any filter string.

Hard rules: apply **per-segment during extraction** (not post-concat, which re-encodes twice). Never go aggressive without testing skin tones.

## Subtitles (when requested)

Subtitles have three dimensions worth reasoning about: **chunking** (1/2/3/sentence per line), **case** (UPPER/Title/Natural), and **placement** (margin from bottom). The right combo depends on content. In one-line Chinese narration, treat each comma-delimited phrase as an indivisible unit: pack whole phrases into a cue, never split a phrase across adjacent cues. Omit prose-style full stops and semicolons from burned captions unless the user asks to retain them.

**Worked styles** — pick, adapt, or invent:

**`bold-overlay`** — short-form tech launch, fast-paced social. 2-word chunks, UPPERCASE, break on punctuation, Helvetica 18 Bold, white-on-outline, `MarginV=35`. `render.py` ships with this as `SUB_FORCE_STYLE`.

```
FontName=Helvetica,FontSize=18,Bold=1,
PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H00000000,
BorderStyle=1,Outline=2,Shadow=0,
Alignment=2,MarginV=35
```

**`natural-sentence`** (if you invent this mode) — narrative, documentary, education. 4–7 word chunks, sentence case, break on natural pauses, `MarginV=60–80`, larger font for readability, slightly wider max-width. No shipped force_style — design one if you need it.

Invent a third style if neither fits. Hard rules: subtitles LAST (Rule 1), output-timeline offsets (Rule 5).

## Animations (when requested)

Animations match the content and the brand. **Get the palette, font, and visual language from the conversation** — never assume a default. If the user hasn't told you, propose a palette in the strategy phase and wait for confirmation before building anything.

**Tool options:**

Pick the engine per animation slot. Do not default to Remotion just because the animation is web-adjacent.

- **HyperFrames** — Browser-native HTML/CSS/GSAP video compositions: product UI motion, website-to-video or mockup-to-video captures, kinetic typography, landing-page/storyboard promos, data-driven UI states, transparent WebM overlays, and clips that need deterministic frame capture plus HyperFrames lint/validate/render checks. Best when the animation should be authored and verified like a web composition instead of a React component tree.
- **Remotion** — React/CSS compositions with component state, reusable React primitives, or an existing Remotion brand system. Best when the user specifically asks for React/Remotion or when React composition is the simpler authoring model.
- **Manim** — formal diagrams, state machines, equation derivations, graph morphs. Read `skills/manim-video/SKILL.md` and its references for depth.
- **PIL + PNG sequence + ffmpeg** — simple overlay cards: counters, typewriter text, single bar reveals, progressive draws. Fast to iterate, any aesthetic you want. The launch video used this.

For HyperFrames slots, scaffold the slot inside `edit/animations/slot_<id>/` with `npx --yes hyperframes init . --example blank --non-interactive --skip-skills`, build the HTML composition there, run the HyperFrames checks that fit the slot (`lint`, `validate`, and a draft render when practical), then produce the final overlay video with `npx --yes hyperframes render . -o render.mp4` or `--format webm -o render.webm` when alpha is required. Point the EDL overlay `file` at the actual rendered path.

For Remotion slots, keep the Remotion project isolated inside the same slot directory, scaffold with `npx create-video@latest` or install Remotion locally there, render the composition to `render.mp4` with the project-local `remotion render` command, and verify duration and dimensions with `ffprobe`.

None is mandatory. Invent hybrids if useful (e.g., PIL background with a HyperFrames or Remotion layer on top).

**Duration rules of thumb, context-dependent:**

- **Sync-to-narration explanations.** A viewer needs to parse the content at 1×. Rough floor 3s, typical 5–7s for simple cards, 8–14s for complex diagrams. The launch video shipped at 5–7s per simple card.
- **Beat-synced accents** (music video, fast montage). 0.5–2s is fine — they're visual accents, not information. The "readable at 1×" rule becomes *"recognizable at 1×"*, not *"fully parseable."*
- **Hold the final frame ≥ 1s** before the cut (universal).
- **Over voiceover:** total duration ≥ `narration_length + 1s` (universal).
- **Never parallel-reveal independent elements** — the eye can't track two new things at once. One thing, pause, next thing.

**Animation payoff timing (rule for sync-to-narration):** get the payoff word's timestamp. Start the overlay `reveal_duration` seconds earlier so the landing frame coincides with the spoken payoff word. Without this sync the animation feels disconnected.

**Easing** (universal — never `linear`, it looks robotic):

```python
def ease_out_cubic(t):    return 1 - (1 - t) ** 3
def ease_in_out_cubic(t):
    if t < 0.5: return 4 * t ** 3
    return 1 - (-2 * t + 2) ** 3 / 2
```

`ease_out_cubic` for single reveals (slow landing). `ease_in_out_cubic` for continuous draws.

**Typing text anchor trick:** center on the FULL string's width, not the partial-string width — otherwise text slides left during reveal.

**Example palette** (the launch video — one aesthetic among infinite):
- Background `(10, 10, 10)` near-black
- Accent `#FF5A00` / `(255, 90, 0)` orange
- Labels `(110, 110, 110)` dim gray
- Font: Menlo Bold at `/System/Library/Fonts/Menlo.ttc` (index 1)
- ≤ 2 accent colors, ~40% empty space, minimal chrome
- Result: terminal / retro tech feel

This is one style. If the brand is warm and serif, use that. If it's colorful and playful, use that. If the user handed you a style guide, follow it. If they didn't, propose one and confirm.

**Parallel sub-agent brief** — each animation is one sub-agent spawned via the `Agent` tool. Each prompt is self-contained (sub-agents have no parent context). Include:

1. One-sentence goal: *"Build ONE animation: [spec]. Nothing else."*
2. Absolute output path (`<edit>/animations/slot_<id>/render.mp4`)
3. Exact technical spec: resolution, fps, codec, pix_fmt, CRF, duration
4. Style palette as concrete values (RGB tuples, hex, or reference to a design system)
5. Font path with index
6. Frame-by-frame timeline (what happens when, with easing)
7. Anti-list ("no chrome, no extras, no titles unless specified")
8. Code pattern reference (copy helpers inline, don't import across slots)
9. Deliverable checklist (script, render, verify duration via ffprobe, report)
10. **"Do not ask questions. If anything is ambiguous, pick the most obvious interpretation and proceed."**

One sub-agent = one file (unique filenames, parallel agents don't overwrite each other).

## Output spec

Match the source unless the user asked for something specific. Common targets: `1920×1080@24` cinematic, `1920×1080@30` screen content, `1080×1920@30` vertical social, `3840×2160@24` 4K cinema, `1080×1080@30` square. `render.py` defaults the scale to 1080p from any source; pass `--filter` or edit the extract command for other targets. Worth asking the user which delivery format matters.

## EDL format

```json
{
  "version": 2,
  "sources": {"C0103": "/abs/path/C0103.MP4", "C0108": "/abs/path/C0108.MP4"},
  "ranges": [
    {"source": "C0103", "start": 2.42, "duration": 4.43,
     "cue": 1, "beat": "HOOK", "source_quote": "...", "reason": "Cleanest delivery, stops before slip at 38.46."},
    {"source": "C0108", "start": 14.30, "end": 28.90,
     "beat": "SOLUTION", "quote": "...", "reason": "Only take without the false start."}
  ],
  "grade": "warm_cinematic",
  "overlays": [
    {"file": "animations/slot_1/render.mp4", "start_in_output": 0.0, "duration": 5.0}
  ],
  "subtitles": "master.srt",
  "subtitle_layout": {
    "max_lines": 1,
    "bar_height": 84,
    "bar_position": "bottom",
    "bar_full_width": true,
    "max_units": 48
  },
  "audio_mix": {
    "source_mode": "throughout",
    "source_under_narration": 0.08,
    "source_in_gaps": 0.30,
    "narration_volume": 1.0
  },
  "total_duration_s": 87.4
}
```

`ranges` accepts either `start` + `end` or `start` + `duration`. For narration-driven recaps, add `cue`, `beat`, and the supporting `source_quote` from the source transcript so every picture choice is auditable. `grade` is a preset name or raw ffmpeg filter. `overlays` are rendered animation clips. `subtitles` is optional and applied LAST. `subtitle_layout.max_lines: 1` makes `render.py` create a render-only, non-overlapping SRT whose long cues are split into consecutive one-line captions; `bar_full_width` draws one fixed bottom bar rather than a text-sized box. `audio_mix` controls original source level under narration and in narration gaps (`0.0`–`2.0` linear gain). `source_mode` is `throughout`, `narration_only`, or `muted`.

## Memory — `project.md`

Append one section per session at `<edit>/project.md`:

```markdown
## Session N — YYYY-MM-DD

**Strategy:** one paragraph describing the approach
**Decisions:** take choices, cuts, grades, animations + why
**Reasoning log:** one-line rationale for non-obvious decisions
**Outstanding:** deferred items
```

On startup, read `project.md` if it exists and summarize the last session in one sentence before asking whether to continue.

## Recap-script workflow (narration-driven editing)

When the input is a **recap script** (a pre-written narration + source footage references, typically generated by another skill), the editing process differs from the standard transcript-first workflow. The recap script is the blueprint — it defines what to say, when to say it, and where to pull footage from.

### Recap-script anatomy

Section header format: `### <start>–<end>｜<title>`. Each section may contain `**画面**` (footage) and `**旁白**` (narration).

Three kinds of timestamps coexist — never confuse them:

| Element | Format | Meaning |
|---------|--------|---------|
| Section header | `0:00–0:15` | **Output timeline** — target duration in the final video |
| `**画面**` bracket | `[MM:SS–MM:SS]` | **Source timeline** — where to pull footage from the original |
| Sub-section bracket | `[MM:SS–MM:SS]` | **Source timeline, leaf level** — finer breakdown within a section |

### Timing model

1. **Section header = target output duration.** The delta between start and end is how long this section should be in the final video.
2. **TTS narration extends but never compresses.** If the generated narration audio exceeds the section duration, extend the section to fit. Otherwise, preserve the section duration for visual breathing room.
3. **Source ranges = search zones, not edit points.** The recap-script generator provides intentionally wide windows (often 5–30× the output duration). Extracting the full range verbatim produces poor results. Your job is to scout within — and slightly beyond — these windows to find the precise clips that serve the narration.

### Nested ranges — leaf nodes only

When a section has both a parent range on the `**画面**` line and child sub-sections with their own brackets, use only the leaf-level (child) ranges. The parent is a summary — including it doubles the footage.

### Clip scouting (narrowing wide ranges to precise clips)

Source ranges point at a haystack; your job is to find the needles. This is the creative core of the recap workflow — the skill that the script generator cannot do for you.

**1. Load source transcript.** Check for an SRT file alongside the source video (same directory, same stem or a common subtitle filename). If none is found, ask the user — they may know where the file is, or may want you to transcribe via `transcribe.py`. Do not silently fall back to transcription; do not proceed without transcript data.

**2. Range analysis.** For each section, read transcript/subtitle entries that fall within the source range **plus a small buffer** (~30s before and after). The narration text (`旁白`) is your semantic anchor — it tells you *what* the footage should show. When the narration says "PhD Guy walks straight to the front," find the moment he actually does that.

Before choosing clips, split the narration into the same sentence/clause cues that viewers will read. Build an explicit cue map: `output cue → narration text → source timestamp → source transcript quote → chosen visual`. Section-level correspondence is not sufficient. If the recap changes the order of facts relative to the source, reorder the source visuals to follow the narration; blindly preserving source chronology creates a correct-looking but semantically mismatched edit.

**3. Candidate identification.** Cross-reference transcript content against narration text to build a shortlist of candidate moments. Use `timeline_view.py` at each candidate for visual verification. In this phase, `timeline_view` is used more liberally than in standard editing — scouting a 30-minute range may require 5–10 visual checks. You are looking for:
- The exact visual moment described by the narration
- Emotional peaks, reactions, facial expressions that sell the beat
- Visual variety across the section (avoid pulling all clips from one continuous stretch)
- Clean entry and exit frames (no mid-motion cuts, no flash frames)

**4. Clip selection.** Apply cut craft principles (word boundaries, silence gaps ≥ 400ms, peak preservation, speaker handoffs) to lock precise start/end times. Also decide:
- How many clips to pull per section (one long clip vs. a montage of shorter ones)
- Clip ordering within the section (chronological is default; reorder only when it serves the narrative)
- Entry/exit impact (the first and last frame of each clip carry disproportionate weight)

**5. Creative freedom.** You decide the clip count, arrangement, and exact boundaries. The only constraints are: clips must serve the narration, fit the section's output duration (extended if TTS overflows), and obey the Hard Rules. Everything else — pacing, rhythm, visual emphasis — is yours.

### TTS narration audio

`helpers/tts.py --from-script <script.md> -o <dir>/ --write-subtitles` generates one mp3+srt per section, a `narration_manifest.json`, `master.srt` (output-timeline aligned), and `full_narration.m4a`. Voice configuration is in `config/voices.json`.

### Recap EDL construction

The EDL uses a `narration` field (string path to the concatenated narration audio). When `narration_manifest.json` exists in `<narration_dir>/` (written by `tts.py`), `render.py` **mixes** narration with the source audio according to `audio_mix`. Defaults remain 15% during narration and 100% in gaps for backward compatibility. Without the manifest, narration replaces source audio entirely. Overlay PNGs from `text_overlay.py` go in the `overlays` array as `{"file": "...", "start_in_output": ..., "duration": ...}`.

**Path rule:** the EDL lives in `<videos_dir>/edit/`. All relative paths for `narration`, `subtitles`, and overlay `file` fields resolve from that directory — do not prefix them with `edit/`.

### Recap process (replaces standard "The process" for this workflow)

1. **Parse the script.** Extract sections, source ranges (leaf only), narration text.
2. **Load source transcript.** Check for an SRT alongside the source video. If not found, ask the user for the subtitle file location or whether to transcribe. Do not proceed without transcript data.
3. **Generate narration audio + captions.** `tts.py --from-script --write-subtitles`. Review the timing report, `narration_manifest.json`, and `master.srt`.
4. **Scout clips.** For each section: read transcript entries within the source range, use narration text as the semantic guide, inspect candidates with `timeline_view`, determine precise clip boundaries using cut craft principles. See "Clip scouting" above for the full method.
5. **Strategy confirmation.** Summarize: total duration, overflow sections, grade direction, **clip selection rationale** (which clips from which ranges, why each was chosen), whether background/source audio runs throughout or only in selected windows, its level under narration and in gaps, and whether subtitles allow one or two lines. Wait for user approval in the conversation; no separate Python dialog is needed. Record the approved values in `audio_mix` and `subtitle_layout` rather than relying on prose memory.
6. **Build EDL and assets.** EDL ranges use the scouted clip boundaries — not the original wide source ranges. Each range records its narration `cue`, semantic `beat`, and supporting `source_quote`. Allocate range durations against narration cue windows, then verify the sum against `total_duration_s`. Section header durations remain the base, extended where TTS overflows.
7. **Render.** `render.py edl.json -o out.mp4` — narration is mixed over ducked source audio (source plays at full volume in narration gaps).
8. **Self-eval + iterate.** Same as standard workflow.

## Anti-patterns

Things that consistently fail regardless of style:

- **Hierarchical pre-computed codec formats** with USABILITY / tone tags / shot layers. Over-engineering. Derive from the transcript at decision time.
- **Hand-tuned moment-scoring functions.** The LLM picks better than any heuristic you'll write.
- **Whisper SRT / phrase-level output.** Loses sub-second gap data. Always word-level verbatim.
- **Running Whisper locally on CPU.** Slow and it normalizes fillers. Use hosted Scribe.
- **Burning subtitles into base before compositing overlays.** Overlays hide them. (Hard Rule 1.)
- **Single-pass filtergraph when you have overlays.** Double re-encodes. Use per-segment extract → concat.
- **Linear animation easing.** Looks robotic. Always cubic.
- **Hard audio cuts at segment boundaries.** Audible pops. (Hard Rule 3.)
- **Typing text centered on the partial string.** Text slides left as it grows.
- **Sequential sub-agents for multiple animations.** Always parallel.
- **Editing before confirming the strategy.** Never.
- **Re-transcribing cached sources.** Immutable outputs of immutable inputs.
- **Assuming what kind of video it is.** Look first, ask second, edit last.
