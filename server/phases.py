"""What each phase does when its button is pressed. Every function here runs inside a
job thread, takes the project id and the job's log function, and saves the project
when its output is complete. Nothing here is called without a POST .../run/... route."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import make_video as mv
from . import projects
from .projects import Project


def _commit(project_id: str, phase: str, **changes) -> Project:
    """Reload the project (the user may have edited other fields meanwhile), apply the
    phase's own changes, record the new output version and save."""
    p = projects.load(project_id)
    for key, value in changes.items():
        setattr(p, key, value)
    projects.bump(p, phase)
    projects.save(p)
    return p


def _unlink(*paths: Path) -> None:
    for path in paths:
        if path.exists():
            path.unlink()


@contextmanager
def _keep_old(*paths: Path):
    """Move a phase's old output aside while it is remade, so a failed run leaves the
    old output in place instead of nothing."""
    aside = [(path, path.with_name(path.name + ".old")) for path in paths if path.exists()]
    for path, old in aside:
        path.replace(old)
    try:
        yield
    except BaseException:
        for path, old in aside:
            old.replace(path)
        raise
    _unlink(*(old for _, old in aside))


# -- phase 1: setting -------------------------------------------------------------------
def reference_paths(p: Project) -> list[Path]:
    """The uploaded reference pictures that still exist, in upload order."""
    run = projects.folder(p.id)
    return [run / name for name in p.references if (run / name).is_file()]


def generate_preview(p: Project, log) -> None:
    run = projects.folder(p.id)
    (run / "scene.txt").write_text(p.description)
    name = f"preview_{len(p.previews) + 1}.png"
    mv.generate_preview_image(p.description, p.settings.aspect, run / name, reference_paths(p))
    log(f"Preview saved: {name}")
    _commit(p.id, "setting", previews=[*p.previews, name], current_preview=name)


# -- phase 2: talk track ----------------------------------------------------------------
def draft_talk_track(p: Project, log, notes: str) -> None:
    """Write the talk track from source.pdf in the project folder. The text is an input the
    user edits, so it is saved without a new phase version, as a PATCH would."""
    from google import genai

    pdf = (projects.folder(p.id) / "source.pdf").read_bytes()
    log(f"Drafting the talk track from the PDF ({len(pdf) // 1024} kB) with {mv.PROMPT_MODEL}...")
    client = genai.Client(api_key=mv.require_env("GEMINI_API_KEY"))
    text = mv.draft_talk_track(client, pdf, p.description, notes)
    log(f"Drafted {len(text.split())} words.")
    fresh = projects.load(p.id)
    fresh.talk_track = text
    projects.save(fresh)


def generate_voice(p: Project, log) -> None:
    run = projects.folder(p.id)
    (run / "talk_track.txt").write_text(p.talk_track)
    with _keep_old(run / "voice.mp3", run / "alignment.json"):
        audio, _alignment = mv.generate_voice(p.talk_track, run)
    _commit(p.id, "voice", voice_seconds=round(mv.media_duration(audio), 2))


# -- phase 3: the scene plan --------------------------------------------------------------------
import hashlib  # noqa: E402
import json  # noqa: E402

from segmentation import Segment, plan_segments, words_from_alignment  # noqa: E402


