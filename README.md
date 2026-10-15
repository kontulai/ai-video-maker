# AI video maker

Turns a scene description and a talk track into a narrated video:

1. Asks for the setting / scene description.
2. Asks for the talk track.
3. Creates the voice track with **ElevenLabs** (with per-character timings).
4. Splits the talk track into **4, 6 or 8 s** segments and has **Gemini 4 Argon** write a verbose Veo prompt for each one.
5. Generates each segment with **Veo 3.1 Fast**.
6. Concatenates the segments.
7. Lays the voice track over the final video.

## Setup

```bash
uv venv .venv && uv pip install -p .venv -r requirements.txt
cp .env.example .env   # then fill in GEMINI_API_KEY and ELEVENLABS_API_KEY
```

ffmpeg is bundled through `imageio-ffmpeg`, so nothing else needs installing.
Gemini 4 Argon and Veo need the paid tier of the Gemini API.

## Run

```bash
.venv/bin/python make_video.py
```

You'll be asked for the scene and the talk track (finish each with a line containing `END`).
Before any Veo clip is generated, the script prints every prompt and asks for confirmation.
You can edit `prompts.json` in the run folder at that point and choose "reload".

Useful options:

| Option | What it does |
|---|---|
| `--scene-file F`, `--script-file F` | Read inputs from files instead of asking. |
| `--start-image img.png` | Use an image as the first frame of clip 1, to lock in the character's look. |
| `--aspect 9:16` | Vertical video. |
| `--no-chain` | Generate clips in parallel instead of starting each from the previous clip's last frame. Faster, less consistent. |
| `--no-lip-sync` | Don't put the spoken line in the prompt. Use for pure voice-over. |
| `--resume runs/<id>` | Continue a run; finished steps and clips are skipped. |
| `-y` | Skip the confirmation before calling Veo. |

## How consistency is kept

- **Production bible.** Gemini writes one exhaustive description of the visual style, setting, lighting and each character (face, hair, build, exact wardrobe). The script pastes it **word for word** into every clip prompt, so no clip can drift from it.
- **Frame chaining (default).** Each clip starts from the last frame of the previous clip, so people, clothes and the room carry over pixel-for-pixel. Gemini is told to plan continuous camera moves rather than cuts.
- **Negative prompt.** Every clip forbids captions, cuts, wardrobe or location changes and extra people.
- **Timing.** Segments are cut at even seconds chosen to fall in pauses, preferring sentence ends, so each clip shows what is being said at that moment. When a character speaks on screen, their line goes in the prompt so the mouth moves roughly in time.

Veo's own generated audio is discarded. The ElevenLabs narration is the only soundtrack.

## Output

Each run gets a folder under `runs/` containing the inputs, `voice.mp3`, `segments.json`, `plan.json`, `prompts.json`, `clips/`, and `final.mp4`.

## Tests

```bash
.venv/bin/python -m pytest -q tests
```

The tests need no API keys. They check the segmentation and run the whole pipeline with fake APIs.
