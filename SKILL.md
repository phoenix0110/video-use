---
name: video-use
description: Edit any video by conversation. Transcribe, cut, color grade, generate overlay animations, burn subtitles — for talking heads, montages, tutorials, travel, interviews. No presets, no menus. Ask questions, confirm the plan, execute, iterate, persist. Production-correctness rules are hard; everything else is artistic freedom.
---

# Video Use

## Principle

1. **LLM reasons from raw transcript + on-demand visuals.** The only derived artifact that earns its keep is a packed phrase-level transcript (`takes_packed.md`). Everything else — filler tagging, retake detection, shot classification, emphasis scoring — you derive at decision time.
2. **Audio gives the structure; complete visual beats give the pace.** Find safe cut candidates from speech boundaries and silence gaps, then keep each meaningful shot long enough for viewers to understand the action, expression, or change it carries.
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
14. **Narration-driven recap clips require a full boundary audit.** Check the first and last subtitle entry and the first and last visual frame of every selected EDL range. A range passes only when its entrance already supports the active narration cue and its exit completes that cue without introducing an unexplained new event.
15. **Audit every burned subtitle on the rendered output.** Compare the render-only SRT with the master text after applying only the approved punctuation cleanup, then inspect one rendered frame from every cue. Fail the render if any character is missing, text touches or crosses the safe horizontal margins, a one-line cue wraps, or a cue ends with a comma, full stop, or semicolon unless the user explicitly asked to retain it.
16. **Minimum clip duration is 2 seconds.** No EDL range may be shorter than 2s in the final output. If a candidate clip is under 2s, either extend it to include the surrounding action, merge it into an adjacent range, or drop it entirely. This applies regardless of the clip's narrative importance.
17. **Chinese captions may never be split by character count.** Break only at authored semantic boundaries. Do not divide a word, name, fixed expression, number, or conjunction across adjacent cues. If a one-line cue is too wide and no approved boundary exists, fail the render and revise the cue; never guess by bisecting the string.
18. **Source-led dialogue must be complete and fully translated.** Select a complete scene with a clean spoken entry and exit. Every audible utterance inside the approved source-led window needs a corresponding subtitle or an explicitly approved omission. A representative quote or summary may not stand in for the rest of the dialogue.
19. **Narration-driven recaps: the orchestrating SOP decides when to pause for user approval.** This skill provides the caption-picture review artifact (see Recap process step 7) but does not mandate its own confirmation gate. The calling workflow (e.g. `video-recap-sop`) owns the approval timing.
20. **Narration must start when its visual starts.** `tts.py` generates contiguous audio where each section's speech immediately follows the previous section's. But visual clips have their own durations from the source video. If the narration audio is assembled without gaps, each section's speech will drift ahead of its visual—the viewer hears about topic B while still seeing topic A. After the EDL is finalized and visual clip durations are known, recalculate each narration section's `output_start` in the manifest to equal the cumulative visual duration up to that section, then reassemble the narration audio with silence gaps between sections. Each section's narration begins when its visual begins; the gap between sections gives the viewer a natural breathing pause.

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
   - The outgoing shot completes its narrated action or reaction before the cut
   - The incoming shot immediately belongs to the narration cue active at that moment

   Also sample: first 2s, last 2s, and 2–3 mid-points — check grade consistency and overall coherence. For subtitles, do not rely on sampling: compare the prepared subtitle text with the master text, then inspect every rendered cue for missing characters, horizontal clipping, unintended wrapping, and trailing punctuation. Run `ffprobe` on the output to verify duration matches the EDL expectation.

   Measure the audio, don't assume it: `ffmpeg -i out.mp4 -af ebur128=peak=true -f null -` for integrated loudness and true peak, plus RMS per section (dialogue, music-only, end card). An end card 15 dB under the dialogue, or effects louder than speech, is a bug. You cannot listen: say so, and report the numbers.

   For anything the user will publish (launch, promo, ad), also spawn one **critic sub-agent** with the rendered file, the EDL, and any reference videos the user gave. Brief it to roast, not to praise: a verdict, ranked problems with timecodes and evidence (frames, levels), and the 5 fixes to do first. Fresh eyes catch what the author stopped seeing — cut-off payoff lines, 0.5s memes, unreadable 28px text at phone size.

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
- **Cut on completed beats, not on every clause.** Keep a primary shot through the action and its useful reaction; change shots when the subject, place, information, or emotional state actually changes.
- **After a fast hook, restore comprehension pace.** The body may still feel energetic, but it should not become a stream of unrelated flashes. If a viewer cannot tell what changed before the next cut, the shot is too short or the transition is under-explained.

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

