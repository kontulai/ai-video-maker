"""The HTTP API and, when web/dist exists, the built browser app."""
from __future__ import annotations

import re

from pathlib import Path
from typing import Iterator

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ValidationError

import callouts as fx
import make_video as mv
from segmentation import words_from_alignment
from . import phases, projects
from .jobs import Job, JobBusy, JobRunner
from .projects import Project, Settings

app = FastAPI(title="AI video maker")
runner = JobRunner()
WEB_DIST = Path(__file__).resolve().parents[1] / "web" / "dist"

FILES = {"voice": "voice.mp3", "rough_cut": "rough_cut.mp4", "final": "final.mp4",
         "preview_render": "overlay_preview.mp4"}


# -- views ---------------------------------------------------------------------------------
def _summaries(p: Project) -> dict[str, str]:
    out = {k: "" for k in projects.PHASES}
    if p.current_preview:
        out["setting"] = "preview ready"
    if p.voice.version:
        out["voice"] = f"voice {p.voice_seconds:.0f} s" if p.voice_seconds else "voice ready"
    if p.plan.version:
        items = phases.load_items(p)
        n = max((int(it.get("scene", 1)) for it in items), default=0)
        out["plan"] = f"{n} scene{'s' if n != 1 else ''}"
        views = phases.scene_status(p)
        done = sum(v.status != "missing" for v in views)
        if done or p.video.version:
            out["video"] = f"{done} of {len(views)} scenes"
    if p.overlays.version:
        n = len(phases.load_overlays(p)[1])
        out["overlays"] = f"{n} overlay{'s' if n != 1 else ''}"
    if p.final.version:
        out["final"] = "rendered"
    return out


def view(p: Project) -> dict:
    run = projects.folder(p.id)
    states = projects.phase_states(p)
    for name, summary in _summaries(p).items():
        if summary and not states[name].summary:
            states[name].summary = summary
    job = runner.current(p.id)
    return {
        **p.model_dump(),
        "phases": {k: v.model_dump() for k, v in states.items()},
        "job": job.model_dump() if job else None,
        "files": {key: (run / name).exists() for key, name in FILES.items()},
    }


def get_project(project_id: str) -> Project:
    try:
        return projects.load(project_id)
    except KeyError:
        raise HTTPException(404, "No such project")


# -- projects --------------------------------------------------------------------------------
class NewProject(BaseModel):
    name: str


class ProjectPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    talk_track: str | None = None
    settings: dict | None = None
    current_preview: str | None = None


@app.get("/api/projects")
def list_projects() -> list[dict]:
    return [view(p) for p in projects.list_projects()]


@app.post("/api/projects", status_code=201)
def create_project(body: NewProject) -> dict:
    return view(projects.create(body.name.strip() or "Untitled"))


@app.get("/api/projects/{project_id}")
def read_project(project_id: str) -> dict:
    return view(get_project(project_id))


@app.patch("/api/projects/{project_id}")
def patch_project(project_id: str, body: ProjectPatch) -> dict:
    p = get_project(project_id)
    if body.name is not None:
        p.name = body.name.strip() or p.name
    if body.description is not None:
        p.description = body.description
    if body.talk_track is not None:
        p.talk_track = body.talk_track
    if body.settings is not None:
        try:
            p.settings = Settings.model_validate({**p.settings.model_dump(), **body.settings})
        except ValidationError as e:
            raise HTTPException(422, e.errors(include_url=False))
    if body.current_preview is not None:
        if body.current_preview not in p.previews:
            raise HTTPException(422, "No such preview")
        if body.current_preview != p.current_preview:
            p.current_preview = body.current_preview
            projects.bump(p, "setting")
    projects.save(p)
    return view(p)


IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


@app.post("/api/projects/{project_id}/references")
def add_references(project_id: str, files: list[UploadFile] = File(...)) -> dict:
    """Store pictures of the people, place or style; they go to the preview and the planner.
    Names count up for the life of the project, so a removed picture's name is never reused."""
    p = get_project(project_id)
    for f in files:
        if f.content_type not in IMAGE_TYPES:
            raise HTTPException(415, f"{f.filename}: only PNG, JPEG and WebP pictures can be attached")
    run = projects.folder(p.id)
    (run / "refs").mkdir(exist_ok=True)
    taken = [int(m.group(1)) for name in p.references if (m := re.search(r"ref_(\d+)\.", name))]
    next_n = max(taken, default=0) + 1
    for f in files:
        name = f"refs/ref_{next_n}{IMAGE_TYPES[f.content_type]}"
        (run / name).write_bytes(f.file.read())
        p.references.append(name)
        next_n += 1
    projects.bump(p, "setting")
    projects.save(p)
    return view(p)


