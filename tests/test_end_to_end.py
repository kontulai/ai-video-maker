"""Runs the whole pipeline with ElevenLabs, Gemini and Omni replaced by fakes."""
import base64
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import google.genai
import pytest
import callouts as fx
import make_video as mv
from fake_omni import FakeInteractions
from test_pipeline import SCRIPT, fake_alignment


@pytest.mark.parametrize("whole_scene,max_scene", [(False, 40), (True, 40), (False, 10)])
def test_full_pipeline_offline(tmp_path, monkeypatch, whole_scene, max_scene):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "x")
    (tmp_path / "scene.txt").write_text("A carpenter in a sunlit workshop.")
    (tmp_path / "talk.txt").write_text(SCRIPT)

    # --- fake ElevenLabs ---
    alignment = fake_alignment(SCRIPT)
    audio_len = alignment["character_end_times_seconds"][-1] + 0.2
    mp3 = tmp_path / "fake.mp3"
    mv.ffmpeg("-f", "lavfi", "-i", f"sine=frequency=440:duration={audio_len:.2f}", str(mp3))

    def fake_post(url, **kw):
        assert "with-timestamps" in url
        body = {"audio_base64": base64.b64encode(mp3.read_bytes()).decode(), "alignment": alignment}
        return SimpleNamespace(status_code=200, json=lambda: body, text="")
    monkeypatch.setattr(mv.requests, "post", fake_post)

    # --- fake Gemini + Omni ---
    calls = {"checks": 0}
    omni = FakeInteractions(whole_scene=whole_scene)
    monkeypatch.setattr(mv, "POLL_SECONDS", 0)

    class FakeModels:
        def generate_content(self, model, contents, config):
            if config.response_schema is fx.CalloutPlan:
                first = re.search(r'Narration: "([^"]+)"', contents).group(1).split()
                plan = fx.CalloutPlan(accent_color="#ff3366", callouts=[
                    fx.Callout(segment_index=1, text="Build it", anchor=" ".join(first[1:3]),
                               style="slam", position="top-left"),
                    fx.Callout(segment_index=1, text="Build it", anchor="x",  # duplicate: dropped
                               style="swipe", position="center"),
                    fx.Callout(segment_index=1, text="Point two", anchor=first[-1],
                               style="bullet", position="bottom-right"),  # moved to the left
                ])
                return SimpleNamespace(parsed=plan, text=plan.model_dump_json())
            if config.response_schema is mv.TextCheck:
                calls["checks"] += 1
                check = mv.TextCheck(has_text=False, where="")
                return SimpleNamespace(parsed=check, text=check.model_dump_json())
            assert config.response_schema is mv.VideoPlan
            n = contents.count("\nSegment ")
            plan = mv.VideoPlan(
                title="Bird feeder", visual_style="photoreal", setting="workshop", lighting="sun",
                characters=[mv.Character(name="Ana", appearance="40s", wardrobe="apron")],
                shots=[mv.Shot(index=i + 1, camera="medium", action="works", expression="smiles",
                               speaker_on_screen=True, speaker_name="Ana", continuity="same")
                       for i in range(n)])
            return SimpleNamespace(parsed=plan, text=plan.model_dump_json())

    class FakeClient:
        def __init__(self, **kw):
            self.models, self.interactions = FakeModels(), omni

    monkeypatch.setattr(google.genai, "Client", FakeClient)
    monkeypatch.setattr(sys, "argv", ["make_video.py", "--scene-file", "scene.txt",
                                      "--script-file", "talk.txt", "--yes",
                                      "--max-scene", str(max_scene)])
    mv.main()

    run = next((tmp_path / "runs").iterdir())
    prompts = json.loads((run / "prompts.json").read_text())
    assert [b["response_format"]["duration"] for b in omni.bodies] == [f"{p['duration']}s" for p in prompts]
    # Clip 1 starts from scratch. Within a scene each step extends the step before;
    # a new scene starts from the previous clip's last frame.
    assert omni.task(0) == "text_to_video"
    for i, p in enumerate(prompts[1:], 1):
        if p["step"] > 0:
            assert omni.task(i) == "extend"
            assert omni.bodies[i]["previous_interaction_id"] == f"int-{i}"
        else:
            assert omni.task(i) == "image_to_video" and "previous_interaction_id" not in omni.bodies[i]
    scenes = {p["scene"] for p in prompts}
    if max_scene == 40:
        assert scenes == {1} and len(prompts) > 1
    else:
        assert len(scenes) == len(prompts) > 1
    for p in prompts:
        clip = run / "clips" / f"clip_{p['index']:02d}.mp4"
        assert abs(mv.media_duration(clip) - p["duration"]) < 0.1
    assert calls["checks"] == len(prompts)  # every clip was checked for lettering
    # Keyword graphics: one kept, the clip gets a layout hint, and they were burned in.
    callouts = json.loads((run / "callouts.json").read_text())
    assert [c["text"] for c in callouts["callouts"]] == ["Build it", "Point two"]
    assert callouts["callouts"][1]["position"] == "bottom-left"
    assert "upper-left third and the lower-left third" in prompts[0]["prompt"]
    assert "uncluttered" not in prompts[1]["prompt"]
    ass = (run / "callouts.ass").read_text()
    assert "PlayResX: 320" in ass and "BUILD IT" in ass and "&H6633FF&" in ass
    final = run / "final.mp4"
    assert final.exists()
    assert abs(mv.media_duration(final) - (audio_len + 0.5)) < 0.3
