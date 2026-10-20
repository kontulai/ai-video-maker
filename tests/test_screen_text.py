"""Video models garble text, so shot descriptions must never ask for readable words."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import make_video as mv
from segmentation import Segment
from fake_omni import FakeInteractions

# Real examples from a run where the video model rendered "EXTERNAL SHARE BOLCKED".
CASES = {
    "Maya opens a dashboard titled 'DATA PRIVACY ARCHITECTURE' showing file icons.":
        "Maya opens a dashboard showing file icons.",
    "She drags documents into column headers labeled 'SENSITIVITY', 'VALUE', and 'REGULATORY'.":
        "She drags documents into column headers.",
    "A red lock icon blocks the upload: 'ACCESS CONTROL: EXTERNAL SHARE BLOCKED'.":
        "A red lock icon blocks the upload.",
    "A vault icon labeled in dark crimson: 'RESTRICTED / HIGHLY CONFIDENTIAL' with prompts.":
        "A vault icon with prompts.",
    "Encrypted folders for 'SSN Registry', 'Patient Health Records', and 'Core Trade Secrets' "
    "locked behind a shield.": "Encrypted folders locked behind a shield.",
    'A banner reading “INTERNAL ONLY” slides down.': "A banner slides down.",
}


def test_strips_quoted_screen_text():
    for before, after in CASES.items():
        assert mv.has_screen_text(before), before
        assert mv.strip_screen_text(before) == after


def test_keeps_possessives_and_plain_emphasis():
    text = "Liam's 'quick' fix; Maya's screen glows in 1990s' style."
    assert not mv.has_screen_text(text)
    assert mv.strip_screen_text(text) == text


def _shot(i, action):
    return mv.Shot(index=i, camera="Close-up", action=action, expression="calm",
                   speaker_on_screen=False, speaker_name="", continuity="same")


class _RewritingClient:
    """Fake Gemini that rewrites shots; records what it was sent."""
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail
        self.models = self

    def generate_content(self, model, contents, config):
        assert config.response_schema is mv.ShotList
        if self.fail:
            raise RuntimeError("boom")
        shots = mv.ShotList.model_validate_json(contents).shots
        self.sent.append([s.index for s in shots])
        fixed = mv.ShotList(shots=[s.model_copy(update={"action": "A red padlock icon appears."})
                                   for s in shots])
        return SimpleNamespace(parsed=fixed, text=fixed.model_dump_json())


def test_only_shots_with_text_are_rewritten():
    client = _RewritingClient()
    shots = [_shot(1, "Maya types."), _shot(2, "Dialog shows 'UPLOAD BLOCKED'.")]
    out = mv.remove_screen_text(client, shots)
    assert client.sent == [[2]]
    assert [s.action for s in out] == ["Maya types.", "A red padlock icon appears."]


def test_regex_backstop_when_rewrite_fails():
    out = mv.remove_screen_text(_RewritingClient(fail=True), [_shot(1, "Dialog shows 'UPLOAD BLOCKED'.")])
    assert out[0].action == "Dialog shows."


def test_every_prompt_forbids_text():
    plan = mv.VideoPlan(title="t", visual_style="s", setting="o", lighting="l", characters=[],
                        shots=[_shot(1, "A sign reads 'SALE 50%'.")])
    seg = Segment(1, 0, 4, 4, "Hello there.")
    [item] = mv.assemble_prompts(_RewritingClient(fail=True), plan, [seg], lip_sync=False)
    assert mv.NO_TEXT_LINE in item["prompt"]
    assert "SALE" not in item["prompt"]
    assert "readable text" in mv.NEGATIVE_PROMPT


def test_bible_text_is_stripped():
    bible = mv.Bible(visual_style="photoreal", setting="Office with a poster reading 'THINK SECURE'.",
                     lighting="soft", characters=[mv.Character(
                         name="Maya", appearance="30s", wardrobe="Grey hoodie printed 'ACME CORP'.")])
    text = mv.bible_text(mv.clean_bible(bible))
    assert "THINK" not in text and "ACME" not in text


def test_text_fix_goes_near_the_top():
    out = mv.with_text_fix("Single shot.\nCAMERA: wide", "monitor screen")
    assert out.splitlines()[1].startswith("FIX:") and "monitor screen" in out
    assert out.endswith("CAMERA: wide")


class _OmniClient:
    """Fake Omni + Gemini: the first `texty_takes` takes show text, later ones are clean."""
    def __init__(self, texty_takes=1, amount="too_much"):
        self.models, self.interactions = self, FakeInteractions()
        self.texty_takes, self.amount = texty_takes, amount

    @property
    def prompts(self):
        return [b["input"] if isinstance(b["input"], str) else b["input"][-1]["text"]
                for b in self.interactions.bodies]

    def generate_content(self, model, contents, config):
        assert config.response_schema is mv.TextCheck
        assert contents[0].inline_data.mime_type == "video/mp4"
        # The check always looks at the newest take.
        texty = len(self.interactions.bodies) <= self.texty_takes
        check = mv.TextCheck(amount=self.amount if texty else "none",
                             where="monitor screen" if texty else "")
        return SimpleNamespace(parsed=check, text=check.model_dump_json())


ITEMS = [{"index": 1, "duration": 4, "scene": 1, "step": 0, "prompt": "Single shot.\nACTION: types"},
         {"index": 2, "duration": 3, "scene": 1, "step": 1,
          "prompt": "Extend this video by 3 seconds as the same continuous shot; no cut.\nACTION: x"}]


def _clips(tmp_path, monkeypatch, client, items=ITEMS[:1], **kw):
    import google.genai
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setattr(google.genai, "Client", lambda **_: client)
    monkeypatch.setattr(mv, "POLL_SECONDS", 0)
    return mv.generate_clips(items, tmp_path, "16:9", "720p", start_image=None, **kw)


def _saved_interaction(tmp_path, n):
    return json.loads((tmp_path / "clips" / "steps.json").read_text())[str(n)]["interaction"]


def test_clip_with_text_is_retaken(tmp_path, monkeypatch):
    client = _OmniClient(texty_takes=1)
    [clip] = _clips(tmp_path, monkeypatch, client)
    assert _saved_interaction(tmp_path, 1) == "int-2"  # the clean retake replaced the first take
    assert abs(mv.media_duration(clip) - 4) < 0.1
    assert "FIX:" not in client.prompts[0] and "monitor screen" in client.prompts[1]


def test_text_retake_of_an_extension_extends_the_same_take(tmp_path, monkeypatch):
    client = _OmniClient(texty_takes=0)
    _clips(tmp_path, monkeypatch, client, items=ITEMS)
    client.texty_takes = 99  # pretend the extension, not the first take, showed text
    (tmp_path / "clips" / "clip_02.mp4").unlink()
    _clips(tmp_path, monkeypatch, client, items=ITEMS, text_retries=1)
    bodies = client.interactions.bodies[2:]
    assert len(bodies) == 2
    assert all(b["previous_interaction_id"] == "int-1" for b in bodies)


def test_retakes_stop_at_the_limit(tmp_path, monkeypatch):
    client = _OmniClient(texty_takes=99)
    _clips(tmp_path, monkeypatch, client, text_retries=2)
    assert len(client.prompts) == 3


def test_minor_lettering_like_logos_is_kept(tmp_path, monkeypatch):
    client = _OmniClient(texty_takes=99, amount="minor")
    _clips(tmp_path, monkeypatch, client, text_retries=2)
    assert len(client.prompts) == 1


def test_text_check_can_be_turned_off(tmp_path, monkeypatch):
    client = _OmniClient(texty_takes=99)
    _clips(tmp_path, monkeypatch, client, text_retries=0)
    assert len(client.prompts) == 1


def test_old_plans_never_extend_past_the_omni_limit(tmp_path, monkeypatch):
    # A 40 s scene planned by an older version: the last step must become a new take.
    items = [{"index": i + 1, "duration": d, "scene": 1, "step": i,
              "prompt": f"Extend this video by {d} seconds as the same continuous shot; no cut.\nACTION: x"}
             for i, d in enumerate([10, 10, 10, 9])]
    client = _OmniClient(texty_takes=0)
    _clips(tmp_path, monkeypatch, client, items=items)
    tasks = [client.interactions.task(i) for i in range(4)]
    assert tasks == ["text_to_video", "extend", "extend", "image_to_video"]
    assert client.prompts[3].startswith("Single continuous 9-second shot")
