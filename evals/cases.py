"""Eval cases: a brief and a talk track (and, optionally, fixed segments, frames to fit
to and a PDF to draft from), stored as JSON under evals/cases/."""
import json
from pathlib import Path
from typing import Sequence

from pydantic import BaseModel, Field

from segmentation import MAX_SCENE_SECONDS, Segment, Word, plan_segments

WORDS_PER_SECOND = 2.6   # a steady narrator; the timings stand in for ElevenLabs' alignment
CASES_DIR = Path(__file__).resolve().parent / "cases"


class InFrame(BaseModel):
    """What the previous clip's last frame shows, for fitting one segment's prompt to it."""
    segment: int
    in_frame: str


class Case(BaseModel):
    name: str
    brief: str
    talk_track: str
    segments: list[dict] | None = None   # fixed segments; otherwise timed from the talk track
    max_scene: int = MAX_SCENE_SECONDS
    density: str = "medium"
    in_frame: list[InFrame] = []
    pdf: str | None = None               # a PDF to draft a talk track from, relative to the case file
    notes: str = ""                      # the author's notes for that draft
    path: Path | None = Field(default=None, exclude=True)


def load_cases(directory: Path = CASES_DIR, only: Sequence[str] = ()) -> list[Case]:
    cases = []
    for path in sorted(Path(directory).glob("*.json")):
        case = Case.model_validate_json(path.read_text())
        case.path = path
        if not only or case.name in only:
            cases.append(case)
    return cases


def synthesize_words(text: str, wps: float = WORDS_PER_SECOND) -> list[Word]:
    """Word timings for a talk track read at a steady pace, with a breath after punctuation."""
    words, t = [], 0.2
    per_char = 1 / (wps * 6)
    for w in text.split():
        dur = (len(w) + 1) * per_char
        if w[-1] in ".!?":
            dur += 0.45
        elif w[-1] in ",;:":
            dur += 0.2
        words.append(Word(w, t, t + dur))
        t += dur
    return words


def segments_for(case: Case) -> list[Segment]:
    if case.segments:
        return [Segment(**{"duration": d["end"] - d["start"], **d}) for d in case.segments]
    words = synthesize_words(case.talk_track)
    return plan_segments(words, words[-1].end + 0.3, case.max_scene)


def pdf_bytes(case: Case) -> bytes | None:
    if not case.pdf:
        return None
    base = case.path.parent if case.path else Path.cwd()
    return (base / case.pdf).read_bytes()


def case_json(case: Case) -> dict:
    return json.loads(case.model_dump_json())