Subtitles have three dimensions worth reasoning about: **chunking** (1/2/3/sentence per line), **case** (UPPER/Title/Natural), and **placement** (margin from bottom). The right combo depends on content. In one-line Chinese narration, treat each comma-delimited phrase as an indivisible unit: pack whole phrases into a cue, never split a phrase or lexical word across adjacent cues. A displayed cue must be a complete sentence or a complete semantic clause that makes sense on its own. When a rendered cue would end on a comma, full stop, or semicolon, omit that trailing mark while preserving punctuation that remains inside the cue. Retain question and exclamation marks when they carry meaning.

For narration-driven recaps, author the final one-line cues before rendering and set `subtitle_layout.approved_cues: true`. In this mode the renderer validates width, overlap, and forbidden trailing punctuation but does not re-chunk the approved text. If a cue fails, return to the review artifact and revise it; the renderer must not silently alter user-approved wording.

**Worked styles** — pick, adapt, or invent:

**`bold-overlay`** — short-form tech launch, fast-paced social. ~2-word chunks, UPPERCASE, break on punctuation and pauses ≥ 0.3s, grow to 3 words rather than flash a cue < 0.35s (`chunk_words` in `render.py`), Helvetica 18 Bold, white-on-outline, `MarginV=35`. `render.py` ships with this as `SUB_FORCE_STYLE`.

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

**Fonts fail silently.** A web font that didn't load renders in a fallback face with no error — the video ships in "almost Arial". In HyperFrames/Remotion, await the font load and then assert it: `if (!document.fonts.check('700 76px "Inter"')) throw new Error(...)`. In PIL, pass an explicit font path; never rely on the default.

**Worked example — "show the edit" hook** (a launch video for this tool). Instead of a title card, the first 3s visualize the editing itself: each transcript word pops in on its Scribe timestamp, with bars under it drawn from the real audio envelope; a filler ("ummm") grows letter by letter while it is spoken, turns orange and is cut out on screen at the same frame the audio cuts, and the next line lands immediately. It works because the picture is *driven by the same data as the sound* — one composition (Remotion) reads frame-exact word/envelope JSON produced in Python, so nothing can drift. Use the idea whenever the story is "we removed something": make the removal visible.

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

## Music and sound effects (when requested)

Sound is where generated videos sound cheap. Worked rules from launch edits:

