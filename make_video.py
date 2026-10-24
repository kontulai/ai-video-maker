#!/usr/bin/env python3
"""AI video maker: scene description + talk track -> narrated video.

Pipeline
  1. Ask for the setting / scene description.
  2. Ask for the talk track (the words to be spoken).
  3. Generate the voice track with ElevenLabs (with word timings).
  4. Split the talk track into 3-10 s segments, and have Gemini group them into
     scenes that follow the narration (a new idea can start a new scene) and
     write a continuity-preserving prompt for each segment.
  5. Generate each scene with Gemini Omni 1.1 Flash: the first segment is a
     new take (a cut to a new setup, or a continuation from the last frame of
     the previous scene) and every later segment extends that same video.
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
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Callable, Literal, Sequence

import imageio_ffmpeg
import requests
from dotenv import load_dotenv
from pydantic import BaseModel, Field

import callouts as fx
from segmentation import (MAX_SCENE_SECONDS, Segment, apply_scene_plan, plan_segments,
                          words_from_alignment)

load_dotenv()

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
PROMPT_MODEL = os.getenv("PROMPT_MODEL", "gemini-4-argon")
VIDEO_MODEL = os.getenv("VIDEO_MODEL", "gemini-omni-1.1-flash")
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "gemini-3.1-flash-image-preview")
ELEVEN_VOICE = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
ELEVEN_MODEL = os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")
CALLOUT_FONT = os.getenv("CALLOUT_FONT", "Avenir Next Bold")
CALLOUT_FONT_HEAVY = os.getenv("CALLOUT_FONT_HEAVY", "Avenir Next Heavy")

# Omni has no negative-prompt parameter, so this goes into every prompt as an AVOID line.
NEGATIVE_PROMPT = (
    # Kept short: a long list dilutes the model's attention and the tail is ignored.
    "readable text, captions, subtitles, garbled lettering, watermark, scene cut, "
    "objects suddenly appearing or vanishing, props morphing or multiplying, "
    "floating icons or graphics outside screens, furniture shifting position, "
    "extra people entering frame, extra fingers or limbs, distorted faces or hands, "
    "change of face, hair, wardrobe or location, anyone talking or mouthing words, "
    "glossy plastic AI look"
)
# Keep prompts compact: video models weigh the start most and may ignore a long tail.
MAX_PROMPT_CHARS = 3600


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

# The web app sets this per job thread so the same lines the CLI prints reach its log.
LOG_SINK: ContextVar[Callable[[str], None] | None] = ContextVar("LOG_SINK", default=None)


def log(msg: str) -> None:
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    sink = LOG_SINK.get()
    if sink is not None:
        sink(line)


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
        "The location in at most 60 words: architecture, key furniture and props, "
        "background, time of day."))
    layout: str = Field(description=(
        "Where everything sits as the camera sees it, at most 60 words. Use only the "
        "words frame-left, frame-right, centre, foreground and background (never a bare "
        "'left' or 'right'). For each person: their position, which way they face and on "
        "which side of them their monitor or screen stands."))
    lighting: str = Field(description=(
        "Light sources, direction, quality and colour temperature, at most 25 words."))
    characters: list[Character]


class Shot(BaseModel):
    index: int
    camera: str = Field(description=(
        "Shot size, angle and camera movement, at most 20 words. Inside a scene: locked "
        "off or a very slow drift within the framing already established."))
    action: str = Field(description=(
        "What happens on screen, matched to what is said, at most 45 words."))
    expression: str = Field(description=(
        "Facial expression and body language, at most 15 words. Nobody speaks: mouths "
        "stay closed or relaxed."))
    continuity: str = Field(description=(
        "How this shot connects to the previous and next shots (positions, props, "
        "eyeline, motion), at most 25 words."))


class ScenePlan(BaseModel):
    """One continuous take, as the planner groups the segments."""
    first_segment: int = Field(description="Index of the segment this scene starts with.")
    cut: bool = Field(description=(
        "True: a cut to a new setup of the same film (new angle, another part of the "
        "location, a close-up, B-roll, the other character), generated as a fresh take. "
        "False: the take restarts from the exact last frame of the previous scene, with "
        "no visible jump. Ignored for the first scene."))
    setting: str = Field(default="", description=(
        "For a cut: which part of the location this scene shows and what is in the "
        "background, at most 40 words, consistent with the bible's setting. Empty otherwise."))
    layout: str = Field(default="", description=(
        "For a cut: where everyone and everything sits in this scene's first shot as the "
        "camera sees it, at most 40 words, using only frame-left, frame-right, centre, "
        "foreground and background. Empty otherwise."))
    reason: str = Field(default="", description="Why the scene changes here, at most 12 words.")


class VideoPlan(Bible):
    title: str
    scenes: list[ScenePlan] = Field(description=(
        "The scenes in order. The first starts with segment 1."))
    shots: list[Shot]


PLANNER_INSTRUCTIONS = """\
You are a film director and prompt engineer for Google Gemini Omni, a video
model that can extend a video it generated. You will receive a creative brief,
a narration script and a list of timed segments of 3 to 10 seconds. You group
the segments into scenes and write one shot per segment. The narration is laid
over the top as voice-over.

NOBODY SPEAKS. The narration is voice-over: no character ever talks, talks to
camera, mouths words, presents or explains to someone. The video model cannot
match lips to the words, so a moving mouth looks wrong. Characters work, use
their screens and tools, gesture, nod, point, react and look, in silence; a
"conversation" is shown through glances, nods and handing things over. Never
write "speaks", "says", "explains", "presents" or "talks" in a shot.

