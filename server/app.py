"""The HTTP API and, when web/dist exists, the built browser app."""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ValidationError

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


@app.delete("/api/projects/{project_id}", status_code=204)
def delete_project(project_id: str) -> Response:
    get_project(project_id)
    projects.delete(project_id)
    return Response(status_code=204)


# -- jobs ---------------------------------------------------------------------------------------
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


@app.post("/api/projects/{project_id}/run/voice", status_code=202)
def run_voice(project_id: str) -> Job:
    p = get_project(project_id)
    if not p.talk_track.strip():
        raise HTTPException(400, "Write the talk track first")
    return start_job(p, "voice", phases.generate_voice)


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
