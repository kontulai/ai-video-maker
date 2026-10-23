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
from fakes import FakeGemini, FakeInteractions
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


# -- phase 3: the scene plan ------------------------------------------------------------------
from test_pipeline import SCRIPT  # noqa: E402
import re  # noqa: E402
from server import phases  # noqa: E402


def make_voice(client, tmp_path, monkeypatch, pid, text=SCRIPT):
    """Run the voice job against a fake ElevenLabs so the project has alignment.json."""
    alignment = fake_alignment(text)
    mp3 = tmp_path / "fake_voice.mp3"
    if not mp3.exists():
        secs = alignment["character_end_times_seconds"][-1] + 0.5
        mv.ffmpeg("-f", "lavfi", "-i", f"sine=frequency=440:duration={secs}", str(mp3))

    def fake_post(url, **kw):
        body = {"audio_base64": base64.b64encode(mp3.read_bytes()).decode(), "alignment": alignment}
        return SimpleNamespace(status_code=200, json=lambda: body, text="")
    monkeypatch.setattr(mv.requests, "post", fake_post)
    client.patch(f"/api/projects/{pid}", json={"talk_track": text})
    assert client.post(f"/api/projects/{pid}/run/voice").status_code == 202
    assert wait_job(client, pid)["status"] == "done"


def two_scene_plan(cut: bool = True):
    """A planner answer with one shot per segment and two scenes; the second one cuts to
    a new setup or continues the first."""
    def build(contents):
        text = contents if isinstance(contents, str) else contents[-1]
        n = int(re.search(r"SEGMENTS \((\d+) segments", text).group(1))
        shots = [mv.Shot(index=i, camera=f"camera {i}", action=f"action {i}", expression="calm",
                         continuity=f"link {i}") for i in range(1, n + 1)]
        scenes = [mv.ScenePlan(first_segment=1, cut=True)]
        if n > 1:
            scenes.append(mv.ScenePlan(first_segment=n // 2 + 1, cut=cut, setting="The desk, close" if cut else "",
                                       layout="Monitor frame-left, Ana centre" if cut else "", reason="next point"))
        return mv.VideoPlan(title="Claims", visual_style="photoreal", setting="An office",
                            lighting="soft window light", layout="Ana centre, monitor frame-right",
                            characters=[mv.Character(name="Ana", appearance="dark hair", wardrobe="navy blazer")],
                            scenes=scenes, shots=shots)
    return build


def fake_planner(seen, cut: bool = True):
    return FakeGemini(plan=two_scene_plan(cut), seen=seen).client


def planned_project(client, tmp_path, monkeypatch, with_preview=True, cut=True):
    pid = client.post("/api/projects", json={"name": "x"}).json()["id"]
    client.patch(f"/api/projects/{pid}", json={"description": "An office."})
    if with_preview:
        monkeypatch.setattr(google.genai, "Client", fake_image_client([]))
        client.post(f"/api/projects/{pid}/run/preview")
        assert wait_job(client, pid)["status"] == "done"
    make_voice(client, tmp_path, monkeypatch, pid, text=SCRIPT + " " + SCRIPT)  # ~37 s: four steps or more
    seen = []
    monkeypatch.setattr(google.genai, "Client", fake_planner(seen, cut))
    assert client.post(f"/api/projects/{pid}/run/plan").status_code == 202
    job = wait_job(client, pid)
    assert job["status"] == "done", job
    return pid, seen


def no_gemini(monkeypatch):
    class Boom:
        def __init__(self, **kw):
            raise AssertionError("Gemini must not be called")
    monkeypatch.setattr(google.genai, "Client", Boom)


def test_plan_job_builds_items_with_image(client, tmp_path, monkeypatch):
    pid = client.post("/api/projects", json={"name": "x"}).json()["id"]
    assert client.post(f"/api/projects/{pid}/run/plan").status_code == 400   # no voice yet
    assert client.get(f"/api/projects/{pid}/plan").json() == {"plan": None, "segments": [], "items": [],
                                                              "too_long": []}
    pid, seen = planned_project(client, tmp_path, monkeypatch)
    part, text = seen[0]
    assert part.inline_data.mime_type == "image/png" and "attached picture" in text
    data = client.get(f"/api/projects/{pid}/plan").json()
    n = len(data["segments"])
    assert n >= 2 and len(data["items"]) == n and len(data["plan"]["shots"]) == n
    assert data["plan"]["title"] == "Claims"
    assert all(it["source_prompt"] == it["prompt"] and it["pinned_for"] == "" for it in data["items"])
    assert data["segments"][-1]["scene"] == 2 and data["items"][-1]["scene"] == 2
    assert "action 1" in data["items"][0]["prompt"] and "navy blazer" in data["items"][0]["prompt"]
    assert data["too_long"] == []
    p = client.get(f"/api/projects/{pid}").json()
    assert p["plan"]["version"] == 1 and p["phases"]["plan"] == {"status": "ready", "reason": "",
                                                                   "summary": "2 scenes"}
    assert (tmp_path / pid / "plan.json").exists() and (tmp_path / pid / "segments.json").exists()


