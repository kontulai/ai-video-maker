"""The HTTP API: project CRUD, files, and the preview and voice jobs."""
import base64
import struct
import sys
import time
import zlib
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import google.genai
import pytest
from fastapi.testclient import TestClient

import make_video as mv
from server import app as server_app
from server import projects
from test_pipeline import fake_alignment


def tiny_png() -> bytes:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\x80\x80\x80")) + chunk(b"IEND", b""))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "PROJECTS_DIR", tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "x")
    return TestClient(server_app.app)


def wait_job(client, pid, timeout=10.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        job = client.get(f"/api/projects/{pid}/job").json()
        if job and job["status"] != "running":
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_project_crud(client):
    r = client.post("/api/projects", json={"name": "Claims"})
    assert r.status_code == 201
    p = r.json()
    pid = p["id"]
    assert p["name"] == "Claims" and p["phases"]["setting"]["status"] == "empty"
    assert p["job"] is None and p["files"] == {"voice": False, "rough_cut": False,
                                               "final": False, "preview_render": False}
    r = client.patch(f"/api/projects/{pid}", json={"name": "Expense claims", "description": "Office.",
                                                   "settings": {"aspect": "9:16"}})
    assert r.status_code == 200
    assert r.json()["name"] == "Expense claims" and r.json()["settings"]["aspect"] == "9:16"
    assert r.json()["settings"]["resolution"] == "720p"   # other settings kept
    assert client.patch(f"/api/projects/{pid}", json={"settings": {"max_scene": 5}}).status_code == 422
    assert [x["id"] for x in client.get("/api/projects").json()] == [pid]
    assert client.get(f"/api/projects/{pid}").json()["description"] == "Office."
    assert client.get("/api/projects/nope").status_code == 404
    assert client.delete(f"/api/projects/{pid}").status_code == 204
    assert client.get("/api/projects").json() == []


def test_file_route_blocks_escape(client, tmp_path):
    pid = client.post("/api/projects", json={"name": "x"}).json()["id"]
    (tmp_path / "secret.txt").write_text("no")
    (tmp_path / pid / "voice.mp3").write_bytes(b"ID3")
    assert client.get(f"/api/projects/{pid}/files/voice.mp3").content == b"ID3"
    assert client.get(f"/api/projects/{pid}/files/missing.mp3").status_code == 404
    for bad in ("..%2Fsecret.txt", "..%2F..%2Fsecret.txt", "clips%2F..%2F..%2Fsecret.txt"):
        r = client.get(f"/api/projects/{pid}/files/{bad}")
        assert r.status_code in (400, 404), bad
        assert r.content != b"no"
    assert client.get(f"/api/projects/{pid}/files/../secret.txt").content != b"no"


def fake_image_client(seen):
    class FakeClient:
        def __init__(self, **kw):
            self.models = self

        def generate_content(self, model, contents, config):
            seen.append(config.image_config.aspect_ratio)
            part = SimpleNamespace(text=None, inline_data=SimpleNamespace(mime_type="image/png", data=tiny_png()))
            return SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))])
    return FakeClient


