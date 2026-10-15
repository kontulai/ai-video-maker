#!/usr/bin/env python3
"""AI video maker: scene description + talk track -> narrated video.

Pipeline
  1. Ask for the setting / scene description.
  2. Ask for the talk track (the words to be spoken).
  3. Generate the voice track with ElevenLabs (with word timings).
  4. Split the talk track into 4/6/8 s segments and have Gemini write a
     verbose, continuity-preserving Veo prompt for each one.
  5. Generate each segment with Veo 3.1 Fast.
  6. Concatenate the segments.
  7. Lay the voice track over the final video.

Every step writes its output into a run folder (runs/<timestamp>/). Running
again with --resume <folder> skips the steps that are already done, so a
failed Veo clip never costs you the whole run.
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

import imageio_ffmpeg
import requests
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from segmentation import Segment, plan_segments, words_from_alignment

load_dotenv()

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
PROMPT_MODEL = os.getenv("PROMPT_MODEL", "gemini-4-argon")
VIDEO_MODEL = os.getenv("VIDEO_MODEL", "veo-3.1-fast-generate-preview")
ELEVEN_VOICE = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
ELEVEN_MODEL = os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")

NEGATIVE_PROMPT = (
    "subtitles, captions, on-screen text, watermark, logo, scene cut, jump cut, "
    "change of character appearance, change of wardrobe, change of hairstyle, "
    "change of location, extra people, distorted faces, deformed hands, low quality"
)
VEO_PROMPT_SOFT_LIMIT = 4000  # characters; very long prompts get truncated by Veo


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


def ffmpeg(*args: str) -> None:
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args]
    subprocess.run(cmd, check=True)


def media_duration(path: Path) -> float:
    """Duration in seconds, parsed from ffmpeg's header output (no ffprobe needed)."""
    out = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)],
                         capture_output=True, text=True).stderr
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", out)
    if not m:
        raise RuntimeError(f"Could not read duration of {path}")
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(s)


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
# Step 4: segmentation + Veo prompts (Gemini)
# ---------------------------------------------------------------------------

class Character(BaseModel):
    name: str = Field(description="Short name used to refer to the character.")
    appearance: str = Field(description=(
        "Exhaustive physical description that never changes: apparent age, gender, "
        "ethnicity, skin tone, face shape, eye colour, eyebrows, nose, facial hair, "
        "hair colour, length, texture and style, build, height, distinguishing marks."))
    wardrobe: str = Field(description=(
        "Exact clothing and accessories with colours, materials, fit and details."))
    mannerisms: str = Field(description="Posture, typical gestures, demeanour.")


class Shot(BaseModel):
    index: int
    camera: str = Field(description="Shot size, lens feel, angle and camera movement.")
    action: str = Field(description=(
        "What happens on screen during this segment, in detail, matched to what is said."))
    expression: str = Field(description="Facial expression and body language of the people on screen.")
    speaker_on_screen: bool = Field(description=(
        "True if a visible character is speaking the line to camera in this shot; "
        "False if the line is voice-over over B-roll."))
    speaker_name: str = Field(description="Name of the on-screen speaker, or empty string.")
    continuity: str = Field(description=(
        "How this shot connects to the previous and next shots (positions, props, "
        "eyeline, motion) so the clips cut together seamlessly."))


class VideoPlan(BaseModel):
    title: str
    visual_style: str = Field(description=(
        "Film look: medium (e.g. photoreal cinematic), camera/film stock feel, "
        "colour grade, depth of field, frame rate feel. Applies to every shot."))
    setting: str = Field(description=(
        "Exhaustive description of the location: architecture, furniture, props and "
        "their exact positions, background details, time of day, weather."))
    lighting: str = Field(description="Light sources, direction, quality and colour temperature.")
    ambience: str = Field(description="Background sound ambience (no music, no speech).")
    characters: list[Character]
    shots: list[Shot]


PLANNER_INSTRUCTIONS = """\
You are a film director and prompt engineer for Google Veo 3.1, a text-to-video
model. You will receive a creative brief, a narration script and a list of
timed segments. Each segment becomes one separate Veo clip of 4, 6 or 8 seconds,
and the clips are cut together into one continuous video with the narration
laid over the top.

Veo has no memory between clips, so consistency must come from the prompts.
Produce a production bible (visual style, setting, lighting, ambience,
characters) that is extremely specific and concrete: exact colours, materials,
positions, ages, hairstyles, clothing details. Never use vague words like
"stylish" or "modern" without saying exactly what they look like. The bible
will be repeated word-for-word in every clip prompt, so write it once, well,
and keep each field dense (no filler).

Then write exactly one shot per segment, in order, with the same index. Match
the visuals to what is being said in that segment and to its duration. Keep
the same characters, wardrobe and location in every shot unless the brief
explicitly asks otherwise. Describe only things that can be shown in a single
continuous take of that length.
{chain_note}
"""

CHAIN_NOTE = """\
IMPORTANT: each clip will start from the exact last frame of the previous clip.
So every shot must begin where the previous one ended (same framing, same
positions) and use smooth continuous camera movement. Do not plan cuts to new
angles or new locations between shots."""