SCENES. A scene is ONE unbroken take: its first segment is generated fresh and
every later segment extends that same video, so inside a scene nothing can cut
or jump. Omni extends a take to at most the length given in the message, so a
scene must end at or before that (a longer one is split for you). Place the
scene breaks where the narration moves on: a new concept or topic, the next
item of a list, another character, a change of mood, the call to action. Not every
sentence is a new scene: a scene of one or two ideas, usually 10 to 30 seconds,
reads well, while a break every few seconds looks like a slideshow. You choose
how each scene after the first starts:
- cut: a fresh take with a new setup in the same film: a different angle or
  shot size, another part of the same location, a close-up of a prop or a
  screen, B-roll of the thing being talked about, or the other character. Use
  it when the narration brings up something new. The visual style, location,
  light and every character's face, hair and wardrobe stay exactly as in the
  bible; give the scene its own setting detail and layout for its first shot.
- continue: the take restarts from the exact last frame of the previous scene
  with no visible jump. Use it when the moment simply goes on (the same action
  or conversation) and the take only has to be restarted. Its first shot holds
  that framing, people and screen content.
List the scenes in `scenes`, in order, starting with segment 1. Steps are
listed with their times; a scene break can only fall between two segments.

Consistency comes from the extensions and from the prompts. Produce a
production bible (visual style, setting, layout, lighting, characters) that
is specific and concrete: exact colours, materials, positions, ages,
hairstyles, clothing details. Never use vague words like "stylish" or "modern"
without saying exactly what they look like. The bible goes word for word into
the first prompt of every scene, and the model only reads about 900 words in
total, so respect the word limit on every field. Write dense noun phrases, not
full sentences; no filler, no repetition between fields. A cut scene's first
prompt gets the bible with that scene's own setting and layout in place of the
bible's, so a cut may show another part of the location or a closer framing,
but never another location, another look or other people.

LAYOUT. Positions are always given as the camera sees them, using only the
words frame-left, frame-right, centre, foreground and background. A bare
"left" or "right" is ambiguous (the character's left? the viewer's?) and puts
screens on the wrong side, so never use it. Say which way each person faces
and on which side of them their monitor stands, e.g. "Maya frame-left in
profile facing frame-right, her monitor frame-right of her". Use exactly the
same terms in every camera, action and continuity field.

Then write exactly one shot per segment, in order, with the same index. Match
the visuals to what is being said in that segment and to its duration. Keep
the same characters, wardrobe and location in every shot unless the brief
explicitly asks otherwise. Within a scene each shot continues exactly where
the previous one ended (same framing, positions and props); the only cuts in
the film are the scene breaks you planned as cuts. Describe only what can
happen in that many seconds of the same take.

CAMERA. A scene's first shot sets the framing. Every later shot in that scene
keeps it: the camera is locked off or drifts very slowly (a gentle push-in or
small reframe) and never pans, tilts, dollies or zooms out to reveal areas or
people that were not visible in the scene's first shot. All action happens
within what is already in frame. A cut scene's first shot is where the framing
may change: a new angle, a closer or wider shot, another part of the location.

CONTINUED SCENES. A continued scene is generated from a still of the previous
scene's final frame, so its first shot must hold that framing, people and
screen content and must not pan or move to another desk or person: the model
cannot see what is outside the still and invents it. If the story moves to
another character or desk, either cut to them, or let the previous scene's
last shot slowly reframe to bring them in, ending with them clearly visible,
so that the continued scene opens on them. Plan the end of each scene around
what the next scene opens on.

PEOPLE. Only characters visible in a scene's first shot appear in that scene.
Nobody walks in, leans in or appears in the background later; the model
invents a new person when asked for one who is not in frame. A cut is the way
to bring another character into the film: open the new scene on them.

NO AI SLOP. Nothing appears, disappears or changes by itself. Every object in
frame is in the bible's setting or is visibly brought in by a character (picked
up, carried in, handed over). Do not add new props, people or decorations in
later shots, and do not ask for transformations, magic effects or things
materialising. Icons, charts, dialogs and windows exist only on screens: always
say "on the monitor" or "on her screen", never let a graphic float in the air or
lie on the desk. Keep backgrounds simple and physically plausible.

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
            f"LAYOUT (as the camera sees it): {bible.layout}\n"
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


class PlanRevision(BaseModel):
    """The plan after the person reviewing the video asked for a change."""
    changes: str = Field(description=(
        "One or two sentences on what was changed to act on the feedback, or why nothing "
        "could be changed."))
    shots: list[Shot] = Field(description=(
        "Every shot given, with its index, revised where the feedback applies and otherwise "
        "repeated word for word."))
    bible: Bible | None = Field(default=None, description=(
        "The revised production bible when the feedback asks for a change to the style, "
        "setting, lighting, layout or a character and the whole video is being redone, with "
        "every field filled in (the unchanged ones repeated word for word). Otherwise null."))


REVISE_INSTRUCTIONS = """\
You are revising the plan of a short narrated training video. It was generated from this plan,
the person reviewing it was not happy with the scenes listed below and wrote down what was
wrong or what should change. Apply that feedback to the shots being redone.

- Change only what the feedback asks for. Every other detail of every shot stays word for word.
- Write the change into the shot the way the shots are already written: concrete, visible
  actions and expressions, the camera framing, the continuity. A vague wish ("more energy")
  becomes specific things people do.
- Return every shot given, with the same index. Never invent new shots or drop one.
- Keep the rules of the original plan: nobody speaks or mouths words, no legible text anywhere,
  a calm camera that never reveals what the first shot of the scene did not show, nothing
  appears or vanishes by itself.
"""