def test_plan_job_without_preview_sends_text_only(client, tmp_path, monkeypatch):
    pid, seen = planned_project(client, tmp_path, monkeypatch, with_preview=False)
    assert isinstance(seen[0], str)


def test_put_plan_rebuilds_without_gemini(client, tmp_path, monkeypatch):
    pid, _ = planned_project(client, tmp_path, monkeypatch)
    no_gemini(monkeypatch)
    data = client.get(f"/api/projects/{pid}/plan").json()
    before = [it["source_prompt"] for it in data["items"]]
    plan = data["plan"]
    plan["shots"][1]["action"] = "Ana lifts the receipt to the light"
    r = client.put(f"/api/projects/{pid}/plan", json={"plan": plan})
    assert r.status_code == 200
    after = [it["source_prompt"] for it in r.json()["items"]]
    assert after[1] != before[1] and "lifts the receipt" in after[1]
    assert [a for i, a in enumerate(after) if i != 1] == [b for i, b in enumerate(before) if i != 1]
    assert client.get(f"/api/projects/{pid}").json()["plan"]["version"] == 2
    # a scene break can move: the second scene now starts one step later
    plan["scenes"][1]["first_segment"] += 1
    segs = client.put(f"/api/projects/{pid}/plan", json={"plan": plan}).json()["segments"]
    assert [s["scene"] for s in segs].index(2) == plan["scenes"][1]["first_segment"] - 1
    assert client.put(f"/api/projects/{pid}/plan", json={"plan": {"title": "x"}}).status_code == 422


