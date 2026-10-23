"""The small pipeline changes the web app relies on."""
import json
import struct
import sys
import zlib
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import google.genai
import pytest
import callouts as fx
import make_video as mv
from segmentation import Segment
from test_pipeline import SCRIPT, fake_alignment


def tiny_png() -> bytes:
    """A 1x1 grey PNG, built by hand so the test needs no image library."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = zlib.compress(b"\x00\x80\x80\x80")
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


def test_log_sink_receives_lines():
    got = []
    token = mv.LOG_SINK.set(got.append)
    try:
        mv.log("hi there")
    finally:
        mv.LOG_SINK.reset(token)
    assert len(got) == 1 and got[0].endswith("hi there")
    mv.log("not captured")
    assert len(got) == 1


def fake_client(generate):
    class FakeClient:
        def __init__(self, **kw):
            self.models = SimpleNamespace(generate_content=generate)
    return FakeClient


def test_plan_prompts_sends_image(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    image = tmp_path / "preview_1.png"
    image.write_bytes(tiny_png())
    segments = [Segment(index=1, start=0, end=5, duration=5, text="Hello world.")]
    seen = {}

    def generate(model, contents, config):
        seen["contents"] = contents
        plan = mv.VideoPlan(title="t", visual_style="s", setting="s", lighting="l", layout="l",
                            scenes=[], characters=[],
                            shots=[mv.Shot(index=1, camera="c", action="a", expression="e", continuity="k")])
        return SimpleNamespace(parsed=plan, text=plan.model_dump_json())

    monkeypatch.setattr(google.genai, "Client", fake_client(generate))
    items = mv.plan_prompts("A room.", "Hello world.", segments, tmp_path, image=image)
    assert len(items) == 1
    part, text = seen["contents"]
    assert part.inline_data.mime_type == "image/png"
    assert bytes(part.inline_data.data) == tiny_png()
    assert "attached picture" in text


def test_generate_preview_image_writes_png(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    seen = {}

    def generate(model, contents, config):
        seen["model"], seen["config"], seen["contents"] = model, config, contents
        part = SimpleNamespace(text=None, inline_data=SimpleNamespace(mime_type="image/png", data=tiny_png()))
        cand = SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(text="here", inline_data=None), part]))
        return SimpleNamespace(candidates=[cand])

    monkeypatch.setattr(google.genai, "Client", fake_client(generate))
    out = mv.generate_preview_image("An office at dawn.", "9:16", tmp_path / "preview_1.png")
    assert out.read_bytes() == tiny_png()
    assert seen["model"] == mv.IMAGE_MODEL
    assert seen["config"].response_modalities == ["IMAGE"]
    assert seen["config"].image_config.aspect_ratio == "9:16"
    assert "An office at dawn." in seen["contents"] and "lettering" in seen["contents"]


def test_generate_preview_image_without_image_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "x")

    def generate(model, contents, config):
        cand = SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(text="no", inline_data=None)]))
        return SimpleNamespace(candidates=[cand])

    monkeypatch.setattr(google.genai, "Client", fake_client(generate))
    with pytest.raises(RuntimeError, match="no image"):
        mv.generate_preview_image("x", "16:9", tmp_path / "p.png")


def test_stack_bullets_groups_overlapping_same_list():
    a1 = fx.TimedCallout("One", "bullet", "top-left", 1.0, 8.0, list_name="a")
    a2 = fx.TimedCallout("Two", "bullet", "bottom-left", 3.0, 9.0, list_name="a")
    a3 = fx.TimedCallout("Three", "bullet", "top-left", 20.0, 25.0, list_name="a")  # too late to join
    b = fx.TimedCallout("B", "bullet", "center", 2.0, 6.0, list_name="b")
    slam = fx.TimedCallout("50%", "slam", "center", 1.0, 4.0)
    out = fx.stack_bullets([slam, a2, a1, b, a3])
    by = {t.text: t for t in out}
    assert (by["One"].row, by["One"].rows, by["One"].position) == (0, 2, "top-left")
    assert (by["Two"].row, by["Two"].rows, by["Two"].position) == (1, 2, "top-left")
    assert (by["Three"].row, by["Three"].rows) == (0, 1)
    assert (by["B"].row, by["B"].rows, by["B"].position) == (0, 1, "center")
    assert (by["50%"].start, by["50%"].end, by["50%"].rows) == (1.0, 4.0, 1)
    assert [t.start for t in out] == sorted(t.start for t in out)
    assert (by["Two"].start, by["Two"].end) == (3.0, 9.0)   # times never change


def test_concatenate_and_add_voice_take_names(tmp_path):
    clip = tmp_path / "c.mp4"
    mv.ffmpeg("-f", "lavfi", "-i", "testsrc=s=64x36:r=24:d=1", "-pix_fmt", "yuv420p", str(clip))
    audio = tmp_path / "a.mp3"
    mv.ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(audio))
    silent = mv.concatenate([clip], tmp_path, name="rough_cut.mp4")
    assert silent.name == "rough_cut.mp4" and silent.exists()
    final = mv.add_voice(silent, audio, tmp_path, name="overlay_preview.mp4")
    assert final.name == "overlay_preview.mp4" and final.exists()
