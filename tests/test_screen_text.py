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
