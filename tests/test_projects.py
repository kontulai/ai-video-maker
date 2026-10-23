"""Project folders on disk and the stale/ready state of the six phases."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from pydantic import ValidationError

from server import projects


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "PROJECTS_DIR", tmp_path)
    return tmp_path


def test_create_load_list_delete(store):
    p = projects.create("Expense claims")
    assert p.name == "Expense claims" and p.settings.aspect == "16:9"
    assert (store / p.id / "project.json").exists()
    p.description = "An office."
    projects.save(p)
    again = projects.load(p.id)
    assert again.description == "An office." and again.updated >= p.created
    q = projects.create("Second")
    assert [x.id for x in projects.list_projects()] == sorted([p.id, q.id], reverse=True) or \
           {x.id for x in projects.list_projects()} == {p.id, q.id}
    projects.delete(p.id)
    assert not (store / p.id).exists()
    with pytest.raises(KeyError):
        projects.load(p.id)


def test_safe_file_rejects_escape(store):
    p = projects.create("x")
    assert projects.safe_file(p.id, "voice.mp3") == store / p.id / "voice.mp3"
    assert projects.safe_file(p.id, "clips/clip_01.mp4") == store / p.id / "clips" / "clip_01.mp4"
    for bad in ("../x", "clips/../../x", "/etc/passwd", ""):
        with pytest.raises(ValueError):
            projects.safe_file(p.id, bad)
    with pytest.raises(ValueError):
        projects.safe_file("nope", "voice.mp3")


def test_phase_states_mark_stale_downstream(store):
    p = projects.create("x")
    states = projects.phase_states(p)
    assert {k: s.status for k, s in states.items()} == {k: "empty" for k in projects.PHASES}
    projects.bump(p, "setting")
    projects.bump(p, "voice")
    projects.bump(p, "plan")
    projects.bump(p, "video")
    projects.bump(p, "overlays")
    projects.bump(p, "final")
    assert all(s.status == "ready" for s in projects.phase_states(p).values())
    projects.bump(p, "voice")
    projects.bump(p, "voice")
    states = projects.phase_states(p)
    assert {k: s.status for k, s in states.items()} == {
        "setting": "ready", "voice": "ready", "plan": "stale",
        "video": "ready", "overlays": "stale", "final": "stale"}
    assert states["plan"].reason == "redo after 2"
    assert states["final"].reason == "redo after 2"
    assert states["video"].reason == ""
    projects.bump(p, "plan")
    assert projects.phase_states(p)["video"].reason == "redo after 3"
    assert p.plan.inputs == {"setting": 1, "voice": 3}


def test_settings_validation():
    with pytest.raises(ValidationError):
        projects.Settings(max_scene=5)
    with pytest.raises(ValidationError):
        projects.Settings(aspect="4:3")
    with pytest.raises(ValidationError):
        projects.Settings(text_retries=-1)
    assert projects.Settings(density="off").density == "off"
