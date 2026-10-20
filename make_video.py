#!/usr/bin/env python3
"""AI video maker: scene description + talk track -> narrated video.

Pipeline
  1. Ask for the setting / scene description.
  2. Ask for the talk track (the words to be spoken).
  3. Generate the voice track with ElevenLabs (with word timings).
  4. Split the talk track into 3-10 s segments grouped into scenes of up to
     40 s, and have Gemini write a continuity-preserving prompt for each one.
  5. Generate each scene with Gemini Omni 1.1 Flash: the first segment is a
     new take and every later segment extends that same video.
  6. Concatenate the segments.
  7. Lay the voice track over the final video, with infomercial-style keyword
     graphics (chosen by Gemini) burned in.

Every step writes its output into a run folder (runs/<timestamp>/). Running
again with --resume <folder> skips the steps that are already done, so a
failed clip never costs you the whole run.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Literal

import imageio_ffmpeg
import requests
from dotenv import load_dotenv
from pydantic import BaseModel, Field

import callouts as fx
from segmentation import MAX_SCENE_SECONDS, Segment, plan_segments, words_from_alignment

load_dotenv()

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
PROMPT_MODEL = os.getenv("PROMPT_MODEL", "gemini-4-argon")
VIDEO_MODEL = os.getenv("VIDEO_MODEL", "gemini-omni-1.1-flash")
ELEVEN_VOICE = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
ELEVEN_MODEL = os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")
CALLOUT_FONT = os.getenv("CALLOUT_FONT", "Avenir Next Bold")
CALLOUT_FONT_HEAVY = os.getenv("CALLOUT_FONT_HEAVY", "Avenir Next Heavy")

# Omni has no negative-prompt parameter, so this goes into every prompt as an AVOID line.
NEGATIVE_PROMPT = (
    "subtitles, captions, on-screen text, readable text, words, letters, typography, "
    "misspelled or garbled text, text on computer screens, signs with writing, "
    "watermark, logo, scene cut, jump cut, fingers touching a desktop monitor, "
    "change of character appearance, change of wardrobe, change of hairstyle, "
    "change of location, extra people, distorted faces, deformed hands, "
    # Typical AI-video artefacts: things that pop into existence or change by themselves.
    "objects suddenly appearing or vanishing, props materialising out of nowhere, "
    "objects morphing into other objects, duplicated or multiplying objects, "
    "items floating, melting or warping, furniture shifting position, "
    "extra fingers or limbs, faces morphing, flickering or crawling textures, "
    "nonsensical background clutter, impossible physics, glossy plastic AI look, low quality"
)
# Keep prompts compact: video models weigh the start most and may ignore a long tail.
MAX_PROMPT_CHARS = 3600


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def read_multiline(question: str) -> str:
    print(f"\n{question}")
    print("(Paste or type freely. Finish with a line containing only END, or Ctrl-D.)")
    lines: list[str] = []
    try:
        while True:
            line = input()
            if line.strip() == "END":
                break
            lines.append(line)
    except EOFError:
        pass
    text = "\n".join(lines).strip()
    if not text:
        sys.exit("Nothing entered, aborting.")
    return text


def ffmpeg(*args: str, cwd: Path | None = None) -> None:
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args]
    subprocess.run(cmd, check=True, cwd=cwd)


def media_duration(path: Path) -> float:
    """Duration in seconds, parsed from ffmpeg's header output (no ffprobe needed)."""
    out = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)],
                         capture_output=True, text=True).stderr
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", out)
    if not m:
        raise RuntimeError(f"Could not read duration of {path}")
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(s)


def video_size(path: Path) -> tuple[int, int]:
    """(width, height) of the first video stream, parsed from ffmpeg's header output."""
    out = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)],
                         capture_output=True, text=True).stderr
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", out)
    if not m:
        raise RuntimeError(f"Could not read video size of {path}")
    return int(m.group(1)), int(m.group(2))


def require_env(name: str) -> str:
    val = os.getenv(name)
    if not val:
        sys.exit(f"Missing {name}. Copy .env.example to .env and fill it in.")
    return val


# ---------------------------------------------------------------------------
# Step 3: voice track (ElevenLabs, with character timestamps)
# ---------------------------------------------------------------------------

