"""A Gemini judge: scores a planner answer, a set of graphics, a drafted talk track or a
fitted shot against a short rubric, 1 to 5 per criterion, with the reason."""
import json
import os

from pydantic import BaseModel, Field

import callouts as fx
import make_video as mv
from segmentation import Segment

from .cases import Case

JUDGE_MODEL = os.getenv("JUDGE_MODEL", mv.PROMPT_MODEL)

JUDGE_INSTRUCTIONS = """\
You are a strict reviewer for an AI video pipeline. A planning model was given a
brief and a narration and answered with the material below; the material will
be fed, as written, to a video model that renders exactly what it is told and
cannot show readable text or lip-synced speech. Score the material against each
criterion from 1 (fails it) to 5 (fully meets it). Be hard to please: a 5 means
you would change nothing. Return one score for every criterion, in the order
given, each with a one-sentence reason that quotes the evidence (the shot, scene
or graphic concerned). End with a two-sentence verdict naming the single most
useful improvement.
"""

PLAN_RUBRIC = {
    "faithfulness": ("The bible and the shots show what the brief asks for and, segment by segment, "
                     "what the narration says; nothing is invented or ignored."),
    "concreteness": ("The bible gives exact, filmable detail (colours, materials, positions, ages, "
                     "hairstyles, clothes) and avoids vague words."),
    "continuity": ("Within a scene each shot continues exactly where the previous one ended; scene "
                   "breaks fall where the narration moves on; cut scenes give a usable new setup."),
    "filmability": ("Each shot can happen in its seconds of one take: nothing appears or changes "
                    "by itself, nobody speaks, no readable text is asked for, the camera stays "
                    "within the framing the scene opened on."),
    "layout_clarity": ("Where people, screens and props sit is unambiguous, always as the camera "
                       "sees it (frame-left, frame-right, centre), and consistent across fields."),
}
CALLOUT_RUBRIC = {
    "selection": "The graphics pick what a viewer should remember: names, numbers, offers, steps, the call to action; no transcribed sentences.",
    "timing": "Each graphic is anchored on the words that name it and the video is rarely without one, without crowding any segment.",
    "design": "Styles and positions vary like an edited film, lists look alike and stack, and graphics stay away from faces and the main action in the shot described.",
}
TALK_TRACK_RUBRIC = {
    "coverage": "The narration teaches what the source document teaches, in the order a learner needs, leaving out boilerplate.",
    "spoken_style": "It reads as spoken prose: short plain sentences, contractions, second person, everything spelled out as the voice would say it.",
    "structure": "It opens by saying what the viewer will learn and closes with what to do next, at the length asked for.",
}
FIT_RUBRIC = {
    "matches_frame": "The fitted shot mentions only the people, screens and props the frame description lists, on the sides it gives, and nobody enters.",
    "keeps_point": "The fitted action still makes the point of the planned shot and matches the narration over it.",
}


class Score(BaseModel):
    criterion: str
    score: int = Field(ge=1, le=5)
    reason: str


class Judgement(BaseModel):
    scores: list[Score]
    verdict: str

    def mean(self) -> float:
        return round(sum(s.score for s in self.scores) / len(self.scores), 3) if self.scores else 0.0

    def to_dict(self) -> dict:
        return {"mean": self.mean(), **{s.criterion: s.score for s in self.scores},
                "reasons": {s.criterion: s.reason for s in self.scores}, "verdict": self.verdict}


def judge(client, rubric: dict[str, str], material: str) -> Judgement:
    from google.genai import types

    criteria = "\n".join(f"- {name}: {text}" for name, text in rubric.items())
    resp = client.models.generate_content(
        model=JUDGE_MODEL,
        contents=f"CRITERIA:\n{criteria}\n\n{material}",
        config=types.GenerateContentConfig(
            system_instruction=JUDGE_INSTRUCTIONS,
            response_mime_type="application/json",
            response_schema=Judgement,
        ),
    )
    return resp.parsed or Judgement.model_validate_json(resp.text)


def _segments(segments: list[Segment]) -> str:
    return "\n".join(f"Segment {s.index}: {s.start}s-{s.end}s. Narration: \"{s.text}\"" for s in segments)


def judge_plan(client, case: Case, plan: mv.VideoPlan, items: list[dict],
               segments: list[Segment] | None = None) -> Judgement:
    first_prompts = "\n\n".join(it["prompt"] for it in items[:2])
    material = (f"BRIEF:\n{case.brief}\n\nNARRATION:\n{case.talk_track}\n\n"
                + (f"SEGMENTS:\n{_segments(segments)}\n\n" if segments else "")
                + f"THE PLAN (bible, scenes, one shot per segment):\n{plan.model_dump_json(indent=2)}\n\n"
                f"THE FIRST PROMPTS BUILT FROM IT:\n{first_prompts}")
    return judge(client, PLAN_RUBRIC, material)


def judge_callouts(client, case: Case, segments: list[Segment], shots: dict[int, str],
                   callouts: list[fx.Callout]) -> Judgement:
    shot_list = "\n".join(f"Segment {s.index}: {s.start}s-{s.end}s. Shot: {shots.get(s.index, 'n/a')}\n"
                          f"  Narration: \"{s.text}\"" for s in segments)
    material = (f"BRIEF:\n{case.brief}\n\nSEGMENTS AND SHOTS:\n{shot_list}\n\n"
                f"THE GRAPHICS CHOSEN:\n{json.dumps([c.model_dump() for c in callouts], indent=2)}")
    return judge(client, CALLOUT_RUBRIC, material)


def judge_talk_track(client, case: Case, pdf: bytes, text: str) -> Judgement:
    from google.genai import types

    criteria = "\n".join(f"- {name}: {text}" for name, text in TALK_TRACK_RUBRIC.items())
    material = (f"CRITERIA:\n{criteria}\n\nVIDEO:\n{case.brief}\n\nAUTHOR'S NOTES:\n{case.notes or '(none)'}\n\n"
                f"THE NARRATION DRAFTED FROM THE ATTACHED DOCUMENT:\n{text}")
    resp = client.models.generate_content(
        model=JUDGE_MODEL,
        contents=[types.Part.from_bytes(data=pdf, mime_type="application/pdf"), material],
        config=types.GenerateContentConfig(
            system_instruction=JUDGE_INSTRUCTIONS,
            response_mime_type="application/json",
            response_schema=Judgement,
        ),
    )
    return resp.parsed or Judgement.model_validate_json(resp.text)


def judge_fit(client, item: dict, in_frame: str, fitted: str) -> Judgement:
    material = (f"WHAT THE PREVIOUS CLIP'S LAST FRAME SHOWS:\n{in_frame}\n\n"
                f"NARRATION OVER THE NEW CLIP:\n\"{item.get('text', '')}\"\n\n"
                f"THE PLANNED PROMPT:\n{item['prompt']}\n\nTHE FITTED PROMPT:\n{fitted}")
    return judge(client, FIT_RUBRIC, material)