@app.delete("/api/projects/{project_id}/references/{name:path}")
def remove_reference(project_id: str, name: str) -> dict:
    p = get_project(project_id)
    if name not in p.references:
        raise HTTPException(404, "No such reference picture")
    p.references.remove(name)
    phases._unlink(projects.folder(p.id) / name)
    projects.bump(p, "setting")
    projects.save(p)
    return view(p)


@app.delete("/api/projects/{project_id}", status_code=204)
def delete_project(project_id: str) -> Response:
    get_project(project_id)
    projects.delete(project_id)
    return Response(status_code=204)


# -- jobs ---------------------------------------------------------------------------------------
def no_job(p: Project, *phases_: str) -> None:
    """Refuse an edit while a job that reads or writes the same files runs."""
    job = runner.current(p.id)
    if job and job.status == "running" and job.phase in phases_:
        raise HTTPException(409, f"The {job.phase} job is running; edit when it has finished")


def start_job(p: Project, phase: str, fn, scenes: list[int] | None = None) -> Job:
    try:
        return runner.start(p.id, phase, lambda log: fn(p, log), scenes=scenes)
    except JobBusy:
        raise HTTPException(409, "A job is already running")


@app.post("/api/projects/{project_id}/run/preview", status_code=202)
def run_preview(project_id: str) -> Job:
    p = get_project(project_id)
    if not p.description.strip():
        raise HTTPException(400, "Describe the video first")
    return start_job(p, "preview", phases.generate_preview)


@app.post("/api/projects/{project_id}/run/draft", status_code=202)
def run_draft(project_id: str, file: UploadFile = File(...), notes: str = Form("")) -> Job:
    """Draft the talk track from an uploaded PDF; the text lands in the project when the job is done."""
    p = get_project(project_id)
    if file.content_type != "application/pdf" and not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(415, "Only a PDF can be used to draft the talk track")
    (projects.folder(p.id) / "source.pdf").write_bytes(file.file.read())
    return start_job(p, "draft", lambda p, log: phases.draft_talk_track(p, log, notes))


@app.post("/api/projects/{project_id}/run/voice", status_code=202)
def run_voice(project_id: str) -> Job:
    p = get_project(project_id)
    if not p.talk_track.strip():
        raise HTTPException(400, "Write the talk track first")
    return start_job(p, "voice", phases.generate_voice)


@app.post("/api/projects/{project_id}/run/plan", status_code=202)
def run_plan(project_id: str) -> Job:
    p = get_project(project_id)
    if not (projects.folder(p.id) / "alignment.json").exists():
        raise HTTPException(400, "Generate the voice track first")
    return start_job(p, "plan", phases.generate_plan)


class VideoBody(BaseModel):
    scenes: list[int] | None = None
    feedback: str = ""  # what was wrong or should change; Gemini works it into the shots first


@app.post("/api/projects/{project_id}/run/video", status_code=202)
def run_video(project_id: str, body: VideoBody | None = None) -> Job:
    p = get_project(project_id)
    views = phases.scene_status(p)
    if not views:
        raise HTTPException(400, "Plan the scenes first")
    scenes = body.scenes if body and body.scenes else None
    if scenes and set(scenes) - {v.number for v in views}:
        raise HTTPException(400, "No such scene")
    feedback = body.feedback if body else ""
    return start_job(p, "video", lambda p, log: phases.generate_video(p, log, scenes, feedback), scenes=scenes)


@app.get("/api/projects/{project_id}/job")
def read_job(project_id: str) -> Job | None:
    get_project(project_id)
    return runner.current(project_id)


@app.get("/api/projects/{project_id}/job/stream")
def stream_job(project_id: str) -> StreamingResponse:
    get_project(project_id)

    def events() -> Iterator[str]:
        for line in runner.subscribe(project_id):
            yield f"data: {line}\n\n"
        yield "event: end\ndata: done\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# -- the scene plan -------------------------------------------------------------------------------------
class PlanBody(BaseModel):
    plan: mv.VideoPlan


class PromptBody(BaseModel):
    prompt: str


def plan_view(p: Project) -> dict:
    plan = phases.load_plan(p)
    items = phases.load_items(p)
    return {
        "plan": plan.model_dump() if plan else None,
        "segments": [s.to_dict() for s in phases.load_segments(p)],
        "items": items,
        "too_long": [it["index"] for it in items if len(it["prompt"]) > mv.MAX_PROMPT_CHARS],
    }


