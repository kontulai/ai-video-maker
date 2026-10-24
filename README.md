# AI video maker

Turns a scene description and a talk track into a narrated video:

1. Asks for the setting / scene description.
2. Asks for the talk track.
3. Creates the voice track with **ElevenLabs** (with per-character timings).
4. Splits the talk track into **3 to 10 s** steps and has **Gemini 4 Argon** group them into scenes that follow the narration (a new idea can start a new scene) and write a verbose prompt for each step.
5. Generates each scene as one continuous video with **Gemini Omni 1.1 Flash**: the first step is a new take (a cut to a new setup, or a continuation from the last frame of the previous scene) and every later step **extends** that same video.
6. Joins the steps.
7. Lays the voice track over the final video.

## Setup

```bash
uv venv .venv && uv pip install -p .venv -r requirements.txt
cp .env.example .env   # then fill in GEMINI_API_KEY and ELEVENLABS_API_KEY
```

ffmpeg is bundled through `imageio-ffmpeg`, so nothing else needs installing.
Gemini 4 Argon and Gemini Omni need the paid tier of the Gemini API.

## Run

```bash
.venv/bin/python make_video.py
```

You'll be asked for the scene and the talk track (finish each with a line containing `END`).
Before any video is generated, the script prints every prompt and asks for confirmation.
You can edit `prompts.json` in the run folder at that point and choose "reload".

Useful options:

| Option | What it does |
|---|---|
| `--scene-file F`, `--script-file F` | Read inputs from files instead of asking. |
| `--start-image img.png` | Use an image as the first frame of clip 1, to lock in the character's look. |
| `--aspect 9:16` | Vertical video. |
| `--max-scene S` | Longest scene in seconds, 10 to 30 (default `30`, the longest Omni will extend one video to). Shorter forces more scene breaks. |
| `--resolution R` | `360p`, `720p` (default), `1080p` or `4k`. |
| `--resume runs/<id>` | Continue a run; finished steps and clips are skipped. |
| `--callouts off\|low\|medium\|high` | How often keyword graphics pop up. Default `medium`, about one every 5 seconds, so some text is on screen most of the time. |
| `--text-retries N` | Retake a clip up to N times when Gemini spots lettering in it. Default `2`; `0` turns the check off. Each retake costs a video generation. |
| `--no-fit` | Don't rewrite each prompt to match what the previous clip actually ends on (see below). |
| `-y` | Skip the confirmation before generating video. |

## Web app

The same pipeline, one project at a time, as a browser app with six phases
that can each be edited and rerun on their own.

```bash
uv pip install -p .venv -r requirements.txt   # adds FastAPI and uvicorn
cd web && npm install && npm run build && cd ..
.venv/bin/python -m server                     # then open http://127.0.0.1:8000
```

`python -m server --port 9000` picks another port. For frontend work run
`npm run dev` in `web/` (Vite on port 5173, proxying `/api` to the Python
server on 8000) next to `python -m server`.

The phases of a project:

1. **Setting**: describe the video, attach reference pictures of the people,
   the place or the look (PNG, JPEG or WebP), and generate a preview image;
   regenerate until it looks right. The pictures go to the image model and to
   the planner, so the preview and the production bible match them. The chosen
   preview is the first frame of the video.
2. **Talk track**: write the narration, or upload a PDF and let Gemini draft it
   (with notes on length, tone or what to cover), edit it, and generate the
   ElevenLabs voice.
3. **Scene plan**: segment the narration and plan the bible, scenes and shots;
   edit any of them, move scene breaks, or edit the assembled prompts.
4. **Video**: generate every scene, then regenerate any single scene or the
   whole video; each scene plays as one stitched video, plus the rough cut. Regenerating asks what was
   wrong or what should change: Gemini works the answer into that scene's shots
   (and into the production bible when the whole video is redone), the prompts
   are rebuilt and the clips made again. An empty answer rolls again with the
   same prompts.
5. **Overlays**: Gemini suggests keyword graphics over the rough cut; edit them
   in a video editor with a timeline (add, move, retime, restyle, delete);
   "Render preview" burns them with ffmpeg for an exact check.
6. **Final**: burn the overlays, lay the voice over the picture, download.