- **Fewer effects.** Every effect is tied to something visible (a cut, a landing, a click). ~20 stock whooshes/risers/impacts in 18s reads as generic; ~8 reads as designed.
- **Hit on the frame.** Most effects have an attack (silence or a build before the transient). Measure it (first sample above ~-30 dBFS of the peak) and start the file `attack` seconds *before* the visible contact frame.
- **Duck music under speech** (roughly -12 to -15 dB relative to its music-only level), and ramp it out before a stinger or end card instead of letting its own tail decay under your CTA.
- **Master once:** mix to PCM, then two-pass loudnorm (-14 LUFS, true peak ≤ -1 dBTP) on the final mix. Then measure per section (see Self-eval).
- **Music taste is the user's call.** Generated music defaults to "hype"; offer two contrasting beds and let the user listen. Don't claim a mix sounds good — you can only measure it.

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
    "max_units": 48,
    "approved_cues": true
  },
  "audio_mix": {
    "source_mode": "throughout",
    "source_under_narration": 0.08,
    "source_in_gaps": 0.30,
    "narration_volume": 1.0,
    "cue_source_volume": {
      "1": 1.0,
      "3": 1.0
    }
  },
  "total_duration_s": 87.4
}
```

`ranges` accepts either `start` + `end` or `start` + `duration`. For narration-driven recaps, add `cue`, `beat`, and the supporting `source_quote` from the source transcript so every picture choice is auditable. `grade` is a preset name or raw ffmpeg filter. `overlays` are rendered animation clips. `subtitles` is optional and applied LAST. `subtitle_layout.max_lines: 1` makes `render.py` create a render-only, non-overlapping SRT whose long cues are split into consecutive one-line captions; `bar_full_width` draws one fixed bottom bar rather than a text-sized box. `audio_mix` controls original source level under narration and in narration gaps (`0.0`–`2.0` linear gain). `source_mode` is `throughout`, `narration_only`, or `muted`. `cue_source_volume` is an optional per-cue override dict (`{"<cue_num>": <0.0–2.0>}`) that forces the source volume to that value during the cue's output window — use this for `**原声保留**` segments where the original audio must stay at full volume while Chinese narration plays underneath (the standard `source_under_narration` would otherwise duck the original to 0.08 and make it inaudible).

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
| Section header | `0:00–0:15` | **Output timeline plan** — ordering and rough pacing before TTS measurement |
| `**画面**` bracket | `[MM:SS–MM:SS]` | **Source timeline** — where to pull footage from the original |
| Sub-section bracket | `[MM:SS–MM:SS]` | **Source timeline, leaf level** — finer breakdown within a section |

### Timing model

1. **Narration timing is measured, not assumed.** Section-header spans express sequence and initial pacing. After TTS, use each cue's actual start and end as the default section timing.
2. **Measured narration sets the ordinary section length.** Extra time belongs only to a deliberate source-led moment or a reaction that completes the beat.
3. **A source-led moment must be explicit.** Mark a section with `**原声保留**` only when the original clip contains a self-contained statement, demonstration, reaction, or dramatic event that is stronger in its own voice. Its entry, full event, and exit must all be preserved. `tts.py` keeps the declared slot only for these marked sections; ordinary sections collapse to measured narration duration.
   After scouting the exact source-led excerpt, make its slot equal to the narration audio plus that excerpt's complete playback time, then rerun `tts.py`. Cached speech is reused while the master narration and subtitles receive corrected offsets.
   The script's representative `原话` is only a scouting hint. Before approval, transcribe the exact selected source-led window from entry to exit and translate every audible utterance. If the current boundary enters after speech has begun or exits before a response finishes, extend or move the boundary even when that increases the runtime.
4. **Source ranges = search zones, not edit points.** The recap-script generator may provide wide windows. Scout within — and slightly beyond — them to find the precise clips that serve the narration.

### Nested ranges — leaf nodes only

When a section has both a parent range on the `**画面**` line and child sub-sections with their own brackets, use only the leaf-level (child) ranges. The parent is a summary — including it doubles the footage.

### Clip scouting (narrowing wide ranges to precise clips)

Source ranges point at a haystack; your job is to find the needles. This is the creative core of the recap workflow — the skill that the script generator cannot do for you.

**1. Load source transcript.** Check for an SRT file alongside the source video (same directory, same stem or a common subtitle filename). If none is found, ask the user — they may know where the file is, or may want you to transcribe via `transcribe.py`. Do not silently fall back to transcription; do not proceed without transcript data.

**2. Range analysis.** For each section, read transcript/subtitle entries that fall within the source range **plus a small buffer** (~30s before and after). The narration text (`旁白`) is your semantic anchor — it tells you *what* the footage should show. When the narration says "PhD Guy walks straight to the front," find the moment he actually does that.

Before choosing clips, split the narration into the same sentence/clause cues that viewers will read. Build an explicit cue map: `narration start/end → narration text → source timestamp → source transcript quote → chosen visual → boundary evidence`. Section-level correspondence is not sufficient. If the recap changes the order of facts relative to the source, reorder the source visuals to follow the narration; blindly preserving source chronology creates a correct-looking but semantically mismatched edit.

**3. Candidate identification.** Cross-reference transcript content against narration text to build a shortlist of candidate moments. Use `timeline_view.py` at each candidate for visual verification. In this phase, `timeline_view` is used more liberally than in standard editing — scouting a 30-minute range may require 5–10 visual checks. You are looking for:
- The exact visual moment described by the narration
- Emotional peaks, reactions, facial expressions that sell the beat
- A primary shot that can carry the complete narrative beat without needless cutting
- Clean entry and exit frames (no mid-motion cuts, no flash frames)

**4. Clip selection.** Apply cut craft principles (word boundaries, silence gaps ≥ 400ms, peak preservation, speaker handoffs) to lock precise start/end times. Also decide:
- How many clips to pull per section (one long clip vs. a montage of shorter ones)
- Clip ordering within the section (chronological is default; reorder only when it serves the narrative)
- Entry/exit impact (the first and last frame of each clip carry disproportionate weight)
- Cue coverage: the combined selected clips begin with the narration cue and cover it through its actual TTS end

For **every** selected clip, inspect both boundaries against the source transcript and visuals. At the start, the clip must already establish the action, person, or place named by the cue. At the end, the described action or reaction must be complete. If either edge fails, move the boundary, replace the clip, or revise the narration; do not rely on a middle-frame spot check.

**5. Creative freedom.** You decide the clip count, arrangement, and exact boundaries. Let the original video's rhythm guide the body: hold complete actions and expressions, explain each transition, and use faster cutting only where the material itself accelerates. Clips must cover the measured narration cue; extra time needs a named narrative purpose.

### TTS narration audio

`helpers/tts.py --from-script <script.md> -o <dir>/ --write-subtitles` generates one mp3+srt per section, a `narration_manifest.json`, `master.srt` (output-timeline aligned), and `full_narration.m4a`. Voice configuration is in `config/voices.json`.

### Recap EDL construction

The EDL uses a `narration` field (string path to the concatenated narration audio). When `narration_manifest.json` exists in `<narration_dir>/` (written by `tts.py`), `render.py` **mixes** narration with the source audio according to `audio_mix`. Defaults remain 15% during narration and 100% in gaps for backward compatibility, but the recap workflow should create gaps only for explicit source-led moments. Without the manifest, narration replaces source audio entirely. Overlay PNGs from `text_overlay.py` go in the `overlays` array as `{"file": "...", "start_in_output": ..., "duration": ...}`.

**Path rule:** the EDL lives in `<videos_dir>/edit/`. All relative paths for `narration`, `subtitles`, and overlay `file` fields resolve from that directory — do not prefix them with `edit/`.

### Recap process (replaces standard "The process" for this workflow)

1. **Parse the script.** Extract sections, source ranges (leaf only), narration text.
2. **Load source transcript.** Check for an SRT alongside the source video. If not found, ask the user for the subtitle file location or whether to transcribe. Do not proceed without transcript data.
3. **Generate narration audio + captions.** `tts.py --from-script --write-subtitles`. Review measured cue timings, `narration_manifest.json`, and `master.srt`; ordinary sections should be contiguous rather than padded to estimates. After master.srt is finalized, inject Chinese translation cues for every 原声保留 gap window (tts.py only generates subtitles for narration lines; source-led segments need their translations added separately using the gap windows from `narration_manifest.json`).
4. **Scout clips.** For each section: read transcript entries within the source range, use narration text as the semantic guide, inspect candidates with `timeline_view`, and choose complete visual beats instead of maximizing shot count.
5. **Strategy confirmation.** Summarize: overall pacing, clip-selection rationale, which moments—if any—will be source-led, background/source-audio behavior, source level under narration and in gaps, and whether subtitles allow one or two lines. Wait for user approval in the conversation; no separate Python dialog is needed. Record approved values in `audio_mix` and `subtitle_layout`.
6. **Build the cue map and EDL, then align narration to the visual timeline.** Use actual narration cue windows. Every range records its narration `cue`, semantic `beat`, and supporting `source_quote`; also retain the first/last transcript evidence used to approve its boundaries. The ranges must cover the narration through its measured end. For a source-led section, correct the slot to its exact selected excerpt and rebuild the cached TTS timeline. Do not fill unused script-header time.
   After the EDL ranges are finalized, align the narration to the visual timeline (Hard Rule 20): group visual ranges by narration section, sum each section's visual duration, and recalculate `output_start` in the manifest so each section's narration begins at the cumulative visual position—not at the end of the previous section's audio. Reassemble `full_narration.m4a` with silence gaps so the speech in each section starts when its visual starts. Regenerate `master.srt` with the corrected timestamps, then inject translation cues for 原声保留 gaps.
7. **Author the caption-picture review.** Produce an output-timeline document grouped by section. For every visual range list: source timestamps, what the viewer sees, every final displayed caption in order, and the boundary evidence. For every source-led range also list the full original transcript and line-by-line Chinese translation for the exact selected window. Mark any intentionally inaudible, uncertain, or omitted speech explicitly.
8. **Hand off for user confirmation (if orchestrator requires it).** The orchestrating SOP (e.g. `video-recap-sop`) decides whether and when to present the review artifact to the user. When it does, verify: wording, complete sentences/semantic clauses, picture-caption correspondence, full source-dialogue coverage, and any intended omissions. After approval, freeze the caption text and set `subtitle_layout.approved_cues: true`. Any later wording or source-window change invalidates this approval and requires a new review.
9. **Boundary audit.** Inspect the start and end of every selected range against both transcript and frames. Then review the assembled body for overly frequent cuts and under-explained transitions.
10. **Render.** `render.py edl.json -o out.mp4`. Source audio becomes prominent only in approved source-led moments; incidental gaps are removed rather than filled.
11. **Self-eval + iterate.** Apply the rendered-output boundary review to every cut, then check the whole sequence at normal speed for comprehension and flow.

## Anti-patterns

Things that consistently fail regardless of style:

- **Hierarchical pre-computed codec formats** with USABILITY / tone tags / shot layers. Over-engineering. Derive from the transcript at decision time.
- **Hand-tuned moment-scoring functions.** The LLM picks better than any heuristic you'll write.
- **Whisper SRT / phrase-level output.** Loses sub-second gap data. Always word-level verbatim.
- **Running Whisper locally on CPU.** Slow and it normalizes fillers. Use hosted Scribe.
- **Burning subtitles into base before compositing overlays.** Overlays hide them. (Hard Rule 1.)
- **Single-pass filtergraph when you have overlays.** Double re-encodes. Use per-segment extract → concat.
- **Linear animation easing.** Looks robotic. Always cubic.
- **Unverified web fonts.** A failed load silently falls back to a system face. Assert the font loaded before rendering.
- **Stock SFX on every transition.** Tie each effect to a visible event; cap the count.
- **Hard audio cuts at segment boundaries.** Audible pops. (Hard Rule 3.)
- **Typing text centered on the partial string.** Text slides left as it grows.
- **Sequential sub-agents for multiple animations.** Always parallel.
- **Editing before confirming the strategy.** Never.
- **Re-transcribing cached sources.** Immutable outputs of immutable inputs.
- **Assuming what kind of video it is.** Look first, ask second, edit last.
