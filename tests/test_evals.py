"""The prompt evals: cases, deterministic checks, the Gemini judge, runs and comparisons."""
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import callouts as fx
import make_video as mv
from evals import cases as ev_cases
from evals import checks as ev_checks
from evals import judge as ev_judge
from evals import runner as ev_runner
from evals.cli import main as evals_main
from fakes import FakeGemini
from segmentation import Segment

CASE = {
    "name": "lathe",
    "brief": "A woodworking workshop. Ana, 40s, turns a bowl on a lathe. Warm photoreal look.",
    "talk_track": ("Welcome to the workshop. Today we turn a small bowl. First, mount the blank "
                   "on the lathe and check it spins true. Then take light cuts with the gouge, "
                   "working from the rim to the centre. Finally, sand it smooth and oil it. "
                   "Now go and turn one yourself."),
    "density": "medium",
    "in_frame": [{"segment": 2, "in_frame": "Ana centre at the lathe, blank spinning, gouge rack frame-right"}],
}


def write_case(directory: Path, data: dict = CASE) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{data['name']}.json"
    path.write_text(json.dumps(data))
    return path


def bible(**over) -> dict:
    base = dict(
        visual_style="Photoreal, warm tungsten grade, shallow depth of field, 35mm look.",
        setting="Timber workshop: lathe centre, tool rack frame-right, window frame-left, sawdust floor.",
        layout="Ana centre facing frame-right, the lathe in front of her, tool rack frame-right background.",
        lighting="Soft window light from frame-left, warm overhead tungsten, gentle shadows.",
        characters=[{"name": "Ana", "appearance": "Woman in her 40s, olive skin, dark hair tied back, "
                     "brown eyes, strong hands.", "wardrobe": "Brown canvas apron over a grey T-shirt."}])
    base.update(over)
    return base


