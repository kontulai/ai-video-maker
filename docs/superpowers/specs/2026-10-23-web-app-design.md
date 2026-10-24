# Web app for the AI video maker: design

Date: 2026-10-23. Status: ready for approval.

## Intent

The command-line script in this repository turns a scene description and a
talk track into a narrated training video in one pass. Fixing anything means
rerunning it or hand-editing JSON in the run folder. The web app wraps the
same pipeline in a **project** that moves through six phases, so a video can
be made step by step and any phase can be edited and rerun on its own while
the later phases keep what still fits.

The videos are training videos, made one at a time by one person. The voice
carries the content; the picture keeps attention; the text overlays carry the
teaching points, so they get the most editing control.

## Goals

- Create, open, rename and delete video projects from a browser.
- Walk a project through six phases, in order, each with its own output that
  can be looked at, edited and regenerated:
  1. **Setting**: describe the video; get a preview image; regenerate until it
     looks right. The approved image is the first frame of the video.
  2. **Talk track**: write the words; generate the ElevenLabs voice track; listen;
     edit and regenerate.
  3. **Scene plan**: from the voice track, segment the narration and have Gemini
     write the production bible, the scenes and the shots; edit them.
  4. **Video**: generate every scene by default; regenerate any single scene
     afterwards; play each scene and a rough cut of all of them.
  5. **Overlays**: Gemini suggests the keyword graphics; the user edits them in a
     video editor with a timeline: add, move, retime, restyle, delete.
  6. **Final**: burn the overlays, lay the voice over the picture, download.
- Make it clear which later phases are stale after an earlier phase changes,
  and never rerun an expensive phase without the user pressing the button.
- Keep the command-line script working; it and the web app share one pipeline.

## Non-goals

- More than one user, accounts, or deployment anywhere but the user's own
  machine.
- Reusing characters or settings between projects.
- Editing the footage itself in the editor (trimming clips, reordering scenes).
  The editor is for overlays only.
- Controls for aspect ratio or visual style in the setting phase. The user
  writes a description and regenerates the preview until it looks good.

## Anti-goals

- A change in one phase that silently triggers a model call or a video
  generation somewhere else. Generation happens only on an explicit button.
- Losing manual overlay edits. Regenerating the suggestions is a separate
  action that asks before replacing edited overlays.
- A project whose state on disk cannot be understood without the app. The
  project folder stays as readable as today's run folders.

## Constraints

- Everything model-related already exists in `make_video.py`, `callouts.py`
  and `segmentation.py`: ElevenLabs voice with word timings, Gemini
  `gemini-4-argon` planning with structured output, Gemini Omni 1.1 Flash
  video with 30-second scenes built from a take plus extensions, Gemini
  review of each clip, ffmpeg concatenation, ASS overlays through
  `callouts.py`. The web app calls these; it does not rewrite them.
- Video generation takes minutes per scene and costs money. Omni's content
  filter sometimes refuses an ordinary request; the pipeline already retries.
- Only ever one generation running per project.
- API keys live in `.env` and never reach the browser. `.env` is never
  committed.

## Decisions already made

### Shape of the product

- Single-user local tool. Projects live in `projects/<id>/` next to today's
  `runs/`. Each project folder holds `project.json` (the state) plus the same
  files the CLI writes today: `scene.txt`, `talk_track.txt`, `voice.mp3`,
  `segments.json`, `plan.json`, `prompts.json`, `callouts.json`, `clips/`,
  `preview.png`, `rough_cut.mp4`, `callouts.ass`, `final.mp4`. No database.
- Navigation is a **stepper**: a row of the six phases across the top, one
  phase on screen at a time (the mockup in `.superpowers/brainstorm/` was
  approved). Each step shows its state: not started, running, done, or
  **stale** with the reason ("redo after 4"). The user can open any phase
  that has an input available.
- A phase's output records which versions of the upstream outputs it was made
  from. When an upstream phase produces a new output, the phases after it are
  shown as stale. Stale output is kept and still viewable; it is replaced only
  when the user regenerates. Overlays are keyed to the voice track's time, so
  regenerating a scene does not make them stale, only the final render.

### The six phases

1. **Setting.** A text box for the description and a generate button. The
   image is made by a Gemini image model (`IMAGE_MODEL` environment variable;
   the builder picks the current default) from the description, in the
   project's aspect ratio, with the same "no readable text" rule the video
   prompts use. Each generation is kept (`preview_1.png`, `preview_2.png`,
   ...) and the user picks which one is current. The current preview is the
   `start_image` of the video phase, and it is also shown to the planner in
   phase 3 so the production bible describes the people and room in the
   picture rather than inventing others.
2. **Talk track.** A text box, a narrator picker and a generate button. The
   picker lists the ElevenLabs account's voices (`GET /api/voices`) with a
   preview clip; the choice is saved in the project settings as `voice_id`
   and `voice_name`, and an empty id means `ELEVENLABS_VOICE_ID` from the
   environment. Output: `voice.mp3` and the word timings, with a player.
   Changing the text or the voice marks the voice track stale; regenerating
   it makes phases 3 to 6 stale.
3. **Scene plan.** A generate button runs the existing segmentation and
   planner. The page shows the production bible (style, setting, lighting,
   characters, layout), then each scene with its steps. For each step it shows
   the narration and the length, which come from the voice timings and are
   **read-only**, and the shot text (camera, action, expression, continuity),
   which is editable. Per scene, whether it starts with a cut or a
   continuation, and a cut's own setting and layout, are editable, and the
   user can move a scene break to another step boundary. The bible is
   editable. Saving rebuilds the per-step prompts from the edited plan
   without a model call: the text cleanup is the regular-expression one and
   the bible is not condensed (a too-long prompt is flagged on the page). A
   "show prompts" toggle reveals the assembled prompt of each step for direct
   editing; a hand-edited prompt is kept as written until its shot or the
   bible changes. The plan can also be regenerated from scratch.