def test_preview_job_writes_png_and_bumps_setting(client, tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(google.genai, "Client", fake_image_client(seen))
    pid = client.post("/api/projects", json={"name": "x"}).json()["id"]
    client.patch(f"/api/projects/{pid}", json={"description": "An office at dawn.", "settings": {"aspect": "9:16"}})
    r = client.post(f"/api/projects/{pid}/run/preview")
    assert r.status_code == 202 and r.json()["phase"] == "preview"
    job = wait_job(client, pid)
    assert job["status"] == "done", job
    p = client.get(f"/api/projects/{pid}").json()
    assert p["previews"] == ["preview_1.png"] and p["current_preview"] == "preview_1.png"
    assert p["setting"]["version"] == 1 and p["phases"]["setting"]["status"] == "ready"
    assert (tmp_path / pid / "preview_1.png").read_bytes() == tiny_png()
    assert seen == ["9:16"]
    # a second preview is kept next to the first and becomes current
    client.post(f"/api/projects/{pid}/run/preview")
    wait_job(client, pid)
    p = client.get(f"/api/projects/{pid}").json()
    assert p["previews"] == ["preview_1.png", "preview_2.png"] and p["current_preview"] == "preview_2.png"
    assert p["setting"]["version"] == 2
    # picking the first again is a new setting version too
    p = client.patch(f"/api/projects/{pid}", json={"current_preview": "preview_1.png"}).json()
    assert p["current_preview"] == "preview_1.png" and p["setting"]["version"] == 3
    assert client.patch(f"/api/projects/{pid}", json={"current_preview": "nope.png"}).status_code == 422
    # the log reached the job record
    assert any("preview image" in line for line in job["log"])


def test_preview_without_description_fails_cleanly(client):
    pid = client.post("/api/projects", json={"name": "x"}).json()["id"]
    assert client.post(f"/api/projects/{pid}/run/preview").status_code == 400


def test_voice_job_uses_fake_elevenlabs(client, tmp_path, monkeypatch):
    text = "Submitting an expense claim takes three steps. First, photograph the receipt."
    alignment = fake_alignment(text)
    mp3 = tmp_path / "fake.mp3"
    mv.ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(mp3))

    def fake_post(url, **kw):
        assert "with-timestamps" in url and kw["json"]["text"] == text
        body = {"audio_base64": base64.b64encode(mp3.read_bytes()).decode(), "alignment": alignment}
        return SimpleNamespace(status_code=200, json=lambda: body, text="")
    monkeypatch.setattr(mv.requests, "post", fake_post)

    pid = client.post("/api/projects", json={"name": "x"}).json()["id"]
    assert client.post(f"/api/projects/{pid}/run/voice").status_code == 400   # no talk track yet
    client.patch(f"/api/projects/{pid}", json={"talk_track": text})
    assert client.post(f"/api/projects/{pid}/run/voice").status_code == 202
    job = wait_job(client, pid)
    assert job["status"] == "done", job
    p = client.get(f"/api/projects/{pid}").json()
    assert p["voice"]["version"] == 1 and p["files"]["voice"] is True
    assert p["phases"]["voice"]["summary"].startswith("voice ")
    assert (tmp_path / pid / "talk_track.txt").read_text() == text
    assert (tmp_path / pid / "alignment.json").exists()
    assert client.get(f"/api/projects/{pid}/files/voice.mp3").status_code == 200
    # regenerating replaces the old voice instead of skipping it
    (tmp_path / pid / "voice.mp3").write_bytes(b"old")
    client.post(f"/api/projects/{pid}/run/voice")
    wait_job(client, pid)
    assert (tmp_path / pid / "voice.mp3").read_bytes() != b"old"
    assert client.get(f"/api/projects/{pid}").json()["voice"]["version"] == 2
    # the stream replays the finished job's log and ends
    r = client.get(f"/api/projects/{pid}/job/stream")
    assert r.headers["content-type"].startswith("text/event-stream")
    assert "data: " in r.text and r.text.rstrip().endswith("event: end\ndata: done")


def test_run_twice_is_409(client, monkeypatch):
    import threading
    gate = threading.Event()
    monkeypatch.setattr(server_app.phases, "generate_voice", lambda p, log: gate.wait(5))
    pid = client.post("/api/projects", json={"name": "x"}).json()["id"]
    client.patch(f"/api/projects/{pid}", json={"talk_track": "Hello."})
    assert client.post(f"/api/projects/{pid}/run/voice").status_code == 202
    r = client.post(f"/api/projects/{pid}/run/voice")
    assert r.status_code == 409 and r.json() == {"detail": "A job is already running"}
    assert client.get(f"/api/projects/{pid}").json()["job"]["status"] == "running"
    gate.set()
    wait_job(client, pid)
