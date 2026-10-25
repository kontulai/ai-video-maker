"""Deterministic checks: the rules the prompts state, applied to what the model answered.
Each check passes or fails with a detail naming the offending shot, scene or graphic."""
import re
from dataclasses import asdict, dataclass

import callouts as fx
import make_video as mv
from segmentation import Segment


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _check(name: str, faults: list[str], note: str = "") -> Check:
    return Check(name, not faults, "; ".join(faults) if faults else note)


# The word limits the planner is given (see the Field descriptions on the models), with a
# quarter slack: a few words over is noise, twice the limit is a model ignoring the rule.
WORD_LIMITS = {
    "visual_style": 30, "setting": 60, "layout": 60, "lighting": 25,
    "appearance": 45, "wardrobe": 30,
    "camera": 20, "action": 45, "expression": 15, "continuity": 25,
    "scene.setting": 40, "scene.layout": 40, "scene.reason": 12,
}
SLACK = 1.25
# A side is fine when named as the camera sees it: frame-left, centre-right...
BARE_SIDE = re.compile(r"(?<!frame-)(?<!centre-)(?<!center-)\b(left|right)\b", re.IGNORECASE)
SPEECH = re.compile(r"\b(speak|speaks|speaking|say|says|saying|explain|explains|explaining|"
                    r"present|presents|presenting|talk|talks|talking|tell|tells|telling|"
                    r"mouth|mouths|mouthing|narrat\w*)\b", re.IGNORECASE)
# "mouth closed" and "closed-mouth smile" are the model obeying the rule, not breaking it.
MOUTH_SHUT = re.compile(r"\bclosed[- ]mouths?\b|\bmouths? (?:closed|shut|still)\b", re.IGNORECASE)
# Things may appear on a screen in frame; this is about people and props joining the shot.
ENTERS = re.compile(r"\b(walks? in|enters?|entering|comes? in|steps? in|"
                    r"joins?|joining|arrives?|arriving)\b", re.IGNORECASE)
MARKUP = re.compile(r"^\s*(#+\s|[-*•]\s|\d+[.)]\s)|\*\*|\[[^\]]*\]\(", re.MULTILINE)
UNSPOKEN = re.compile(r"\d|%|&|\b(e\.g\.|i\.e\.|etc\.|vs\.)", re.IGNORECASE)
_SHOT_LINE = re.compile(r"^(CAMERA|ACTION|EXPRESSION|CONTINUITY): (.*)$")


def _words(text: str) -> int:
    return len(text.split())


def _over(field: str, text: str, label: str) -> str | None:
    limit = WORD_LIMITS[field]
    n = _words(text)
    return f"{label} {field.split('.')[-1]} has {n} words (limit {limit})" if n > limit * SLACK else None


def scene_spans(plan: mv.VideoPlan, segments: list[Segment]) -> list[tuple[int, int, int]]:
    """(first_segment, last_segment, seconds) of each planned scene, in plan order."""
    by_index = {s.index: s for s in segments}
    starts = [sp.first_segment for sp in plan.scenes if sp.first_segment in by_index]
    bounds = sorted(set(starts)) + [segments[-1].index + 1]
    spans = []
    for a, b in zip(bounds, bounds[1:]):
        secs = sum(by_index[i].duration for i in range(a, b) if i in by_index)
        spans.append((a, b - 1, secs))
    return spans


def check_plan(plan: mv.VideoPlan, segments: list[Segment], max_scene: int) -> list[Check]:
    checks = []
    want = [s.index for s in segments]
    got = sorted(s.index for s in plan.shots)
    checks.append(_check("one_shot_per_segment",
                         [] if got == want else [f"shots for segments {got}, wanted {want}"]))

    firsts = [sp.first_segment for sp in plan.scenes]
    order_faults = []
    if not firsts or firsts[0] != 1:
        order_faults.append("the first scene must start with segment 1")
    if firsts != sorted(set(firsts)):
        order_faults.append(f"scene starts out of order or repeated: {firsts}")
    if any(f not in want for f in firsts):
        order_faults.append(f"scene starts at segments that do not exist: {[f for f in firsts if f not in want]}")
    checks.append(_check("scenes_in_order", order_faults))

    spans = scene_spans(plan, segments)
    checks.append(_check("scene_length",
                         [f"scene from segment {a} runs {secs}s (limit {max_scene}s)" for a, _, secs in spans if secs > max_scene],
                         f"{len(spans)} scenes"))
    total = sum(s.duration for s in segments)
    mean = total / len(spans) if spans else total
    checks.append(_check("not_a_slideshow",
                         [] if mean >= 10 or total < 10 else [f"{len(spans)} scenes over {total}s, {mean:.0f}s each on average"],
                         f"{mean:.0f}s per scene"))

    checks.append(_check("cut_scenes_have_setup",
                         [f"cut scene at segment {sp.first_segment} lacks {' and '.join(k for k in ('setting', 'layout') if not getattr(sp, k).strip())}"
                          for sp in plan.scenes if sp.cut and sp.first_segment != 1
                          and not (sp.setting.strip() and sp.layout.strip())]))

    texty, speech, sides, over = [], [], [], []
    for field in ("visual_style", "setting", "layout", "lighting"):
        text = getattr(plan, field)
        if mv.has_screen_text(text):
            texty.append(f"bible {field}: {text[:100]}")
        if (o := _over(field, text, "bible")):
            over.append(o)
    if BARE_SIDE.search(plan.layout):
        sides.append(f"bible layout: {plan.layout[:100]}")
    for c in plan.characters:
        for field in ("appearance", "wardrobe"):
            text = getattr(c, field)
            if mv.has_screen_text(text):
                texty.append(f"{c.name} {field}: {text[:100]}")
            if (o := _over(field, text, c.name)):
                over.append(o)
    for sp in plan.scenes:
        for field in ("setting", "layout", "reason"):
            text = getattr(sp, field)
            if mv.has_screen_text(text):
                texty.append(f"scene {sp.first_segment} {field}: {text[:100]}")
            if (o := _over(f"scene.{field}", text, f"scene {sp.first_segment}")):
                over.append(o)
        if BARE_SIDE.search(sp.layout):
            sides.append(f"scene {sp.first_segment} layout: {sp.layout[:100]}")
    for shot in plan.shots:
        for field in mv.SHOT_FIELDS:
            text = getattr(shot, field)
            if mv.has_screen_text(text):
                texty.append(f"shot {shot.index} {field}: {text[:100]}")
            if field != "expression" and BARE_SIDE.search(text):
                sides.append(f"shot {shot.index} {field}: {text[:100]}")
            if field != "camera" and SPEECH.search(MOUTH_SHUT.sub("", text)):
                speech.append(f"shot {shot.index} {field}: {text[:100]}")
            if (o := _over(field, text, f"shot {shot.index}")):
                over.append(o)
    checks.append(_check("no_screen_text", texty))
    checks.append(_check("nobody_speaks", speech))
    checks.append(_check("frame_terms", sides))
    checks.append(_check("word_limits", over))
    return checks


