"""Runs the whole pipeline with ElevenLabs, Gemini and Veo replaced by fakes."""
import base64
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import google.genai
import make_video as mv
from test_pipeline import SCRIPT, fake_alignment


def test_full_pipeline_offline(tmp_path, monkeypatch):
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

    # --- fake Gemini + Veo ---
    calls = {"veo": []}

    class FakeModels:
        def generate_content(self, model, contents, config):
            n = contents.count("\nSegment ")
            plan = mv.VideoPlan(
                title="Bird feeder", visual_style="photoreal", setting="workshop",
                lighting="sun", ambience="room tone",
                characters=[mv.Character(name="Ana", appearance="40s", wardrobe="apron", mannerisms="calm")],
                shots=[mv.Shot(index=i + 1, camera="medium", action="works", expression="smiles",
                               speaker_on_screen=True, speaker_name="Ana", continuity="same")
                       for i in range(n)])
            return SimpleNamespace(parsed=plan, text=plan.model_dump_json())

        def generate_videos(self, model, prompt, config, image=None):
            calls["veo"].append({"duration": config.duration_seconds, "image": image is not None})
            vid = SimpleNamespace(duration=config.duration_seconds)
            return SimpleNamespace(done=True, error=None,
                                   response=SimpleNamespace(generated_videos=[SimpleNamespace(video=vid)]))

    class FakeFiles:
        def download(self, file, destination):
            mv.ffmpeg("-f", "lavfi", "-i", f"testsrc=s=320x180:r=24:d={file.duration}",
                      "-f", "lavfi", "-i", f"sine=frequency=200:duration={file.duration}",
                      "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", destination)

    class FakeClient:
        def __init__(self, **kw):
            self.models, self.files = FakeModels(), FakeFiles()

    monkeypatch.setattr(google.genai, "Client", FakeClient)
    monkeypatch.setattr(sys, "argv", ["make_video.py", "--scene-file", "scene.txt",
                                      "--script-file", "talk.txt", "--yes"])
    mv.main()

    run = next((tmp_path / "runs").iterdir())
    prompts = json.loads((run / "prompts.json").read_text())
    assert [c["duration"] for c in calls["veo"]] == [p["duration"] for p in prompts]
    # Clip 1 starts from scratch; later clips start from the previous last frame.
    assert [c["image"] for c in calls["veo"]] == [False] + [True] * (len(prompts) - 1)
    final = run / "final.mp4"
    assert final.exists()
    assert abs(mv.media_duration(final) - (audio_len + 0.5)) < 0.3