4. **Video.** "Generate all" makes every scene in order, as the CLI does today
   (a scene is a take plus extensions; the next scene continues from the last
   frame or cuts, opening on a frame the image model draws from the preview
   image and the reference pictures so the look stays the same; each clip is
   reviewed for lettering and the next prompt is fitted to the footage). Each scene is a card with a thumbnail, its clips,
   a status, a play button and a **Regenerate** button that redoes only that
   scene: the first take restarts from the previous scene's last frame (or as
   a cut, on a freshly drawn opening frame) and the scene's clips are replaced. Regenerating a scene marks the
   scenes after it as "continued from older footage" but does not rerun them;
   the user decides. Staleness is **per scene**: each scene records the
   prompts it was made from, a plan edit marks only the scenes whose prompts
   changed, a bible edit marks them all, and "Generate all" redoes only stale
   or missing scenes. A live log and a rough cut (the scenes concatenated, no
   voice, no overlays) are on the page. The rough cut is the input of phase 5.
5. **Overlays.** Everything about overlays happens **after the video is glued
   together**, so the user works on the picture they will actually get. The
   video prompts carry no hints about where a graphic will sit (the CLI's
   layout hints are not used by the app). Opening the phase for the first
   time runs Gemini's suggestions (the existing callout planner and timing)
   over the rough cut's narration and shots. Then a video editor:
   - the rough cut playing with the overlays drawn on top, in the browser;
   - a timeline with three tracks: clips (one block per scene and step), the
     spoken words from the ElevenLabs timings, and the overlays;
   - clicking an overlay selects it; the side panel edits its text, start
     time (snapping to a spoken word, with the word shown), end time, style
     (slam, swipe, reveal, bullet), position, list name; plus duplicate and
     delete; the accent colour for the whole video is editable too;
   - the start and end the user sets are **exactly what gets drawn**. Two
     overlays may overlap in time and place; that is allowed and sometimes
     wanted, so the editor does not trim or warn. Bullets with the same list
     name that overlap in time stack into one list;
   - "Add overlay" at the playhead; dragging a block on the track moves it;
     dragging its ends retimes it; dragging the overlay in the picture moves it
     between positions;
   - changes save as they are made, to `callouts.json`;
   - the browser drawing approximates the four styles with HTML and CSS; a
     "Render preview" button burns the overlays onto the rough cut with ffmpeg
     for an exact check (seconds, no model calls);
   - "Regenerate suggestions" asks Gemini again and asks the user before
     replacing overlays they have edited.
6. **Final.** A render button burns the ASS overlays and lays the voice over
   the picture, as the CLI does today. Shows the result with a download link.

### Project settings

Aspect ratio (16:9 or 9:16), resolution, longest scene, overlay density and
text-check retries keep the CLI's defaults and live in `project.json`. They
are editable in an "Advanced" drawer on the project page, not in the phases.
This is my call: the user asked for no aspect or style controls in phase 1,
and these are the CLI's knobs, kept out of the way.

### Stack

- **Backend**: Python, FastAPI with uvicorn, in a `server/` package that
  imports `make_video`, `callouts` and `segmentation` directly. Long jobs run
  in a background thread per project with a job record (phase, status, log
  lines, started, finished) saved in the project folder; the browser follows
  the log over server-sent events. The CLI's `log()` gains a hook so job logs
  come from the same lines the CLI prints.
- **Frontend**: React with TypeScript, built with Vite, in `web/`. In
  development Vite proxies `/api` to FastAPI; for use, the built files are
  served by FastAPI so one command starts everything.
- **Pipeline changes**, the only ones allowed: functions that today take a run
  folder and read or write fixed file names keep doing so (the project folder
  is the run folder); `plan_prompts` accepts an optional image; `generate_clips`
  can run for one scene and start from a given frame; the confirmation prompt
  and the interactive questions are skipped when called from the server; the
  `log()` hook. The CLI keeps its behaviour and its tests.

### Overlay data

`callouts.json` grows from Gemini's planned callouts into the editor's model:
each overlay has an id, text, style, position, list name, and explicit start
and end times in seconds of the voice track. A suggested overlay also keeps
the anchor words it came from. The existing timing code produces the first
start and end times (including its trimming of a graphic when the next one
needs the same spot); after that the times in the file are the truth and the
ASS writer draws them as they are, with no conflict rule. Only the stacking
of same-list bullets is derived at render time.

## Review notes

A builder check was run on 2026-10-23. Its points were settled as follows.

- Overlay suggestions after the glued video, with no layout hints in the video
  prompts: **owner's decision**.
- Explicit overlay times, overlaps allowed: **owner's decision**.
- Narration and step lengths read-only in phase 3: owner's intent, now stated.
- Per-scene staleness in phase 4, and "Generate all" redoing only stale or
  missing scenes: **my call**.
- Plan save rebuilds prompts without Gemini, hand-edited prompts pinned until
  their shot changes: **my call**.

## Left to the builder

- Exact API routes and the shape of `project.json`, within what is above.
- The Gemini image model and prompt for the preview.
- How the browser approximates the four overlay styles.
- How the segments of a single regenerated scene are reconciled with the fit
  and review steps that follow it.
- Testing approach, following the repository's habit: no API keys needed,
  fakes for ElevenLabs, Gemini and Omni, the CLI tests kept green.
- Error presentation: refused or failed generations show in the phase with
  the log, and the phase stays editable.

