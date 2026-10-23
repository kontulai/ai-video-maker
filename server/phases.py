"""What each phase does when its button is pressed. Every function here runs inside a
job thread, takes the project id and the job's log function, and saves the project
when its output is complete. Nothing here is called without a POST .../run/... route."""
from __future__ import annotations

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


# -- phase 1: setting -------------------------------------------------------------------
def generate_preview(p: Project, log) -> None:
    run = projects.folder(p.id)
    (run / "scene.txt").write_text(p.description)
    name = f"preview_{len(p.previews) + 1}.png"
    mv.generate_preview_image(p.description, p.settings.aspect, run / name)
    log(f"Preview saved: {name}")
    _commit(p.id, "setting", previews=[*p.previews, name], current_preview=name)


# -- phase 2: talk track ----------------------------------------------------------------
def generate_voice(p: Project, log) -> None:
    run = projects.folder(p.id)
    (run / "talk_track.txt").write_text(p.talk_track)
    _unlink(run / "voice.mp3", run / "alignment.json")
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
        out[scene].update(item.get("source_prompt", item["prompt"]).encode())
    return {scene: h.hexdigest() for scene, h in out.items()}


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
    write_json(run / "segments.json", [s.to_dict() for s in segments])
    log(f"{len(segments)} steps, {segments[-1].end}s for {duration:.1f}s of audio.")
    _unlink(run / "plan.json", run / "prompts.json")
    items = mv.plan_prompts(p.description, p.talk_track, segments, run, p.settings.max_scene,
                            image=preview_path(p))
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
            item["prompt"] = prompt
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