def test_pinned_prompt_survives_unrelated_edit_and_drops_on_its_shot_edit(client, tmp_path, monkeypatch):
    pid, _ = planned_project(client, tmp_path, monkeypatch)
    no_gemini(monkeypatch)
    r = client.put(f"/api/projects/{pid}/prompts/1", json={"prompt": "HAND WRITTEN"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert items[0]["prompt"] == "HAND WRITTEN" and items[0]["pinned_for"] != ""
    assert items[0]["source_prompt"] != "HAND WRITTEN"
    assert client.put(f"/api/projects/{pid}/prompts/99", json={"prompt": "x"}).status_code == 404
    plan = client.get(f"/api/projects/{pid}/plan").json()["plan"]
    plan["shots"][1]["camera"] = "tight close-up"
    items = client.put(f"/api/projects/{pid}/plan", json={"plan": plan}).json()["items"]
    assert items[0]["prompt"] == "HAND WRITTEN"           # unrelated shot edit keeps the pin
    plan["shots"][0]["camera"] = "wide shot"
    items = client.put(f"/api/projects/{pid}/plan", json={"plan": plan}).json()["items"]
    assert items[0]["prompt"] == items[0]["source_prompt"] and items[0]["pinned_for"] == ""
    assert "wide shot" in items[0]["prompt"]
    # a bible edit drops every pin
    client.put(f"/api/projects/{pid}/prompts/2", json={"prompt": "PINNED TWO"})
    plan["lighting"] = "hard noon sun"
    items = client.put(f"/api/projects/{pid}/plan", json={"plan": plan}).json()["items"]
    assert items[1]["prompt"] == items[1]["source_prompt"] and items[1]["pinned_for"] == ""
    assert "noon sun" in items[0]["prompt"]  # the take carries the bible; extensions do not


def test_scene_fingerprints_bible_vs_shot(client, tmp_path, monkeypatch):
    pid, _ = planned_project(client, tmp_path, monkeypatch)
    no_gemini(monkeypatch)
    base = phases.scene_fingerprints(projects.load(pid))
    assert set(base) == {1, 2}
    plan = client.get(f"/api/projects/{pid}/plan").json()["plan"]
    last = len(plan["shots"])
    plan["shots"][last - 1]["action"] = "Ana closes the laptop"
    client.put(f"/api/projects/{pid}/plan", json={"plan": plan})
    one = phases.scene_fingerprints(projects.load(pid))
    assert one[1] == base[1] and one[2] != base[2]
    plan["visual_style"] = "grainy 16mm"
    client.put(f"/api/projects/{pid}/plan", json={"plan": plan})
    both = phases.scene_fingerprints(projects.load(pid))
    assert both[1] != one[1] and both[2] != one[2]
    # the first frame belongs to scene 1 only
    monkeypatch.setattr(google.genai, "Client", fake_image_client([]))
    client.post(f"/api/projects/{pid}/run/preview")
    wait_job(client, pid)
    pic = phases.scene_fingerprints(projects.load(pid))
    assert pic[1] != both[1] and pic[2] == both[2]


# -- phase 4: the video, scene by scene -------------------------------------------------------------
def video_project(client, tmp_path, monkeypatch, cut=False):
    """A planned project whose Gemini and Omni are fakes, ready to make clips."""
    pid, _ = planned_project(client, tmp_path, monkeypatch, cut=cut)
    gem = FakeGemini(omni=FakeInteractions())
    monkeypatch.setattr(google.genai, "Client", gem.client)
    monkeypatch.setattr(mv, "POLL_SECONDS", 0)
    return pid, gem


def video_view(client, pid):
    return client.get(f"/api/projects/{pid}/video").json()


def run_video(client, pid, scenes=None):
    body = {"scenes": scenes} if scenes else {}
    assert client.post(f"/api/projects/{pid}/run/video", json=body).status_code == 202
    job = wait_job(client, pid)
    assert job["status"] == "done", job
    return job


def test_generate_all_makes_rough_cut(client, tmp_path, monkeypatch):
    pid = client.post("/api/projects", json={"name": "x"}).json()["id"]
    assert client.post(f"/api/projects/{pid}/run/video", json={}).status_code == 400  # no plan yet
    assert video_view(client, pid) == {"scenes": [], "rough_cut": False}
    pid, gem = video_project(client, tmp_path, monkeypatch)
    data = video_view(client, pid)
    assert [s["status"] for s in data["scenes"]] == ["missing", "missing"] and data["rough_cut"] is False
    assert [s["number"] for s in data["scenes"]] == [1, 2] and data["scenes"][1]["start"] == data["scenes"][0]["end"]
    assert client.post(f"/api/projects/{pid}/run/video", json={"scenes": [9]}).status_code == 400
    job = run_video(client, pid)
    assert job["scenes"] is None
    data = video_view(client, pid)
    assert data["rough_cut"] and [s["status"] for s in data["scenes"]] == ["ready", "ready"]
    steps = [st for s in data["scenes"] for st in s["steps"]]
    assert all(st["clip"] for st in steps) and len(steps) == len(gem.omni.bodies) >= 4
    assert client.get(f"/api/projects/{pid}/files/{steps[0]['clip']}").status_code == 200
    assert client.get(f"/api/projects/{pid}/files/rough_cut.mp4").status_code == 200
    assert gem.checks == len(steps) and gem.fits == len(steps) - 1  # every clip reviewed, every later one fitted
    proj = client.get(f"/api/projects/{pid}").json()
    assert proj["video"]["version"] == 1 and proj["files"]["rough_cut"]
    assert proj["phases"]["video"] == {"status": "ready", "reason": "", "summary": "2 of 2 scenes"}
    # Generate all again: everything is ready, so nothing is made.
    run_video(client, pid)
    assert len(gem.omni.bodies) == len(steps)


def test_regenerate_one_scene_replaces_only_its_clips_and_flags_next(client, tmp_path, monkeypatch):
    pid, gem = video_project(client, tmp_path, monkeypatch)
    run_video(client, pid)
    folder = projects.folder(pid)
    scenes = video_view(client, pid)["scenes"]
    clips = [[folder / st["clip"] for st in s["steps"]] for s in scenes]
    before = [[c.stat().st_mtime_ns for c in group] for group in clips]
    made = len(gem.omni.bodies)
    assert run_video(client, pid, scenes=[1])["scenes"] == [1]
    data = video_view(client, pid)
    assert [s["status"] for s in data["scenes"]] == ["ready", "older_footage"]
    assert len(gem.omni.bodies) == made + len(clips[0])
    assert [c.stat().st_mtime_ns for c in clips[1]] == before[1]  # scene 2 untouched
    assert all(c.stat().st_mtime_ns != b for c, b in zip(clips[0], before[0]))
    assert client.get(f"/api/projects/{pid}").json()["video"]["version"] == 2  # rough cut rebuilt
    run_video(client, pid, scenes=[2])  # continue from the new footage
    assert [s["status"] for s in video_view(client, pid)["scenes"]] == ["ready", "ready"]
    # A cut never continues older footage.
    pid2, gem2 = video_project(client, tmp_path, monkeypatch, cut=True)
    run_video(client, pid2)
    run_video(client, pid2, scenes=[1])
    assert [s["status"] for s in video_view(client, pid2)["scenes"]] == ["ready", "ready"]


def test_plan_edit_marks_its_scene_stale_and_generate_all_redoes_only_it(client, tmp_path, monkeypatch):
    pid, gem = video_project(client, tmp_path, monkeypatch)
    run_video(client, pid)
    made = len(gem.omni.bodies)
    plan = client.get(f"/api/projects/{pid}/plan").json()["plan"]
    plan["shots"][-1]["action"] = "Ana closes the laptop"
    client.put(f"/api/projects/{pid}/plan", json={"plan": plan})
    scenes = video_view(client, pid)["scenes"]
    assert [s["status"] for s in scenes] == ["ready", "stale"]
    run_video(client, pid)
    assert len(gem.omni.bodies) == made + len(scenes[1]["steps"])
    assert [s["status"] for s in video_view(client, pid)["scenes"]] == ["ready", "ready"]


def test_regenerated_scene_refits_prompt(client, tmp_path, monkeypatch):
    pid, gem = video_project(client, tmp_path, monkeypatch)
    run_video(client, pid)
    first_of_2 = video_view(client, pid)["scenes"][1]["steps"][0]["index"]
    item = next(it for it in client.get(f"/api/projects/{pid}/plan").json()["items"] if it["index"] == first_of_2)
    assert item["prompt"].splitlines()[1] == "IN FRAME NOW: Ana centre, bench frame-left"
    assert "IN FRAME NOW" not in item["source_prompt"]
    gem.in_frame = "Ana at the window"  # scene 1's footage now ends differently
    run_video(client, pid, scenes=[1])
    fits = gem.fits
    run_video(client, pid, scenes=[2])
    item = next(it for it in client.get(f"/api/projects/{pid}/plan").json()["items"] if it["index"] == first_of_2)
    assert item["prompt"].splitlines()[1] == "IN FRAME NOW: Ana at the window"
    assert "fitted" in item["prompt"] and gem.fits == fits + len(video_view(client, pid)["scenes"][1]["steps"])


# -- Task 7: overlays and renders -----------------------------------------------------------------
import subprocess  # noqa: E402
import callouts as fx  # noqa: E402


def suggest_callouts(contents):
    """Answer the overlay planner with one 'swipe' per segment anchored on its first two words."""
    chosen = []
    for m in re.finditer(r'Segment (\d+) \(.*?\)\. Shot: .*\n  Narration: "(.*?)"', contents):
        idx, words = int(m.group(1)), m.group(2).split()
        chosen.append(fx.Callout(segment_index=idx, text=" ".join(words[:2]).upper(),
                                 anchor=" ".join(words[:2]), style="swipe", position="top-right"))
    return fx.CalloutPlan(accent_color="#123456", callouts=chosen)


def overlay_project(client, tmp_path, monkeypatch):
    """A project with a rough cut, whose Gemini answers overlay suggestions."""
    pid, gem = video_project(client, tmp_path, monkeypatch)
    run_video(client, pid)
    gem.callouts = suggest_callouts
    return pid, gem


def overlays_view(client, pid):
    return client.get(f"/api/projects/{pid}/overlays").json()


def run_phase(client, pid, phase, body=None):
    r = client.post(f"/api/projects/{pid}/run/{phase}", json=body or {})
    assert r.status_code == 202, r.text
    job = wait_job(client, pid)
    assert job["status"] == "done", job
    return job


def overlay(**kw):
    base = {"id": kw.pop("id", "x"), "text": "TEXT", "style": "slam", "position": "top-left",
            "list_name": "", "start": 1.0, "end": 4.0, "anchor": "", "segment_index": None, "edited": True}
    return {**base, **kw}


def audio_streams(path: Path) -> int:
    out = subprocess.run([mv.FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True, text=True).stderr
    return len(re.findall(r"Stream #\d+:\d+.*?: Audio", out))


def test_suggest_overlays_times_and_keeps_edited(client, tmp_path, monkeypatch):
    bare = client.post("/api/projects", json={"name": "x"}).json()["id"]
    assert client.post(f"/api/projects/{bare}/run/overlays", json={"replace_edited": False}).status_code == 400
    pid, gem = overlay_project(client, tmp_path, monkeypatch)
    run_phase(client, pid, "overlays", {"replace_edited": False})
    data = overlays_view(client, pid)
    ovs = data["overlays"]
    assert data["accent_color"] == "#123456" and len(ovs) >= 2
    assert all(o["id"] and o["start"] < o["end"] and o["edited"] is False and o["anchor"] for o in ovs)
    assert ovs[0]["text"] == "WELCOME TO" and abs(ovs[0]["start"] - (0.2 - fx.LEAD_IN)) < 0.05
    assert data["duration"] > 30 and data["words"][0]["text"] == "Welcome" and len(data["segments"]) >= 4
    proj = client.get(f"/api/projects/{pid}").json()
    assert proj["overlays"]["version"] == 1 and proj["phases"]["overlays"]["status"] == "ready"
    assert proj["phases"]["overlays"]["summary"] == f"{len(ovs)} overlays"
    # the user edits one and changes the accent; new suggestions keep both until asked to replace
    ovs[0] = {**ovs[0], "text": "MY EDIT", "edited": True}
    r = client.put(f"/api/projects/{pid}/overlays", json={"accent_color": "#ABCDEF", "overlays": ovs})
    assert r.status_code == 200 and r.json()["overlays"][0]["text"] == "MY EDIT"
    run_phase(client, pid, "overlays", {"replace_edited": False})
    data = overlays_view(client, pid)
    texts = [o["text"] for o in data["overlays"]]
    assert "MY EDIT" in texts and texts.count("WELCOME TO") == 1 and data["accent_color"] == "#ABCDEF"
    assert sum(not o["edited"] for o in data["overlays"]) == len(ovs)
    run_phase(client, pid, "overlays", {"replace_edited": True})
    data = overlays_view(client, pid)
    assert "MY EDIT" not in [o["text"] for o in data["overlays"]] and data["accent_color"] == "#123456"
    assert client.get(f"/api/projects/{pid}").json()["overlays"]["version"] == 4
    # density off: no model call, no overlays
    calls = len(gem.seen)
    assert client.patch(f"/api/projects/{pid}", json={"settings": {"density": "off"}}).status_code == 200
    run_phase(client, pid, "overlays", {"replace_edited": True})
    assert overlays_view(client, pid)["overlays"] == [] and len(gem.seen) == calls


def test_render_preview_burns_exact_times(client, tmp_path, monkeypatch):
    pid, gem = overlay_project(client, tmp_path, monkeypatch)
    ovs = [overlay(id="a", text="FIRST", style="slam", position="top-left", start=1.0, end=8.0),
           overlay(id="b", text="SECOND", style="swipe", position="top-left", start=4.0, end=10.0)]
    assert client.put(f"/api/projects/{pid}/overlays", json={"accent_color": "#00FF00", "overlays": ovs}).status_code == 200
    assert client.get(f"/api/projects/{pid}").json()["files"]["preview_render"] is False
    run_phase(client, pid, "preview-render")
    ass = (projects.folder(pid) / "callouts.ass").read_text()
    lines = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
    first = [l for l in lines if "FIRST" in l]
    second = [l for l in lines if "SECOND" in l]
    assert first and second  # both drawn although they overlap in time and place
    assert min(l.split(",")[1] for l in first) == fx._ass_time(1.0)
    assert min(l.split(",")[1] for l in second) == fx._ass_time(4.0)
    assert max(l.split(",")[2] for l in first) == fx._ass_time(8.0)
    assert "00FF00" in ass.upper()
    proj = client.get(f"/api/projects/{pid}").json()
    assert proj["files"]["preview_render"] and proj["final"]["version"] == 0
    assert client.get(f"/api/projects/{pid}/files/overlay_preview.mp4").status_code == 200


def test_final_render_has_voice_and_bumps_final(client, tmp_path, monkeypatch):
    bare = client.post("/api/projects", json={"name": "x"}).json()["id"]
    assert client.post(f"/api/projects/{bare}/run/final").status_code == 400
    pid, gem = overlay_project(client, tmp_path, monkeypatch)
    assert client.put(f"/api/projects/{pid}/overlays",
                      json={"accent_color": "#FFC400", "overlays": [overlay(id="a", text="HELLO")]}).status_code == 200
    run_phase(client, pid, "final")
    final = projects.folder(pid) / "final.mp4"
    assert final.exists() and audio_streams(final) == 1
    assert "HELLO" in (projects.folder(pid) / "callouts.ass").read_text()
    proj = client.get(f"/api/projects/{pid}").json()
    assert proj["final"]["version"] == 1 and proj["phases"]["final"]["status"] == "ready"
    assert proj["phases"]["final"]["summary"] == "rendered" and proj["files"]["final"]
    # no overlays at all still renders, with the voice
    assert client.put(f"/api/projects/{pid}/overlays", json={"accent_color": "#FFC400", "overlays": []}).status_code == 200
    run_phase(client, pid, "final")
    assert audio_streams(final) == 1 and client.get(f"/api/projects/{pid}").json()["final"]["version"] == 2
