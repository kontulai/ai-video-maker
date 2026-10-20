"""Split a timed voice track into segments for Gemini Omni (3 to 10 seconds each).

Pure functions only, so this module can be tested without any API keys.

The idea: each segment is one generation step, either the first take of a scene
or an extension of the scene so far. Segments are laid back to back from t=0,
so cuts are at whole seconds. We pick the segment lengths with dynamic
programming so the cuts land in pauses between words (ideally at the end of a
sentence) instead of in the middle of a word.

Segments are then grouped into scenes. Omni extends one video to at most 40
seconds, so a longer talk track needs several scenes; each new scene starts
from the last frame of the previous one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict

DURATIONS = tuple(range(3, 11))   # Omni: 3-10 s per generation or extension
MAX_SCENE_SECONDS = 40           # Omni extends a video to at most 40 s

# Cost tuning. Lower total cost wins.
COST_CUT_INSIDE_WORD = 10.0
BONUS_SENTENCE_END = 3.0
BONUS_CLAUSE_END = 1.5
BONUS_PAUSE = 0.5          # any gap between words
COST_PER_SEGMENT = 1.5     # prefer fewer, longer steps (cheaper, fewer seams)
COST_SCENE_BREAK = 100.0   # a new take is a visible jump: as few as possible
SCENE_BREAK_WEIGHT = 3.0   # ...and placed at a sentence end if at all possible


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class Segment:
    index: int
    start: int
    end: int
    duration: int
    text: str
    scene: int = 1      # which continuous take this segment belongs to
    step: int = 0       # 0 = the scene's first take, 1+ = extensions of it

    def to_dict(self) -> dict:
        return asdict(self)


def words_from_alignment(alignment: dict) -> list[Word]:
    """Group ElevenLabs character-level alignment into words."""
    chars = alignment["characters"]
    starts = alignment["character_start_times_seconds"]
    ends = alignment["character_end_times_seconds"]

    words: list[Word] = []
    buf, w_start, w_end = "", None, None
    for ch, s, e in zip(chars, starts, ends):
        if ch.isspace():
            if buf:
                words.append(Word(buf, w_start, w_end))
                buf, w_start, w_end = "", None, None
            continue
        if not buf:
            w_start = s
        buf += ch
        w_end = e
    if buf:
        words.append(Word(buf, w_start, w_end))
    return words


def _boundary_cost(t: float, words: list[Word]) -> float:
    """Cost of cutting the video at time t."""
    for i, w in enumerate(words):
        if w.start < t < w.end:
            # Unavoidable sometimes with a 2 s grid. Cutting near a word edge is
            # less noticeable than cutting through the middle of it.
            depth = min(t - w.start, w.end - t) / max(w.end - w.start, 1e-6)  # 0..0.5
            return COST_CUT_INSIDE_WORD * (0.5 + depth)
        if t <= w.start:
            # t is in the gap before word i, so word i-1 is the one just spoken.
            if i == 0:
                return 0.0
            prev = words[i - 1].text
            if prev.endswith((".", "!", "?", "…", '."', '!"', '?"')):
                return -BONUS_SENTENCE_END
            if prev.endswith((",", ";", ":", "—", "-")):
                return -BONUS_CLAUSE_END
            return -BONUS_PAUSE
    return 0.0  # after the last word


def target_length(audio_duration: float) -> int:
    """Smallest total video length (whole seconds, >= 3) that covers the audio."""
    return max(min(DURATIONS), math.ceil(audio_duration - 1e-6))


def plan_segments(words: list[Word], audio_duration: float,
                  max_scene: int = MAX_SCENE_SECONDS) -> list[Segment]:
    """Choose step durations (3-10 s) covering the audio, cutting at pauses, and
    group them into scenes of at most max_scene seconds. Both are planned
    together, so a new scene starts at the best pause, not wherever the steps
    happened to land."""
    total = target_length(audio_duration)

    # State (t, e): a cut at t seconds, e seconds into the current scene.
    # best[state] = (cost, previous state, scene break at t?).
    State = tuple[int, int]
    best: dict[State, tuple[float, State | None, bool]] = {(0, 0): (0.0, None, False)}
    for t in range(0, total + 1):
        for e in range(0, max_scene + 1):
            if (t, e) not in best:
                continue
            here = best[(t, e)][0]
            for d in DURATIONS:
                nt = t + d
                if nt > total:
                    continue
                cut_cost = 0.0 if nt == total else _boundary_cost(nt, words)
                options = []
                if e + d <= max_scene:
                    options.append(((nt, e + d), here + COST_PER_SEGMENT + cut_cost, False))
                if t > 0 and d <= max_scene:
                    # Start a new scene at t. Few scenes first, then the best pause.
                    brk = COST_SCENE_BREAK + SCENE_BREAK_WEIGHT * _boundary_cost(t, words)
                    options.append(((nt, d), here + brk + COST_PER_SEGMENT + cut_cost, True))
                for state, cost, is_break in options:
                    if state not in best or cost < best[state][0]:
                        best[state] = (cost, (t, e), is_break)

    # Walk back from the cheapest way to reach the end.
    end = min((st for st in best if st[0] == total), key=lambda st: best[st][0])
    cuts, breaks, st = [total], set(), end
    while best[st][1] is not None:
        prev = best[st][1]
        if best[st][2]:
            breaks.add(prev[0])
        cuts.append(prev[0])
        st = prev
    cuts.reverse()

    segments: list[Segment] = []
    scene, step = 1, 0
    for i, (a, b) in enumerate(zip(cuts, cuts[1:])):
        if a in breaks:
            scene, step = scene + 1, 0
        last = i == len(cuts) - 2
        seg_words = [
            w.text for w in words
            if a <= (w.start + w.end) / 2 < b or (last and (w.start + w.end) / 2 >= b)
        ]
        segments.append(Segment(i + 1, a, b, b - a, " ".join(seg_words), scene, step))
        step += 1
    return segments