@app.get("/api/projects/{project_id}/plan")
def read_plan(project_id: str) -> dict:
    return plan_view(get_project(project_id))


@app.put("/api/projects/{project_id}/plan")
def put_plan(project_id: str, body: PlanBody) -> dict:
    p = get_project(project_id)
    if not phases.load_segments(p):
        raise HTTPException(400, "Generate the plan first")
    no_job(p, "plan", "video")
    phases.rebuild_prompts(p, body.plan, phases.pinned_prompts(p))
    return plan_view(p)


@app.put("/api/projects/{project_id}/prompts/{index}")
def put_prompt(project_id: str, index: int, body: PromptBody) -> dict:
    p = get_project(project_id)
    no_job(p, "plan", "video")
    try:
        phases.pin_prompt(p, index, body.prompt)
    except KeyError:
        raise HTTPException(404, "No such step")
    return plan_view(p)


@app.get("/api/projects/{project_id}/video")
def read_video(project_id: str) -> dict:
    p = get_project(project_id)
    return {"scenes": [v.model_dump() for v in phases.scene_status(p)],
            "rough_cut": (projects.folder(p.id) / "rough_cut.mp4").exists()}


# -- overlays and renders -------------------------------------------------------------------------------
class OverlaysBody(BaseModel):
    accent_color: str
    overlays: list[phases.Overlay]


class SuggestBody(BaseModel):
    replace_edited: bool = False


def overlays_view(p: Project) -> dict:
    run = projects.folder(p.id)
    accent, overlays = phases.load_overlays(p)
    words = words_from_alignment(phases.read_json(run / "alignment.json", {}))
    rough_cut = run / "rough_cut.mp4"
    return {
        "accent_color": accent or fx.DEFAULT_ACCENT,
        "overlays": [o.model_dump() for o in overlays],
        "words": [{"text": w.text, "start": w.start, "end": w.end} for w in words],
        "segments": [s.to_dict() for s in phases.load_segments(p)],
        "duration": mv.media_duration(rough_cut) if rough_cut.exists() else 0.0,
    }


def need_rough_cut(p: Project) -> None:
    if not (projects.folder(p.id) / "rough_cut.mp4").exists():
        raise HTTPException(400, "Generate the video first")


@app.get("/api/projects/{project_id}/overlays")
def read_overlays(project_id: str) -> dict:
    return overlays_view(get_project(project_id))


@app.put("/api/projects/{project_id}/overlays")
def put_overlays(project_id: str, body: OverlaysBody) -> dict:
    p = get_project(project_id)
    no_job(p, "overlays", "preview-render", "final")
    phases.save_overlays(p, body.accent_color, body.overlays)
    return overlays_view(p)


@app.post("/api/projects/{project_id}/run/overlays", status_code=202)
def run_overlays(project_id: str, body: SuggestBody | None = None) -> Job:
    p = get_project(project_id)
    need_rough_cut(p)
    replace = bool(body and body.replace_edited)
    return start_job(p, "overlays", lambda p, log: phases.suggest_overlays(p, log, replace))


@app.post("/api/projects/{project_id}/run/preview-render", status_code=202)
def run_preview_render(project_id: str) -> Job:
    p = get_project(project_id)
    need_rough_cut(p)
    return start_job(p, "preview-render", lambda p, log: phases.render(p, log, "overlay_preview.mp4"))


@app.post("/api/projects/{project_id}/run/final", status_code=202)
def run_final(project_id: str) -> Job:
    p = get_project(project_id)
    need_rough_cut(p)
    return start_job(p, "final", lambda p, log: phases.render(p, log, "final.mp4"))


# -- files ----------------------------------------------------------------------------------------
@app.get("/api/projects/{project_id}/files/{name:path}")
def read_file(project_id: str, name: str) -> FileResponse:
    get_project(project_id)
    try:
        path = projects.safe_file(project_id, name)
    except ValueError:
        raise HTTPException(400, "Bad file name")
    if not path.is_file():
        raise HTTPException(404, "No such file")
    return FileResponse(path)


# -- the browser app ------------------------------------------------------------------------------------
if WEB_DIST.is_dir():
    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(404)
        target = (WEB_DIST / path) if path else WEB_DIST / "index.html"
        if path and target.is_file() and WEB_DIST in target.resolve().parents:
            return FileResponse(target)
        return FileResponse(WEB_DIST / "index.html")
