"""Video projects on disk: one folder per project under PROJECTS_DIR holding project.json
(the state) and the same files the CLI writes to a run folder."""
from __future__ import annotations

import json
import os
import secrets
import shutil
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

PROJECTS_DIR = Path(os.getenv("PROJECTS_DIR", "projects"))

PHASES = ("setting", "voice", "plan", "video", "overlays", "final")
PHASE_NUMBER = {name: i + 1 for i, name in enumerate(PHASES)}
UPSTREAM: dict[str, tuple[str, ...]] = {
    "setting": (),
    "voice": (),
    "plan": ("setting", "voice"),
    "video": ("plan", "setting"),
    "overlays": ("voice",),
    "final": ("video", "overlays", "voice"),
}


class Settings(BaseModel):
    aspect: Literal["16:9", "9:16"] = "16:9"
    resolution: Literal["360p", "720p", "1080p", "4k"] = "720p"
    max_scene: int = Field(30, ge=10, le=30)
    density: Literal["off", "low", "medium", "high"] = "medium"
    text_retries: int = Field(2, ge=0)


class PhaseOutput(BaseModel):
    version: int = 0
    inputs: dict[str, int] = Field(default_factory=dict)


class SceneState(BaseModel):
    fingerprint: str
    generation: int
    previous_generation: int | None = None


class Project(BaseModel):
    id: str
    name: str
    created: str
    updated: str
    settings: Settings = Field(default_factory=Settings)
    description: str = ""
    talk_track: str = ""
    voice_seconds: float | None = None
    previews: list[str] = Field(default_factory=list)
    current_preview: str | None = None
    setting: PhaseOutput = Field(default_factory=PhaseOutput)
    voice: PhaseOutput = Field(default_factory=PhaseOutput)
    plan: PhaseOutput = Field(default_factory=PhaseOutput)
    video: PhaseOutput = Field(default_factory=PhaseOutput)
    overlays: PhaseOutput = Field(default_factory=PhaseOutput)
    final: PhaseOutput = Field(default_factory=PhaseOutput)
    scenes: dict[str, SceneState] = Field(default_factory=dict)

    def phase(self, name: str) -> PhaseOutput:
        return getattr(self, name)


class PhaseState(BaseModel):
    status: Literal["empty", "ready", "stale"]
    reason: str = ""
    summary: str = ""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_id() -> str:
    return f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"


def folder(project_id: str) -> Path:
    return PROJECTS_DIR / project_id


def create(name: str) -> Project:
    now = _now()
    p = Project(id=new_id(), name=name, created=now, updated=now)
    folder(p.id).mkdir(parents=True, exist_ok=False)
    save(p)
    return p


def load(project_id: str) -> Project:
    path = folder(project_id) / "project.json"
    if not project_id or "/" in project_id or project_id in (".", "..") or not path.exists():
        raise KeyError(project_id)
    return Project.model_validate_json(path.read_text())


def save(p: Project) -> None:
    p.updated = _now()
    path = folder(p.id) / "project.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(p.model_dump_json(indent=2))
    tmp.replace(path)


def list_projects() -> list[Project]:
    if not PROJECTS_DIR.exists():
        return []
    out = []
    for d in PROJECTS_DIR.iterdir():
        if (d / "project.json").exists():
            try:
                out.append(load(d.name))
            except ValueError:
                continue
    return sorted(out, key=lambda p: p.updated, reverse=True)


def delete(project_id: str) -> None:
    load(project_id)   # raises KeyError for unknown ids
    shutil.rmtree(folder(project_id))


def safe_file(project_id: str, name: str) -> Path:
    """A path inside the project folder, or ValueError. `name` may name a file in a
    subfolder (clips/clip_01.mp4) but may not climb out."""
    try:
        base = folder(load(project_id).id).resolve()
    except KeyError:
        raise ValueError(f"unknown project {project_id!r}")
    parts = name.split("/") if name else []
    if not parts or any(part in ("", ".", "..") or "\\" in part for part in parts):
        raise ValueError(f"bad file name {name!r}")
    path = base.joinpath(*parts)
    if base not in path.resolve().parents:
        raise ValueError(f"bad file name {name!r}")
    return folder(project_id).joinpath(*parts)


def phase_states(p: Project) -> dict[str, PhaseState]:
    states = {}
    for name in PHASES:
        out = p.phase(name)
        if out.version == 0:
            states[name] = PhaseState(status="empty")
            continue
        changed = [up for up in UPSTREAM[name] if out.inputs.get(up) != p.phase(up).version]
        if changed:
            first = min(PHASE_NUMBER[up] for up in changed)
            states[name] = PhaseState(status="stale", reason=f"redo after {first}")
        else:
            states[name] = PhaseState(status="ready")
    return states


def bump(p: Project, phase: str) -> None:
    """Record a new output of `phase`, made from the current upstream outputs."""
    out = p.phase(phase)
    out.version += 1
    out.inputs = {up: p.phase(up).version for up in UPSTREAM[phase]}