def build_veo_prompt(plan: VideoPlan, shot: Shot, seg: Segment, n_segments: int,
                     lip_sync: bool) -> str:
    chars = "\n".join(
        f"- {c.name}: {c.appearance} Wardrobe: {c.wardrobe} Mannerisms: {c.mannerisms}"
        for c in plan.characters
    )
    if shot.speaker_on_screen and lip_sync and seg.text:
        who = shot.speaker_name or "The character"
        dialogue = f'{who} speaks directly to camera, saying: "{seg.text}"'
    else:
        dialogue = ("No one on screen speaks; this shot plays under a voice-over. "
                    "Mouths stay closed or move naturally without talking.")

    return f"""\
A {seg.duration}-second single continuous shot. Clip {seg.index} of {n_segments} from the same film; it must match the other clips exactly.

VISUAL STYLE: {plan.visual_style}

SETTING (identical in every clip): {plan.setting}

LIGHTING (identical in every clip): {plan.lighting}

CHARACTERS (look exactly like this in every clip):
{chars}

CAMERA: {shot.camera}

ACTION: {shot.action}

EXPRESSION AND BODY LANGUAGE: {shot.expression}

CONTINUITY: {shot.continuity}

DIALOGUE: {dialogue}

AUDIO: {plan.ambience}. No music.

No subtitles, captions or on-screen text."""


def plan_prompts(scene: str, talk_track: str, segments: list[Segment], run: Path,
                 chain: bool, lip_sync: bool) -> list[dict]:
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

