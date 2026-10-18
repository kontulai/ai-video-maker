"""Veo garbles text, so shot descriptions must never ask for readable words."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import make_video as mv
from segmentation import Segment

# Real examples from a run where Veo rendered "EXTERNAL SHARE BOLCKED".
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


class _VeoClient:
    """Fake Veo + Gemini: the first take of each clip shows text, retakes are clean."""
    def __init__(self, texty_takes=1, **kw):
        self.models, self.files, self.prompts = self, self, []
        self.texty_takes, self.takes = texty_takes, 0

    def generate_videos(self, model, source, config):
        self.prompts.append(source.prompt)
        vid = SimpleNamespace(take=len(self.prompts))
        return SimpleNamespace(done=True, error=None,
                               response=SimpleNamespace(generated_videos=[SimpleNamespace(video=vid)]))

    def download(self, file, destination):
        Path(destination).write_text(str(file.take))

    def generate_content(self, model, contents, config):
        assert config.response_schema is mv.TextCheck
        take = int(contents[0].inline_data.data)
        texty = take <= self.texty_takes
        check = mv.TextCheck(has_text=texty, where="monitor screen" if texty else "")
        return SimpleNamespace(parsed=check, text=check.model_dump_json())


def _clips(tmp_path, monkeypatch, client, **kw):
    import google.genai
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setattr(google.genai, "Client", lambda **_: client)
    items = [{"index": 1, "duration": 4, "prompt": "Single shot.\nACTION: types"}]
    return mv.generate_clips(items, tmp_path, "16:9", "720p", chain=False, start_image=None, **kw)


def test_clip_with_text_is_retaken(tmp_path, monkeypatch):
    client = _VeoClient(texty_takes=1)
    [clip] = _clips(tmp_path, monkeypatch, client)
    assert clip.read_text() == "2"  # the clean retake replaced the first take
    assert "FIX:" not in client.prompts[0] and "monitor screen" in client.prompts[1]


def test_retakes_stop_at_the_limit(tmp_path, monkeypatch):
    client = _VeoClient(texty_takes=99)
    _clips(tmp_path, monkeypatch, client, text_retries=2)
    assert len(client.prompts) == 3


def test_text_check_can_be_turned_off(tmp_path, monkeypatch):
    client = _VeoClient(texty_takes=99)
    _clips(tmp_path, monkeypatch, client, text_retries=0)
    assert len(client.prompts) == 1
