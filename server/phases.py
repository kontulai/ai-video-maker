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