REVISE_BIBLE_NOTE = """\
The whole video is being redone, so the feedback may also be about the production bible (the
style, the setting, the lighting, the layout, a character's look). When it is, return the
revised bible with every field filled in; otherwise return null for it.
"""


def revise_plan(client, plan: VideoPlan, steps: list[dict], feedback: str, whole: bool) -> tuple[VideoPlan, str]:
    """Ask Gemini to apply the reviewer's feedback to the shots of `steps` (dicts with
    index, scene and text), and to the bible too when `whole`. Returns the new plan and
    Gemini's note on what changed. Shots Gemini did not return, or that were not asked
    about, are left as they were."""
    from google.genai import types

    asked = {int(st["index"]) for st in steps}
    shots = [sh for sh in plan.shots if sh.index in asked]
    bible = Bible(visual_style=plan.visual_style, setting=plan.setting, layout=plan.layout,
                  lighting=plan.lighting, characters=plan.characters)
    narration = "\n".join(f"- step {st['index']} (scene {st['scene']}): \"{st['text']}\"" for st in steps)
    contents = (
        f"FEEDBACK:\n{feedback.strip()}\n\n"
        f"PRODUCTION BIBLE:\n{bible.model_dump_json()}\n\n"
        + (REVISE_BIBLE_NOTE + "\n" if whole else "")
        + f"NARRATION OF THE STEPS BEING REDONE:\n{narration}\n\n"
        f"SHOTS BEING REDONE:\n{ShotList(shots=shots).model_dump_json()}\n\n"
        "Return the revised shots (and the revised bible when it applies)."
    )
    resp = client.models.generate_content(
        model=PROMPT_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=REVISE_INSTRUCTIONS,
            response_mime_type="application/json",
            response_schema=PlanRevision,
        ),
    )
    rev: PlanRevision = resp.parsed or PlanRevision.model_validate_json(resp.text)
    new = {sh.index: sh for sh in rev.shots if sh.index in asked}
    update: dict = {"shots": [new.get(sh.index, sh) for sh in plan.shots]}
    if whole and rev.bible is not None:
        update.update({f: getattr(rev.bible, f) for f in ("visual_style", "setting", "layout", "lighting", "characters")})
    return plan.model_copy(update=update), rev.changes


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
        "layout": strip_screen_text(bible.layout),
        "lighting": strip_screen_text(bible.lighting),
        "characters": chars})


def take_header(duration: int, step: int, from_frame: bool = False, cut: bool = False) -> str:
    if step > 0:
        return (f"Extend this video by {duration} seconds as the same continuous shot; no cut. "
                "Everyone and everything already in frame stays exactly as it is; "
                "nothing and nobody else enters.")
    if from_frame:
        start = (" It starts exactly on the given first frame and holds that framing; the people "
                 "in it keep exactly the same face, hair and clothes.")
    elif cut:
        start = (" A new setup of the same film: the same look, location and light, the same "
                 "people with exactly the same face, hair and clothes, framed as described.")
    else:
        start = ""
    return f"Single continuous {duration}-second shot from one film; no scene cuts.{start}"


DIALOGUE_LINE = ("DIALOGUE: None. Voice-over only; nobody on screen speaks, talks to camera "
                 "or moves their lips.")


def shot_lines(shot: Shot) -> str:
    return (f"CAMERA: {shot.camera}\n"
            f"ACTION: {shot.action}\n"
            f"EXPRESSION: {shot.expression}\n"
            f"CONTINUITY: {shot.continuity}\n"
            f"{DIALOGUE_LINE}\n"
            f"{NO_TEXT_LINE}")


def build_clip_prompt(bible: Bible, shot: Shot, seg: Segment) -> str:
    # Video models weigh the start of a prompt most, so the shot comes first. Only a
    # scene's first take gets the bible: an extension continues footage that already
    # shows the people and the room, and re-listing every prop and person invites the
    # model to add the ones that are not in frame.
    header = take_header(seg.duration, seg.step, from_frame=seg.scene > 1 and not seg.cut, cut=seg.cut)
    body = f"{header}\n{shot_lines(shot)}"
    if seg.step == 0:
        body += f"\n{bible_text(bible)}"
    return f"{body}\nAVOID: {NEGATIVE_PROMPT}."


def scene_bible(bible: Bible, seg: Segment, scenes: dict[int, ScenePlan]) -> Bible:
    """The bible for a segment's prompt: a cut scene's first take shows the part of the
    location and the framing the planner gave that scene."""
    plan = scenes.get(seg.index) if seg.step == 0 and seg.cut else None
    if plan is None:
        return bible
    own = {k: v for k in ("setting", "layout") if (v := getattr(plan, k).strip())}
    return clean_bible(bible.model_copy(update=own)) if own else bible


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


