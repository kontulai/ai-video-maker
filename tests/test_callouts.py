"""Keyword graphics: anchor timing, overlap handling and the ASS output."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import callouts as fx
from segmentation import Segment, Word


def _words(text, start=0.0, step=0.5):
    return [Word(t, start + i * step, start + i * step + 0.4) for i, t in enumerate(text.split())]


WORDS = _words("Get fifty percent off today. Free shipping on every single order, guaranteed now.")
SEGS = [Segment(1, 0, 4, 4, ""), Segment(2, 4, 8, 4, "")]


def _c(seg, anchor, text="X", style="slam", position="center"):
    return fx.Callout(segment_index=seg, text=text, anchor=anchor, style=style, position=position)


def test_starts_just_before_anchor_words():
    [t] = fx.time_callouts([_c(1, "fifty percent,")], WORDS, SEGS, 8)
    assert abs(t.start - (0.5 - fx.LEAD_IN)) < 1e-6
    assert t.end >= t.start + fx.MIN_SHOW


def test_prefers_match_inside_its_segment_and_falls_back():
    words = _words("order now order now order now order now order now")  # repeats every second
    [t] = fx.time_callouts([_c(2, "order now")], words, SEGS, 8)
    assert t.start >= 4 - fx.LEAD_IN
    [t] = fx.time_callouts([_c(2, "not spoken")], WORDS, SEGS, 8)
    assert abs(t.start - (4 + 0.3 - fx.LEAD_IN)) < 1e-6


def test_no_overlap_and_clipped_to_video_end():
    timed = fx.time_callouts([_c(1, "Get"), _c(1, "fifty"), _c(2, "guaranteed")], WORDS, SEGS, 6.5)
    for a, b in zip(timed, timed[1:]):
        assert a.end <= b.start - fx.GAP + 1e-9
    assert timed[-1].end <= 6.5


def test_ass_has_every_style_and_accent_color():
    timed = [fx.TimedCallout("50% OFF", "slam", "center", 0, 2),
             fx.TimedCallout("Free {shipping}", "swipe", "bottom-left", 2.5, 4),
             fx.TimedCallout("Feel it", "reveal", "top-right", 4.5, 6)]
    ass = fx.write_ass(timed, 1280, 720, "#FFC400", "Avenir Next")
    assert "PlayResX: 1280" in ass and "PlayResY: 720" in ass
    assert "&H00C4FF&" in ass                         # accent as BGR
    assert "{shipping}" not in ass.split("[Events]")[1].replace("{\\", "")  # braces escaped
    assert ",box," in ass                             # swipe bar uses the box style
    assert "\\p1" in ass                              # reveal underline is a drawing
    assert ass.count("Dialogue:") == 7
    assert "0:00:02.50" in ass