def read_json(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def load_items(p: Project) -> list[dict]:
    return read_json(projects.folder(p.id) / "prompts.json", [])


def load_segments(p: Project) -> list[Segment]:
    return [Segment.from_dict(d) for d in read_json(projects.folder(p.id) / "segments.json", [])]


def load_plan(p: Project) -> mv.VideoPlan | None:
    path = projects.folder(p.id) / "plan.json"
    return mv.VideoPlan.model_validate_json(path.read_text()) if path.exists() else None


def preview_path(p: Project) -> Path | None:
    if p.current_preview:
        path = projects.folder(p.id) / p.current_preview
        if path.exists():
            return path
    return None


def shot_fingerprint(plan: mv.VideoPlan, index: int) -> str:
    """What a hand-edited prompt is pinned to: the bible and its own shot."""
    shot = next((s for s in plan.shots if s.index == index), None)
    bible = mv.Bible.model_validate(plan.model_dump())
    text = mv.bible_text(bible) + (shot.model_dump_json() if shot else "")
    return hashlib.sha1(text.encode()).hexdigest()


def scene_fingerprints(p: Project) -> dict[int, str]:
    """Per scene: a hash of the prompts it would be made from. The first frame (the
    current preview) belongs to scene 1."""
    out: dict[int, hashlib._Hash] = {}
    for item in load_items(p):
        scene = int(item.get("scene", 1))
        if scene not in out:
            out[scene] = hashlib.sha1()
            if scene == 1 and p.current_preview:
                out[scene].update(p.current_preview.encode())
        text = item["prompt"] if item.get("pinned_for") else item.get("source_prompt", item["prompt"])
        out[scene].update(text.encode())
    return {scene: h.hexdigest() for scene, h in out.items()}


def without_in_frame(prompt: str) -> str:
    """The prompt without the IN FRAME NOW line a previous fit added; fitting puts a
    fresh one in when the clip is made."""
    return "\n".join(ln for ln in prompt.splitlines() if not ln.startswith(mv.IN_FRAME_PREFIX))


def _write_plan_files(run: Path, plan: mv.VideoPlan, segments: list[Segment], items: list[dict]) -> None:
    (run / "plan.json").write_text(plan.model_dump_json(indent=2))
    write_json(run / "segments.json", [s.to_dict() for s in segments])
    write_json(run / "prompts.json", items)


def generate_plan(p: Project, log) -> None:
    run = projects.folder(p.id)
    alignment = read_json(run / "alignment.json", None)
    if alignment is None:
        raise RuntimeError("Generate the voice track first.")
    duration = mv.media_duration(run / "voice.mp3")
    segments = plan_segments(words_from_alignment(alignment), duration, p.settings.max_scene)
    log(f"{len(segments)} steps, {segments[-1].end}s for {duration:.1f}s of audio.")
    with _keep_old(run / "plan.json", run / "segments.json", run / "prompts.json"):
        write_json(run / "segments.json", [s.to_dict() for s in segments])
        items = mv.plan_prompts(p.description, p.talk_track, segments, run, p.settings.max_scene,
                                image=preview_path(p), references=reference_paths(p))
        for item in items:
            item["source_prompt"] = item["prompt"]
            item["pinned_for"] = ""
        segments = [Segment.from_dict(it) for it in items]
        write_json(run / "segments.json", [s.to_dict() for s in segments])
        write_json(run / "prompts.json", items)
    log(f"{segments[-1].scene} scene(s) planned.")
    _commit(p.id, "plan")


def rebuild_prompts(p: Project, plan: mv.VideoPlan, pinned: dict[int, str]) -> list[dict]:
    """Rebuild every step's prompt from an edited plan, with no model call. A pinned
    (hand-edited) prompt stays as long as its shot and the bible are unchanged."""
    run = projects.folder(p.id)
    old = {it["index"]: it for it in load_items(p)}
    base = load_segments(p)
    segments = mv.scenes_from_plan(plan, base, p.settings.max_scene)
    items = mv.assemble_prompts(None, plan, segments, attempts=0)
    for item in items:
        item["source_prompt"] = item["prompt"]
        item["pinned_for"] = ""
        if item["index"] in pinned:
            fp = shot_fingerprint(plan, item["index"])
            if old.get(item["index"], {}).get("pinned_for") == fp:
                item["prompt"] = pinned[item["index"]]
                item["pinned_for"] = fp
    _write_plan_files(run, plan, segments, items)
    projects.bump(p, "plan")
    projects.save(p)
    return items


def pin_prompt(p: Project, index: int, prompt: str) -> list[dict]:
    """Keep a hand-edited prompt for one step until its shot or the bible changes."""
    run = projects.folder(p.id)
    plan = load_plan(p)
    items = load_items(p)
    for item in items:
        if item["index"] == index:
            item["prompt"] = without_in_frame(prompt)
            item["pinned_for"] = shot_fingerprint(plan, index) if plan else "hand"
            break
    else:
        raise KeyError(index)
    write_json(run / "prompts.json", items)
    projects.bump(p, "plan")
    projects.save(p)
    return items


def pinned_prompts(p: Project) -> dict[int, str]:
    return {it["index"]: it["prompt"] for it in load_items(p) if it.get("pinned_for")}


# -- phase 4: the video, scene by scene --------------------------------------------------------------
from typing import Literal  # noqa: E402

from pydantic import BaseModel  # noqa: E402

from .projects import SceneState  # noqa: E402


class SceneStep(BaseModel):
    index: int
    duration: int
    clip: str | None  # path inside the project folder, when the clip exists


class SceneView(BaseModel):
    number: int
    start: float
    end: float
    cut: bool
    steps: list[SceneStep]
    status: Literal["missing", "stale", "older_footage", "ready"]
    video: str | None = None  # the scene's clips stitched into one file, when they all exist


def clip_name(index: int) -> str:
    return f"clips/clip_{index:02d}.mp4"


def scene_name(number: int) -> str:
    return f"clips/scene_{number:02d}.mp4"


def scene_video(run: Path, number: int, steps: list[SceneStep]) -> str | None:
    """The stitched scene file, when it exists and is newer than every clip in it."""
    path = run / scene_name(number)
    if any(s.clip is None for s in steps) or not path.exists():
        return None
    newest = max((run / s.clip).stat().st_mtime_ns for s in steps if s.clip)
    return scene_name(number) if path.stat().st_mtime_ns >= newest else None


def scene_status(p: Project) -> list[SceneView]:
    """Every planned scene with its clips and whether it needs (re)generating: missing
    clips, prompts changed since it was made, or (for a continued scene) the scene
    before it was remade since."""
    run = projects.folder(p.id)
    by_scene: dict[int, list[dict]] = {}
    for it in load_items(p):
        by_scene.setdefault(int(it.get("scene", 1)), []).append(it)
    prints = scene_fingerprints(p)
    views = []
    for n in sorted(by_scene):
        group = by_scene[n]
        steps = [SceneStep(index=it["index"], duration=it["duration"],
                           clip=clip_name(it["index"]) if (run / clip_name(it["index"])).exists() else None)
                 for it in group]
        cut = bool(group[0].get("cut"))
        state, before = p.scenes.get(str(n)), p.scenes.get(str(n - 1))
        if any(s.clip is None for s in steps):
            status = "missing"
        elif state is None or state.fingerprint != prints.get(n):
            status = "stale"
        elif n > 1 and not cut and state.previous_generation != (before.generation if before else None):
            status = "older_footage"
        else:
            status = "ready"
        views.append(SceneView(number=n, start=group[0]["start"], end=group[-1]["end"], cut=cut,
                               steps=steps, status=status, video=scene_video(run, n, steps)))
    return views


def generate_video(p: Project, log, scenes: list[int] | None = None, feedback: str = "") -> None:
    """Make the clips of the given scenes, or of every scene that is not ready, then
    glue all clips into the rough cut. A remade scene starts again from the frame
    before it and its prompts are fitted to that footage afresh. With `feedback` (what
    was wrong, what should change) Gemini first revises the shots of the scenes being
    redone, and the bible too when the whole video is redone; then every scene asked
    for is made again, whether or not its shots changed."""
    run = projects.folder(p.id)
    items = load_items(p)
    if not items:
        raise RuntimeError("Plan the scenes first.")
    views = scene_status(p)
    unknown = set(scenes or []) - {v.number for v in views}
    if unknown:
        raise ValueError(f"No such scene: {sorted(unknown)}")
    if feedback.strip():
        targets = sorted(set(scenes)) if scenes else [v.number for v in views]
        items = apply_feedback(p, log, items, targets, feedback, whole=not scenes)
    else:
        targets = sorted(set(scenes)) if scenes else [v.number for v in views if v.status != "ready"]
    clip_dir = run / "clips"
    clip_dir.mkdir(exist_ok=True)
    state_path = clip_dir / "steps.json"
    state = read_json(state_path, {})
    pinned: dict[int, str] = {}
    for n in targets:
        _unlink(run / scene_name(n))
    for pos, it in enumerate(items):
        if int(it.get("scene", 1)) not in targets:
            continue
        _unlink(run / clip_name(it["index"]), clip_dir / f"lastframe_{pos:02d}.png")
        state.pop(str(it["index"]), None)
        if it.get("pinned_for"):
            pinned[it["index"]] = it["prompt"]  # fitted to the footage when sent, kept as written after
        elif it.get("source_prompt"):
            it["prompt"] = it["source_prompt"]  # drop the old fit so the prompt is fitted again
    write_json(state_path, state)
    write_json(run / "prompts.json", items)
    # The clips are made in order up to the last scene asked for: a missing earlier scene
    # is made on the way (the asked-for one continues from it); later ones are left alone.
    last = max((pos for pos, it in enumerate(items) if int(it.get("scene", 1)) in targets), default=-1)
    work = items[:last + 1]
    made = {int(it.get("scene", 1)) for it in work if not (run / clip_name(it["index"])).exists()}
    if targets:
        log(f"Generating scene(s) {', '.join(map(str, sorted(made)))}...")
    else:
        log("Every scene is up to date.")
    try:
        if work:
            mv.generate_clips(work, run, p.settings.aspect, p.settings.resolution, start_image=preview_path(p),
                              text_retries=p.settings.text_retries, fit=True)
    finally:
        # generate_clips saves the fitted prompts of `work` only; write the whole list back.
        for it in items:
            if it["index"] in pinned:
                it["prompt"] = pinned[it["index"]]
        write_json(run / "prompts.json", items)
        _record_scenes(p.id, sorted(made), log)


def apply_feedback(p: Project, log, items: list[dict], targets: list[int], feedback: str, whole: bool) -> list[dict]:
    """Have Gemini work the reviewer's feedback into the shots of `targets` (and the bible
    when `whole`), then rebuild the prompts. Returns the new prompt items."""
    plan = load_plan(p)
    if plan is None:
        raise RuntimeError("Plan the scenes first.")
    steps = [{"index": it["index"], "scene": int(it.get("scene", 1)), "text": it.get("text", "")}
             for it in items if int(it.get("scene", 1)) in targets]
    log(f"Applying the feedback to scene(s) {', '.join(map(str, targets))} with {mv.PROMPT_MODEL}: {feedback.strip()}")
    from google import genai
    client = genai.Client(api_key=mv.require_env("GEMINI_API_KEY"))
    before = {sh.index: sh for sh in plan.shots}
    new_plan, changes = mv.revise_plan(client, plan, steps, feedback, whole)
    changed = [sh.index for sh in new_plan.shots if sh != before.get(sh.index)]
    bible_changed = any(getattr(new_plan, f) != getattr(plan, f)
                        for f in ("visual_style", "setting", "layout", "lighting", "characters"))
    log(f"Gemini: {changes}")
    log(f"Changed: {'the bible, ' if bible_changed else ''}shot(s) {', '.join(map(str, changed)) or 'none'}.")
    return rebuild_prompts(p, new_plan, pinned_prompts(p))


def _record_scenes(project_id: str, targets: list[int], log) -> None:
    """Remember what each remade scene was made from, and glue the rough cut when every
    clip exists. Works on a fresh copy so edits made during the job are kept."""
    fresh = projects.load(project_id)
    run = projects.folder(project_id)
    prints = scene_fingerprints(fresh)
    views = scene_status(fresh)
    for v in views:
        if all(s.clip for s in v.steps) and v.video is None:
            mv.concatenate([run / s.clip for s in v.steps], run, name=scene_name(v.number))
            v.video = scene_name(v.number)
    for v in views:
        if v.number in targets and all(s.clip for s in v.steps):
            old, before = fresh.scenes.get(str(v.number)), fresh.scenes.get(str(v.number - 1))
            fresh.scenes[str(v.number)] = SceneState(
                fingerprint=prints[v.number], generation=(old.generation + 1 if old else 1),
                previous_generation=before.generation if before else None)
    if views and all(s.clip for v in views for s in v.steps):
        mv.concatenate([run / s.clip for v in views for s in v.steps], run, name="rough_cut.mp4")
        log("Rough cut saved: rough_cut.mp4")
        projects.bump(fresh, "video")
    projects.save(fresh)


# -- phases 5 and 6: overlays and renders ------------------------------------------------------------
import uuid  # noqa: E402

import callouts as fx  # noqa: E402


class Overlay(BaseModel):
    """One keyword graphic, with the exact seconds it is drawn. The timing code gives a
    suggestion its first start and end; after that the times in the file are the truth."""
    id: str
    text: str
    style: Literal["slam", "swipe", "reveal", "bullet"]
    position: Literal["top-left", "top-right", "bottom-left", "bottom-right", "center"]
    list_name: str = ""
    start: float
    end: float
    anchor: str = ""
    segment_index: int | None = None
    edited: bool = False


def callouts_path(p: Project) -> Path:
    return projects.folder(p.id) / "callouts.json"


def load_overlays(p: Project) -> tuple[str | None, list[Overlay]]:
    """The accent colour (None before the first suggestion or save) and the overlays."""
    data = read_json(callouts_path(p), {})
    return data.get("accent_color"), [Overlay.model_validate(o) for o in data.get("overlays", [])]


def _write_overlays(p: Project, accent: str, overlays: list[Overlay], raw: list[dict] | None = None) -> None:
    data = read_json(callouts_path(p), {})
    data["accent_color"] = accent
    if raw is not None:
        data["callouts"] = raw
    data["overlays"] = [o.model_dump() for o in overlays]
    write_json(callouts_path(p), data)


def suggest_overlays(p: Project, log, replace_edited: bool) -> None:
    """Ask Gemini for keyword graphics over the rough cut's narration and shots, and time them
    to the spoken words. Overlays the user edited stay unless `replace_edited`; so does the
    user's accent colour. Density 'off' leaves no overlays and makes no model call."""
    run = projects.folder(p.id)
    rough_cut = run / "rough_cut.mp4"
    if not rough_cut.exists():
        raise RuntimeError("Generate the video first.")
    old_accent, old = load_overlays(p)
    kept = [] if replace_edited else [o for o in old if o.edited]
    density = p.settings.density
    if density == "off":
        log("Overlay density is off: no graphics suggested.")
        _write_overlays(p, old_accent or fx.DEFAULT_ACCENT, kept, raw=[])
        _commit(p.id, "overlays")
        return
    from google import genai

    segments = load_segments(p)
    plan = read_json(run / "plan.json", {})
    shots = {s["index"]: f"{s.get('camera', '')} {s.get('action', '')}".strip() for s in plan.get("shots", [])}
    client = genai.Client(api_key=mv.require_env("GEMINI_API_KEY"))
    log(f"Choosing on-screen keywords with {mv.PROMPT_MODEL} (density: {density})...")
    accent, chosen = fx.plan_callouts(client, mv.PROMPT_MODEL, segments, shots, plan.get("visual_style", ""), density)
    words = words_from_alignment(read_json(run / "alignment.json", {}))
    timed = fx.time_callouts(chosen, words, segments, mv.media_duration(rough_cut))
    fresh = []
    for t in timed:
        src = next((c for c in chosen if c.text == t.text), None)
        fresh.append(Overlay(id=uuid.uuid4().hex[:8], text=t.text, style=t.style, position=t.position,
                             list_name=t.list_name, start=round(t.start, 3), end=round(t.end, 3),
                             anchor=src.anchor if src else "", segment_index=src.segment_index if src else None))
    accent = old_accent if (kept and old_accent) else accent
    _write_overlays(p, accent, sorted(kept + fresh, key=lambda o: o.start), raw=[c.model_dump() for c in chosen])
    log(f"Suggested {len(fresh)} keyword graphics" + (f", kept {len(kept)} edited." if kept else "."))
    _commit(p.id, "overlays")


def save_overlays(p: Project, accent_color: str, overlays: list[Overlay]) -> None:
    """Write the overlays as the editor sends them (the client sets `edited`)."""
    _write_overlays(p, accent_color, overlays)
    projects.bump(p, "overlays")
    projects.save(p)


def render(p: Project, log, name: str) -> Path:
    """Burn the overlays onto the rough cut and lay the voice over it. `overlay_preview.mp4`
    is the phase 5 check; `final.mp4` is the deliverable and bumps the final phase."""
    run = projects.folder(p.id)
    rough_cut, voice = run / "rough_cut.mp4", run / "voice.mp3"
    if not rough_cut.exists():
        raise RuntimeError("Generate the video first.")
    if not voice.exists():
        raise RuntimeError("Generate the voice track first.")
    accent, overlays = load_overlays(p)
    ass_path = None
    if overlays:
        timed = fx.stack_bullets([fx.TimedCallout(o.text, o.style, o.position, o.start, o.end, list_name=o.list_name)
                                  for o in overlays])
        width, height = mv.video_size(rough_cut)
        ass_path = run / "callouts.ass"
        ass_path.write_text(fx.write_ass(timed, width, height, accent or fx.DEFAULT_ACCENT,
                                         mv.CALLOUT_FONT, mv.CALLOUT_FONT_HEAVY))
        log(f"Drawing {len(overlays)} keyword graphics.")
    out = mv.add_voice(rough_cut, voice, run, overlay=ass_path, name=name)
    log(f"Saved {name}")
    if name == "final.mp4":
        _commit(p.id, "final")
    return out
