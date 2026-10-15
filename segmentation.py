"""Split a timed voice track into Veo-sized segments (4, 6 or 8 seconds).

Pure functions only, so this module can be tested without any API keys.

The idea: video clips are laid back to back starting at t=0, so the cut points
are always at even seconds. We pick the sequence of clip lengths with dynamic
programming so that the cuts land in pauses between words (ideally at the end
of a sentence) instead of in the middle of a word.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict

VEO_DURATIONS = (4, 6, 8)

# Cost tuning. Lower total cost wins.
COST_CUT_INSIDE_WORD = 10.0
BONUS_SENTENCE_END = 3.0
BONUS_CLAUSE_END = 1.5
BONUS_PAUSE = 0.5          # any gap between words
COST_PER_SEGMENT = 0.75    # prefer fewer, longer clips (cheaper, fewer seams)


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
    """Smallest total video length (even seconds, >= 4) that covers the audio."""
    n = max(4, math.ceil(audio_duration))
    return n + (n % 2)


def plan_segments(words: list[Word], audio_duration: float) -> list[Segment]:
    """Choose clip durations (4/6/8 s) covering the audio, cutting at pauses."""
    total = target_length(audio_duration)

    # best[t] = (cost, previous t) for reaching time t with cuts at even seconds.
    best: dict[int, tuple[float, int | None]] = {0: (0.0, None)}
    for t in range(0, total + 1, 2):
        if t not in best:
            continue
        for d in VEO_DURATIONS:
            nt = t + d
            if nt > total:
                continue
            cut_cost = 0.0 if nt == total else _boundary_cost(nt, words)
            cost = best[t][0] + COST_PER_SEGMENT + cut_cost
            if nt not in best or cost < best[nt][0]:
                best[nt] = (cost, t)

    # Walk back from the end.
    cuts = [total]
    while best[cuts[-1]][1] is not None:
        cuts.append(best[cuts[-1]][1])
    cuts.reverse()

    segments: list[Segment] = []
    for i, (a, b) in enumerate(zip(cuts, cuts[1:])):
        last = i == len(cuts) - 2
        seg_words = [
            w.text for w in words
            if a <= (w.start + w.end) / 2 < b or (last and (w.start + w.end) / 2 >= b)
        ]
        segments.append(Segment(i + 1, a, b, b - a, " ".join(seg_words)))
    return segments