SEGMENTS ({len(segments)} clips, write one shot for each):
{seg_list}
"""
    log(f"Planning {len(segments)} shots with {PROMPT_MODEL}...")
    resp = client.models.generate_content(
        model=PROMPT_MODEL,
        contents=user_msg,
        config=types.GenerateContentConfig(
            system_instruction=PLANNER_INSTRUCTIONS.format(chain_note=CHAIN_NOTE if chain else ""),
            response_mime_type="application/json",
            response_schema=VideoPlan,
        ),
    )
    plan: VideoPlan = resp.parsed or VideoPlan.model_validate_json(resp.text)
    (run / "plan.json").write_text(plan.model_dump_json(indent=2))

    shots = {s.index: s for s in plan.shots}
    if len(shots) != len(segments):
        log(f"Warning: planner returned {len(shots)} shots for {len(segments)} segments; "
            "reusing the nearest shot where one is missing.")
    out = []
    for seg in segments:
        shot = shots.get(seg.index) or plan.shots[min(seg.index, len(plan.shots)) - 1]
        prompt = build_veo_prompt(plan, shot, seg, len(segments), lip_sync)
        if len(prompt) > VEO_PROMPT_SOFT_LIMIT:
            log(f"Warning: prompt {seg.index} is {len(prompt)} chars; Veo may ignore the tail.")
        out.append({**seg.to_dict(), "prompt": prompt})
    prompts_path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    return out


# ---------------------------------------------------------------------------
# Step 5: Veo clip generation
# ---------------------------------------------------------------------------

def extract_last_frame(video: Path, out: Path) -> Path:
    ffmpeg("-sseof", "-0.1", "-i", str(video), "-frames:v", "1", "-update", "1", str(out))
    return out


def load_image(path: Path):
    from google.genai import types
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return types.Image(image_bytes=path.read_bytes(), mime_type=mime)


def start_clip(client, item: dict, aspect: str, resolution: str, first_frame: Path | None):
    from google.genai import types
    kwargs = {}
    if first_frame is not None:
        kwargs["image"] = load_image(first_frame)
    return client.models.generate_videos(
        model=VIDEO_MODEL,
        prompt=item["prompt"],
        config=types.GenerateVideosConfig(
            aspect_ratio=aspect,
            resolution=resolution,
            duration_seconds=item["duration"],
            number_of_videos=1,
            negative_prompt=NEGATIVE_PROMPT,
        ),
        **kwargs,
    )


def wait_and_save(client, operation, dest: Path) -> None:
    while not operation.done:
        time.sleep(10)
        operation = client.operations.get(operation)
    if operation.error:
        raise RuntimeError(f"Veo error: {operation.error}")
    videos = (operation.response and operation.response.generated_videos) or []
    if not videos:
        reason = getattr(operation.response, "rai_media_filtered_reasons", None)
        raise RuntimeError(f"Veo returned no video (likely safety filter): {reason}")
    client.files.download(file=videos[0].video, destination=str(dest))


def generate_clips(items: list[dict], run: Path, aspect: str, resolution: str,
                   chain: bool, start_image: Path | None, retries: int = 3) -> list[Path]:
    from google import genai

    client = genai.Client(api_key=require_env("GEMINI_API_KEY"))
    clip_dir = run / "clips"
    clip_dir.mkdir(exist_ok=True)
    paths = [clip_dir / f"clip_{it['index']:02d}.mp4" for it in items]

    def first_frame_for(i: int) -> Path | None:
        if i == 0:
            return start_image
        if chain:
            return extract_last_frame(paths[i - 1], clip_dir / f"lastframe_{i:02d}.png")
        return None

    def make(i: int) -> None:
        item = items[i]
        frame = first_frame_for(i)
        for attempt in range(1, retries + 1):
            try:
                log(f"Clip {item['index']}/{len(items)} ({item['duration']}s), attempt {attempt}...")
                op = start_clip(client, item, aspect, resolution, frame)
                wait_and_save(client, op, paths[i])
                log(f"Clip {item['index']} saved.")
                return
            except Exception as e:  # noqa: BLE001 - surface any API failure and retry
                log(f"Clip {item['index']} failed: {e}")
                if attempt == retries - 1 and frame is not None and i > 0:
                    log("Last attempt: dropping the chained first frame.")
                    frame = None
                time.sleep(5 * attempt)
        sys.exit(f"Clip {item['index']} failed {retries} times. "
                 f"Fix the prompt in {run / 'prompts.json'} and rerun with --resume {run}")

    if chain:
        # Each clip needs the previous clip's last frame, so run in order.
        for i in range(len(items)):
            if not paths[i].exists():
                make(i)
    else:
        # Independent clips: submit them all, then collect.
        pending = []
        for i, item in enumerate(items):
            if paths[i].exists():
                continue
            log(f"Submitting clip {item['index']}/{len(items)} ({item['duration']}s)...")
            pending.append((i, start_clip(client, item, aspect, resolution,
                                          start_image if i == 0 else None)))
        for i, op in pending:
            try:
                wait_and_save(client, op, paths[i])
                log(f"Clip {items[i]['index']} saved.")
            except Exception as e:  # noqa: BLE001
                log(f"Clip {items[i]['index']} failed ({e}); retrying on its own.")
                make(i)
    return paths


# ---------------------------------------------------------------------------
# Steps 6 + 7: concatenate, then add the voice track
# ---------------------------------------------------------------------------

def concatenate(clips: list[Path], run: Path) -> Path:
    out = run / "video_silent.mp4"
    list_file = run / "concat.txt"
    list_file.write_text("".join(f"file '{p.resolve()}'\n" for p in clips))
    log("Concatenating clips...")
    # Re-encode so clips with slightly different timestamps join cleanly. Veo's own
    # audio is dropped here; the narration replaces it in the next step.
    ffmpeg("-f", "concat", "-safe", "0", "-i", str(list_file),
           "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
           "-pix_fmt", "yuv420p", "-r", "24", str(out))
    return out


def add_voice(video: Path, audio: Path, run: Path) -> Path:
    out = run / "final.mp4"
    tail = 0.5  # keep a little picture after the last word
    length = min(media_duration(video), media_duration(audio) + tail)
    log("Adding voice track...")
    ffmpeg("-i", str(video), "-i", str(audio),
           "-map", "0:v:0", "-map", "1:a:0",
           "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
           "-t", f"{length:.3f}", "-movflags", "+faststart", str(out))
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
    print("\n" + "=" * 78)
    for it in items:
        print(f"\n--- Clip {it['index']} | {it['start']}-{it['end']}s ({it['duration']}s) ---")
        print(it["prompt"])
    print("\n" + "=" * 78)
    print(f"{len(items)} clips, {total}s of video. Prompts saved in {run / 'prompts.json'}.")
    while True:
        ans = input("Generate the clips with Veo now? [y]es / [e]dited the file, reload / [n]o: ").strip().lower()
        if ans in ("y", "yes"):
            return items
        if ans in ("e", "edit"):
            items = json.loads((run / "prompts.json").read_text())
            print("Reloaded prompts.json.")
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
    ap.add_argument("--resolution", default="720p", choices=["720p"],
                    help="Veo only allows 1080p/4k for 8 s clips, so mixed 4/6/8 s clips use 720p.")
    ap.add_argument("--no-chain", action="store_true",
                    help="Don't start each clip from the previous clip's last frame. "
                         "Faster (clips run in parallel) but less consistent.")
    ap.add_argument("--no-lip-sync", action="store_true",
                    help="Don't put the spoken line in the prompt (on-screen mouths won't move with the words).")
    ap.add_argument("--yes", "-y", action="store_true", help="Skip the confirmation before calling Veo.")
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
    segments = plan_segments(words_from_alignment(alignment), duration)
    (run / "segments.json").write_text(json.dumps([s.to_dict() for s in segments], indent=2))
    log("Segments: " + ", ".join(f"{s.duration}s" for s in segments)
        + f" = {segments[-1].end}s for {duration:.1f}s of audio")
    chain = not args.no_chain
    items = plan_prompts(scene, talk, segments, run, chain, not args.no_lip_sync)
    if not args.yes:
        items = confirm_prompts(items, run)

    # 5: clips
    clips = generate_clips(items, run, args.aspect, args.resolution, chain, args.start_image)

    # 6 + 7: assemble
    silent = concatenate(clips, run)
    final = add_voice(silent, audio, run)
    log(f"Done: {final.resolve()}")


if __name__ == "__main__":
    main()