def assemble_prompts(client, plan: VideoPlan, segments: list[Segment],
                     max_chars: int = MAX_PROMPT_CHARS, attempts: int = 2) -> list[dict]:
    shots = {s.index: s for s in plan.shots}
    if len(shots) != len(segments):
        log(f"Warning: planner returned {len(shots)} shots for {len(segments)} segments; "
            "reusing the nearest shot where one is missing.")
    chosen = [shots.get(seg.index) or plan.shots[min(seg.index, len(plan.shots)) - 1]
              for seg in segments]
    pairs = list(zip(segments, remove_screen_text(client, chosen)))

    bible = clean_bible(Bible.model_validate(plan.model_dump()))
    scenes = {sp.first_segment: sp for sp in plan.scenes}
    for attempt in range(attempts + 1):
        bibles = [scene_bible(bible, seg, scenes) for seg, _ in pairs]
        prompts = [build_clip_prompt(b, shot, seg) for b, (seg, shot) in zip(bibles, pairs)]
        longest = max(len(p) for p in prompts)
        if longest <= max_chars or attempt == attempts:
            break
        # Room left for the bible in the longest prompt that carries it, with a little slack.
        longest_take = max(len(p) - len(bible_text(b)) for p, b, (seg, _) in zip(prompts, bibles, pairs)
                           if seg.step == 0)
        room = max_chars - longest_take - 100
        if room < 400:
            log("Warning: shot descriptions alone are close to the prompt limit; "
                "consider shorter segments of narration.")
            room = 400
        bible = condense_bible(client, bible, room)

    for i, p in enumerate(prompts, 1):
        if len(p) > max_chars:
            log(f"Warning: prompt {i} is {len(p)} chars (limit ~{max_chars}); "
                "the model may ignore the end. Edit prompts.json to shorten it.")
    # The bible rides along so an extension that has to become a new take can get it back.
    return [{**seg.to_dict(), "prompt": p, "bible": bible_text(b)}
            for (seg, _), p, b in zip(pairs, prompts, bibles)]


def scenes_from_plan(plan: VideoPlan, segments: list[Segment], max_scene: int) -> list[Segment]:
    """Regroup the steps into the planner's scenes. Without a usable scene list the
    timing-based grouping stands."""
    valid = {sp.first_segment: sp for sp in plan.scenes if 1 <= sp.first_segment <= len(segments)}
    if not valid:
        if plan.scenes:
            log("Warning: the planner's scene list names no existing segment; keeping the "
                "timing-based scenes.")
        return segments
    out = apply_scene_plan(segments, {i: sp.cut for i, sp in valid.items()}, max_scene)
    for s in out:
        if s.step == 0 and s.index in valid and valid[s.index].reason:
            log(f"  Scene {s.scene} ({'cut' if s.cut else 'continues' if s.scene > 1 else 'opens'}"
                f" at {s.start}s): {valid[s.index].reason}")
    return out


class TalkTrackDraft(BaseModel):
    talk_track: str = Field(description="The narration, as plain spoken prose.")


DRAFT_INSTRUCTIONS = """You write the voice-over narration for a short training video from a source
document. Rules:
- Say what the document teaches, in the order a learner needs it; leave out boilerplate, legal
  notes, tables of contents and references.
- Spoken prose only: short sentences, plain words, contractions, second person ("you"). No
  headings, bullets, numbering, markdown, stage directions, speaker labels or sound cues.
- Everything written is read aloud: spell out what the voice should say (say "fifty percent",
  not "50%"; say "for example", not "e.g.").
- Open with one sentence that says what the viewer will learn and close with one that says
  what to do next.
- Length: about 150 to 220 words (one to one and a half minutes) unless the notes ask for
  another length. Follow the notes wherever they say something else.
Return only the narration."""


def draft_talk_track(client, pdf: bytes, description: str, notes: str) -> str:
    """Have Gemini write the narration from a PDF, for the video `description` describes,
    following any `notes` (length, tone, what to cover)."""
    from google.genai import types

    user_msg = (f"VIDEO (setting and people, for context):\n{description.strip() or '(not described yet)'}\n\n"
                f"NOTES FROM THE AUTHOR:\n{notes.strip() or '(none)'}\n\n"
                "Write the narration from the attached document.")
    resp = client.models.generate_content(
        model=PROMPT_MODEL,
        contents=[types.Part.from_bytes(data=pdf, mime_type="application/pdf"), user_msg],
        config=types.GenerateContentConfig(
            system_instruction=DRAFT_INSTRUCTIONS,
            response_mime_type="application/json",
            response_schema=TalkTrackDraft,
        ),
    )
    draft = resp.parsed or TalkTrackDraft.model_validate_json(resp.text)
    return draft.talk_track.strip()


REFERENCES_NOTE = ("The attached reference pictures show the people, the place or the look wanted: "
                   "match their faces, builds, clothes, rooms, colours and style closely.")