def check_prompts(items: list[dict]) -> list[Check]:
    return [_check("prompt_length",
                   [f"prompt {it.get('index', i + 1)} is {len(it['prompt'])} chars (limit {mv.MAX_PROMPT_CHARS})"
                    for i, it in enumerate(items) if len(it["prompt"]) > mv.MAX_PROMPT_CHARS],
                   f"longest {max(len(it['prompt']) for it in items)} chars" if items else "")]


def _norm(text: str) -> str:
    return re.sub(r"[^\w\s]", "", text).lower()


def check_callouts(callouts: list[fx.Callout], segments: list[Segment], density: str) -> list[Check]:
    narration = {s.index: _norm(s.text) for s in segments}
    anchors, lengths, per_seg, looks, kinds = [], [], [], [], []
    count: dict[int, int] = {}
    lists: dict[str, tuple[str, str]] = {}
    for c in callouts:
        label = f"'{c.text}' in segment {c.segment_index}"
        if c.segment_index not in narration:
            anchors.append(f"{label}: no such segment")
        elif _norm(c.anchor) not in narration[c.segment_index] or not c.anchor.strip():
            anchors.append(f"{label}: anchor '{c.anchor}' is not in the narration")
        words = _words(re.sub(r"^\d+[.)]\s*", "", c.text))  # a list item's number is not a word
        if not 1 <= words <= 4 or len(c.text) > 22:
            lengths.append(f"{label}: {words} words, {len(c.text)} chars")
        count[c.segment_index] = count.get(c.segment_index, 0) + 1
        if c.style not in fx.STYLES or c.position not in fx.POSITIONS \
                or (c.style == "bullet" and c.position not in fx.BULLET_POSITIONS):
            kinds.append(f"{label}: {c.style} at {c.position}")
        if c.list_name.strip():
            look = (c.style, c.position)
            key = c.list_name.strip().lower()
            if key in lists and lists[key] != look:
                looks.append(f"list '{key}': {look} differs from {lists[key]}")
            lists.setdefault(key, look)
    per_seg = [f"segment {i} has {n} graphics (limit {fx.MAX_PER_SEGMENT})" for i, n in count.items() if n > fx.MAX_PER_SEGMENT]
    total = segments[-1].end
    target = max(1, round(total / fx.DENSITY_SECONDS[density]))
    n = len(callouts)
    density_faults = [] if 0.5 * target <= n <= 1.5 * target else [f"{n} graphics for a target of about {target}"]
    return [_check("anchor_in_narration", anchors), _check("callout_text_length", lengths),
            _check("callouts_per_segment", per_seg), _check("callout_styles", kinds),
            _check("list_items_alike", looks), _check("callout_density", density_faults, f"{n} graphics, target {target}")]


def check_talk_track(text: str, limits: tuple[int, int] = (150, 220)) -> list[Check]:
    n = _words(text)
    lo, hi = limits[0] / 1.15, limits[1] * 1.15
    return [_check("spoken_prose", [f"markup: {m.group(0)!r}" for m in MARKUP.finditer(text)][:5]),
            _check("spelled_out", [f"not spoken: {m.group(0)!r}" for m in UNSPOKEN.finditer(text)][:5]),
            _check("talk_track_length", [] if lo <= n <= hi else [f"{n} words (wanted {limits[0]} to {limits[1]})"], f"{n} words")]


def check_fit(prompt: str, in_frame: str) -> list[Check]:
    lines = dict(m.groups() for ln in prompt.splitlines() if (m := _SHOT_LINE.match(ln)))
    sides = [f"{k}: {v[:100]}" for k, v in lines.items() if k != "EXPRESSION" and BARE_SIDE.search(v)]
    texty = [f"{k}: {v[:100]}" for k, v in lines.items() if mv.has_screen_text(v)]
    enters = [f"{k}: {v[:100]}" for k, v in lines.items() if k in ("ACTION", "CONTINUITY") and ENTERS.search(v)]
    present = [] if f"{mv.IN_FRAME_PREFIX}{in_frame}" in prompt else ["the frame description is missing from the prompt"]
    return [_check("frame_terms", sides), _check("no_screen_text", texty),
            _check("nobody_enters", enters), _check("in_frame_line", present)]
