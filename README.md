# AI video maker

Turns a scene description and a talk track into a narrated video:

1. Asks for the setting / scene description.
2. Asks for the talk track.
3. Creates the voice track with **ElevenLabs** (with per-character timings).
4. Splits the talk track into **3 to 10 s** steps, grouped into scenes of up to 40 s, and has **Gemini 4 Argon** write a verbose prompt for each step.
5. Generates each scene as one continuous video with **Gemini Omni 1.1 Flash**: the first step is a new take and every later step **extends** that same video.
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
| `--max-scene S` | Longest scene in seconds, 10 to 40 (default `40`, Omni's limit for extending one video). Shorter means more fresh takes. |
| `--resolution R` | `360p`, `720p` (default), `1080p` or `4k`. |
| `--no-lip-sync` | Don't put the spoken line in the prompt. Use for pure voice-over. |
| `--resume runs/<id>` | Continue a run; finished steps and clips are skipped. |
| `--callouts off\|low\|medium\|high` | How often keyword graphics pop up. Default `medium`, about one every 9 seconds. |
| `--text-retries N` | Retake a clip up to N times when Gemini spots lettering in it. Default `2`; `0` turns the check off. Each retake costs a video generation. |
| `-y` | Skip the confirmation before generating video. |

## How consistency is kept

- **Production bible.** Gemini writes one exhaustive description of the visual style, setting, lighting and each character (face, hair, build, exact wardrobe). The script pastes it **word for word** into every clip prompt, so no clip can drift from it.
- **Extending, not cutting.** A scene is one Omni video. Its first step is generated, and each later step extends that video, so the model sees everything that came before and people, props and the room carry over. Gemini plans each step as a continuation of the same shot, not a new one. A talk track longer than one scene (40 s) gets several scenes. The scene breaks fall at sentence ends where possible, and each new scene starts from the last frame of the previous one.
- **No AI slop.** Gemini is told that nothing may appear, vanish or change by itself. Every object must be in the production bible from the start or be visibly carried in. Omni has no negative-prompt setting, so every prompt ends with an `AVOID:` line. It bans objects suddenly appearing or vanishing, morphing or duplicated props, warping hands and faces, flickering textures, nonsensical clutter, impossible physics and the glossy plastic AI look. It also bans captions, cuts, wardrobe or location changes and extra people.
- **Timing.** Steps are cut at whole seconds chosen to fall in pauses, preferring sentence ends, so each step shows what is being said at that moment. Each step is trimmed, or padded with its last frame, to its exact length so the picture stays in sync with the narration. When a character speaks on screen, their line goes in the prompt so the mouth moves roughly in time.
- **Resuming.** The interaction id of each step is saved in `clips/steps.json`, so `--resume` can carry on extending a scene. If an extension keeps failing, the script starts a new take from the previous step's last frame.

The video model's own generated audio is discarded. The ElevenLabs narration is the only soundtrack.

## Keyword graphics

Gemini picks a few keywords worth showing, such as prices, numbers, product names or the call to action. Most clips get none. A clip can get several when it makes sense, for example a list of points shown as bullets. Each graphic appears exactly when its words are spoken, using the ElevenLabs word timings.

They are animated motion graphics, not subtitles. They sit in a corner or the centre, away from faces, in one accent colour taken from the film's palette. Gemini chooses one of four looks for each:

- **slam**: huge tilted text that crashes in. For prices, numbers and offers.
- **swipe**: a label on a colour bar that wipes in, like a TV lower-third. For product names and features.
- **reveal**: letters type on and an accent line grows under them. For a key benefit.
- **bullet**: one point of a list. Each point pops in with an accent marker as it is spoken, and the list stays up together until shortly after the last point.

The video prompt for a clip with a graphic asks for a calm, empty area where the graphic will sit. The choices are saved in `callouts.json` in the run folder. Edit or delete entries there before the final step; the file is read again when the video is assembled. Set `CALLOUT_FONT` and `CALLOUT_FONT_HEAVY` in `.env` to change the fonts. Use exact face names such as `Avenir Next Bold`, because asking for plain `Avenir Next` in bold picks the italic face on macOS.

## No text in the footage

Video models can't spell, so any words they draw come out garbled. The only text in the finished video should be the keyword graphics, which ffmpeg adds afterwards. Three things keep text out of the footage:

- **Prompts.** Gemini is told never to name or quote what screens, papers, signs or badges say. Screens, documents and signs are shown with colour coding, simple icons and blurred shapes instead. Clothing has no logos and the setting has no posters or signage. Any quoted labels that slip through are rewritten by Gemini, and as a last resort the quoted words are removed. Every prompt also says no lettering anywhere, and the `AVOID:` line bans text.
- **Checking each clip.** After each clip is generated, Gemini watches it for lettering. A little is fine: brand logos on the back of a monitor, desktop icon labels, buttons and small window chrome are kept. If there is a lot of text, or prominent gibberish such as garbled headings, signs or screens full of fake writing, the step is retaken (from the same point in the scene) with a note saying where the text appeared. The retake replaces the clip only when it succeeds. After `--text-retries` retakes the clip is kept with a warning. Delete the clip and use `--resume` to try again. Set `TEXT_CHECK_MODEL` to use a different Gemini model for the check.
- **Limits.** Omni has no setting that guarantees zero text. Small or very blurry lettering can get past the check.

## Output

Each run gets a folder under `runs/` containing the inputs, `voice.mp3`, `segments.json`, `plan.json`, `prompts.json`, `callouts.json`, `clips/`, `callouts.ass`, and `final.mp4`.

## Tests

```bash
.venv/bin/python -m pytest -q tests
```

The tests need no API keys. They check the segmentation and run the whole pipeline with fake APIs.