def image_mime(path: Path) -> str:
    return {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(path.suffix.lower(), "image/png")


def image_part(path: Path):
    from google.genai import types
    return types.Part.from_bytes(data=path.read_bytes(), mime_type=image_mime(path))


def generate_preview_image(description: str, aspect: str, out: Path,
                           references: Sequence[Path] = ()) -> Path:
    """Make a still that shows what the video will look like, in the given aspect ratio,
    with the same no-lettering rule the video prompts use. `references` are pictures of
    the people, place or style to match. Returns `out`."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=require_env("GEMINI_API_KEY"))
    prompt = (f"{description}\n\n{NO_TEXT_LINE}\n"
              "A still frame from a film, photoreal, no captions or lettering.")
    contents: str | list = prompt
    if references:
        contents = [*(image_part(r) for r in references), f"{REFERENCES_NOTE}\n\n{prompt}"]
    log(f"Generating a preview image with {IMAGE_MODEL}...")
    resp = client.models.generate_content(
        model=IMAGE_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(aspect_ratio=aspect),
        ),
    )
    for cand in resp.candidates or []:
        for part in cand.content.parts or []:
            data = getattr(getattr(part, "inline_data", None), "data", None)
            if data:
                out.write_bytes(bytes(data))
                return out
    raise RuntimeError("The image model returned no image.")


def plan_prompts(scene: str, talk_track: str, segments: list[Segment], run: Path,
                 max_scene: int = MAX_SCENE_SECONDS, image: Path | None = None,
                 references: Sequence[Path] = ()) -> list[dict]:
    """Have Gemini write the production bible, group the steps into scenes and write one
    shot per step; returns one item per step with the prompt and the scene assignment.
    With `image` (the first frame of the video) the bible describes what the picture shows;
    `references` are further pictures of the people, place or style to describe."""
    prompts_path = run / "prompts.json"
    if prompts_path.exists():
        log("Prompts already exist, skipping planning.")
        return json.loads(prompts_path.read_text())

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=require_env("GEMINI_API_KEY"))
    seg_list = "\n".join(
        f"Segment {s.index}: {s.start}s-{s.end}s ({s.duration}s). Narration: \"{s.text}\""
        for s in segments
    )
    user_msg = f"""CREATIVE BRIEF (setting, scene, characters, style):
{scene}

FULL NARRATION SCRIPT:
{talk_track}

SEGMENTS ({len(segments)} segments, {segments[-1].end}s in all; group them into scenes of at most
{max_scene}s each and write one shot for each segment):
{seg_list}
"""
    parts: list = []
    if image is not None:
        user_msg += ("\nThe first attached picture is the first frame of the video: describe its people, "
                     "room, light and style in the bible, do not invent others.\n")
        parts.append(image_part(image))
    if references:
        user_msg += (f"\nThe {'other ' if image is not None else ''}attached pictures are references: "
                     "they show the people, the place or the look wanted. Describe them in the bible "
                     "(faces, builds, clothes, room, colours, style) so the video matches them.\n")
        parts.extend(image_part(r) for r in references)
    contents: str | list = [*parts, user_msg] if parts else user_msg
    log(f"Planning {len(segments)} shots with {PROMPT_MODEL}...")
    resp = client.models.generate_content(
        model=PROMPT_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=PLANNER_INSTRUCTIONS,
            response_mime_type="application/json",
            response_schema=VideoPlan,
        ),
    )
    plan: VideoPlan = resp.parsed or VideoPlan.model_validate_json(resp.text)
    (run / "plan.json").write_text(plan.model_dump_json(indent=2))

    segments = scenes_from_plan(plan, segments, max_scene)
    out = assemble_prompts(client, plan, segments)
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
            it["prompt"] = before_avoid(it["prompt"], fx.layout_hint(cs))
            changed = True
    if changed:
        (run / "prompts.json").write_text(json.dumps(items, indent=2, ensure_ascii=False))
    return data


# ---------------------------------------------------------------------------
# Step 5: clip generation (Gemini Omni: one take per scene, extended step by step)
# ---------------------------------------------------------------------------

POLL_SECONDS = 10
# Omni's input filter is not deterministic: the same request has been refused three
# times in a minute and accepted unchanged later. So a refused request waits, and is
# then sent again in a leaner form rather than verbatim.
BLOCK_WAIT_SECONDS = 30


def is_content_block(error: Exception) -> bool:
    text = str(error)
    return "content_blocked" in text or "Input blocked" in text


def lean_prompt(prompt: str) -> str:
    """The prompt reduced to the take header, what is in frame and the shot itself:
    no bible, fix notes or layout hints. Used when the full prompt keeps being refused."""
    keep = ("CAMERA", "ACTION", "EXPRESSION", "CONTINUITY", "DIALOGUE", "TEXT", "AVOID")
    first, *rest = prompt.splitlines()
    lines = [first] + [ln for ln in rest
                       if ln.startswith(IN_FRAME_PREFIX) or ln.partition(": ")[0] in keep]
    return "\n".join(lines)


def extract_last_frame(video: Path, out: Path) -> Path:
    ffmpeg("-sseof", "-0.1", "-i", str(video), "-frames:v", "1", "-update", "1", str(out))
    return out


def before_avoid(prompt: str, text: str) -> str:
    """Add text to a prompt, keeping the AVOID list as its last line."""
    body, sep, avoid = prompt.rpartition("\nAVOID: ")
    return f"{body}\n{text}{sep}{avoid}" if sep else f"{prompt}\n{text}"


def as_fresh_take(prompt: str, duration: int, bible: str = "") -> str:
    """Turn an extension prompt into one for a new take that starts on a given frame.
    Extension prompts carry no bible, so it goes back in before the AVOID line."""
    _, _, rest = prompt.partition("\n")
    if bible:
        rest = before_avoid(rest, bible)
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
        prompt = (item["prompt"] if item.get("step", 0) == 0
                  else as_fresh_take(item["prompt"], item["duration"], item.get("bible", "")))
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


class ClipReview(BaseModel):
    """What Gemini reports after watching a clip: lettering, and what the last frame shows."""
    amount: Literal["none", "minor", "too_much"] = Field(description=(
        "none: no lettering at all. minor: only small, incidental lettering that a viewer "
        "would not notice or that looks natural (a brand logo, UI buttons, icon labels, "
        "a window title bar, tiny or blurred interface text). too_much: a lot of text, or "
        "any prominent gibberish a viewer would notice (garbled headings, signs, captions, "
        "lines of fake writing on screens or paper, misspelt words that draw the eye)."))
    where: str = Field(description=(
        "Which surface shows the text and roughly where (e.g. 'monitor screen, centre'), "
        "or empty if none."))
    in_frame: str = Field(description=(
        "What the final frame shows, as the camera sees it, in at most 70 words: the shot "
        "size; each person by their name from the cast list (or 'a man'/'a woman'), where "
        "they are (frame-left, frame-right, centre, foreground, background), which way they "
        "face and what their hands do; every monitor or screen, on which side of its user it "
        "stands and what it shows; the props on the desk. Only what is actually visible."))


REVIEW_INSTRUCTIONS = """\
You review AI-generated video clips. Two things are needed.

1. Distracting text. The only text in the finished video is added later as
graphics, so writing the model painted into the footage is a problem when it
draws attention, above all garbled gibberish.

A small amount is fine and counts as "minor", for example:
- a brand logo on the back of a monitor or laptop (e.g. a DELL logo, a circular logo)
- desktop icon labels, a file explorer window, buttons and other small UI chrome
- tiny, blurred or out-of-focus interface text in the background

"too_much" means a lot of text, or prominent gibberish a viewer would notice:
garbled headings or banners, signs, captions, documents or screens filled with
lines of fake writing, misspelt words in focus. Plain icons and symbols without
letters (padlock, checkmark, shapes) are not text.

2. What the final frame shows (in_frame). The next piece of video is generated
as a continuation of this exact frame, and its prompt will be rewritten to match
your description, so be literal and precise about sides: use frame-left,
frame-right, centre, foreground and background as the camera sees them, never a
bare "left" or "right". Name people with the cast list when they match; say
which way each person faces and on which side of them their monitor stands.
List only what is visible in that last frame.
"""


def review_clip(client, clip: Path, cast: str = "") -> ClipReview | None:
    """Ask Gemini to watch a clip: how much lettering it shows and what its last frame
    contains. Returns None when the check fails, so a failed review never stops a run."""
    from google.genai import types
    try:
        resp = client.models.generate_content(
            model=TEXT_CHECK_MODEL,
            contents=[types.Part.from_bytes(data=clip.read_bytes(), mime_type="video/mp4"),
                      f"CAST:\n{cast or '(unknown)'}\n\nHow much text does this clip show, "
                      "and what does its final frame contain?"],
            config=types.GenerateContentConfig(
                system_instruction=REVIEW_INSTRUCTIONS,
                response_mime_type="application/json",
                response_schema=ClipReview,
            ),
        )
        review = resp.parsed or ClipReview.model_validate_json(resp.text)
        return ClipReview.model_validate(review.model_dump())
    except Exception as e:  # noqa: BLE001 - a failed check must not stop the run
        log(f"Warning: review of {clip.name} failed ({e}); keeping the clip.")
        return None


def too_much_text(review: ClipReview | None, clip: Path) -> str:
    """Where the clip shows too much lettering, or "" when it is clean or only shows
    minor lettering such as logos and buttons."""
    if review is None:
        return ""
    if review.amount == "minor":
        log(f"{clip.name}: minor lettering ({review.where.strip() or 'small'}); keeping it.")
    return (review.where.strip() or "somewhere in frame") if review.amount == "too_much" else ""


IN_FRAME_PREFIX = "IN FRAME NOW: "
_SHOT_LINES = ("CAMERA", "ACTION", "EXPRESSION", "CONTINUITY")

FIT_INSTRUCTIONS = """\
You adapt a planned shot to the footage it will continue from. The planned shot
was written before any video existed; IN FRAME NOW describes what the final
frame of the previous clip actually shows, and the new clip continues from that
exact frame. Rewrite camera, action, expression and continuity so they match
that frame: mention only the people, screens and props it lists, with the sides
it gives (frame-left, frame-right, centre, foreground, background, never a bare
"left" or "right"). If the planned action needs something that is not in frame,
use the nearest thing that is (the person who is there, the screen that is
there) and keep the point of the action. Nobody enters frame and nothing new
appears. The camera stays locked off or drifts very slowly within this framing.
Icons and graphics only on screens. Never quote or name readable text. Keep the
shot's index and the other fields unchanged; keep every field within its word limit.
"""


def fit_to_footage(client, item: dict, in_frame: str) -> str:
    """Rewrite the shot lines of a prompt so they match what the previous clip's last
    frame shows, and put that description near the top. Falls back to adding only the
    description when the rewrite fails or the prompt has no shot lines."""
    from google.genai import types
    lines = item["prompt"].splitlines()
    fields = {k: v for k, v in (ln.split(": ", 1) for ln in lines if ": " in ln) if k in _SHOT_LINES}
    if "ACTION" in fields:
        shot = Shot(index=item["index"], camera=fields.get("CAMERA", ""), action=fields["ACTION"],
                    expression=fields.get("EXPRESSION", ""), continuity=fields.get("CONTINUITY", ""))
        try:
            resp = client.models.generate_content(
                model=PROMPT_MODEL,
                contents=(f"{IN_FRAME_PREFIX}{in_frame}\n\nThe new clip lasts {item['duration']} s"
                          f". Narration over it: \"{item.get('text', '')}\"\n\nPLANNED SHOT:\n"
                          + shot.model_dump_json(indent=2)),
                config=types.GenerateContentConfig(
                    system_instruction=FIT_INSTRUCTIONS,
                    response_mime_type="application/json", response_schema=Shot),
            )
            fitted = clean_shot(Shot.model_validate((resp.parsed or Shot.model_validate_json(resp.text)).model_dump()))
            new = {"CAMERA": fitted.camera, "ACTION": fitted.action,
                   "EXPRESSION": fitted.expression, "CONTINUITY": fitted.continuity}

            def replace(ln: str) -> str:
                key, sep, _ = ln.partition(": ")
                return f"{key}: {new[key]}" if sep and key in fields else ln
            lines = [replace(ln) for ln in lines]
        except Exception as e:  # noqa: BLE001 - keep the planned shot, add what is in frame
            log(f"Warning: could not fit clip {item['index']} to the footage ({e}); "
                "keeping the planned shot.")
    lines = [ln for ln in lines if not ln.startswith(IN_FRAME_PREFIX)]
    return "\n".join([lines[0], f"{IN_FRAME_PREFIX}{in_frame}", *lines[1:]])


def with_text_fix(prompt: str, where: str) -> str:
    """Put a correction near the top of the prompt, where the model weighs it most."""
    first, _, rest = prompt.partition("\n")
    fix = (f"FIX: the last take showed lettering ({where[:120]}); that surface must be blank "
           "or show only colour blocks and simple icons.")
    return f"{first}\n{fix}\n{rest}" if rest else f"{first}\n{fix}"


def cast_list(items: list[dict]) -> str:
    """The CHARACTERS lines of the bible, so reviews can name who is in frame."""
    bible = next((it.get("bible", "") for it in items if it.get("bible")), "")
    _, _, chars = bible.partition("CHARACTERS")
    return chars.partition(":\n")[2] if chars else ""


def generate_clips(items: list[dict], run: Path, aspect: str, resolution: str,
                   start_image: Path | None, retries: int = 3,
                   text_retries: int = 2, fit: bool = True) -> list[Path]:
    """Make one clip per segment. Each scene is a single Omni video: its first segment
    is a new take and every later segment extends it, so people, props and the room
    carry over. A new scene either starts from the last frame of the previous clip or,
    when the planner cut to a new setup, from the prompt alone.

    After each clip Gemini watches it and reports what its last frame shows; the next
    prompt is rewritten to match that (`fit`), because the plan was written before any
    footage existed and Omni only continues what it can see."""
    from google import genai

    client = genai.Client(api_key=require_env("GEMINI_API_KEY"))
    clip_dir = run / "clips"
    clip_dir.mkdir(exist_ok=True)
    paths = [clip_dir / f"clip_{it['index']:02d}.mp4" for it in items]
    cast = cast_list(items)
    # Interaction ids let a later step (or a --resume) extend the scene so far; the
    # review of each clip's last frame is kept beside them for fitting the next prompt.
    state_path = clip_dir / "steps.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    prompts_path = run / "prompts.json"

    def save_prompts() -> None:
        if prompts_path.exists():
            prompts_path.write_text(json.dumps(items, indent=2, ensure_ascii=False))

    def last_frame(i: int) -> Path | None:
        if i == 0:
            return start_image
        return extract_last_frame(paths[i - 1], clip_dir / f"lastframe_{i:02d}.png")

    def render(i: int, item: dict, previous_id: str | None, scene_len: float,
               frame: Path | None, required: bool = True) -> bool:
        """Generate one clip, retrying on failure. A required clip stops the run when
        every attempt fails; an optional one (a retake) just reports False."""
        n, raw = item["index"], paths[i].with_suffix(".raw.mp4")
        for attempt in range(1, retries + 1):
            kind = ("extending the scene" if previous_id else
                    "new take from a frame" if frame is not None else "new take")
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
                return True
            except Exception as e:  # noqa: BLE001 - surface any API failure and retry
                log(f"Clip {n} failed: {e}")
                if is_content_block(e):
                    if attempt == 1:
                        log(f"Omni's content filter refused the input; it is not consistent, "
                            f"so waiting {BLOCK_WAIT_SECONDS}s and trying the same request again.")
                        time.sleep(BLOCK_WAIT_SECONDS)
                    elif lean_prompt(item["prompt"]) != item["prompt"]:
                        log("Refused again; sending only the shot, without the bible and notes.")
                        item = {**item, "prompt": lean_prompt(item["prompt"]), "bible": ""}
                if attempt == retries - 1 and previous_id:
                    log("Last attempt: starting a new take from the previous clip's last frame "
                        "instead of extending.")
                    previous_id, frame = None, last_frame(i)
                time.sleep(5 * attempt)
        if required:
            sys.exit(f"Clip {n} failed {retries} times. "
                     f"Fix the prompt in {run / 'prompts.json'} and rerun with --resume {run}")
        return False

    def review(i: int, previous_id: str | None, scene_len: float, frame: Path | None) -> None:
        """Watch the clip: retake the step while Gemini sees too much lettering (Omni
        sometimes paints it anyway), then remember what the last frame shows."""
        if text_retries <= 0 and not fit:
            return
        n = items[i]["index"]
        for take in range(text_retries + 1):
            result = review_clip(client, paths[i], cast)
            where = too_much_text(result, paths[i]) if text_retries > 0 else ""
            if not where:
                break
            if take == text_retries:
                log(f"Warning: clip {n} still shows too much text ({where}) after {text_retries} retake(s); "
                    f"keeping it. Delete {paths[i]} and rerun with --resume {run} to try again.")
                break
            log(f"Clip {n} shows too much text ({where}); retaking ({take + 1}/{text_retries})...")
            if not render(i, {**items[i], "prompt": with_text_fix(items[i]["prompt"], where)},
                          previous_id, scene_len, frame, required=False):
                log(f"Warning: the retake of clip {n} failed; keeping the clip with its text. "
                    f"Delete {paths[i]} and rerun with --resume {run} to try again.")
                break
        if result is not None and result.in_frame.strip():
            state[str(n)]["in_frame"] = result.in_frame.strip()
            state_path.write_text(json.dumps(state, indent=2))

    # Extensions build on the step before, so the clips are made in order.
    for i, item in enumerate(items):
        if paths[i].exists():
            continue
        prev = state.get(str(items[i - 1]["index"]), {}) if i else {}
        # A cut opens on a new setup, so it neither continues the last frame nor fits to it.
        cut = i > 0 and item.get("step", 0) == 0 and item.get("cut", False)
        if fit and not cut and prev.get("in_frame") and IN_FRAME_PREFIX not in item["prompt"]:
            log(f"Fitting the prompt of clip {item['index']} to what clip "
                f"{items[i - 1]['index']} ends on...")
            item["prompt"] = fit_to_footage(client, item, prev["in_frame"])
            save_prompts()
        previous_id = prev.get("interaction") if item.get("step", 0) > 0 else None
        scene_len = prev.get("scene_len", 0.0) if previous_id else 0.0
        if previous_id and scene_len + item["duration"] > MAX_SCENE_SECONDS + 0.5:
            # Omni rejects extending past this length (plans from older versions allowed 40 s).
            log(f"Clip {item['index']} would make the scene {scene_len + item['duration']:.0f}s, "
                f"over Omni's {MAX_SCENE_SECONDS}s; starting a new take from the last frame.")
            previous_id, scene_len = None, 0.0
        frame = None
        if not previous_id and not cut:
            if item.get("step", 0) > 0 and str(items[i - 1]["index"]) not in state:
                log(f"No saved interaction to extend for clip {item['index']}; "
                    "starting a new take from the previous clip's last frame.")
            frame = last_frame(i)
        render(i, item, previous_id, scene_len, frame)
        review(i, previous_id, scene_len, frame)
    return paths


# ---------------------------------------------------------------------------
# Steps 6 + 7: concatenate, then add the voice track
# ---------------------------------------------------------------------------

def concatenate(clips: list[Path], run: Path, name: str = "video_silent.mp4") -> Path:
    out = run / name
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


def add_voice(video: Path, audio: Path, run: Path, overlay: Path | None = None,
              name: str = "final.mp4") -> Path:
    out = run / name
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
        kind = ("extends the take" if it.get("step", 0) > 0 else
                "new take (cut)" if it.get("cut") else
                "new take from the last frame" if it.get("scene", 1) > 1 else "new take")
        print(f"\n--- Clip {it['index']} | {it['start']}-{it['end']}s ({it['duration']}s) | "
              f"scene {it.get('scene', 1)}, {kind} ---")
        print(it["prompt"])
    print("\n" + "=" * 78)
    if callouts_path.exists():
        data = json.loads(callouts_path.read_text())
        print(f"Keyword graphics (accent {data.get('accent_color')}):")
        for c in data.get("callouts", []):
            group = f", list '{c['list_name']}'" if c.get("list_name") else ""
            print(f"  clip {c['segment_index']}: \"{c['text']}\"  [{c['style']}, {c['position']}"
                  f"{group}]  on \"{c['anchor']}\"")
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
                    help="Longest single take before a new scene has to start "
                         f"(10-{MAX_SCENE_SECONDS}, default {MAX_SCENE_SECONDS}).")
    ap.add_argument("--callouts", default="medium", choices=["off", "low", "medium", "high"],
                    help="How often keyword graphics pop up on screen (default: medium).")
    ap.add_argument("--text-retries", type=int, default=2, metavar="N",
                    help="Gemini watches each clip for lettering and Omni retakes it up to N times "
                         "(default: 2; 0 turns the check off). Retakes cost video credits.")
    ap.add_argument("--no-fit", action="store_true",
                    help="Don't rewrite each prompt to match what the previous clip actually "
                         "ends on (Gemini describes the last frame of every clip).")
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
    seg_path = run / "segments.json"
    if (run / "prompts.json").exists() and seg_path.exists():
        # Resuming: keep the plan the prompts were written for.
        segments = [Segment.from_dict(d) for d in json.loads(seg_path.read_text())]
    else:
        segments = plan_segments(words_from_alignment(alignment), duration, args.max_scene)
    seg_path.write_text(json.dumps([s.to_dict() for s in segments], indent=2))
    log(f"{len(segments)} steps, {segments[-1].end}s for {duration:.1f}s of audio.")
    items = plan_prompts(scene, talk, segments, run, args.max_scene)
    if [(i["index"], i["duration"]) for i in items] != [(s.index, s.duration) for s in segments]:
        sys.exit(f"{run / 'prompts.json'} was planned with different segments (an older version of "
                 "this script?). Start a new run, or delete prompts.json and callouts.json.")
    # The planner groups the steps into scenes; the prompts carry that grouping.
    segments = [Segment.from_dict(it) for it in items]
    seg_path.write_text(json.dumps([s.to_dict() for s in segments], indent=2))
    log(f"{segments[-1].scene} scene(s). Each scene is one video: a new take, then extended "
        "step by step.")
    for n in range(1, segments[-1].scene + 1):
        steps = [s for s in segments if s.scene == n]
        ext = " ".join(f"+{s.duration}s" for s in steps[1:]) or "none"
        how = ("cut to a new setup" if steps[0].cut else
               "from the last frame" if n > 1 else "new take")
        log(f"  Scene {n}: {sum(s.duration for s in steps)}s = {steps[0].duration}s {how}, "
            f"extended {ext}")
    callout_data = plan_callouts(items, segments, run, args.callouts)
    if not args.yes:
        items = confirm_prompts(items, run)

    # 5: clips
    clips = generate_clips(items, run, args.aspect, args.resolution, args.start_image,
                           text_retries=args.text_retries, fit=not args.no_fit)

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