def good_plan(segments: list[Segment]) -> mv.VideoPlan:
    shots = [mv.Shot(index=s.index, camera="Medium shot, locked off", action=f"Ana works at the lathe, step {s.index}",
                     expression="Calm focus", continuity="Same framing, lathe centre") for s in segments]
    scenes = [mv.ScenePlan(first_segment=1, cut=False)]
    if len(segments) > 2:
        scenes.append(mv.ScenePlan(first_segment=segments[len(segments) // 2].index, cut=True,
                                   setting="The lathe bed in close-up, shavings flying", layout="Gouge frame-left, blank centre",
                                   reason="Close-up for the cutting"))
    return mv.VideoPlan(title="Turning a bowl", scenes=scenes, shots=shots, **bible())


def judgement(contents=None) -> "ev_judge.Judgement":
    return ev_judge.Judgement(scores=[ev_judge.Score(criterion="faithfulness", score=4, reason="Follows the brief"),
                                      ev_judge.Score(criterion="continuity", score=5, reason="Shots connect")],
                              verdict="Solid plan")


def callouts_for(contents) -> fx.CalloutPlan:
    return fx.CalloutPlan(accent_color="#FFC400", callouts=[
        fx.Callout(segment_index=1, text="TURN A BOWL", anchor="small bowl", style="slam", position="top-left")])


def fake_client(**kw) -> FakeGemini:
    gem = FakeGemini(plan=lambda contents: good_plan(gem.segments), callouts=callouts_for,
                     judge=judgement, draft=lambda c: mv.TalkTrackDraft(talk_track="You will learn to turn a bowl. " * 10), **kw)
    return gem


# -- cases ------------------------------------------------------------------------------------

def test_cases_load_from_json_and_get_timed_segments(tmp_path):
    write_case(tmp_path / "cases")
    [case] = ev_cases.load_cases(tmp_path / "cases")
    assert case.name == "lathe" and case.max_scene == 30 and case.in_frame[0].segment == 2
    segments = ev_cases.segments_for(case)
    assert [s.index for s in segments] == list(range(1, len(segments) + 1))
    assert all(3 <= s.duration <= 10 for s in segments)
    assert segments[0].start == 0 and segments[-1].end >= 15   # ~55 words at 2.6 words/s
    assert " ".join(s.text for s in segments).split() == case.talk_track.split()


def test_cases_can_carry_their_own_segments_and_be_filtered(tmp_path):
    write_case(tmp_path / "cases", {**CASE, "segments": [
        {"index": 1, "start": 0, "end": 5, "text": "Welcome to the workshop."},
        {"index": 2, "start": 5, "end": 12, "text": "Today we turn a small bowl."}]})
    write_case(tmp_path / "cases", {**CASE, "name": "other"})
    [case] = ev_cases.load_cases(tmp_path / "cases", only=["lathe"])
    segments = ev_cases.segments_for(case)
    assert [(s.index, s.start, s.end, s.duration) for s in segments] == [(1, 0, 5, 5), (2, 5, 12, 7)]
    assert sorted(c.name for c in ev_cases.load_cases(tmp_path / "cases")) == ["lathe", "other"]


# -- deterministic checks ---------------------------------------------------------------------

def segs(n: int, dur: int = 6) -> list[Segment]:
    return [Segment(i, (i - 1) * dur, i * dur, dur, f"Narration {i}") for i in range(1, n + 1)]


def failed(checks) -> set[str]:
    return {c.name for c in checks if not c.passed}


def test_a_well_formed_plan_passes_every_plan_check():
    segments = segs(5)
    checks = ev_checks.check_plan(good_plan(segments), segments, max_scene=30)
    assert checks and failed(checks) == set()
    assert {c.name for c in checks} >= {"one_shot_per_segment", "scenes_in_order", "scene_length",
                                        "cut_scenes_have_setup", "no_screen_text", "nobody_speaks",
                                        "frame_terms", "word_limits", "not_a_slideshow"}


def test_plan_checks_name_each_fault():
    segments = segs(5)
    plan = good_plan(segments)
    plan.shots = plan.shots[:4]                                   # a segment without a shot
    plan.shots[0].action = 'She opens a file labelled "Q3 budget" and speaks to Ben'
    plan.shots[1].camera = "Pan left to the window"               # bare left
    plan.shots[2].action = " ".join(["word"] * 60)                # over the 45-word limit
    plan.scenes = [mv.ScenePlan(first_segment=1, cut=False), mv.ScenePlan(first_segment=2, cut=True),
                   mv.ScenePlan(first_segment=3, cut=True, setting="x", layout="y"), mv.ScenePlan(first_segment=4, cut=True, setting="x", layout="y"),
                   mv.ScenePlan(first_segment=5, cut=True, setting="x", layout="y")]
    checks = ev_checks.check_plan(plan, segments, max_scene=30)
    assert failed(checks) == {"one_shot_per_segment", "no_screen_text", "nobody_speaks", "frame_terms",
                              "word_limits", "cut_scenes_have_setup", "not_a_slideshow"}
    detail = next(c.detail for c in checks if c.name == "no_screen_text")
    assert "shot 1" in detail and "Q3 budget" in detail


def test_plan_checks_catch_scene_order_and_length():
    segments = segs(5, dur=10)
    plan = good_plan(segments)
    plan.scenes = [mv.ScenePlan(first_segment=3, cut=True, setting="x", layout="y"), mv.ScenePlan(first_segment=1, cut=False)]
    assert "scenes_in_order" in failed(ev_checks.check_plan(plan, segments, max_scene=30))
    plan.scenes = [mv.ScenePlan(first_segment=1, cut=False)]        # one 50 s scene, limit 30
    assert "scene_length" in failed(ev_checks.check_plan(plan, segments, max_scene=30))


def test_prompt_checks_watch_the_length_limit():
    ok = [{"index": 1, "prompt": "Short prompt\nACTION: fine"}]
    assert failed(ev_checks.check_prompts(ok)) == set()
    long = [{"index": 1, "prompt": "x" * (mv.MAX_PROMPT_CHARS + 1)}]
    assert failed(ev_checks.check_prompts(long)) == {"prompt_length"}


def test_callout_checks():
    segments = [Segment(1, 0, 10, 10, "Get fifty percent off today"), Segment(2, 10, 20, 10, "Free shipping on every order")]
    good = [fx.Callout(segment_index=1, text="50% OFF", anchor="fifty percent", style="slam", position="center"),
            fx.Callout(segment_index=2, text="FREE SHIPPING", anchor="Free shipping", style="swipe", position="bottom-left")]
    assert failed(ev_checks.check_callouts(good, segments, "medium")) == set()
    bad = [fx.Callout(segment_index=1, text="A rather long line of text here", anchor="half price", style="slam", position="center"),
           fx.Callout(segment_index=1, text="ONE", anchor="Get", style="bullet", position="top-left", list_name="steps"),
           fx.Callout(segment_index=2, text="TWO", anchor="Free", style="swipe", position="bottom-right", list_name="steps")]
    bad_checks = ev_checks.check_callouts(bad, segments, "high")
    assert failed(bad_checks) == {"anchor_in_narration", "callout_text_length", "list_items_alike"}
    assert failed(ev_checks.check_callouts([], segments, "medium")) == {"callout_density"}


def test_talk_track_checks():
    good = ("You will learn how to turn a bowl. " * 20).strip()           # 160 words, spoken prose
    assert failed(ev_checks.check_talk_track(good)) == set()
    bad = "## Steps\n- Mount the blank, e.g. 50% of the way.\n" + "word " * 30
    assert failed(ev_checks.check_talk_track(bad)) == {"spoken_prose", "spelled_out", "talk_track_length"}


def test_fit_checks():
    frame = "Ana centre at the lathe, gouge rack frame-right"
    good = "Header\nIN FRAME NOW: " + frame + "\nCAMERA: Medium shot\nACTION: Ana lifts a gouge from the rack frame-right\nEXPRESSION: Calm\nCONTINUITY: Same"
    assert failed(ev_checks.check_fit(good, frame)) == set()
    bad = "Header\nIN FRAME NOW: " + frame + "\nCAMERA: Pan right to Ben\nACTION: Ben walks in reading a sign that says 'EXIT'\nEXPRESSION: Calm\nCONTINUITY: Same"
    assert failed(ev_checks.check_fit(bad, frame)) == {"frame_terms", "no_screen_text", "nobody_enters"}


def test_checks_accept_what_the_prompts_ask_for():
    """Seen in a real baseline run: the model complying with a rule must not fail its check."""
    segments = [Segment(1, 0, 10, 10, "Mount the blank then lock it down"), Segment(2, 10, 20, 10, "Bring the speed up slowly")]
    plan = good_plan(segments)
    plan.shots[0].expression = "Warm, satisfied closed-mouth smile, mouths closed"
    plan.shots[1].action = "Ana stands centre-left; the shaped bowl sits center-right on the lathe"
    assert failed(ev_checks.check_plan(plan, segments, 30)) == set()
    numbered = [fx.Callout(segment_index=1, text="1. MOUNT THE BLANK", anchor="Mount the blank", style="swipe",
                           position="top-left", list_name="steps"),
                fx.Callout(segment_index=2, text="2. SPEED UP SLOWLY", anchor="speed up slowly", style="swipe",
                           position="top-left", list_name="steps")]
    assert failed(ev_checks.check_callouts(numbered, segments, "medium")) == set()
    frame = "Priya seated frame-left, monitor at centre"
    fit = ("Header\nIN FRAME NOW: " + frame + "\nCAMERA: Medium shot\nACTION: Priya moves the cursor; a dark tooltip "
           "appears beside it on the monitor\nEXPRESSION: Alert\nCONTINUITY: Same")
    assert failed(ev_checks.check_fit(fit, frame)) == set()


# -- the judge --------------------------------------------------------------------------------

def test_judge_scores_a_plan_against_the_rubric(tmp_path):
    write_case(tmp_path / "cases")
    [case] = ev_cases.load_cases(tmp_path / "cases")
    gem = fake_client()
    gem.segments = ev_cases.segments_for(case)
    plan = good_plan(gem.segments)
    items = [{"index": s.index, "prompt": f"prompt {s.index}"} for s in gem.segments]
    result = ev_judge.judge_plan(gem.client(), case, plan, items)
    assert [s.criterion for s in result.scores] == ["faithfulness", "continuity"]
    assert result.mean() == 4.5
    sent = gem.seen[-1]
    assert case.brief in sent and case.talk_track in sent and "Turning a bowl" in sent
    assert all(c in sent for c in ev_judge.PLAN_RUBRIC)      # every criterion is named to the judge


# -- runs -------------------------------------------------------------------------------------

def test_run_suite_writes_per_case_results_and_a_summary(tmp_path):
    write_case(tmp_path / "cases")
    cases = ev_cases.load_cases(tmp_path / "cases")
    gem = fake_client()
    gem.segments = ev_cases.segments_for(cases[0])
    out = tmp_path / "runs" / "a"
    summary = ev_runner.run_suite(gem.client(), cases, out, stages=("plan", "callouts", "fit"), judge=True)
    data = json.loads((out / "lathe.json").read_text())
    assert data["plan"]["title"] == "Turning a bowl" and len(data["items"]) == len(gem.segments)
    assert data["items"][0]["prompt"].startswith("Single continuous")
    assert all(c["passed"] for c in data["checks"]["plan"]) and data["judge"]["plan"]["mean"] == 4.5
    assert data["callouts"]["callouts"][0]["text"] == "TURN A BOWL" and data["checks"]["callouts"]
    assert data["fit"][0]["segment"] == 2 and "IN FRAME NOW: Ana centre" in data["fit"][0]["prompt"]
    assert ", fitted" in data["fit"][0]["prompt"]
    assert gem.fits == 1
    assert summary["cases"] == ["lathe"] and summary["checks"]["plan"]["one_shot_per_segment"] == 1.0
    assert summary["judge"]["plan"]["faithfulness"] == 4.0 and summary["judge"]["plan"]["mean"] == 4.5
    assert summary["prompts"]["planner"] == hashlib.sha256(mv.PLANNER_INSTRUCTIONS.encode()).hexdigest()[:12]
    assert json.loads((out / "summary.json").read_text()) == summary
    md = (out / "summary.md").read_text()
    assert "one_shot_per_segment" in md and "faithfulness" in md and "lathe" in md


def test_run_suite_can_skip_the_judge_and_repeat_cases(tmp_path):
    write_case(tmp_path / "cases")
    cases = ev_cases.load_cases(tmp_path / "cases")
    gem = fake_client()
    gem.segments = ev_cases.segments_for(cases[0])
    out = tmp_path / "runs" / "b"
    summary = ev_runner.run_suite(gem.client(), cases, out, stages=("plan",), judge=False, repeat=2)
    assert "judge" not in json.loads((out / "lathe.json").read_text())
    assert (out / "lathe.2.json").exists() and len(gem.seen) == 2       # two planner calls, no judge
    assert summary["runs"] == 2 and summary["judge"] == {}


def test_a_case_that_fails_is_reported_not_fatal(tmp_path):
    write_case(tmp_path / "cases")
    cases = ev_cases.load_cases(tmp_path / "cases")

    def broken(contents):
        raise RuntimeError("quota")
    gem = FakeGemini(plan=broken)
    out = tmp_path / "runs" / "c"
    summary = ev_runner.run_suite(gem.client(), cases, out, stages=("plan",), judge=False)
    data = json.loads((out / "lathe.json").read_text())
    assert "quota" in data["error"] and summary["errors"] == ["lathe: RuntimeError: quota"]


def test_compare_shows_the_change_between_two_runs(tmp_path):
    a = {"cases": ["lathe"], "runs": 1, "checks": {"plan": {"no_screen_text": 0.5, "frame_terms": 1.0}},
         "judge": {"plan": {"faithfulness": 3.0, "mean": 3.0}}, "prompts": {"planner": "aaaa"}, "errors": []}
    b = {"cases": ["lathe"], "runs": 1, "checks": {"plan": {"no_screen_text": 1.0, "frame_terms": 1.0}},
         "judge": {"plan": {"faithfulness": 4.5, "mean": 4.5}}, "prompts": {"planner": "bbbb"}, "errors": []}
    for name, s in (("a", a), ("b", b)):
        (tmp_path / name).mkdir()
        (tmp_path / name / "summary.json").write_text(json.dumps(s))
    md = ev_runner.compare(tmp_path / "a", tmp_path / "b")
    assert "no_screen_text" in md and "50%" in md and "100%" in md and "+50%" in md
    assert "faithfulness" in md and "+1.5" in md
    assert "planner" in md and "aaaa" in md and "bbbb" in md


# -- the command line -------------------------------------------------------------------------

def test_cli_runs_with_prompt_overrides_and_restores_them(tmp_path, capsys):
    write_case(tmp_path / "cases")
    variant = tmp_path / "planner.txt"
    variant.write_text("You are a terse director.\n")
    gem = fake_client()
    gem.segments = ev_cases.segments_for(ev_cases.load_cases(tmp_path / "cases")[0])
    before = mv.PLANNER_INSTRUCTIONS
    rc = evals_main(["run", "--cases", str(tmp_path / "cases"), "--out", str(tmp_path / "runs"),
                     "--stages", "plan", "--no-judge", "--prompt", f"planner={variant}", "--label", "terse"],
                    client_factory=lambda: gem.client())
    assert rc == 0 and mv.PLANNER_INSTRUCTIONS == before
    [run_dir] = [p for p in (tmp_path / "runs").iterdir() if p.is_dir()]
    assert run_dir.name.endswith("-terse")
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["prompts"]["planner"] == hashlib.sha256(variant.read_text().encode()).hexdigest()[:12]
    assert summary["label"] == "terse"
    assert str(run_dir) in capsys.readouterr().out


def test_cli_compares_and_lists(tmp_path, capsys):
    for name, score in (("a", 3.0), ("b", 4.0)):
        (tmp_path / name).mkdir()
        (tmp_path / name / "summary.json").write_text(json.dumps(
            {"cases": ["x"], "runs": 1, "checks": {}, "judge": {"plan": {"mean": score}}, "prompts": {}, "errors": []}))
    assert evals_main(["compare", str(tmp_path / "a"), str(tmp_path / "b")]) == 0
    assert "+1.0" in capsys.readouterr().out
    write_case(tmp_path / "cases")
    assert evals_main(["list", "--cases", str(tmp_path / "cases")]) == 0
    assert "lathe" in capsys.readouterr().out


def test_cli_refuses_an_unknown_prompt_name(tmp_path, capsys):
    rc = evals_main(["run", "--cases", str(tmp_path), "--out", str(tmp_path / "runs"), "--prompt", "nope=x"],
                    client_factory=lambda: None)
    assert rc == 2 and "nope" in capsys.readouterr().err


# -- the shipped cases ------------------------------------------------------------------------

def test_shipped_cases_are_valid():
    cases = ev_cases.load_cases(Path(__file__).resolve().parents[1] / "evals" / "cases")
    assert len(cases) >= 3
    for case in cases:
        segments = ev_cases.segments_for(case)
        assert segments and " ".join(s.text for s in segments).split() == case.talk_track.split()
        assert all(1 <= f.segment <= len(segments) for f in case.in_frame)
    assert any(c.in_frame for c in cases)