A phase whose inputs changed since it ran is marked stale with the phase to
redo; nothing is regenerated without pressing a button. Projects live in
`projects/<id>/` (override with `PROJECTS_DIR`) with the same files the
command-line run folder has, plus `project.json` and `job.json`.

## How consistency is kept

- **Production bible.** Gemini writes one exhaustive description of the visual style, setting, lighting and each character (face, hair, build, exact wardrobe), plus a **layout**: where everyone and everything is as the camera sees it, in `frame-left` / `frame-right` / `centre` / `foreground` / `background` terms, never a bare "left" or "right" (the model reads those from the character's point of view, which is how monitors end up on the wrong side). The bible goes **word for word** into the first prompt of every scene. Extensions don't repeat it: the model is continuing footage that already shows the room and the people, and a long bible on an extension only distracts it from the shot.
- **Extending within a scene.** A scene is one Omni video. Its first step is generated, and each later step extends that video, so the model sees everything that came before and people, props and the room carry over. Gemini plans each step as a continuation of the same shot, not a new one. A scene can last at most 30 s (`--max-scene`), the longest Omni will extend one video to.
- **Scenes that follow the talk track.** The steps are cut at pauses in the speech. Gemini then groups them into scenes so that the picture changes where the narration moves on: a new concept, the next item of a list, another character, the call to action. It chooses how each scene starts. A **cut** is a fresh take with a new setup in the same film: another angle or shot size, another part of the location, a close-up of a prop or screen, B-roll, the other character. Its prompt carries the production bible with that scene's own setting and layout, so the style, lighting and the characters' looks stay as in the brief while the framing is free. A **continued** scene restarts the take from the exact last frame of the previous one, with no visible jump, for when the moment simply goes on but the take has reached its limit. A scene Gemini leaves longer than the limit is split, continuing from the frame. Gemini's reasons for each break are logged, the grouping is saved in `segments.json`, and the confirmation shows each clip as "new take (cut)", "new take from the last frame" or "extends the take". If Gemini returns no scene list, the steps are grouped by timing alone, as few scenes as possible, broken at sentence ends.
- **A calm camera.** The first shot of a scene sets the framing. Later shots are locked off or drift slowly within it and never reveal a part of the room or a person that wasn't in the first shot: a pan to "the other desk" is where the model invents a new person. A cut is the one place the framing may change, and the way to bring another character into the film. Because a continued scene starts from a still of the previous one, it has to hold that framing; the only place the camera may slowly reframe to bring the next subject in is the last shot of the scene before.
- **Fitting prompts to the footage.** The model doesn't always do what the prompt said, and once it has put the monitor on the other side, every later prompt that describes the planned layout is now wrong. So after each clip, Gemini watches it and describes what the last frame shows (who stands where, which way they face, what each screen shows, the props). That description goes into the next prompt as an `IN FRAME NOW:` line, and Gemini rewrites the planned shot to match it: a missing prop is replaced with the nearest thing in frame, nobody is asked to enter, and the camera stays put. A cut opens on a new setup, so it is not fitted to the frame before it. The fitted prompts are saved in `prompts.json`, so `--resume` reuses them. `--no-fit` turns this off.
- **No AI slop.** Gemini is told that nothing may appear, vanish or change by itself. Every object must be in the production bible from the start or be visibly carried in. Icons, charts and dialogs exist only on screens, so prompts always say "on the monitor" and never let a graphic float in the air. Omni has no negative-prompt setting, so every prompt ends with a short `AVOID:` line (a long list dilutes the model's attention). It bans objects suddenly appearing or vanishing, morphing or multiplying props, floating graphics, furniture shifting, people entering, extra limbs and warped faces, wardrobe or location changes, captions, cuts and the glossy plastic AI look.
- **Timing.** Steps are cut at whole seconds chosen to fall in pauses, preferring sentence ends, so each step shows what is being said at that moment. Each step is trimmed, or padded with its last frame, to its exact length so the picture stays in sync with the narration.
- **Nobody speaks.** The narration is pure voice-over. The video model can't match lips to the words, so a talking character looks wrong. Gemini is told that no character ever talks, talks to camera or mouths words: people work, use their screens and tools, gesture, nod and react in silence, and a "conversation" is shown through glances and nods. Every prompt carries a `DIALOGUE: None` line saying so, the spoken words never go to the video model, and the `AVOID:` line bans talking and mouthing words.
- **Resuming.** The interaction id of each step is saved in `clips/steps.json`, so `--resume` can carry on extending a scene. If an extension keeps failing, the script starts a new take from the previous step's last frame.
- **Refused inputs.** Omni sometimes answers `content_blocked` ("Input blocked") for an ordinary office scene, and the refusal is not consistent: the same request can be refused three times in a minute and accepted unchanged later. Refused requests are not charged. The script waits 30 s and sends the same request once more, then sends only the shot (no bible, fix notes or layout hint) before giving up on that take.

The video model's own generated audio is discarded. The ElevenLabs narration is the only soundtrack.

## Keyword graphics

Gemini picks the keywords worth showing, such as prices, numbers, product names or the call to action. Nearly every clip gets one, and a clip can get several when it makes sense: a headline and then the points under it, or a list of points shown as bullets. Each graphic appears exactly when its words are spoken, using the ElevenLabs word timings, and stays up until the next graphic needs its half of the frame, for up to six seconds after its words. A headline in the upper half can stay up while the points appear in the lower half. At the default density some text is on screen most of the time.

They are animated motion graphics, not subtitles. They sit in a corner or the centre, away from faces, in one accent colour taken from the film's palette. Gemini chooses one of four looks for each:

- **slam**: huge tilted text that crashes in. For prices, numbers and offers.
- **swipe**: a label on a colour bar that wipes in, like a TV lower-third. For product names and features.
- **reveal**: letters type on and an accent line grows under them. For a key benefit.
- **bullet**: one point of a list. Each point pops in with an accent marker as it is spoken, and the list stays up together until a while after the last point.

Items of one enumeration ("three key factors", "four tiers", "first... second... third") look alike. Gemini gives them one `list_name`, and every item is rendered in the style and position of the first, even when the items fall in different clips. Items spoken within about 15 seconds of each other are bullets that stack into one list across clips; items further apart are numbered swipes that replace each other in the same spot.

The video prompt for a clip with a graphic asks for a calm, empty area where the graphic will sit. The choices are saved in `callouts.json` in the run folder. Edit or delete entries there before the final step; the file is read again when the video is assembled. Set `CALLOUT_FONT` and `CALLOUT_FONT_HEAVY` in `.env` to change the fonts. Use exact face names such as `Avenir Next Bold`, because asking for plain `Avenir Next` in bold picks the italic face on macOS.

## No text in the footage

Video models can't spell, so any words they draw come out garbled. The only text in the finished video should be the keyword graphics, which ffmpeg adds afterwards. Three things keep text out of the footage:

- **Prompts.** Gemini is told never to name or quote what screens, papers, signs or badges say. Screens, documents and signs are shown with colour coding, simple icons and blurred shapes instead. Clothing has no logos and the setting has no posters or signage. Any quoted labels that slip through are rewritten by Gemini, and as a last resort the quoted words are removed. Every prompt also says no lettering anywhere, and the `AVOID:` line bans text.
- **Checking each clip.** After each clip is generated, Gemini watches it for lettering (the same pass that describes what is in frame). A little is fine: brand logos on the back of a monitor, desktop icon labels, buttons and small window chrome are kept. If there is a lot of text, or prominent gibberish such as garbled headings, signs or screens full of fake writing, the step is retaken (from the same point in the scene) with a note saying where the text appeared. The retake replaces the clip only when it succeeds; a retake that fails (for example one Omni's content filter refuses) is dropped and the run goes on with the clip it has. After `--text-retries` retakes the clip is kept with a warning. Delete the clip and use `--resume` to try again. Set `TEXT_CHECK_MODEL` to use a different Gemini model for the check.
- **Limits.** Omni has no setting that guarantees zero text. Small or very blurry lettering can get past the check.

## Output

Each run gets a folder under `runs/` containing the inputs, `voice.mp3`, `segments.json`, `plan.json`, `prompts.json`, `callouts.json`, `clips/`, `callouts.ass`, and `final.mp4`.

## Tests

```bash
.venv/bin/python -m pytest -q tests
```

The tests need no API keys. They check the segmentation, run the whole pipeline with fake APIs, and exercise the web server's API with the same fakes.

```bash
cd web && npm test
```

The web tests (vitest and Testing Library) cover the parts of the UI that keep edits safe while jobs run: the scene plan page, the overlay editor and the stepper.