def generate_voice(talk_track: str, run: Path) -> tuple[Path, dict]:
    audio_path, align_path = run / "voice.mp3", run / "alignment.json"
    if audio_path.exists() and align_path.exists():
        log("Voice track already exists, skipping.")
        return audio_path, json.loads(align_path.read_text())

    log(f"Generating voice track with ElevenLabs (voice {ELEVEN_VOICE}, model {ELEVEN_MODEL})...")
    resp = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVEN_VOICE}/with-timestamps",
        params={"output_format": "mp3_44100_128"},
        headers={"xi-api-key": require_env("ELEVENLABS_API_KEY"),
                 "Content-Type": "application/json"},
        json={"text": talk_track, "model_id": ELEVEN_MODEL},
        timeout=600,
    )
    if resp.status_code != 200:
        sys.exit(f"ElevenLabs error {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    alignment = data.get("alignment") or data.get("normalized_alignment")
    if not alignment:
        sys.exit("ElevenLabs returned no timing alignment; cannot segment.")

    audio_path.write_bytes(base64.b64decode(data["audio_base64"]))
    align_path.write_text(json.dumps(alignment))
    log(f"Voice track saved: {audio_path.name} ({media_duration(audio_path):.1f}s)")
    return audio_path, alignment


# ---------------------------------------------------------------------------
# Step 4: segmentation + clip prompts (Gemini)
# ---------------------------------------------------------------------------

class Character(BaseModel):
    name: str = Field(description="Short name used to refer to the character.")
    appearance: str = Field(description=(
        "Fixed physical identity in at most 45 words: apparent age, gender, ethnicity, "
        "skin tone, face shape, eye colour, hair colour/length/style, facial hair, build, "
        "distinguishing marks."))
    wardrobe: str = Field(description=(
        "Exact clothing and accessories with colours and materials, at most 30 words."))


class Bible(BaseModel):
    """The parts repeated word for word in every clip prompt."""
    visual_style: str = Field(description=(
        "Film look in at most 30 words: medium (e.g. photoreal cinematic), lens/film feel, "
        "colour grade, depth of field."))
    setting: str = Field(description=(
        "The location in at most 60 words: architecture, key furniture and props with "
        "their positions, background, time of day."))
    lighting: str = Field(description=(
        "Light sources, direction, quality and colour temperature, at most 25 words."))
    characters: list[Character]


class Shot(BaseModel):
    index: int
    camera: str = Field(description="Shot size, angle and camera movement, at most 20 words.")
    action: str = Field(description=(
        "What happens on screen, matched to what is said, at most 45 words."))
    expression: str = Field(description="Facial expression and body language, at most 15 words.")
    speaker_on_screen: bool = Field(description=(
        "True if a visible character is speaking the line to camera in this shot; "
        "False if the line is voice-over over B-roll."))
    speaker_name: str = Field(description="Name of the on-screen speaker, or empty string.")
    continuity: str = Field(description=(
        "How this shot connects to the previous and next shots (positions, props, "
        "eyeline, motion), at most 25 words."))


class VideoPlan(Bible):
    title: str
    shots: list[Shot]


PLANNER_INSTRUCTIONS = """\
You are a film director and prompt engineer for Google Gemini Omni, a video
model that can extend a video it generated. You will receive a creative brief,
a narration script and a list of timed segments grouped into scenes. Each scene
is ONE unbroken take: its first segment is generated fresh and every later
segment extends that same video by 3 to 10 seconds. A new scene starts from the
exact last frame of the previous scene. The narration is laid over the top.

Consistency comes from the extensions and from the prompts. Produce a production bible (visual style, setting, lighting, characters) that
is specific and concrete: exact colours, materials, positions, ages,
hairstyles, clothing details. Never use vague words like "stylish" or "modern"
without saying exactly what they look like. The bible is repeated word for
word in every prompt, and the model only reads about 900 words in total, so
respect the word limit on every field. Write dense noun phrases, not full
sentences; no filler, no repetition between fields.

Then write exactly one shot per segment, in order, with the same index. Match
the visuals to what is being said in that segment and to its duration. Keep
the same characters, wardrobe and location in every shot unless the brief
explicitly asks otherwise. Each shot continues exactly where the previous one
ended (same framing, positions and props) with smooth, motivated camera
movement; never plan a cut or a jump to a new angle or location. Describe only
what can happen in that many seconds of the same take.

NO AI SLOP. Nothing appears, disappears or changes by itself. Every object in
frame is in the bible's setting or is visibly brought in by a character (picked
up, carried in, handed over). Do not add new props, people or decorations in
later shots, and do not ask for transformations, magic effects or things
materialising. Keep backgrounds simple and physically plausible.

NO READABLE TEXT. The video model cannot spell: any words, titles, labels, file names,
numbers or messages you ask for come out garbled. So never quote text and never
name what a screen, document, sign, badge or dialog says. Show meaning through
colour coding (green, amber, red), simple icons (padlock, shield, warning
triangle, folder, checkmark), blurred abstract interface shapes, actions and
facial reactions. The important keywords are added later as separate motion
graphics, so the footage itself must carry none. This applies to the bible
too: plain clothing with no logos, slogans or name badges, and no posters,
signage, whiteboard writing, labelled boxes or book titles in the setting.

Keep interactions physically realistic: people use keyboard, mouse or trackpad;
desktop monitors are not touchscreens unless the brief says so.
"""


def bible_text(bible: Bible) -> str:
    chars = "\n".join(f"- {c.name}: {c.appearance} Wearing: {c.wardrobe}" for c in bible.characters)
    return (f"STYLE: {bible.visual_style}\n"
            f"SETTING: {bible.setting}\n"
            f"LIGHTING: {bible.lighting}\n"
            f"CHARACTERS (identical in every clip):\n{chars}")


NO_TEXT_LINE = ("TEXT: No legible words, letters or numbers anywhere in frame; screens, papers "
                "and signs show only soft-focus abstract shapes, colour blocks and simple icons.")

_QUOTE = r"""(?<![\w])['‘"“][^'‘’"“”\n]{1,80}?['’"”](?![\w])"""
_QUOTE_RUN = rf"{_QUOTE}(?:\s*,\s*(?:and\s+|or\s+)?{_QUOTE}|\s+(?:and|or)\s+{_QUOTE})*"
_QUOTE_LEAD = (r"(?:\s+(?:labell?ed|titled|named|called|reading|reads|that reads|which reads|"
               r"saying|says|marked|headed|captioned|stamped|for|like|such as|with the (?:words?|text)|"
               r"showing the (?:words?|text))(?:\s+(?:in|with)\s+[\w\- ]{1,25}?)?)?\s*:?\s*")


_SCREENISH = re.compile(r"[A-Z0-9_/\\.@#%$]")


def _strip_match(m: re.Match) -> str:
    # Only quotes that look like screen text (capitals, digits, file names); keep
    # ordinary quoted emphasis such as 'quick'.
    quoted = re.findall(_QUOTE, m.group(0))
    return " " if any(_SCREENISH.search(q[1:-1]) for q in quoted) else m.group(0)


def has_screen_text(text: str) -> bool:
    return any(_SCREENISH.search(q[1:-1]) for q in re.findall(_QUOTE, text))


def strip_screen_text(text: str) -> str:
    """Remove quoted on-screen text ('labeled "SALE"', titles, file names) that the video model would
    render as garbled letters. Last-resort backstop after the planner rules and the
    Gemini rewrite."""
    out = re.sub(_QUOTE_LEAD + _QUOTE_RUN, _strip_match, text)
    out = re.sub(r"\s+([.,;:!?])", r"\1", out)
    out = re.sub(r":([.,;!?])", r"\1", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.strip()


SHOT_FIELDS = ("camera", "action", "expression", "continuity")


class ShotList(BaseModel):
    shots: list[Shot]


REWRITE_INSTRUCTIONS = """\
These video shot descriptions ask for readable text (quoted words, titles,
labels, file names, messages). The video model renders text as garbled letters, so rewrite
each shot with no readable text at all. Keep everything else identical: same
index, camera, characters, action and meaning. Replace text with colour coding,
simple icons (padlock, shield, warning triangle, folder, checkmark), blurred
abstract interface shapes and the characters' reactions. Never quote anything.
"""


def remove_screen_text(client, shots: list[Shot]) -> list[Shot]:
    """Ask Gemini to rewrite shots that still contain on-screen text, then strip any
    quoted text that is left."""
    bad = [s for s in shots if any(has_screen_text(getattr(s, f)) for f in SHOT_FIELDS)]
    if bad and client is not None:
        from google.genai import types
        log(f"Rewriting {len(bad)} shot(s) that ask for on-screen text (the video model garbles text)...")
        try:
            resp = client.models.generate_content(
                model=PROMPT_MODEL,
                contents=ShotList(shots=bad).model_dump_json(indent=2),
                config=types.GenerateContentConfig(
                    system_instruction=REWRITE_INSTRUCTIONS,
                    response_mime_type="application/json",
                    response_schema=ShotList,
                ),
            )
            fixed = resp.parsed or ShotList.model_validate_json(resp.text)
            by_index = {s.index: s for s in ShotList.model_validate(fixed.model_dump()).shots}
            shots = [by_index.get(s.index, s) for s in shots]
        except Exception as e:  # the regex backstop below still applies
            log(f"Warning: shot rewrite failed ({e}); stripping quoted text instead.")
    return [clean_shot(s) for s in shots]


def clean_shot(shot: Shot) -> Shot:
    return shot.model_copy(update={f: strip_screen_text(getattr(shot, f)) for f in SHOT_FIELDS})


def clean_bible(bible: Bible) -> Bible:
    chars = [c.model_copy(update={"appearance": strip_screen_text(c.appearance),
                                  "wardrobe": strip_screen_text(c.wardrobe)})
             for c in bible.characters]
    return bible.model_copy(update={
        "visual_style": strip_screen_text(bible.visual_style),
        "setting": strip_screen_text(bible.setting),
        "lighting": strip_screen_text(bible.lighting),
        "characters": chars})


def take_header(duration: int, step: int, from_frame: bool = False) -> str:
    if step > 0:
        return (f"Extend this video by {duration} seconds as the same continuous shot; no cut. "
                "Everyone and everything already in frame stays exactly as it is.")
    start = " It starts exactly on the given first frame." if from_frame else ""
    return f"Single continuous {duration}-second shot from one film; no scene cuts.{start}"


def build_clip_prompt(bible: Bible, shot: Shot, seg: Segment, lip_sync: bool) -> str:
    if shot.speaker_on_screen and lip_sync and seg.text:
        dialogue = f'{shot.speaker_name or "The character"} says to camera: "{seg.text}"'
    else:
        dialogue = "Voice-over only; nobody on screen talks."
    # Video models weigh the start of a prompt most, so the shot comes first and the
    # shared bible follows.
    return (f"{take_header(seg.duration, seg.step, from_frame=seg.scene > 1)}\n"
            f"CAMERA: {shot.camera}\n"
            f"ACTION: {shot.action}\n"
            f"EXPRESSION: {shot.expression}\n"
            f"CONTINUITY: {shot.continuity}\n"
            f"DIALOGUE: {dialogue}\n"
            f"{NO_TEXT_LINE}\n"
            f"AVOID: {NEGATIVE_PROMPT}.\n"
            f"{bible_text(bible)}")


def condense_bible(client, bible: Bible, max_chars: int) -> Bible:
    """Ask Gemini to shorten the shared bible while keeping identity details."""
    from google.genai import types
    current = len(bible_text(bible))
    log(f"Prompts too long; condensing the shared description "
        f"from {current} to at most {max_chars} characters...")
    resp = client.models.generate_content(
        model=PROMPT_MODEL,
        contents=(
            f"Rewrite this production bible so that all its text fields together are at most "
            f"{max_chars} characters (currently {current}). Keep character names unchanged. "
            "Keep the details that make characters and the location recognisable: faces, "
            "hair, skin tone, clothing colours, key props. Drop generic or redundant words. "
            "Use dense noun phrases.\n\n" + bible.model_dump_json(indent=2)),
        config=types.GenerateContentConfig(
            response_mime_type="application/json", response_schema=Bible),
    )
    out = resp.parsed or Bible.model_validate_json(resp.text)
    return Bible.model_validate(out.model_dump())


def assemble_prompts(client, plan: VideoPlan, segments: list[Segment], lip_sync: bool,
                     max_chars: int = MAX_PROMPT_CHARS, attempts: int = 2) -> list[dict]:
    shots = {s.index: s for s in plan.shots}
    if len(shots) != len(segments):
        log(f"Warning: planner returned {len(shots)} shots for {len(segments)} segments; "
            "reusing the nearest shot where one is missing.")
    chosen = [shots.get(seg.index) or plan.shots[min(seg.index, len(plan.shots)) - 1]
              for seg in segments]
    pairs = list(zip(segments, remove_screen_text(client, chosen)))

    bible = clean_bible(Bible.model_validate(plan.model_dump()))
    for attempt in range(attempts + 1):
        prompts = [build_clip_prompt(bible, shot, seg, lip_sync) for seg, shot in pairs]
        longest = max(len(p) for p in prompts)
        if longest <= max_chars or attempt == attempts:
            break
        # Room left for the bible in the longest prompt, with a little slack.
        room = max_chars - (longest - len(bible_text(bible))) - 100
        if room < 400:
            log("Warning: shot descriptions alone are close to the prompt limit; "
                "consider shorter segments of narration.")
            room = 400
        bible = condense_bible(client, bible, room)

    for i, p in enumerate(prompts, 1):
        if len(p) > max_chars:
            log(f"Warning: prompt {i} is {len(p)} chars (limit ~{max_chars}); "
                "the model may ignore the end. Edit prompts.json to shorten it.")
    return [{**seg.to_dict(), "prompt": p} for (seg, _), p in zip(pairs, prompts)]


def plan_prompts(scene: str, talk_track: str, segments: list[Segment], run: Path,
                 lip_sync: bool) -> list[dict]:
    prompts_path = run / "prompts.json"
    if prompts_path.exists():
        log("Prompts already exist, skipping planning.")
        return json.loads(prompts_path.read_text())

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=require_env("GEMINI_API_KEY"))
    seg_list = "\n".join(
        f"Segment {s.index}: {s.start}s-{s.end}s ({s.duration}s), scene {s.scene}, "
        + ("new take" if s.step == 0 and s.scene == 1 else
           "starts from the last frame of the previous scene" if s.step == 0 else
           "extends the same take")
        + f". Narration: \"{s.text}\""
        for s in segments
    )
    user_msg = f"""CREATIVE BRIEF (setting, scene, characters, style):
{scene}

FULL NARRATION SCRIPT:
{talk_track}

SEGMENTS ({len(segments)} segments in {segments[-1].scene} scene(s), write one shot for each):
{seg_list}
"""
    log(f"Planning {len(segments)} shots with {PROMPT_MODEL}...")
    resp = client.models.generate_content(
        model=PROMPT_MODEL,
        contents=user_msg,
        config=types.GenerateContentConfig(
            system_instruction=PLANNER_INSTRUCTIONS,
            response_mime_type="application/json",
            response_schema=VideoPlan,
        ),
    )
    plan: VideoPlan = resp.parsed or VideoPlan.model_validate_json(resp.text)
    (run / "plan.json").write_text(plan.model_dump_json(indent=2))

    out = assemble_prompts(client, plan, segments, lip_sync)
    longest = max(len(o["prompt"]) for o in out)
    log(f"Built {len(out)} prompts (longest {longest} chars, limit ~{MAX_PROMPT_CHARS}).")
    prompts_path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    return out


def plan_callouts(items: list[dict], segments: list[Segment], run: Path,
                  density: str) -> dict | None:
    """Ask Gemini which keywords deserve an on-screen graphic. Saved to callouts.json
    (edit it freely; delete entries you don't want)."""
    path = run / "callouts.json"
    if density == "off":
        return None
    if path.exists():
        return json.loads(path.read_text())

    from google import genai

    plan = json.loads((run / "plan.json").read_text()) if (run / "plan.json").exists() else {}
    shots = {s["index"]: f"{s.get('camera', '')} {s.get('action', '')}".strip()
             for s in plan.get("shots", [])}
    client = genai.Client(api_key=require_env("GEMINI_API_KEY"))
    log(f"Choosing on-screen keywords with {PROMPT_MODEL} (density: {density})...")
    accent, chosen = fx.plan_callouts(client, PROMPT_MODEL, segments, shots,
                                      plan.get("visual_style", ""), density)
    data = {"accent_color": accent, "callouts": [c.model_dump() for c in chosen]}
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    log(f"Planned {len(chosen)} keyword graphics for {len(segments)} clips.")

    # Tell the video model to keep that part of the frame clear in the clips that get a graphic.
    by_seg: dict[int, list] = {}
    for c in chosen:
        by_seg.setdefault(c.segment_index, []).append(c)
    changed = False
    for it in items:
        cs = by_seg.get(it["index"])
        if cs and "uncluttered background" not in it["prompt"]:
            it["prompt"] = f"{it['prompt']}\n\n{fx.layout_hint(cs)}"
            changed = True
    if changed:
        (run / "prompts.json").write_text(json.dumps(items, indent=2, ensure_ascii=False))
    return data


# ---------------------------------------------------------------------------
# Step 5: clip generation (Gemini Omni: one take per scene, extended step by step)
# ---------------------------------------------------------------------------

POLL_SECONDS = 10


def extract_last_frame(video: Path, out: Path) -> Path:
    ffmpeg("-sseof", "-0.1", "-i", str(video), "-frames:v", "1", "-update", "1", str(out))
    return out


def as_fresh_take(prompt: str, duration: int) -> str:
    """Turn an extension prompt into one for a new take that starts on a given frame."""
    _, _, rest = prompt.partition("\n")
    return f"{take_header(duration, step=0, from_frame=True)}\n{rest}"


def video_request(item: dict, aspect: str, resolution: str, previous_id: str | None,
                  first_frame: Path | None) -> dict:
    """Body for client.interactions.create: extend the scene so far, or start a new take
    (from a first frame when one is given)."""
    fmt = {"type": "video", "aspect_ratio": aspect, "resolution": resolution,
           "duration": f"{item['duration']}s", "delivery": "uri"}
    body = {"model": VIDEO_MODEL, "response_format": fmt, "background": True}
    if previous_id:
        # The previous interaction is what makes this an extension; Omni rejects a
        # video task alongside it ("previous_interaction_id is not allowed when
        # video task is set").
        body.update(previous_interaction_id=previous_id, input=item["prompt"])
        return body
    if first_frame is not None:
        mime = "image/png" if first_frame.suffix.lower() == ".png" else "image/jpeg"
        image = {"type": "image", "mime_type": mime,
                 "data": base64.b64encode(first_frame.read_bytes()).decode()}
        prompt = item["prompt"] if item.get("step", 0) == 0 else as_fresh_take(item["prompt"], item["duration"])
        body["input"] = [image, {"type": "text", "text": prompt}]
        task = "image_to_video"
    else:
        body["input"] = item["prompt"]
        task = "text_to_video"
    body["generation_config"] = {"video_config": {"task": task}}
    return body


def run_interaction(client, body: dict):
    it = client.interactions.create(**body)
    while it.status in ("queued", "in_progress"):
        time.sleep(POLL_SECONDS)
        it = client.interactions.get(id=it.id)
    if it.status != "completed":
        raise RuntimeError(f"Omni {it.status}: {getattr(it, 'error', None) or 'no details'}")
    if it.output_video is None or not (it.output_video.data or it.output_video.uri):
        raise RuntimeError("Omni returned no video (likely safety filter).")
    return it


def save_video(client, it, dest: Path) -> None:
    video = it.output_video
    if video.data:
        data = video.data
        dest.write_bytes(base64.b64decode(data) if isinstance(data, (str, bytes)) else bytes(data))
        return
    for _ in range(60):  # URI delivery: wait until the file is ready
        try:
            state = str(client.files.get(name=video.uri).state)
        except Exception:  # noqa: BLE001 - some URIs can't be looked up; try the download
            break
        if "ACTIVE" in state:
            break
        if "FAILED" in state:
            raise RuntimeError("Omni video file failed to process.")
        time.sleep(POLL_SECONDS)
    client.files.download(file=video.uri, destination=str(dest))


def keep_new_part(raw: Path, part: Path, scene_len: float, duration: int) -> float:
    """Save exactly `duration` seconds of new footage from a step and return the length
    of the scene video so far. An extension may come back as the whole scene or as
    just the continuation; both are handled by looking at its length."""
    got = media_duration(raw)
    whole = scene_len > 0 and abs(got - (scene_len + duration)) < abs(got - duration)
    start = scene_len if whole else 0.0
    # Trim to the planned length (padding with the last frame if it came up short) so
    # the footage stays in sync with the narration.
    ffmpeg("-ss", f"{start:.3f}", "-i", str(raw), "-an",
           "-vf", "fps=24,tpad=stop_mode=clone:stop_duration=3", "-t", str(duration),
           "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p", str(part))
    return got if whole else scene_len + got


TEXT_CHECK_MODEL = os.getenv("TEXT_CHECK_MODEL", PROMPT_MODEL)


class TextCheck(BaseModel):
    amount: Literal["none", "minor", "too_much"] = Field(description=(
        "none: no lettering at all. minor: only small, incidental lettering that a viewer "
        "would not notice or that looks natural (a brand logo, UI buttons, icon labels, "
        "a window title bar, tiny or blurred interface text). too_much: a lot of text, or "
        "any prominent gibberish a viewer would notice (garbled headings, signs, captions, "
        "lines of fake writing on screens or paper, misspelt words that draw the eye)."))
    where: str = Field(description=(
        "Which surface shows the text and roughly where (e.g. 'monitor screen, centre'), "
        "or empty if none."))


TEXT_CHECK_INSTRUCTIONS = """\
You check AI-generated video clips for distracting text. The only text in the
finished video is added later as graphics, so writing the model painted into
the footage is a problem when it draws attention, above all garbled gibberish.

A small amount is fine and counts as "minor", for example:
- a brand logo on the back of a monitor or laptop (e.g. a DELL logo, a circular logo)
- desktop icon labels, a file explorer window, buttons and other small UI chrome
- tiny, blurred or out-of-focus interface text in the background

"too_much" means a lot of text, or prominent gibberish a viewer would notice:
garbled headings or banners, signs, captions, documents or screens filled with
lines of fake writing, misspelt words in focus. Plain icons and symbols without
letters (padlock, checkmark, shapes) are not text. Answer amount and where.
"""


def find_text(client, clip: Path) -> str:
    """Ask Gemini whether a clip shows too much lettering. Returns where it is, or ""
    when the clip is clean or shows only minor lettering such as logos and buttons."""
    from google.genai import types
    try:
        resp = client.models.generate_content(
            model=TEXT_CHECK_MODEL,
            contents=[types.Part.from_bytes(data=clip.read_bytes(), mime_type="video/mp4"),
                      "How much text does this clip show?"],
            config=types.GenerateContentConfig(
                system_instruction=TEXT_CHECK_INSTRUCTIONS,
                response_mime_type="application/json",
                response_schema=TextCheck,
            ),
        )
        check = resp.parsed or TextCheck.model_validate_json(resp.text)
        check = TextCheck.model_validate(check.model_dump())
    except Exception as e:  # noqa: BLE001 - a failed check must not stop the run
        log(f"Warning: text check for {clip.name} failed ({e}); keeping the clip.")
        return ""
    if check.amount == "minor":
        log(f"{clip.name}: minor lettering ({check.where.strip() or 'small'}); keeping it.")
    return (check.where.strip() or "somewhere in frame") if check.amount == "too_much" else ""


def with_text_fix(prompt: str, where: str) -> str:
    """Put a correction near the top of the prompt, where the model weighs it most."""
    first, _, rest = prompt.partition("\n")
    fix = (f"FIX: the last take showed lettering ({where[:120]}); that surface must be blank "
           "or show only colour blocks and simple icons.")
    return f"{first}\n{fix}\n{rest}" if rest else f"{first}\n{fix}"


def generate_clips(items: list[dict], run: Path, aspect: str, resolution: str,
                   start_image: Path | None, retries: int = 3,
                   text_retries: int = 2) -> list[Path]:
    """Make one clip per segment. Each scene is a single Omni video: its first segment
    is a new take and every later segment extends it, so people, props and the room
    carry over. A new scene starts from the last frame of the previous clip."""
    from google import genai

    client = genai.Client(api_key=require_env("GEMINI_API_KEY"))
    clip_dir = run / "clips"
    clip_dir.mkdir(exist_ok=True)
    paths = [clip_dir / f"clip_{it['index']:02d}.mp4" for it in items]
    # Interaction ids let a later step (or a --resume) extend the scene so far.
    state_path = clip_dir / "steps.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}

    def last_frame(i: int) -> Path | None:
        if i == 0:
            return start_image
        return extract_last_frame(paths[i - 1], clip_dir / f"lastframe_{i:02d}.png")

    def render(i: int, item: dict, previous_id: str | None, scene_len: float,
               frame: Path | None) -> None:
        n, raw = item["index"], paths[i].with_suffix(".raw.mp4")
        for attempt in range(1, retries + 1):
            kind = "extending the scene" if previous_id else "new take"
            try:
                log(f"Clip {n}/{len(items)} ({item['duration']}s, scene {item.get('scene', 1)}, "
                    f"{kind}), attempt {attempt}...")
                it = run_interaction(client, video_request(item, aspect, resolution, previous_id, frame))
                save_video(client, it, raw)
                # Write beside the clip first so a failed retake never leaves a
                # half-written or text-filled clip that --resume would skip.
                tmp = paths[i].with_suffix(".new.mp4")
                length = keep_new_part(raw, tmp, scene_len if previous_id else 0.0, item["duration"])
                tmp.replace(paths[i])
                raw.unlink(missing_ok=True)
                state[str(n)] = {"interaction": it.id, "scene_len": length}
                state_path.write_text(json.dumps(state, indent=2))
                log(f"Clip {n} saved.")
                return
            except Exception as e:  # noqa: BLE001 - surface any API failure and retry
                log(f"Clip {n} failed: {e}")
                if attempt == retries - 1 and previous_id:
                    log("Last attempt: starting a new take from the previous clip's last frame "
                        "instead of extending.")
                    previous_id, frame = None, last_frame(i)
                time.sleep(5 * attempt)
        sys.exit(f"Clip {n} failed {retries} times. "
                 f"Fix the prompt in {run / 'prompts.json'} and rerun with --resume {run}")

    def check_text(i: int, previous_id: str | None, scene_len: float, frame: Path | None) -> None:
        """Omni sometimes paints lettering anyway; retake the step while Gemini sees any."""
        if text_retries <= 0:
            return
        n = items[i]["index"]
        for take in range(text_retries + 1):
            where = find_text(client, paths[i])
            if not where:
                return
            if take == text_retries:
                log(f"Warning: clip {n} still shows too much text ({where}) after {text_retries} retake(s); "
                    f"keeping it. Delete {paths[i]} and rerun with --resume {run} to try again.")
                return
            log(f"Clip {n} shows too much text ({where}); retaking ({take + 1}/{text_retries})...")
            render(i, {**items[i], "prompt": with_text_fix(items[i]["prompt"], where)},
                   previous_id, scene_len, frame)

    # Extensions build on the step before, so the clips are made in order.
    for i, item in enumerate(items):
        if paths[i].exists():
            continue
        prev = state.get(str(items[i - 1]["index"]), {}) if i else {}
        previous_id = prev.get("interaction") if item.get("step", 0) > 0 else None
        scene_len = prev.get("scene_len", 0.0) if previous_id else 0.0
        frame = None
        if not previous_id:
            if item.get("step", 0) > 0:
                log(f"No saved interaction to extend for clip {item['index']}; "
                    "starting a new take from the previous clip's last frame.")
            frame = last_frame(i)
        render(i, item, previous_id, scene_len, frame)
        check_text(i, previous_id, scene_len, frame)
    return paths


# ---------------------------------------------------------------------------
# Steps 6 + 7: concatenate, then add the voice track
# ---------------------------------------------------------------------------

def concatenate(clips: list[Path], run: Path) -> Path:
    out = run / "video_silent.mp4"
    list_file = run / "concat.txt"
    list_file.write_text("".join(f"file '{p.resolve()}'\n" for p in clips))
    log("Concatenating clips...")
    # Re-encode so clips with slightly different timestamps join cleanly. The video
    # model's own audio is dropped here; the narration replaces it in the next step.
    ffmpeg("-f", "concat", "-safe", "0", "-i", str(list_file),
           "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
           "-pix_fmt", "yuv420p", "-r", "24", str(out))
    return out


def write_callouts_ass(callout_data: dict, alignment: dict, segments: list[Segment],
                       video: Path, length: float, run: Path) -> Path | None:
    callouts = [fx.Callout.model_validate(c) for c in callout_data.get("callouts", [])]
    timed = fx.time_callouts(callouts, words_from_alignment(alignment), segments, length)
    if not timed:
        return None
    width, height = video_size(video)
    accent = callout_data.get("accent_color") or fx.DEFAULT_ACCENT
    path = run / "callouts.ass"
    path.write_text(fx.write_ass(timed, width, height, accent, CALLOUT_FONT, CALLOUT_FONT_HEAVY), encoding="utf-8")
    return path


def add_voice(video: Path, audio: Path, run: Path, overlay: Path | None = None) -> Path:
    out = run / "final.mp4"
    tail = 0.5  # keep a little picture after the last word
    length = min(media_duration(video), media_duration(audio) + tail)
    if overlay is None:
        log("Adding voice track...")
        vcodec = ["-c:v", "copy"]
    else:
        log("Adding voice track and keyword graphics...")
        # The ass filter path is relative to cwd (avoids escaping issues in filter syntax).
        vcodec = ["-vf", f"ass={overlay.name}", "-c:v", "libx264", "-preset", "medium",
                  "-crf", "18", "-pix_fmt", "yuv420p"]
    ffmpeg("-i", str(video.resolve()), "-i", str(audio.resolve()),
           "-map", "0:v:0", "-map", "1:a:0", *vcodec, "-c:a", "aac", "-b:a", "192k",
           "-t", f"{length:.3f}", "-movflags", "+faststart", str(out.resolve()),
           cwd=run)
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def get_text_input(run: Path, name: str, question: str, file_arg: str | None) -> str:
    path = run / f"{name}.txt"
    if path.exists():
        return path.read_text()
    text = Path(file_arg).read_text().strip() if file_arg else read_multiline(question)
    path.write_text(text)
    return text


def confirm_prompts(items: list[dict], run: Path) -> list[dict]:
    total = sum(i["duration"] for i in items)
    callouts_path = run / "callouts.json"
    print("\n" + "=" * 78)
    for it in items:
        kind = "new take" if it.get("step", 0) == 0 else "extends the take"
        print(f"\n--- Clip {it['index']} | {it['start']}-{it['end']}s ({it['duration']}s) | "
              f"scene {it.get('scene', 1)}, {kind} ---")
        print(it["prompt"])
    print("\n" + "=" * 78)
    if callouts_path.exists():
        data = json.loads(callouts_path.read_text())
        print(f"Keyword graphics (accent {data.get('accent_color')}):")
        for c in data.get("callouts", []):
            print(f"  clip {c['segment_index']}: \"{c['text']}\"  [{c['style']}, {c['position']}]"
                  f"  on \"{c['anchor']}\"")
        if not data.get("callouts"):
            print("  (none)")
        print(f"Edit {callouts_path} to change them (any time before the final step).")
    print(f"{len(items)} clips in {items[-1].get('scene', 1)} scene(s), {total}s of video. Prompts saved in {run / 'prompts.json'}.")
    while True:
        ans = input(f"Generate the clips with {VIDEO_MODEL} now? [y]es / [e]dited the files, reload / [n]o: ").strip().lower()
        if ans in ("y", "yes"):
            return items
        if ans in ("e", "edit"):
            items = json.loads((run / "prompts.json").read_text())
            print("Reloaded prompts.json (callouts.json is read again at the end).")
            continue
        sys.exit(f"Stopped before video generation. Resume later with: --resume {run}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--resume", metavar="RUN_DIR", help="Continue an earlier run folder.")
    ap.add_argument("--scene-file", help="Read the scene description from a file instead of asking.")
    ap.add_argument("--script-file", help="Read the talk track from a file instead of asking.")
    ap.add_argument("--start-image", type=Path,
                    help="Optional PNG/JPEG used as the first frame of clip 1 (locks the character's look).")
    ap.add_argument("--aspect", default="16:9", choices=["16:9", "9:16"])
    ap.add_argument("--resolution", default="720p", choices=["360p", "720p", "1080p", "4k"],
                    help="1080p and 4k are upscaled by Omni (default: 720p).")
    ap.add_argument("--max-scene", type=int, default=MAX_SCENE_SECONDS, metavar="SECONDS",
                    help="Longest single take before a new scene starts from the last frame "
                         f"(10-{MAX_SCENE_SECONDS}, default {MAX_SCENE_SECONDS}).")
    ap.add_argument("--no-lip-sync", action="store_true",
                    help="Don't put the spoken line in the prompt (on-screen mouths won't move with the words).")
    ap.add_argument("--callouts", default="medium", choices=["off", "low", "medium", "high"],
                    help="How often keyword graphics pop up on screen (default: medium).")
    ap.add_argument("--text-retries", type=int, default=2, metavar="N",
                    help="Gemini watches each clip for lettering and Omni retakes it up to N times "
                         "(default: 2; 0 turns the check off). Retakes cost video credits.")
    ap.add_argument("--yes", "-y", action="store_true", help="Skip the confirmation before generating video.")
    args = ap.parse_args()

    if args.resume:
        run = Path(args.resume)
        if not run.is_dir():
            sys.exit(f"No such run folder: {run}")
    else:
        run = Path("runs") / datetime.now().strftime("%Y%m%d-%H%M%S")
        run.mkdir(parents=True)
    log(f"Run folder: {run}")

    # 1 + 2: inputs
    scene = get_text_input(run, "scene", "1) Describe the setting, scene, characters, style "
                           "and anything else that should guide the video:", args.scene_file)
    talk = get_text_input(run, "talk_track", "2) Paste the talk track (exactly what should be said):",
                          args.script_file)

    # 3: voice
    audio, alignment = generate_voice(talk, run)
    duration = media_duration(audio)

    # 4: segments + prompts
    if not 10 <= args.max_scene <= MAX_SCENE_SECONDS:
        sys.exit(f"--max-scene must be between 10 and {MAX_SCENE_SECONDS}.")
    segments = plan_segments(words_from_alignment(alignment), duration, args.max_scene)
    (run / "segments.json").write_text(json.dumps([s.to_dict() for s in segments], indent=2))
    log(f"{segments[-1].scene} scene(s), {segments[-1].end}s for {duration:.1f}s of audio. "
        "Each scene is one video: a new take, then extended step by step.")
    for n in range(1, segments[-1].scene + 1):
        steps = [s for s in segments if s.scene == n]
        ext = " ".join(f"+{s.duration}s" for s in steps[1:]) or "none"
        log(f"  Scene {n}: {sum(s.duration for s in steps)}s = {steps[0].duration}s take, "
            f"extended {ext}")
    items = plan_prompts(scene, talk, segments, run, not args.no_lip_sync)
    if [(i["index"], i["duration"]) for i in items] != [(s.index, s.duration) for s in segments]:
        sys.exit(f"{run / 'prompts.json'} was planned with different segments (an older version of "
                 "this script?). Start a new run, or delete prompts.json and callouts.json.")
    callout_data = plan_callouts(items, segments, run, args.callouts)
    if not args.yes:
        items = confirm_prompts(items, run)

    # 5: clips
    clips = generate_clips(items, run, args.aspect, args.resolution, args.start_image,
                           text_retries=args.text_retries)

    # 6 + 7: assemble
    silent = concatenate(clips, run)
    overlay = None
    if callout_data:
        callout_data = json.loads((run / "callouts.json").read_text())  # pick up manual edits
        length = min(media_duration(silent), duration + 0.5)
        overlay = write_callouts_ass(callout_data, alignment, segments, silent, length, run)
    final = add_voice(silent, audio, run, overlay)
    log(f"Done: {final.resolve()}")


if __name__ == "__main__":
    main()
